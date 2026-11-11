from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import json
import math
import time as wallclock
import pandas as pd
import altair as alt
import streamlit as st
import config as c
from car_settings import PRESETS, apply_preset
from route_planning import render_route_planning
from io import BytesIO
import hashlib
from units import Units
from analysis_view import render_analysis
from replay import plan_trajectory
import numpy as np
from race_analysis import retain_targets, full_race_frames
from benchmark import load_gpx, fetch_weather
from planner import remaining_route, optimize
from linked_view import linked_html, road_grade
from replay import route_slice, scenario_weather, new_replay, advance_replay, accept_plan, seek_replay, apply_replay_action

ROOT = Path(__file__).resolve().parent
try:
    zone_name=st.sidebar.text_input('Race time zone · IANA name',value='America/Chicago')
    ZONE = ZoneInfo(zone_name)
except Exception:
    st.error('Enter a valid time zone, for example America/Chicago or UTC.');st.stop()
st.set_page_config(page_title='Solar race strategy', page_icon='☀️', layout='wide')
st.title('Solar race strategy')
st.caption('Solar race route and strategy simulator')
st.caption('Fastest retained candidate from an approximate search, not a guaranteed global optimum. Preset values other than the supplied array specifications are placeholders.')

@st.cache_data
def get_route(content):
    return load_gpx(BytesIO(content))

@st.cache_data(show_spinner=False,max_entries=4)
def calculate_plan(route, weather, start, energy, reserve, cap, parameters, stops, pending, beam, speed, finish_max):
    return optimize(route,weather,start,energy,reserve,cap,beam_width=beam,parameters=parameters,stops=stops,initial_delay_s=pending,initial_speed_kmh=speed,finish_max_wh=finish_max)

def converted_control(label, key, minimum, maximum, default, step, factor, slider=False):
    """Preserve the physical setting when changing display units."""
    value = min(maximum,max(minimum,st.session_state.get('physical_'+key,default)))
    display_key = 'display_'+key
    changed_units = st.session_state.get('factor_'+key)!=factor
    if changed_units or not minimum*factor <= st.session_state.get(display_key,value*factor) <= maximum*factor:
        st.session_state[display_key] = value*factor
    widget = st.slider if slider else st.number_input
    shown = widget(label,min_value=float(minimum*factor),max_value=float(maximum*factor),step=float(step*factor),key=display_key,format='%.2f')
    canonical = value if changed_units else min(maximum,max(minimum,round(shown/factor,8)))
    st.session_state['physical_'+key] = canonical
    st.session_state['factor_'+key] = factor
    return canonical

uploaded = st.sidebar.file_uploader('Load race route · GPX',type=['gpx'])
route_bytes = uploaded.getvalue() if uploaded else (ROOT/'Seg1B.gpx').read_bytes()
route_id = hashlib.sha256(route_bytes).hexdigest()[:16]
try:
    route = get_route(route_bytes)
except Exception as error:
    st.error(f'Unable to load GPX: {error}');st.stop()
if st.session_state.get('loaded_route_id')!=route_id:
    st.session_state.loaded_route_id=route_id
    for key in ['physical_finish','display_finish','factor_finish','gps_lat','gps_lon','run_identity','full_race_analysis','weather_key','traffic_stops']:
        st.session_state.pop(key,None)
st.sidebar.caption(f"Route: {uploaded.name if uploaded else 'Seg1B.gpx'} · {route.distance_m.iloc[-1]/1000:.2f} km")
leg = st.session_state.pop('pending_leg',None)
if leg:
    st.session_state['physical_finish']=float(leg['end_km'])
    st.session_state.pop('factor_finish',None)
    st.session_state.gps_lat=float(np.interp(float(leg['start_km'])*1000,route.distance_m,route.lat))
    st.session_state.gps_lon=float(np.interp(float(leg['start_km'])*1000,route.distance_m,route.lon))
    st.session_state.historical_date=pd.Timestamp(leg['date']).date()
    st.session_state.forecast_date=pd.Timestamp(leg['date']).date()
    st.session_state.race_clock=pd.Timestamp(str(leg['date'])+' '+str(leg['start_time'])).time()
