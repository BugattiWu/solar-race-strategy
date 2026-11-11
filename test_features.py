import unittest
from io import BytesIO
import numpy as np
import pandas as pd
from dynamics import update_energy
from car_settings import PRESETS,apply_preset
from route_planning import validate_legs
from benchmark import load_gpx
from planner import optimize
import test_planner

class FeatureTests(unittest.TestCase):
    def test_charge_ceiling_preserves_excess_then_caps(self):
        self.assertEqual(update_energy(4500,100,5000,4000),4500)
        self.assertEqual(update_energy(4500,-700,5000,4000),3800)
        self.assertEqual(update_energy(3800,500,5000,4000),4000)
    def test_charge_ceiling_during_stops_and_drive(self):
        f=test_planner.PlannerTests();f.setUp()
        f.weather['fields'][:,0,:]=1500
        p=optimize(f.route,f.weather,f.start,4500,500,30,parameters={'MAX_CHARGE_SOC_PERCENT':80},initial_delay_s=120)
        trace=pd.DataFrame(p.attrs['trajectory'])
        self.assertLessEqual(trace.battery_wh.max(),4500)
        self.assertTrue((np.diff(np.r_[4500,trace.battery_wh])<=1e-7).all())
    def test_presets_reset_all_constants(self):
        state={};apply_preset(state,'sunstruck');state['car_area']=1;state['car_capacity']=9000
        apply_preset(state,'sunstruck')
        for key,value in PRESETS['sunstruck'].items():self.assertEqual(state['car_'+key],value)
        apply_preset(state,'azimuth');self.assertEqual(state['car_area'],4);self.assertEqual(state['car_efficiency'],23)
    def test_leg_validation(self):
        legs=pd.DataFrame([dict(name='a',start_km=0,end_km=10,date='2026-07-23',start_time='09:00'),dict(name='b',start_km=10,end_km=20,date='2026-07-24',start_time='09:00')])
        self.assertEqual(len(validate_legs(legs,20)),2)
        legs.loc[1,'start_km']=11
        with self.assertRaises(ValueError):validate_legs(legs,20)
    def test_uploaded_gpx_and_solar_trace(self):
        route=load_gpx(BytesIO(b'<gpx><trk><trkseg><trkpt lat="43" lon="-91"><ele>200</ele></trkpt><trkpt lat="43.01" lon="-91"><ele>201</ele></trkpt></trkseg></trk></gpx>'))
        self.assertGreater(route.distance_m.iloc[-1],1000)
        f=test_planner.PlannerTests();f.setUp()
        p=optimize(f.route,f.weather,f.start,4000,500,80)
        self.assertTrue(all(row['irradiance_w_m2']==700 for row in p.attrs['trajectory']))
if __name__=='__main__':unittest.main()
