"""Energy accounting shared by the route optimizer and verification tests."""
import numpy as np


def electrical_power(wheel_power_w, solar_w, vehicle):
    """Positive motor power discharges the battery; negative power is regen."""
    available_regen = np.maximum(0,vehicle.MAX_BATTERY_CHARGE_POWER_W-solar_w+vehicle.AUXILIARY_POWER_W)
    recovered = np.minimum(np.maximum(0,-wheel_power_w)*vehicle.REGEN_EFFICIENCY,
                           np.minimum(vehicle.MAX_REGEN_ELECTRICAL_POWER_W,available_regen))
    motor = np.where(wheel_power_w>=0,wheel_power_w/vehicle.DRIVETRAIN_EFFICIENCY,-recovered)
    net = np.minimum(solar_w-motor-vehicle.AUXILIARY_POWER_W,vehicle.MAX_BATTERY_CHARGE_POWER_W)
    return motor,net


def kinetic_energy_wh(mass_kg, start_m_s, end_m_s):
    return .5*mass_kg*(np.asarray(end_m_s)**2-np.asarray(start_m_s)**2)/3600


def update_energy(energy_wh, delta_wh, capacity_wh, charge_ceiling_wh):
    """Curtail positive charging at ceiling without discarding existing energy."""
    ceiling = np.maximum(np.asarray(energy_wh),charge_ceiling_wh)
    return np.minimum(capacity_wh,np.minimum(ceiling,np.asarray(energy_wh)+delta_wh))