with st.sidebar.expander('Car parameters and presets',expanded=True):
    a,b=st.columns(2)
    a.button('sunstruck',on_click=apply_preset,args=(st.session_state,'sunstruck'))
    b.button('azimuth',on_click=apply_preset,args=(st.session_state,'azimuth'))
    for key,value in PRESETS['sunstruck'].items(): st.session_state.setdefault('car_'+key,value)
    array_area=st.number_input('Array area · m² (maximum 6)',0.1,6.0,step=.1,key='car_area')
    array_eff=st.number_input('Array efficiency · %',1.0,40.0,step=.1,key='car_efficiency')/100
    capacity=st.number_input('Usable battery capacity · Wh',100.0,30000.0,step=100.0,key='car_capacity')
    min_soc=st.slider('Minimum permitted battery SOC · %',0,50,key='car_min_soc')
    rolling=st.number_input('Rolling resistance coefficient',0.0,.1,step=.001,format='%.4f',key='car_rolling')
    drive_eff=st.number_input('Drivetrain efficiency · %',1.0,100.0,step=1.0,key='car_drive_efficiency')/100
    st.caption('Preset buttons reset every editable car constant, including the dynamics controls below, and restart the replay. Array assumes a horizontal surface. Remaining preset constants are arbitrary placeholders.')
