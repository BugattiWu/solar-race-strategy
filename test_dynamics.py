import unittest
from types import SimpleNamespace
import numpy as np
import config as c
import test_planner
from dynamics import electrical_power, kinetic_energy_wh
from planner import optimize
from replay import plan_trajectory

class DynamicsTests(unittest.TestCase):
    def setUp(self):
        self.vehicle=SimpleNamespace(**{name:getattr(c,name) for name in dir(c) if name.isupper()})

    def test_regen_efficiency_and_limit(self):
        motor,net=electrical_power(np.array([-1000.,-10000.,1000.]),0,self.vehicle)
        self.assertAlmostEqual(motor[0],-650)
        self.assertAlmostEqual(motor[1],-2000)
        self.assertAlmostEqual(motor[2],1000/c.DRIVETRAIN_EFFICIENCY)
        self.assertAlmostEqual(net[0],650-c.AUXILIARY_POWER_W)

    def test_combined_charge_limit(self):
        _,net=electrical_power(np.array([-10000.,-10000.]),np.array([2800.,4000.]),self.vehicle)
        self.assertTrue((net<=c.MAX_BATTERY_CHARGE_POWER_W).all())
        self.vehicle.REGEN_EFFICIENCY=0
        motor,_=electrical_power(np.array([-1000.]),0,self.vehicle)
        self.assertEqual(motor[0],0)

    def test_acceleration_energy_conservation(self):
        fixture=test_planner.PlannerTests();fixture.setUp()
        weather=fixture.weather
        weather['fields'][:,0,:]=0
        parameters=dict(AIR_DENSITY_KG_M3=0,ROLLING_RESISTANCE_COEFFICIENT=0,AUXILIARY_POWER_W=0,DRIVETRAIN_EFFICIENCY=.8)
        plan=optimize(fixture.route,weather,fixture.start,4000,1000,80,parameters=parameters)
        final_speed=plan.end_speed_kmh.iloc[-1]/3.6
        self.assertAlmostEqual(4000-plan.battery_wh.iloc[-1],kinetic_energy_wh(c.MASS_KG,0,final_speed)/.8,places=6)
        self.assertGreater(plan_trajectory(plan).speed_kmh.iloc[-1],plan_trajectory(plan).speed_kmh.iloc[0])

    def test_deceleration_rate_and_minimum_cruise(self):
        fixture=test_planner.PlannerTests();fixture.setUp()
        plan=optimize(fixture.route,fixture.weather,fixture.start,4000,1000,80,
                      initial_speed_kmh=80,parameters={'MIN_PLANNING_SPEED_KMH':40,'MAX_DECELERATION_M_S2':.3},
                      stops=[{'route_km':1,'delay_s':10}])
        trace=plan_trajectory(plan)
        from datetime import datetime
        t=np.r_[fixture.start.timestamp(),[datetime.fromisoformat(t).timestamp() for t in trace.time]]
        v=np.r_[80,trace.speed_kmh]/3.6
        rates=np.diff(v)/np.diff(t)
        self.assertGreaterEqual(rates.min(),-.300001)
        self.assertLessEqual(rates.max(),c.MAX_ACCELERATION_M_S2+.000001)
        self.assertTrue((plan.loc[plan.kind=='drive','target_kmh']>=40).all())
        self.assertTrue((trace.speed_kmh==0).any())

    def test_regen_does_not_overfill_battery(self):
        fixture=test_planner.PlannerTests();fixture.setUp()
        fixture.route['elevation_m']=-fixture.route.distance_m*.02
        plan=optimize(fixture.route,fixture.weather,fixture.start,5000,1000,60,initial_speed_kmh=60)
        self.assertTrue((plan_trajectory(plan).battery_wh<=c.BATTERY_CAPACITY_WH).all())

if __name__=='__main__':unittest.main()
