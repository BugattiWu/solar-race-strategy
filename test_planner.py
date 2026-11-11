"""Regression checks for time-coupled planning and GPS trimming."""
import unittest
from datetime import datetime, timezone
import numpy as np
import pandas as pd
import config as c
from planner import optimize, remaining_route, control_sections, weather_batch

class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.start=datetime(2026,7,23,12,tzinfo=timezone.utc)
        self.route=pd.DataFrame(dict(distance_m=np.arange(0,2100,100),lat=np.linspace(43,43.018,21),lon=np.full(21,-91.),elevation_m=np.zeros(21),heading_rad=np.zeros(21)))
        times=self.start.timestamp()+np.arange(-3600,10801,3600)
        fields=np.zeros((2,4,len(times)))
        fields[:,0,:]=700
        self.weather=dict(times=times,distances=np.array([0,2000]),fields=fields)

    def test_flat_fastest_and_energy(self):
        plan=optimize(self.route,self.weather,self.start,2000,1000,80,initial_speed_kmh=80)
        self.assertTrue((plan.target_kmh==80).all())
        self.assertTrue((plan.battery_wh>=c.MIN_BATTERY_WH).all())
        self.assertAlmostEqual((datetime.fromisoformat(plan.arrival_time.iloc[-1])-self.start).total_seconds(),90)
        self.assertTrue(((plan.end_km-plan.start_km)<=.501).all())

    def test_finish_band_accepts_fastest_and_rejects_excess(self):
        fastest=optimize(self.route,self.weather,self.start,2000,1000,80)
        finish=float(fastest.battery_wh.iloc[-1])
        bounded=optimize(self.route,self.weather,self.start,2000,finish-1,80,finish_max_wh=finish+1)
        self.assertAlmostEqual(bounded.battery_wh.iloc[-1],finish)
        self.assertEqual(bounded.arrival_time.iloc[-1],fastest.arrival_time.iloc[-1])
        adjusted=optimize(self.route,self.weather,self.start,2000,2000,80,finish_max_wh=2020)
        self.assertTrue(2000<=adjusted.battery_wh.iloc[-1]<=2020)
        self.assertGreater(adjusted.arrival_time.iloc[-1],fastest.arrival_time.iloc[-1])
        with self.assertRaisesRegex(ValueError,'upper bound'):
            optimize(self.route,self.weather,self.start,4000,500,80,finish_max_wh=600)
        with self.assertRaisesRegex(ValueError,'band'):
            optimize(self.route,self.weather,self.start,2000,1000,80,finish_max_wh=900)

    def test_gps_projection(self):
        remaining,offset=remaining_route(self.route,43.009,-91)
        self.assertAlmostEqual(remaining.distance_m.iloc[0],1000)
        self.assertLess(offset,1)
        with self.assertRaises(ValueError): remaining_route(self.route,40,-91)

    def test_time_and_night(self):
        self.weather['fields'][:,0,:]=[0,0,1000,0,0]
        values,_=weather_batch(self.weather,500,np.array([self.start.timestamp()-1,self.start.timestamp()+1]))
        self.assertEqual(list(values[0]),[0,1000])

    def test_hill_changes_target_and_resolution(self):
        self.route['elevation_m']=np.r_[np.zeros(11),np.arange(1,11)*8]
        sections=control_sections(self.route)
        self.assertTrue(any(b-a==1 for a,b in sections))
        plan=optimize(self.route,self.weather,self.start,4500,1000,80)
        self.assertGreater(plan.target_kmh.max(),plan.target_kmh.min())

    def test_weather_encounter_changes_with_speed(self):
        self.weather['fields'][:,3,:]=[0,0,100,100,100]
        fast=optimize(self.route,self.weather,self.start,2000,1000,80)
        slow=optimize(self.route,self.weather,self.start,2000,1000,40)
        self.assertGreater(slow.forecast_cloud_percent.iloc[-1],fast.forecast_cloud_percent.iloc[-1])
        self.assertGreater(slow.encounter_time.iloc[-1],fast.encounter_time.iloc[-1])

    def test_missing_weather_fails(self):
        self.weather['fields'][:]=np.nan
        with self.assertRaises(ValueError): optimize(self.route,self.weather,self.start,2000,1000,80)

if __name__=='__main__': unittest.main()
