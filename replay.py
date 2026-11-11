"""Session-local replay, scenario perturbations and cloud overlay samples."""
from datetime import datetime
import numpy as np
import pandas as pd
from planner import weather_batch


def route_slice(route, start_m, finish_m):
    if not route.distance_m.iloc[0] <= start_m <= finish_m <= route.distance_m.iloc[-1]:
        raise ValueError('Requested distance is outside the route.')
    distances = route.distance_m.to_numpy()
    def point(distance):
        index = min(max(int(np.searchsorted(distances, distance, side='right'))-1,0),len(route)-1)
        row = route.iloc[index].copy()
        for name in ['distance_m','lat','lon','elevation_m']:
            row[name] = np.interp(distance,distances,route[name])
        return row
    middle = route[(route.distance_m>start_m)&(route.distance_m<finish_m)]
    if finish_m-start_m < .01:
        return point(finish_m).to_frame().T.reset_index(drop=True)
    return pd.concat([point(start_m).to_frame().T,middle,point(finish_m).to_frame().T],ignore_index=True)


def scenario_weather(weather, solar_percent=100, wind_percent=100, cloud_shift=0):
    result = dict(weather)
    result['fields'] = weather['fields'].copy()
    result['fields'][:,0,:] *= solar_percent/100
    result['fields'][:,1:3,:] *= wind_percent/100
    result['fields'][:,3,:] = np.clip(result['fields'][:,3,:]+cloud_shift,0,100)
    return result


def new_replay(start, distance_m, energy_wh, speed_kmh=0):
    return dict(time=start, distance_m=distance_m, energy_wh=energy_wh, speed_kmh=speed_kmh,
                started=start, running=False, finished=False, plan=None,
                plan_start=None, history=[], multiplier=60, wall=None, pending_stop_s=0,
                track=[dict(time=start.timestamp(),distance_m=distance_m,energy_wh=energy_wh,speed_kmh=speed_kmh)])


def plan_trajectory(plan):
    if 'trajectory' in plan.attrs:
        return pd.DataFrame(plan.attrs['trajectory'])
    return pd.DataFrame(dict(distance_km=plan.end_km,time=plan.arrival_time,
                             battery_wh=plan.battery_wh,speed_kmh=plan.target_kmh))


def advance_replay(state, seconds):
    """Advance using the accepted integration trace, including finite-speed ramps."""
    if seconds<0:
        raise ValueError('Replay interval must be nonnegative.')
    if state['plan'] is None or state['finished'] or seconds==0:
        return
    plan = state['plan']
    trace = plan_trajectory(plan)
    origin = state['plan_start']
    times = np.r_[origin['time'].timestamp(),[datetime.fromisoformat(t).timestamp() for t in trace.time]]
    target = min(state['time'].timestamp()+seconds,times[-1])
    old_time = state['time'].timestamp()
    state.setdefault('track',[])
    for i in np.flatnonzero((times>old_time)&(times<target)):
        row = trace.iloc[i-1]
        state['track'].append(dict(time=float(times[i]),distance_m=float(row.distance_km*1000),energy_wh=float(row.battery_wh),speed_kmh=float(row.speed_kmh),irradiance_w_m2=float(row.get('irradiance_w_m2',np.nan)),cloud_percent=float(row.get('cloud_percent',np.nan))))
    state['distance_m'] = float(np.interp(target,times,np.r_[origin['distance_m'],trace.distance_km.to_numpy()*1000]))
    state['energy_wh'] = float(np.interp(target,times,np.r_[origin['energy_wh'],trace.battery_wh.to_numpy()]))
    state['speed_kmh'] = float(np.interp(target,times,np.r_[origin.get('speed_kmh',0),trace.speed_kmh.to_numpy()]))
    state['time'] = datetime.fromtimestamp(target,state['time'].tzinfo)
    end_times = np.array([datetime.fromisoformat(t).timestamp() for t in plan.arrival_time])
    next_index = min(int(np.searchsorted(end_times,target,side='right')),len(plan)-1)
    row = plan.iloc[next_index]
    state['pending_stop_s'] = max(0,end_times[next_index]-target) if row.target_kmh==0 else 0
    state['track'].append(dict(time=target,distance_m=state['distance_m'],energy_wh=state['energy_wh'],speed_kmh=state['speed_kmh'],irradiance_w_m2=float(trace.iloc[min(max(0,int(np.searchsorted(times,target))-1),len(trace)-1)].get('irradiance_w_m2',np.nan))))
    state['finished'] = target>=times[-1]
    if state['finished']:
        state['running'] = False


