import unittest
import test_planner
from road_review import parse_speed, review_roads

class RoadReviewTests(unittest.TestCase):
    def test_units_and_unknown(self):
        self.assertAlmostEqual(parse_speed('35 mph'),56.32704)
        self.assertEqual(parse_speed('50'),50)
        self.assertIsNone(parse_speed('signals'))
        self.assertIsNone(parse_speed('50 @ (school_hours)'))

    def test_direction_and_control_candidate(self):
        fixture=test_planner.PlannerTests();fixture.setUp()
        payload={'elements':[
            {'type':'way','id':1,'tags':{'highway':'primary','maxspeed:forward':'35 mph','maxspeed:backward':'25 mph'},'geometry':[{'lat':43,'lon':-91},{'lat':43.018,'lon':-91}]},
            {'type':'node','id':2,'lat':43.009,'lon':-91,'tags':{'highway':'stop','direction':'forward'}},
            {'type':'node','id':3,'lat':43.009,'lon':-91.01,'tags':{'highway':'traffic_signals'}}]}
        limits,controls=review_roads(fixture.route,payload)
        self.assertTrue((limits.speed_kmh==parse_speed('35 mph')).all())
        self.assertEqual(len(controls),1)
        self.assertEqual(controls.kind.iloc[0],'stop')
        self.assertIn('review',controls.status.iloc[0])

    def test_missing_data_remains_unknown(self):
        fixture=test_planner.PlannerTests();fixture.setUp()
        limits,controls=review_roads(fixture.route,{'elements':[]})
        self.assertTrue(limits.speed_kmh.isna().all())
        self.assertIn('route_km',controls.columns)

if __name__=='__main__':unittest.main()