min_energy=capacity*min_soc/100
length_km = float(route.distance_m.iloc[-1]/1000)
road_limits = pd.read_csv(ROOT/'speed_limits_review.csv') if uploaded is None and (ROOT/'speed_limits_review.csv').exists() else pd.DataFrame()
road_controls = pd.read_csv(ROOT/'traffic_controls_review.csv') if uploaded is None and (ROOT/'traffic_controls_review.csv').exists() else pd.DataFrame()
today = datetime.now(ZONE).date()
with st.sidebar:
    units = Units(st.radio('Display units',['Metric','Imperial'],horizontal=True,key='display_units')=='Imperial')
    mode = st.selectbox('Run mode',['Historical race replay','Forecast scenario','Live telemetry'])
    historical = mode=='Historical race replay'
    date_key='historical_date' if historical else 'forecast_date'
    st.session_state.setdefault(date_key,datetime(2026,7,23).date() if historical else today)
    st.session_state.setdefault('race_clock',time(9))
    day = st.date_input('Date',key=date_key)
    clock = st.time_input(f'Start time · {zone_name}',key='race_clock')
    initial_pct = st.slider('Initial battery · %',min_soc,100,max(80,min_soc))
    charge_soc = st.slider('Maximum charging SOC during route · %',min_soc,100,100)
    st.caption('Starting above this ceiling is allowed. Solar/regen surplus is curtailed rather than deleting stored energy.')
    end_km = converted_control(f'Finish at route {units.distance}','finish',0.1,length_km,length_km,10,units.distance_factor)
    st.caption('Set a nearer finish to test a shorter section of Seg1B. Reserve applies at this finish.')
    st.session_state.setdefault('gps_lat',float(route.lat.iloc[0]))
    st.session_state.setdefault('gps_lon',float(route.lon.iloc[0]))
    latitude = st.number_input('Initial / manual GPS latitude',format='%.6f',key='gps_lat')
    longitude = st.number_input('Initial / manual GPS longitude',format='%.6f',key='gps_lon')
    cap = converted_control(f'Speed limit assumption · {units.speed}','cap',20,100,80,1,units.distance_factor,True)
    minimum_speed = converted_control(f'Minimum planning speed · {units.speed}','minimum',5,cap,10,1,units.distance_factor,True)
    st.caption('Minimum applies to cruising targets; starts, braking and stops may be slower.')
    finish_pct = st.slider('Target finish SOC · %',min_soc,100,max(20,min_soc))
    tolerance = st.slider('Finish SOC tolerance · ± percentage points',1,30,5)
    finish_low = max(min_soc,finish_pct-tolerance)
    finish_high = min(100,finish_pct+tolerance)
    reserve = capacity*finish_low/100
    finish_max = capacity*finish_high/100
    st.caption(f'Fastest retained plan finishing within {finish_low:.0f}–{finish_high:.0f}% ({reserve:.0f}–{finish_max:.0f} Wh). The lower edge respects the usable battery minimum.')
    initial_speed = converted_control(f'Initial / manual car speed · {units.speed}','initial_speed',0,150,0,5,units.distance_factor)
    refresh = st.button('Fetch / refresh weather',type='primary')
    st.caption('Historical replay uses reanalysis. July 23 is an editable example date. Live weather refreshes every 15 minutes.')
    with st.expander('What-if parameters',expanded=True):
        solar = st.slider('Irradiance · % of weather data',0,150,100,5)
        wind = st.slider('Wind speed · % of weather data',0,200,100,10)
        clouds = st.slider('Cloud cover adjustment · percentage points',-100,100,0,5)
        st.caption('Cloud adjustment changes the overlay. Adjust irradiance to test the energy effect of different cloud conditions.')
        mass = converted_control(f'Total mass · {units.mass}','mass',50,1000,st.session_state.car_mass,10,units.mass_factor)
        auxiliary = st.number_input('Auxiliary load · W',min_value=0.0,max_value=1000.0,step=10.0,key='car_auxiliary')
        margin = st.slider('Solar forecast reduction for planning · %',0,50,0,5)
        beam = st.select_slider('Search width · retained states',options=[96,192,384],value=96)
        st.caption('A solar reduction plans against less incoming energy. More retained states take longer and may recover discarded plans.')
        regen_pct = st.slider('Regen efficiency · % (placeholder)',0,100,key='car_regen')
        regen_limit = st.number_input('Regen electrical power limit · W (placeholder)',min_value=0.0,max_value=10000.0,step=100.0,key='car_regen_limit')
        charge_limit = st.number_input('Battery charge power limit · W (placeholder)',min_value=0.0,max_value=20000.0,step=100.0,key='car_charge_limit')
        acceleration = st.slider('Acceleration limit · m/s² (placeholder)',0.1,1.0,step=0.1,key='car_acceleration')
        deceleration = st.slider('Planned deceleration limit · m/s² (placeholder)',0.1,2.0,step=0.1,key='car_deceleration')
        st.caption('Engineering acceleration units remain SI. Braking may blend regen and friction brakes; this is not a measured motor braking curve.')
        motor_limit = st.number_input('Motor electrical limit · W',min_value=100.0,max_value=20000.0,step=100.0,key='car_motor')

with st.sidebar.expander('Stops and traffic delays'):
    st.caption('Stop entry and telemetry use explicitly labeled km / SI units in both display modes.')
    stop_table = st.data_editor(pd.DataFrame({'route_km':pd.Series(dtype=float),'delay_s':pd.Series(dtype=float)}),num_rows='dynamic',key='traffic_stops')
    st.caption('Enter known stop locations and expected delay in seconds. Traffic signals use your expected wait. No stops are inferred from GPX. Stops at the race finish are excluded.')
    stops = stop_table.dropna(how='all').to_dict('records')
    if any(not all(math.isfinite(float(row[k])) for k in ['route_km','delay_s']) or not 0<=float(row['route_km'])<end_km or float(row['delay_s'])<0 for row in stops):
        st.error('Enter finite stop locations before the finish and nonnegative delays.')
        st.stop()

start = datetime.combine(day,clock,tzinfo=ZONE)
weather_key = (historical,str(day),route_id)
if refresh:
    try:
        if historical and day>today-timedelta(days=5):
            raise ValueError('Choose a historical date at least five days ago.')
        if not historical and not today<=day<=today+timedelta(days=14):
            raise ValueError('Choose a date within the next 14 days.')
        with st.spinner('Fetching weather along Seg1B…'):
            st.session_state.weather = fetch_weather(route,day,historical)
        st.session_state.weather_key = weather_key
    except Exception as error:
        st.error(f'Weather request failed: {error}')
        st.stop()
