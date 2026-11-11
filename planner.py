"""Bounded beam search over distance, arrival time, battery energy and speed.

Approximate search: discarded states can contain a better or feasible solution.
"""
from datetime import datetime
import numpy as np
import pandas as pd
import config as c
from types import SimpleNamespace
from dynamics import electrical_power, kinetic_energy_wh, update_energy


def remaining_route(route, latitude, longitude, progress_km=None):
    """Project GPS onto route edges, optionally constrained by prior progress."""
    scale = np.cos(np.radians(latitude))
    xy = np.column_stack(((route.lon.to_numpy()-longitude)*scale, route.lat.to_numpy()-latitude))*111195
    edge = np.diff(xy, axis=0)
    fraction = np.clip(-np.sum(xy[:-1]*edge, axis=1)/np.maximum(np.sum(edge*edge, axis=1), 1e-9), 0, 1)
    separation = np.linalg.norm(xy[:-1]+edge*fraction[:,None], axis=1)
    distances = route.distance_m.to_numpy()
    projected = distances[:-1]+np.diff(distances)*fraction
    if progress_km is not None:
        separation[np.abs(projected-progress_km*1000)>2000] = np.inf
    index = int(np.argmin(separation))
    if separation[index] > 300:
        raise ValueError('GPS is more than 300 m from the expected route. Check position/progress.')
    first = route.iloc[index].copy()
    for column in ['lat','lon','elevation_m','distance_m']:
        first[column] += fraction[index]*(route.iloc[index+1][column]-first[column])
    tail = route.iloc[index+1:].copy()
    tail = tail[tail.distance_m > first.distance_m+0.01]
    return pd.concat([first.to_frame().T,tail], ignore_index=True), float(separation[index])


def control_sections(route):
    distance = route.distance_m.to_numpy()
    grade = np.diff(route.elevation_m)/np.diff(distance)
    boundaries = [0]
    for i in range(1,len(route)-1):
        width = distance[i]-distance[boundaries[-1]]
        steep = abs(grade[i]) > .015 or abs(grade[i]-grade[i-1]) > .005
        if width >= (100 if steep else 500):
            boundaries.append(i)
    boundaries.append(len(route)-1)
    return list(zip(boundaries[:-1],boundaries[1:]))


def weather_batch(weather, distance, stamps):
    times = weather['times']
    valid = (stamps>times[0]) & (stamps<=times[-1])
    upper = int(np.clip(np.searchsorted(weather['distances'],distance),1,len(weather['distances'])-1))
    lower = upper-1
    fraction = (distance-weather['distances'][lower])/(weather['distances'][upper]-weather['distances'][lower])
    fields = weather['fields'][lower]*(1-fraction)+weather['fields'][upper]*fraction
    hour = np.clip(np.searchsorted(times,stamps),0,len(times)-1)
    values = np.array([fields[0,hour]]+[np.interp(stamps,times,fields[i]) for i in range(1,4)])
    valid &= np.isfinite(values).all(axis=0)
    return values,valid


def prepare_stops(route, stops):
    """Insert exact stop locations so no control section crosses a stop."""
    result = route.copy()
    result['delay_s'] = 0.0
    for stop in stops or []:
        distance, delay = float(stop['route_km'])*1000, float(stop['delay_s'])
        if not np.isfinite([distance,delay]).all() or delay<0:
            raise ValueError('Stop distances and delays must be finite, with nonnegative delays.')
        # A stop at the current position is handled by pending_stop_s on replay.
        if not result.distance_m.iloc[0]<distance<result.distance_m.iloc[-1] or delay==0:
            continue
        if not np.isclose(result.distance_m,distance,atol=.001,rtol=0).any():
            index = int(np.searchsorted(result.distance_m,distance))-1
            row = result.iloc[index].copy()
            for name in ['distance_m','lat','lon','elevation_m']:
                row[name] = np.interp(distance,result.distance_m,result[name])
            row['delay_s'] = 0.0
            result = pd.concat([result,row.to_frame().T]).sort_values('distance_m').reset_index(drop=True)
        result.loc[np.isclose(result.distance_m,distance,atol=.001,rtol=0),'delay_s'] += delay
    return result


