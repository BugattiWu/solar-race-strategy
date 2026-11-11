"""Complete replay history plus the remaining projection for the analysis page."""
import pandas as pd
from replay import plan_trajectory


def retain_targets(state, plan):
    """Replace only untraveled targets when a new plan is accepted."""
    progress = state['distance_m']/1000
    previous = pd.DataFrame(state.get('target_history', []))
    if not previous.empty:
        previous = previous[previous.start_km < progress].copy()
        previous['end_km'] = previous.end_km.clip(upper=progress)
    future = plan[['start_km','end_km','target_kmh']].copy()
    state['target_history'] = pd.concat([previous,future],ignore_index=True).to_dict('records')


def full_race_frames(state, plan, capacity):
    track = pd.DataFrame(state['track'])
    battery = pd.DataFrame({'end_km':track.distance_m/1000,'battery_wh':track.energy_wh,
                            'phase':'Completed','time':pd.to_datetime(track.time,unit='s',utc=True).astype(str),
                            'irradiance_w_m2':track.get('irradiance_w_m2',float('nan'))})
    if plan is not None and not state['finished']:
        trace = plan_trajectory(plan)
        future = trace[trace.distance_km > state['distance_m']/1000].copy()
        projected = pd.DataFrame({'end_km':future.distance_km,'battery_wh':future.battery_wh,'phase':'Projected','time':future.time,'irradiance_w_m2':future.get('irradiance_w_m2',float('nan'))})
        # Repeat the current point so the projected curve joins the history.
        anchor = battery.tail(1).assign(phase='Projected')
        battery = pd.concat([battery,anchor,projected],ignore_index=True)
    battery['battery_percent'] = battery.battery_wh/capacity*100
    targets = pd.DataFrame(state.get('target_history',[]))
    if targets.empty and plan is not None:
        targets = plan[['start_km','end_km','target_kmh']].copy()
    return battery,targets