if st.session_state.get('weather_key')!=weather_key:
    preview_tab,planning_tab = st.tabs(['Race overview','Route planning'])
    with preview_tab:
        st.info('Select a run mode and fetch weather to begin.')
        st.map(route[['lat','lon']])
    with planning_tab:
        render_route_planning(route,route_id,day)
    st.stop()


def battery_chart(plan):
    display = plan.copy()
    display['end_km'] *= units.distance_factor
    base = alt.Chart(display).encode(x=alt.X('end_km:Q',title=f'Route distance ({units.distance})'),
                                  tooltip=[alt.Tooltip('end_km:Q',format='.2f'),alt.Tooltip('battery_wh:Q',format='.0f'),alt.Tooltip('battery_percent:Q',format='.1f')])
    watts = base.mark_line(color='#008c83').encode(y=alt.Y('battery_wh:Q',title='Battery energy (Wh)',scale=alt.Scale(domain=[0,capacity])))
    percent = base.mark_line(opacity=0).encode(y=alt.Y('battery_percent:Q',title='Battery (%)',axis=alt.Axis(orient='right'),scale=alt.Scale(domain=[0,100])))
    st.altair_chart(alt.layer(watts,percent).resolve_scale(y='independent'),width='stretch')


def queue_action(name, argument=None):
    st.session_state.replay_action = (name,argument)
    st.session_state.schedule_changed = True


