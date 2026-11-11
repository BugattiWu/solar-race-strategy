from datetime import timedelta, datetime
datetime_from_iso = datetime.fromisoformat
import unittest
import numpy as np
import config as c
import test_planner
from planner import optimize
from replay import new_replay, accept_plan, advance_replay, scenario_weather, route_slice, cloud_samples, seek_replay, apply_replay_action

class ReplayTests(unittest.TestCase):
    def setUp(self):
        fixture=test_planner.PlannerTests()
        fixture.setUp()
        self.route,self.weather,self.start=fixture.route,fixture.weather,fixture.start

    def test_pause_step_finish(self):
        state=new_replay(self.start,0,2000,80)
        plan=optimize(self.route,self.weather,self.start,2000,1000,80,initial_speed_kmh=80)
        accept_plan(state,plan)
        advance_replay(state,30)
        self.assertAlmostEqual(state['distance_m'],80/3.6*30)
        self.assertEqual(state['time'],self.start+timedelta(seconds=30))
        advance_replay(state,10000)
        self.assertTrue(state['finished'])
        self.assertFalse(state['running'])
        self.assertEqual(state['distance_m'],2000)
        self.assertAlmostEqual(state['energy_wh'],plan.battery_wh.iloc[-1])

    def test_remaining_replan_and_override(self):
        state=new_replay(self.start,0,2000,80)
        accept_plan(state,optimize(self.route,self.weather,self.start,2000,1000,80,initial_speed_kmh=80))
        advance_replay(state,20)
        state['energy_wh']=3000
        remaining=route_slice(self.route,state['distance_m'],2000)
        new=optimize(remaining,self.weather,state['time'],3000,1000,70,initial_speed_kmh=state['speed_kmh'])
        accept_plan(state,new)
        advance_replay(state,10)
        self.assertGreaterEqual(state['distance_m'],80/3.6*20+70/3.6*10)
        self.assertLessEqual(state['distance_m'],80/3.6*30)
        self.assertGreater(state['energy_wh'],2900)

    def test_seek_back_and_forward(self):
        state=new_replay(self.start,0,2000,80)
        accept_plan(state,optimize(self.route,self.weather,self.start,2000,1000,80,initial_speed_kmh=80))
        advance_replay(state,60)
        seek_replay(state,500)
        self.assertAlmostEqual((state['time']-self.start).total_seconds(),22.5)
        remaining=route_slice(self.route,500,2000)
        accept_plan(state,optimize(remaining,self.weather,state['time'],state['energy_wh'],1000,80,initial_speed_kmh=state['speed_kmh']))
        seek_replay(state,1500)
        self.assertAlmostEqual((state['time']-self.start).total_seconds(),67.5)

    def test_stop_and_replan_during_stop(self):
        stops=[dict(route_km=1,delay_s=60)]
        plan=optimize(self.route,self.weather,self.start,2000,1000,80,stops=stops)
        self.assertEqual(len(plan[plan.target_kmh==0]),1)
        self.assertGreater((datetime_from_iso(plan.arrival_time.iloc[-1])-self.start).total_seconds(),150)
        state=new_replay(self.start,0,2000)
        accept_plan(state,plan)
        stop_row=plan[plan.target_kmh==0].iloc[0]
        halfway=(datetime_from_iso(stop_row.arrival_time)-self.start).total_seconds()-30
        advance_replay(state,halfway)
        self.assertAlmostEqual(state['distance_m'],1000)
        self.assertAlmostEqual(state['pending_stop_s'],30)
        resumed=optimize(route_slice(self.route,1000,2000),self.weather,state['time'],state['energy_wh'],1000,80,stops=stops,initial_delay_s=state['pending_stop_s'])
        self.assertEqual(resumed.target_kmh.iloc[0],0)
        self.assertAlmostEqual((datetime_from_iso(resumed.arrival_time.iloc[-1])-datetime_from_iso(plan.arrival_time.iloc[-1])).total_seconds(),0,places=4)

    def test_repeated_finish_restart_reset(self):
        state=new_replay(self.start,0,2000,80)
        for cycle in range(3):
            accept_plan(state,optimize(self.route,self.weather,self.start,2000,1000,80,initial_speed_kmh=80))
            advance_replay(state,10000)
            self.assertTrue(state['finished'])
            state=apply_replay_action(state,('resume',None),self.start,0,2000,80)
            self.assertFalse(state['finished'])
            self.assertTrue(state['running'])
            self.assertEqual(state['distance_m'],0)
            self.assertEqual(state['energy_wh'],2000)
            self.assertEqual(state['history'],[])
            self.assertIsNone(state['plan'])
        state=apply_replay_action(state,('reset',None),self.start,0,2000,80)
        self.assertFalse(state['running'])

    def test_parameters_isolated(self):
        old=c.MASS_KG
        optimize(self.route,self.weather,self.start,2000,1000,80,parameters={'MASS_KG':400})
        self.assertEqual(c.MASS_KG,old)

    def test_weather_override_and_clouds(self):
        adjusted=scenario_weather(self.weather,0,200,150)
        self.assertTrue((adjusted['fields'][:,0]==0).all())
        self.assertTrue((self.weather['fields'][:,0]==700).all())
        samples=cloud_samples(self.route,adjusted,self.start)
        self.assertTrue(all(row['cloud_percent']==100 for row in samples))
        self.assertEqual(len(route_slice(self.route,2000,2000)),1)

if __name__=='__main__':unittest.main()
