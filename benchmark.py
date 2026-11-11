"""GPX benchmark and time-dependent Open-Meteo weather. SI internally."""
from datetime import datetime, timezone
import xml.etree.ElementTree as ET
import numpy as np
import pandas as pd
import requests
import config as c
from main import longitudinal_cda


def load_gpx(path):
    root = ET.parse(path).getroot()
    segments = root.findall('.//{*}trkseg')
    if len(segments)==1:
        points = segments[0].findall('{*}trkpt')
    elif not segments and len(root.findall('.//{*}rte'))==1:
        points = root.findall('.//{*}rtept')
    else:
        raise ValueError('Expected one continuous GPX track segment or route. Split disconnected tracks before loading.')
    values = []
    for p in points:
        elevation = p.find('{*}ele')
        if elevation is None:
            raise ValueError('GPX elevation is required.')
        values.append((float(p.attrib['lat']), float(p.attrib['lon']), float(elevation.text)))
    data = np.array(values)
    if len(data) < 2 or not np.isfinite(data).all():
        raise ValueError('GPX needs at least two valid points.')
    lat, lon = np.radians(data[:, 0]), np.radians(data[:, 1])
    a = np.sin(np.diff(lat)/2)**2 + np.cos(lat[:-1])*np.cos(lat[1:])*np.sin(np.diff(lon)/2)**2
    distance = np.r_[0, np.cumsum(6371000 * 2 * np.arcsin(np.sqrt(np.clip(a, 0, 1))))]
    keep = np.r_[True, np.diff(distance) > 0.01]
    data, distance = data[keep], distance[keep]
    if len(data)<2 or distance[-1]<=0:
        raise ValueError('GPX must contain at least two distinct locations.')
    if (np.abs(data[:,0])>90).any() or (np.abs(data[:,1])>180).any():
        raise ValueError('Invalid GPX latitude or longitude.')
    # 100 m simulation spacing; elevation smoothing over roughly 500 m.
    grid = np.r_[np.arange(0, distance[-1], 100), distance[-1]]
    route = pd.DataFrame({'distance_m': grid})
    for i, name in enumerate(['lat', 'lon', 'elevation_m']):
        route[name] = np.interp(grid, distance, data[:, i])
    route['elevation_m'] = route.elevation_m.rolling(5, center=True, min_periods=1).mean()
    lat, lon = np.radians(route.lat.to_numpy()), np.radians(route.lon.to_numpy())
    bearing = np.arctan2(np.sin(np.diff(lon))*np.cos(lat[1:]),
                        np.cos(lat[:-1])*np.sin(lat[1:])-np.sin(lat[:-1])*np.cos(lat[1:])*np.cos(np.diff(lon)))
    route['heading_rad'] = np.r_[bearing, bearing[-1]]
    return route


def fetch_weather(route, day, historical):
    anchors = np.unique(np.r_[np.arange(0, len(route), 200), len(route)-1])
    locations = route.iloc[anchors]
    endpoint = 'https://archive-api.open-meteo.com/v1/archive' if historical else 'https://api.open-meteo.com/v1/forecast'
    params = dict(latitude=','.join(map(str, locations.lat)), longitude=','.join(map(str, locations.lon)),
                  hourly='shortwave_radiation,wind_speed_10m,wind_direction_10m,cloud_cover',
                  wind_speed_unit='ms', timeformat='unixtime', timezone='GMT')
    if historical:
        from datetime import timedelta
        params.update(start_date=str(day-timedelta(days=1)), end_date=str(day+timedelta(days=2)))
    else:
        params.update(forecast_days=16, past_days=1)
    response = requests.get(endpoint, params=params, timeout=60)
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, dict):
        payload = [payload]
    fields = []
    times = None
    for location in payload:
        hourly = location['hourly']
        current_times = np.array(hourly['time'], dtype=float)
        if times is not None and not np.array_equal(times, current_times):
            raise ValueError('Weather locations have different time coverage.')
        times = current_times
        direction = np.radians(np.array(hourly['wind_direction_10m'], dtype=float))
        speed = np.array(hourly['wind_speed_10m'], dtype=float)
        fields.append(np.array([hourly['shortwave_radiation'], -speed*np.sin(direction),
                                -speed*np.cos(direction), hourly['cloud_cover']], dtype=float))
    return {'times': times, 'distances': locations.distance_m.to_numpy(), 'fields': np.array(fields),
            'retrieved': datetime.now(timezone.utc).isoformat(), 'source': endpoint, 'raw': payload}


