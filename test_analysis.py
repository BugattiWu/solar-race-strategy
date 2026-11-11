import unittest
import test_planner
from planner import optimize
from replay import new_replay,accept_plan,advance_replay,route_slice
from race_analysis import retain_targets,full_race_frames

class AnalysisTests(unittest.TestCase):
    def test_full_history_survives_replanning_and_finish(self):
        fixture=test_planner.PlannerTests();fixture.setUp()
        state=new_replay(fixture.start,0,2000)
        plan=optimize(fixture.route,fixture.weather,fixture.start,2000,1000,80)
        retain_targets(state,plan);accept_plan(state,plan)
        advance_replay(state,50)
        boundary=state['distance_m']/1000
        previous=state['target_history'][0].copy()
        remaining=route_slice(fixture.route,state['distance_m'],2000)
        updated=optimize(remaining,fixture.weather,state['time'],state['energy_wh'],1000,40,initial_speed_kmh=state['speed_kmh'])
        retain_targets(state,updated);accept_plan(state,updated)
        advance_replay(state,10000)
        battery,targets=full_race_frames(state,updated,5000)
        self.assertTrue(state['finished'])
        self.assertEqual(battery.end_km.iloc[0],0)
        self.assertEqual(battery.end_km.iloc[-1],2)
        self.assertEqual(targets.start_km.min(),0)
        self.assertEqual(targets.end_km.max(),2)
        self.assertEqual(targets.target_kmh.iloc[0],previous['target_kmh'])
        self.assertTrue((targets[targets.start_km>=boundary].target_kmh<=40).all())
        self.assertEqual(set(battery.phase),{'Completed'})

if __name__=='__main__': unittest.main()