@st.fragment(run_every='2s' if mode=='Live telemetry' or st.session_state.get('replay',{}).get('running',False) else None)
def dashboard():
    identity = (route_id,capacity,mode,start.isoformat(),latitude,longitude,end_km,initial_pct,initial_speed)
    replay = st.session_state.get('replay')
    try:
        remaining,offset = remaining_route(route,latitude,longitude)
        if remaining.distance_m.iloc[0]>=end_km*1000:
            raise ValueError('Finish must be ahead of the current GPS position.')
        if st.session_state.get('run_identity')!=identity:
            replay = new_replay(start,float(remaining.distance_m.iloc[0]),capacity*initial_pct/100,initial_speed)
            st.session_state.replay = replay
            st.session_state.run_identity = identity
            st.session_state.pop('live_progress',None)
        replay.setdefault('pending_stop_s',0)
        replay.setdefault('speed_kmh',initial_speed)
        replay.setdefault('track',[dict(time=replay['time'].timestamp(),distance_m=replay['distance_m'],energy_wh=replay['energy_wh'])])
        now = wallclock.monotonic()
        action = st.session_state.pop('replay_action',None)
        if action is not None:
            try:
                replay = apply_replay_action(replay,action,start,float(remaining.distance_m.iloc[0]),capacity*initial_pct/100,initial_speed)
                st.session_state.replay = replay
                replay['wall'] = now
                if action[0]=='reset' or action[0]=='resume' and replay['plan'] is None:
                    st.session_state.battery_adjustment = initial_pct
            except ValueError as error:
                st.session_state.seek_error = str(error)
        if historical and replay['running'] and replay['wall'] is not None:
            advance_replay(replay,(now-replay['wall'])*replay['multiplier'])
            if not replay['running']:
                st.rerun()
        replay['wall'] = now
        if historical:
            st.subheader('Historical race replay')
            buttons = st.columns([1,1,1,1,1.3])
            buttons[0].button('Restart race' if replay['finished'] else 'Start / resume',on_click=queue_action,args=('resume',),width='stretch')
            buttons[1].button('Pause',on_click=queue_action,args=('pause',),width='stretch')
            buttons[2].button('Reset replay',on_click=queue_action,args=('reset',),width='stretch')
            buttons[3].button('Run to finish',disabled=replay['finished'],on_click=queue_action,args=('finish',),width='stretch')
            replay['multiplier'] = buttons[4].selectbox('Replay speed',options=[1,10,30,60,120,300,600],index=3,format_func=lambda value:f'{value}×',label_visibility='collapsed',help='Simulated seconds per playback second; computation may reduce achieved speed.')
            with st.expander('Step / jump controls'):
                jumps = st.columns([1.4,1,1,1])
                jump_km = units.km(jumps[0].number_input(f'Jump distance · {units.distance}',min_value=units.d(0.1),max_value=units.d(end_km),value=units.d(min(10.0,end_km)),step=units.d(1.0)))
                jumps[1].button('Back by km',on_click=queue_action,args=('seek',-jump_km))
                jumps[2].button('Forward by km',on_click=queue_action,args=('seek',jump_km))
                jumps[3].button('Step 1 minute',disabled=replay['running'] or replay['finished'],on_click=queue_action,args=('step',))
                st.caption('Jumps restore modeled time and battery. Initial position, date, start time and finish changes reset the run.')
            if st.session_state.get('seek_error'):
                st.warning(st.session_state.pop('seek_error'))
        with st.expander('Change current battery',expanded=True):
            st.session_state.setdefault('battery_adjustment',initial_pct)
            battery_pct = st.slider('Set remaining battery · %',min_soc,100,key='battery_adjustment')
            apply_battery = st.button('Apply battery change')
            if apply_battery and mode!='Live telemetry':
                replay['energy_wh'] = capacity*battery_pct/100
            if mode=='Live telemetry':
                override = st.checkbox('Use battery percentage override for what-if planning')
                st.caption('An enabled override replaces the telemetry battery value for planning.')
            else:
                override = False
        forecast = st.session_state.weather
        telemetry = {}
        if mode=='Live telemetry':
            path = ROOT/'telemetry.json'
            if not path.exists():
                st.info('Waiting for telemetry.json with latitude, longitude, battery_wh and a timestamp including UTC offset.')
                return
            state = json.loads(path.read_text())
            telemetry = {key:float(state[key]) for key in ['speed_kmh','array_power_w','motor_temperature_c','available_motor_power_w'] if key in state}
            if not all(math.isfinite(value) for value in telemetry.values()):
                raise ValueError('Optional telemetry measurements must be finite.')
            current_time = datetime.fromisoformat(state['timestamp'])
            if current_time.utcoffset() is None:
                raise ValueError('Telemetry timestamp needs a UTC offset.')
            age = (datetime.now(ZONE)-current_time).total_seconds()
            if not 0<=age<=60:
                raise ValueError('Telemetry is stale or future-dated. Waiting for a current fix.')
            lat,lon,energy = float(state['latitude']),float(state['longitude']),float(state['battery_wh'])
            if not all(math.isfinite(v) for v in [lat,lon,energy]) or not -90<=lat<=90 or not -180<=lon<=180:
                raise ValueError('Invalid telemetry coordinates or energy.')
            remaining,offset = remaining_route(route,lat,lon,state.get('route_km',st.session_state.get('live_progress')))
            progress = float(remaining.distance_m.iloc[0])
            st.session_state.live_progress = progress/1000
            if override:
                energy = capacity*battery_pct/100
            if (datetime.now(ZONE)-datetime.fromisoformat(forecast['retrieved'])).total_seconds()>900:
                forecast = fetch_weather(route,current_time.date(),False)
                st.session_state.weather = forecast
        else:
            current_time,progress,energy = replay['time'],replay['distance_m'],replay['energy_wh']
        remaining = route_slice(route,min(progress,end_km*1000),end_km*1000)
        forecast = scenario_weather(forecast,solar*(1-margin/100),wind,clouds)
        parameters = dict(ARRAY_AREA_M2=array_area,SOLAR_EFFICIENCY=array_eff,BATTERY_CAPACITY_WH=capacity,MIN_BATTERY_WH=min_energy,MAX_CHARGE_SOC_PERCENT=charge_soc,ROLLING_RESISTANCE_COEFFICIENT=rolling,DRIVETRAIN_EFFICIENCY=drive_eff,MIN_PLANNING_SPEED_KMH=minimum_speed,MAX_ACCELERATION_M_S2=acceleration,MAX_DECELERATION_M_S2=deceleration,MASS_KG=mass,AUXILIARY_POWER_W=auxiliary,MAX_MOTOR_ELECTRICAL_POWER_W=min(motor_limit,telemetry.get('available_motor_power_w',motor_limit)),REGEN_EFFICIENCY=regen_pct/100,MAX_REGEN_ELECTRICAL_POWER_W=regen_limit,MAX_BATTERY_CHARGE_POWER_W=charge_limit)
        current_speed = telemetry.get('speed_kmh',initial_speed) if mode=='Live telemetry' else replay['speed_kmh']
        pending = replay.get('pending_stop_s',0) if mode!='Live telemetry' else float(state.get('stop_remaining_s',0))
        if not math.isfinite(pending) or pending<0:
            raise ValueError('Remaining stop duration must be finite and nonnegative.')
        plan = None
        error_message = None
        if progress>=end_km*1000-.01:
            replay['running'] = False
            replay['finished'] = True
            st.success(f"Finished at {current_time.isoformat()} with {energy:.0f} Wh.")
            if mode!='Live telemetry':
                plan = replay['plan']
        else:
            try:
                plan = calculate_plan(remaining,forecast,current_time,energy,reserve,cap,parameters,stops,pending,beam,current_speed,finish_max)
                if mode!='Live telemetry':
                    previous = replay['plan_start']
                    changed = replay['plan'] is None or previous is None or previous['time']!=current_time or previous['energy_wh']!=energy or not replay['plan'].equals(plan)
                    if changed:
                        retain_targets(replay,plan)
                        accept_plan(replay,plan)
            except ValueError as error:
                error_message = str(error)
                was_running = replay['running']
                replay['running'] = False
                replay['plan'] = None
                if was_running:
                    st.rerun()
        if error_message:
            st.error(error_message)
            st.info('Replay is paused. Adjust battery, weather, vehicle settings or finish distance to seek a feasible plan.')
        if mode!='Live telemetry':
            full_battery,full_targets = full_race_frames(replay,plan,capacity)
            previous_analysis = st.session_state.get('full_race_analysis')
            if full_targets.empty and replay['finished'] and previous_analysis and previous_analysis['time']==current_time:
                full_targets = previous_analysis['targets']
            st.session_state.full_race_analysis = dict(battery=full_battery,targets=full_targets,route=route_slice(route,0,end_km*1000),weather=forecast,time=current_time,progress=progress,finished=replay['finished'],capacity=capacity,start_km=replay['track'][0]['distance_m']/1000,end_km=end_km,array_area=array_area,array_efficiency=array_eff)
        a,b,d,e = st.columns(4)
        completed = progress>=end_km*1000-.01
        target_value = float(plan.target_kmh.iloc[-1 if completed else 0]) if plan is not None else None
        a.metric('Final segment target speed' if completed else 'Target speed now',f'{units.d(target_value):.1f} {units.speed}' if target_value is not None else '—')
        if plan is not None and not completed:
            trace = plan_trajectory(plan)
            stamps = np.array([datetime.fromisoformat(t).timestamp() for t in trace.time])
            advisory = np.interp(current_time.timestamp()+5,np.r_[current_time.timestamp(),stamps],np.r_[current_speed,trace.speed_kmh])
            st.metric('Speed guidance · 5 seconds ahead',f'{units.d(advisory):.1f} {units.speed}')
            st.caption('Guidance follows the modeled acceleration/braking ramp. Segment target is the cruising objective; do not jump instantly to it.')
        b.metric('Remaining battery',f'{100*energy/capacity:.1f}%')
        d.metric('Route position',f'{units.d(progress/1000):.2f} {units.distance}')
        e.metric('Replay status',('Finished' if replay['finished'] else 'Running' if replay['running'] else 'Paused') if historical else mode)
        st.subheader(current_time.strftime('Simulation clock · %b %d, %Y · %H:%M:%S %Z') if historical else current_time.strftime('Telemetry / scenario time · %b %d · %H:%M:%S %Z'))
        details = st.columns(2)
        details[0].metric('Current road gradient',f'{road_grade(route,progress):+.2f}%')
        details[1].metric('Finish crossing speed' if completed else 'Current modeled / measured speed',f'{units.d(current_speed):.1f} {units.speed}')
        st.caption(f"Weather retrieved {forecast['retrieved']}")
        if telemetry:
            st.write('Current telemetry',telemetry)
            st.caption('Measured speed initializes acceleration energy and arrival times. Available motor power limits planning. Array watts and motor temperature are displayed for validation; temperature-based derating requires your motor thermal model.')
        map_tab,analysis_tab,time_tab,speed_tab,roads_tab,planning_tab = st.tabs(['Race overview','Full race analysis','Projected race time','Target speeds','Road data review','Route planning'])
        with planning_tab:
            render_route_planning(route,route_id,day)
        with analysis_tab:
            if mode!='Live telemetry' and st.session_state.get('full_race_analysis'):
                render_analysis(st.session_state.full_race_analysis,units)
            else:
                st.info('Full recorded-race analysis is available for simulation runs.')
        with map_tab:
            if completed:
                st.subheader('Completed race overview')
                final_a,final_b,final_c = st.columns(3)
                final_a.metric('Final target speed',f'{units.d(target_value):.1f} {units.speed}' if target_value is not None else '—')
                final_b.metric('Final battery',f'{100*energy/capacity:.1f}%',f'{energy:.0f} Wh',delta_color='off')
                final_c.metric('Race time',f'{(current_time-start).total_seconds()/3600:.2f} h')
                st.caption('Final target is the last driving-segment instruction, not an instruction to continue beyond the finish.')
                if not reserve-1e-6 <= energy <= finish_max+1e-6:
                    st.warning('This completed run is outside the currently selected SOC band. Restart to apply changed settings.')
            with st.expander('Map appearance and weather layers'):
                theme = st.radio('Map theme',['Dark','Light'],horizontal=True)
                overlay = st.checkbox('Overlay cloud coverage',value=True)
                wind_overlay = st.checkbox('Overlay wind speed and direction',value=False)
                controls_overlay = st.checkbox('Show mapped traffic-control candidates',value=False,disabled=road_controls.empty)
                ahead = st.slider('Weather map time · hours ahead',0,24,0)
            try:
                st.iframe(linked_html(route_slice(route,0,end_km*1000),progress,forecast,current_time+timedelta(hours=ahead),overlay,wind_overlay,theme.lower(),road_controls.to_dict('records') if controls_overlay else [],imperial=units.imperial),height=650,alt='Linked route map and elevation with current position, clouds and wind')
                st.caption(f'Weather overlay valid at {(current_time+timedelta(hours=ahead)).isoformat()}. Scroll over the map to zoom. Hover elevation to locate that point on the map; hover the map to inspect its nearest route point. Wind arrows indicate flow toward; tooltips give speed and direction from.')
            except ValueError as error:
                st.warning(str(error))

        with time_tab:
            elapsed = max(0,(current_time-start).total_seconds())
            if plan is not None:
                arrival = datetime.fromisoformat(plan.arrival_time.iloc[-1])
                left = (arrival-current_time).total_seconds()
                x,y,z = st.columns(3)
                x.metric('Elapsed model time',f'{elapsed/3600:.2f} h')
                y.metric('Projected time remaining',f'{left/3600:.2f} h')
                z.metric('Projected total time',f'{(elapsed+left)/3600:.2f} h')
                st.write(f'Projected finish {arrival.isoformat()}')
                battery_chart(plan)
            history = pd.DataFrame(replay['history'])
            if not history.empty:
                st.dataframe(history)
                st.download_button('Download replay history',history.to_csv(index=False),'replay-history.csv','text/csv')
            st.caption('Projected times include entered stop delays. Race driving windows and uncertain signal phases remain unmodeled.')
        with speed_tab:
            if plan is not None:
                st.line_chart(pd.DataFrame({f'Distance ({units.distance})':plan.start_km*units.distance_factor,f'Target ({units.speed})':plan.target_kmh*units.distance_factor}).set_index(f'Distance ({units.distance})'))
                st.caption('Detailed tables and exports below retain km, km/h and SI units in their column names.')
                st.dataframe(plan)
                st.download_button('Download target speed plan',plan.to_csv(index=False),'speed-plan.csv','text/csv')
            st.caption('Targets cover 100–500 m sections. Each drive row includes forecast cloud cover and irradiance at its final integration midpoint and predicted encounter time. Speed changes shift all subsequent encounters. Stops appear as 0 km/h rows.')
        with roads_tab:
            st.write('Road features from OpenStreetMap are review candidates. Matching uses proximity and road alignment. Conditional limits, parallel roads and the controlled approach at intersections need verification. Missing tags remain unknown. These candidates do not automatically change target speeds or stop delays.')
            if road_limits.empty:
                st.info('Road-data download has not produced a route review yet.')
            else:
                numeric = road_limits[road_limits.speed_kmh.notna()]
                coverage = float((numeric.end_km-numeric.start_km).sum())
                st.metric('Route with numeric speed-limit candidates',f'{coverage:.1f} / {length_km:.1f} km')
                st.dataframe(road_limits)
                st.download_button('Download speed-limit review',road_limits.to_csv(index=False),'speed_limits_review.csv','text/csv')
            if not road_controls.empty:
                st.dataframe(road_controls)
                st.download_button('Download traffic-control review',road_controls.to_csv(index=False),'traffic_controls_review.csv','text/csv')
            if (ROOT/'road_data_osm.json').exists():
                metadata=json.loads((ROOT/'road_data_osm.json').read_text())
                st.caption(f"Downloaded {metadata['retrieved_utc']}. Missing query sections {metadata.get('missing_sections',[])}. OSM edit dates describe feature edits, not field verification.")
            st.caption('Speed-limit assumption remains the planning cap. Enter verified stop delays in the sidebar. A licensed road-attribute feed or race route survey is needed for dependable complete coverage.')
        # Count running time between completed renders, so a slow solve does not
        # cause an unbounded jump on the next UI refresh.
        replay['wall'] = wallclock.monotonic()
        if st.session_state.pop('schedule_changed',False):
            st.rerun()
    except Exception as error:
        if replay is not None:
            replay['running'] = False
            replay['plan'] = None
        st.error(f'No current target available: {error}')