def sample_weather(weather, distance, timestamp):
    times = weather['times']
    if timestamp <= times[0] or timestamp > times[-1]:
        raise ValueError('Arrival falls outside weather coverage. Choose another start date.')
    # GHI is the preceding hour mean, held within its actual interval.
    hour = np.searchsorted(times, timestamp, side='left')
    upper = int(np.clip(np.searchsorted(weather['distances'], distance), 1, len(weather['distances'])-1))
    lower = upper-1
    local = []
    for fields in weather['fields'][lower:upper+1]:
        local.append([fields[0, hour]] + [np.interp(timestamp, times, fields[i]) for i in range(1, 4)])
    values = np.array(local)
    result = np.array([np.interp(distance, weather['distances'][lower:upper+1], values[:, i]) for i in range(4)])
    if not np.isfinite(result).all():
        raise ValueError('Weather is missing for the simulated place/time.')
    return result


def simulate(route, weather, start, speed_kmh, initial_wh, reserve_wh):
    energy = initial_wh
    timestamp = start.timestamp()
    rows = [dict(distance_km=0, battery_wh=energy, time=start.isoformat(), solar_w=0, motor_w=0,
                 irradiance_w_m2=0, wind_m_s=0, cloud_percent=0)]
    reason = ''
    for a, b in zip(route.to_dict('records'), route.to_dict('records')[1:]):
        length = b['distance_m']-a['distance_m']
        speed = speed_kmh/3.6
        duration = length/speed
        ghi, east, north, cloud = sample_weather(weather, (a['distance_m']+b['distance_m'])/2, timestamp+duration/2)
        heading = a['heading_rad']
        forward = speed-east*np.sin(heading)-north*np.cos(heading)
        cross = east*np.cos(heading)-north*np.sin(heading)
        yaw = np.degrees(np.arctan2(cross, forward))
        try:
            cda = longitudinal_cda(yaw)
        except ValueError:
            reason = 'Outside placeholder CFD yaw coverage'
            break
        grade = (b['elevation_m']-a['elevation_m'])/length
        aero = 0.5*c.AIR_DENSITY_KG_M3*(forward**2+cross**2)*cda
        force = aero+c.MASS_KG*9.81*(c.ROLLING_RESISTANCE_COEFFICIENT*np.cos(np.arctan(grade))+grade)
        motor = max(0, force*speed)/c.DRIVETRAIN_EFFICIENCY
        solar = max(0, ghi)*c.ARRAY_AREA_M2*c.SOLAR_EFFICIENCY
        energy = min(c.BATTERY_CAPACITY_WH, energy+(solar-motor-c.AUXILIARY_POWER_W)*duration/3600)
        timestamp += duration
        rows.append(dict(distance_km=b['distance_m']/1000, battery_wh=energy,
                         time=datetime.fromtimestamp(timestamp, start.tzinfo).isoformat(),
                         solar_w=solar, motor_w=motor, irradiance_w_m2=ghi,
                         wind_m_s=np.hypot(east,north), cloud_percent=cloud))
        if motor > c.MAX_MOTOR_ELECTRICAL_POWER_W:
            reason = 'Motor power limit exceeded'
            break
        if energy < c.MIN_BATTERY_WH:
            reason = 'Minimum battery reserve exceeded'
            break
    if not reason and energy < reserve_wh:
        reason = 'Finish reserve missed'
    return pd.DataFrame(rows), reason