def accept_plan(state, plan):
    state['plan'] = plan
    state['plan_start'] = {k:state[k] for k in ['time','distance_m','energy_wh','speed_kmh']}
    state['history'].append(dict(time=state['time'].isoformat(),distance_km=state['distance_m']/1000,
                                 battery_wh=state['energy_wh'],target_kmh=float(plan.target_kmh.iloc[0]),
                                 projected_finish=plan.arrival_time.iloc[-1]))
    # Bound memory for unattended sessions.
    state['history'] = state['history'][-10000:]


def cloud_samples(route, weather, timestamp):
    rows = []
    for distance in weather['distances']:
        values, valid = weather_batch(weather,float(distance),np.array([timestamp.timestamp()]))
        if not valid[0]:
            raise ValueError('Cloud overlay time falls outside available weather.')
        cloud = float(values[3,0])
        rows.append(dict(lon=float(np.interp(distance,route.distance_m,route.lon)),
                         lat=float(np.interp(distance,route.distance_m,route.lat)),
                         cloud_percent=round(cloud,1),label=f'{cloud:.0f}% cloud cover',
                         color=[230,237,245,int(20+cloud*2)],
                         wind_m_s=float(np.hypot(values[1,0],values[2,0])),
                         wind_toward_deg=float(np.degrees(np.arctan2(values[1,0],values[2,0]))%360)))
    return rows


def seek_replay(state, distance_m):
    """Seek through traveled history and the accepted future plan, restoring energy/time."""
    samples = list(state['track'])
    if state['plan'] is not None:
        for row in plan_trajectory(state['plan']).itertuples():
            stamp = datetime.fromisoformat(row.time).timestamp()
            if stamp>state['time'].timestamp():
                samples.append(dict(time=stamp,distance_m=row.distance_km*1000,energy_wh=row.battery_wh,speed_kmh=row.speed_kmh))
    trace = pd.DataFrame(samples).sort_values('time').drop_duplicates('time',keep='last')
    if not trace.distance_m.min()<=distance_m<=trace.distance_m.max():
        raise ValueError('Seek distance must be within traveled history or the current feasible plan.')
    timestamp = float(np.interp(distance_m,trace.distance_m,trace.time))
    energy = float(np.interp(distance_m,trace.distance_m,trace.energy_wh))
    speed = float(np.interp(distance_m,trace.distance_m,trace.get('speed_kmh',pd.Series(0,index=trace.index))))
    state.update(time=datetime.fromtimestamp(timestamp,state['time'].tzinfo),distance_m=distance_m,
                 energy_wh=energy,speed_kmh=speed,running=False,finished=False,plan=None,plan_start=None,pending_stop_s=0)
    state['track'] = trace[trace.time<timestamp].to_dict('records')+[dict(time=timestamp,distance_m=distance_m,energy_wh=energy,speed_kmh=speed)]
    state['history'] = [row for row in state['history'] if datetime.fromisoformat(row['time']).timestamp()<=timestamp]


def apply_replay_action(state, action, start, distance_m, energy_wh, initial_speed_kmh=0):
    """Apply a queued UI command to the latest state, avoiding stale callbacks."""
    name,argument = action
    if name=='reset' or (name=='resume' and state['finished']):
        multiplier = state['multiplier']
        state = new_replay(start,distance_m,energy_wh,initial_speed_kmh)
        state['multiplier'] = multiplier
    if name=='resume':
        state['running'] = True
    elif name=='pause':
        state['running'] = False
    elif name=='step':
        advance_replay(state,60)
    elif name=='seek':
        seek_replay(state,state['distance_m']+argument*1000)
    elif name=='finish':
        advance_replay(state,1e9)
    return state