dashboard()
with st.expander('Model assumptions'):
    st.write('Replay follows the integration trace at up to 50 m spacing, preserving modeled speed, time and energy. It uses the same model for planning and simulated motion. Historical weather is reanalysis. Cloud circles visualize sampled cloud fraction. Weather data has hourly resolution and about 20 km sampling along this route. Acceleration and braking include changes in kinetic energy and finite rate limits. Regeneration and combined solar/regen charging respect placeholder electrical limits and usable battery capacity. Entered stops include deceleration, dwell and restart. Thermal effects, detailed motor efficiency maps, verified road-specific speed limits and signal phases remain unmodeled. Array area, efficiency and battery capacity follow the editable car settings. Array is modeled horizontally; GHI includes solar incidence and cloud effects. Other car values remain placeholders. What-if changes are session-local.')
    st.json(dict(MASS_KG=mass,BATTERY_CAPACITY_WH=capacity,MIN_BATTERY_WH=min_energy,ARRAY_AREA_M2=array_area,SOLAR_EFFICIENCY=array_eff,YAW_DEGREES=c.YAW_DEGREES,LONGITUDINAL_CDA_M2=c.LONGITUDINAL_CDA_M2))
st.caption('Weather: Open-Meteo · https://open-meteo.com/ · Default Seg1B GPX credits OpenStreetMap contributors and GraphHopper; uploaded route provenance is supplied by you.')
