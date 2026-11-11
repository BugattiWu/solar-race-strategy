"""Repeatable presets. Only array area and efficiency are user supplied."""
PRESETS = {
    'sunstruck': dict(area=6.0,efficiency=28.0,capacity=5000.0,min_soc=10,mass=300.0,rolling=.005,drive_efficiency=92.0,auxiliary=40.0,motor=4000.0,regen=65,regen_limit=2000.0,charge_limit=3000.0,acceleration=.4,deceleration=.3),
    'azimuth': dict(area=4.0,efficiency=23.0,capacity=4000.0,min_soc=10,mass=280.0,rolling=.005,drive_efficiency=92.0,auxiliary=40.0,motor=4000.0,regen=65,regen_limit=2000.0,charge_limit=3000.0,acceleration=.4,deceleration=.3),
}

def apply_preset(state,name):
    for key,value in PRESETS[name].items(): state['car_'+key]=value
    state['physical_mass']=PRESETS[name]['mass']
    state.pop('factor_mass',None)
    state.pop('run_identity',None)
    state.pop('full_race_analysis',None)
