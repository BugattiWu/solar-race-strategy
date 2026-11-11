"""Edit these inputs first. Car/weather values are PLACEHOLDERS except the confirmed 6 m² array.

They are invented for learning and have not been calibrated to your car.
Weather is constant for the entire run, including if the run extends into night.
"""

# PLACEHOLDER car parameters. SI units unless the name says otherwise.
MASS_KG = 300.0  # Car + driver + everything carried.
ROLLING_RESISTANCE_COEFFICIENT = 0.005
DRIVETRAIN_EFFICIENCY = 0.92
AUXILIARY_POWER_W = 40.0
MAX_MOTOR_ELECTRICAL_POWER_W = 4000.0
BATTERY_CAPACITY_WH = 5000.0  # Usable capacity.
START_BATTERY_WH = 2000.0
MIN_BATTERY_WH = 500.0  # Minimum permitted anywhere on the route.
FINISH_RESERVE_WH = 1000.0
ARRAY_AREA_M2 = 6.0  # User-confirmed new car array area.
SOLAR_EFFICIENCY = 0.22  # Lumped cells, wiring, and MPPT conversion efficiency.

# PLACEHOLDER body-axis resisting-force coefficient times reference area, m².
# Replace with your CFD results after checking axes, signs, and reference area.
# Symmetric yaw is assumed. Extreme yaw/flow reversal is unsupported.
YAW_DEGREES = (0.0, 10.0, 20.0, 30.0, 45.0, 60.0)
LONGITUDINAL_CDA_M2 = (0.10, 0.11, 0.14, 0.19, 0.28, 0.40)

# PLACEHOLDER weather, uniform across the route and all timestamps.
AIR_DENSITY_KG_M3 = 1.225
IRRADIANCE_W_M2 = 700.0  # Horizontal array assumption.
WIND_SPEED_M_S = 10.0
WIND_FROM_DEG = 270.0  # Meteorological convention: from west.

# DEMO race settings. Replace with your event and rules.
START_TIME = "2026-11-10T09:00:00-08:00"
MIN_TARGET_KMH = 20
MAX_TARGET_KMH = 100
SPEED_STEP_KMH = 1

# PLACEHOLDER dynamics and battery acceptance limits. Calibrate with telemetry.
REGEN_EFFICIENCY = 0.65  # Mechanical braking energy to DC electrical energy.
MAX_REGEN_ELECTRICAL_POWER_W = 2000.0
MAX_BATTERY_CHARGE_POWER_W = 3000.0  # Solar + regen net battery charging.
MAX_ACCELERATION_M_S2 = 0.4
MAX_DECELERATION_M_S2 = 1.0