def optimize(route, weather, start, initial_wh, reserve_wh, cap, beam_width=96,
             parameters=None, stops=None, initial_delay_s=0, initial_speed_kmh=0, finish_max_wh=None):
    defaults = {name: getattr(c, name) for name in dir(c) if name.isupper()}
    defaults.update(parameters or {})
    vehicle = SimpleNamespace(**defaults)
    if len(route)<2:
        raise ValueError('GPS is at the route finish.')
    if not vehicle.MIN_BATTERY_WH <= initial_wh <= vehicle.BATTERY_CAPACITY_WH:
        raise ValueError('Current battery is outside configured usable bounds.')
    if not vehicle.MIN_BATTERY_WH <= reserve_wh <= vehicle.BATTERY_CAPACITY_WH:
        raise ValueError('Finish reserve is outside configured usable bounds.')
    finish_max_wh = vehicle.BATTERY_CAPACITY_WH if finish_max_wh is None else finish_max_wh
    if not np.isfinite(finish_max_wh) or not reserve_wh <= finish_max_wh <= vehicle.BATTERY_CAPACITY_WH:
        raise ValueError('Finish SOC band must be ordered and within usable battery bounds.')
    if not np.isfinite(initial_speed_kmh) or initial_speed_kmh<0:
        raise ValueError('Initial speed must be a finite nonnegative value.')
    if not 0<=vehicle.REGEN_EFFICIENCY<=1 or not 0<vehicle.DRIVETRAIN_EFFICIENCY<=1:
        raise ValueError('Check drivetrain and regenerative braking efficiencies.')
    if min(vehicle.MAX_ACCELERATION_M_S2,vehicle.MAX_DECELERATION_M_S2)<=0 or min(vehicle.MAX_REGEN_ELECTRICAL_POWER_W,vehicle.MAX_BATTERY_CHARGE_POWER_W)<0:
        raise ValueError('Acceleration limits must be positive and charging limits nonnegative.')
    charge_ceiling = defaults.get('MAX_CHARGE_SOC_PERCENT',100)/100*vehicle.BATTERY_CAPACITY_WH
    if not vehicle.MIN_BATTERY_WH<=charge_ceiling<=vehicle.BATTERY_CAPACITY_WH:
        raise ValueError('Charge SOC ceiling must lie within usable battery bounds.')
    route = prepare_stops(route,stops)
    minimum_speed = defaults.get('MIN_PLANNING_SPEED_KMH',10)
    if not np.isfinite([minimum_speed,cap]).all() or not 0<minimum_speed<=cap:
        raise ValueError('Minimum planning speed must be positive and no greater than the speed cap.')
    speeds = np.unique(np.r_[np.arange(minimum_speed,cap,5),cap]).astype(float)
    times = np.array([start.timestamp()])
    energies = np.array([initial_wh])
    velocities = np.array([initial_speed_kmh/3.6])
    distance = route.distance_m.to_numpy()
    elevation = route.elevation_m.to_numpy()
    headings = route.heading_rad.to_numpy()
    delays = route.delay_s.to_numpy()
    first_stop = None
    stop_distances = distance[delays>0]

    def wait(t, energy, seconds, location):
        # Split long stops to preserve hourly weather changes and capacity clipping.
        remaining = seconds
        while remaining>0:
            duration = min(remaining,60)
            (ghi,_,_,_),covered = weather_batch(weather,location,t+duration/2)
            if not covered.all():
                raise ValueError('Weather coverage is missing during a planned stop.')
            energy = update_energy(energy,np.minimum(np.maximum(0,ghi)*vehicle.ARRAY_AREA_M2*vehicle.SOLAR_EFFICIENCY-vehicle.AUXILIARY_POWER_W,vehicle.MAX_BATTERY_CHARGE_POWER_W)*duration/3600,vehicle.BATTERY_CAPACITY_WH,charge_ceiling)
            energy = np.where(energy<vehicle.MIN_BATTERY_WH,-np.inf,energy)
            t = t+duration
            remaining -= duration
        return t,energy

    if initial_delay_s>0:
        times,energies = wait(times,energies,initial_delay_s,distance[0])
        velocities[:] = 0
        if energies[0]<vehicle.MIN_BATTERY_WH:
            raise ValueError('Battery reserve would be exceeded during the remaining stop.')
        first_stop = dict(start_km=distance[0]/1000,end_km=distance[0]/1000,target_kmh=0,
                          arrival_time=datetime.fromtimestamp(times[0],start.tzinfo).isoformat(),battery_wh=energies[0],kind='stop',end_speed_kmh=0)
    boundaries = sorted(set([n for pair in control_sections(route) for n in pair]+list(np.flatnonzero(delays>0))))
    history = []
    for begin,end in zip(boundaries[:-1],boundaries[1:]):
        parents = np.repeat(np.arange(len(times)),len(speeds))
        targets = np.tile(speeds,len(times))
        t,energy = times[parents].copy(),energies[parents].copy()
        velocity = velocities[parents].copy()
        valid = np.ones(len(t),dtype=bool)
        rejected = dict(weather=0,CFD_yaw=0,motor_power=0,battery_reserve=0,finish_reserve=0,finish_upper_bound=0,braking=0)
        section_trace = []
        for i in range(begin,end):
            full_length = distance[i+1]-distance[i]
            grade = (elevation[i+1]-elevation[i])/full_length
            # Two substeps resolve acceleration/braking even between nearby stops.
            for half in range(2):
                length = full_length/2
                endpoint = distance[i]+(half+1)*length
                upcoming = stop_distances[stop_distances>=endpoint-.001]
                stopping_distance = max(0,upcoming[0]-endpoint) if len(upcoming) else np.inf
                braking_cap = np.sqrt(2*vehicle.MAX_DECELERATION_M_S2*stopping_distance)
                acceleration_cap = np.sqrt(velocity**2+2*vehicle.MAX_ACCELERATION_M_S2*length)
                braking_floor = np.sqrt(np.maximum(0,velocity**2-2*vehicle.MAX_DECELERATION_M_S2*length))
                desired = np.minimum(targets/3.6,braking_cap)
                new_velocity = np.maximum(braking_floor,np.minimum(desired,acceleration_cap))
                average_velocity = (velocity+new_velocity)/2
                duration = 2*length/np.maximum(velocity+new_velocity,1e-6)
                encounter = t+duration/2
                (ghi,east,north,cloud),covered = weather_batch(weather,endpoint-length/2,encounter)
                forward = average_velocity-east*np.sin(headings[i])-north*np.cos(headings[i])
                cross = east*np.cos(headings[i])-north*np.sin(headings[i])
                yaw = np.abs(np.degrees(np.arctan2(cross,forward)))
                cda = np.interp(yaw,vehicle.YAW_DEGREES,vehicle.LONGITUDINAL_CDA_M2)
                aero = .5*vehicle.AIR_DENSITY_KG_M3*(forward**2+cross**2)*cda
                force = aero+vehicle.MASS_KG*9.81*(vehicle.ROLLING_RESISTANCE_COEFFICIENT*np.cos(np.arctan(grade))+grade)
                kinetic_wh = kinetic_energy_wh(vehicle.MASS_KG,velocity,new_velocity)
                wheel_power = force*average_velocity+kinetic_wh*3600/duration
                solar = np.maximum(0,ghi)*vehicle.ARRAY_AREA_M2*vehicle.SOLAR_EFFICIENCY
                motor,net = electrical_power(wheel_power,solar,vehicle)
                energy = update_energy(energy,net*duration/3600,vehicle.BATTERY_CAPACITY_WH,charge_ceiling)
                t += duration
                velocity = new_velocity
                checks = dict(weather=covered,CFD_yaw=yaw<=vehicle.YAW_DEGREES[-1],
                              motor_power=motor<=vehicle.MAX_MOTOR_ELECTRICAL_POWER_W+1e-6,
                              battery_reserve=energy>=vehicle.MIN_BATTERY_WH,
                              braking=(velocity<=braking_cap+1e-6)&(average_velocity>0))
                for name,passed in checks.items():
                    rejected[name] += int(np.sum(valid & ~passed))
                    valid &= passed
                section_trace.append((endpoint,t.copy(),energy.copy(),velocity.copy(),ghi.copy(),cloud.copy()))
        move_time,move_energy = t.copy(),energy.copy()
        move_velocity = velocity.copy()
        if delays[end]>0:
            velocity[:] = 0
            # Only surviving candidates need weather during the stop.
            indices = np.flatnonzero(valid)
            if len(indices):
                t[indices],energy[indices] = wait(t[indices],energy[indices],delays[end],distance[end])
                passed = energy>=vehicle.MIN_BATTERY_WH
                rejected['battery_reserve'] += int(np.sum(valid & ~passed))
                valid &= passed
        if end==len(route)-1:
            passed = energy>=reserve_wh
            rejected['finish_reserve'] += int(np.sum(valid & ~passed))
            valid &= passed
            passed = energy<=finish_max_wh
            rejected['finish_upper_bound'] += int(np.sum(valid & ~passed))
            valid &= passed
        indices = np.flatnonzero(valid)
        if not len(indices):
            causes = ', '.join(f'{name.replace("_"," ")}: {count}' for name,count in rejected.items() if count)
            if end==len(route)-1 and rejected['finish_upper_bound']:
                causes += f'. Requested finish band {reserve_wh:.0f}–{finish_max_wh:.0f} Wh; retained candidates exceed its upper bound. Widen the tolerance or revise the target. Speed/power caps can prevent using more energy; this approximate search does not prove infeasibility'
            raise ValueError(f'No surviving plan at route km {distance[end]/1000:.1f}. Candidate rejections — {causes}. Search retains at most {beam_width} states; increasing search width can recover discarded alternatives.')
        order = indices[np.argsort(t[indices])]
        bins = {}
        for j in order:
            key = (int((t[j]-start.timestamp())//120),int(energy[j]//50),int(velocity[j]*3.6//5))
            if key not in bins:
                bins[key] = j
        survivors = np.array(list(bins.values()),dtype=int)
        if len(survivors)>beam_width:
            by_time = survivors[np.argsort(t[survivors])]
            by_energy = survivors[np.argsort(energy[survivors])]
            sample = np.linspace(0,len(survivors)-1,beam_width//2,dtype=int)
            survivors = np.unique(np.r_[by_time[sample],by_energy[sample]])
        history.append(dict(begin=begin,end=end,parents=parents[survivors],targets=targets[survivors],
                            time=t[survivors],energy=energy[survivors],move_time=move_time[survivors],move_energy=move_energy[survivors],
                            cloud=cloud[survivors],ghi=ghi[survivors],encounter=encounter[survivors],velocity=move_velocity[survivors],
                            trace=[(d,tt[survivors],ee[survivors],vv[survivors],gg[survivors],cc[survivors]) for d,tt,ee,vv,gg,cc in section_trace]))
        times,energies,velocities = t[survivors],energy[survivors],velocity[survivors]
    chosen = int(np.argmin(times))
    rows = []
    trace = []
    for h in reversed(history):
        begin,end = h['begin'],h['end']
        section_points = [dict(distance_km=d/1000,time=datetime.fromtimestamp(tt[chosen],start.tzinfo).isoformat(),battery_wh=ee[chosen],speed_kmh=vv[chosen]*3.6,irradiance_w_m2=gg[chosen],cloud_percent=cc[chosen]) for d,tt,ee,vv,gg,cc in h['trace']]
        if delays[end]>0:
            section_points.append(dict(distance_km=distance[end]/1000,time=datetime.fromtimestamp(h['time'][chosen],start.tzinfo).isoformat(),battery_wh=h['energy'][chosen],speed_kmh=0))
        trace[0:0] = section_points
        if delays[end]>0:
            rows.append(dict(start_km=distance[end]/1000,end_km=distance[end]/1000,target_kmh=0,
                             arrival_time=datetime.fromtimestamp(h['time'][chosen],start.tzinfo).isoformat(),battery_wh=h['energy'][chosen],kind='stop',end_speed_kmh=0))
        rows.append(dict(start_km=distance[begin]/1000,end_km=distance[end]/1000,target_kmh=h['targets'][chosen],
                         arrival_time=datetime.fromtimestamp(h['move_time'][chosen],start.tzinfo).isoformat(),battery_wh=h['move_energy'][chosen],kind='drive',end_speed_kmh=h['velocity'][chosen]*3.6,
                         encounter_time=datetime.fromtimestamp(h['encounter'][chosen],start.tzinfo).isoformat(),
                         forecast_cloud_percent=h['cloud'][chosen],forecast_irradiance_w_m2=h['ghi'][chosen]))
        chosen = int(h['parents'][chosen])
    rows.reverse()
    if first_stop is not None:
        rows.insert(0,first_stop)
        trace.insert(0,dict(distance_km=distance[0]/1000,time=first_stop['arrival_time'],battery_wh=first_stop['battery_wh'],speed_kmh=0))
    result = pd.DataFrame(rows)
    result['battery_percent'] = result.battery_wh/vehicle.BATTERY_CAPACITY_WH*100
    result.attrs['trajectory'] = trace
    return result
