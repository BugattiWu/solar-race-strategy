"""Learning prototype: steady speed segments, constant demo weather, no regen.

route.csv contains cumulative road distance and elevation at each point.
Heading and speed limit apply FROM that row TO the next row.
No acceleration, stops, battery thermal model, or charging limits are modeled.
"""

import csv
import math
from datetime import datetime, timedelta
from pathlib import Path

import config as c

PROJECT_DIR = Path(__file__).resolve().parent


def load_route():
    with (PROJECT_DIR / "route.csv").open(newline="") as file:
        route = [{key: float(value) for key, value in row.items()}
                 for row in csv.DictReader(file)]
    if len(route) < 2 or route[0]["distance_km"] != 0:
        raise ValueError("Route needs at least two points and must start at 0 km.")
    for row in route:
        if not all(math.isfinite(value) for value in row.values()):
            raise ValueError("Route values must be finite numbers.")
        if row["speed_limit_kmh"] <= 0 or not 0 <= row["heading_deg"] < 360:
            raise ValueError("Speed limits must be positive; headings must be 0–359.999 degrees.")
    for start, end in zip(route, route[1:]):
        length_m = (end["distance_km"] - start["distance_km"]) * 1000
        if length_m <= abs(end["elevation_m"] - start["elevation_m"]):
            raise ValueError("Route distances must increase and exceed vertical changes.")
    return route


def longitudinal_cda(yaw_deg):
    """Linear interpolation of the PLACEHOLDER CFD table. No extrapolation."""
    yaw_deg = abs(yaw_deg)
    if yaw_deg > c.YAW_DEGREES[-1]:
        raise ValueError("Apparent wind yaw exceeds the demo CFD table.")
    for index in range(len(c.YAW_DEGREES) - 1):
        lower, upper = c.YAW_DEGREES[index:index + 2]
        if yaw_deg <= upper:
            fraction = (yaw_deg - lower) / (upper - lower)
            return (c.LONGITUDINAL_CDA_M2[index] * (1 - fraction)
                    + c.LONGITUDINAL_CDA_M2[index + 1] * fraction)
    raise ValueError("Invalid yaw table.")


def motor_power_w(speed_m_s, heading_deg, slope_sine):
    # Positive headwind increases the longitudinal apparent wind.
    relative_direction = math.radians(c.WIND_FROM_DEG - heading_deg)
    apparent_forward = speed_m_s + c.WIND_SPEED_M_S * math.cos(relative_direction)
    apparent_cross = c.WIND_SPEED_M_S * math.sin(relative_direction)
    apparent_speed_squared = apparent_forward ** 2 + apparent_cross ** 2
    yaw_deg = math.degrees(math.atan2(apparent_cross, apparent_forward))
    aero_force = 0.5 * c.AIR_DENSITY_KG_M3 * apparent_speed_squared * longitudinal_cda(yaw_deg)
    rolling_force = c.MASS_KG * 9.81 * c.ROLLING_RESISTANCE_COEFFICIENT * math.sqrt(1 - slope_sine ** 2)
    grade_force = c.MASS_KG * 9.81 * slope_sine
    wheel_power = (aero_force + rolling_force + grade_force) * speed_m_s
    # Negative wheel power is dissipated through braking; regen is disabled.
    return max(0.0, wheel_power) / c.DRIVETRAIN_EFFICIENCY


def simulate(route, target_kmh):
    timestamp = datetime.fromisoformat(c.START_TIME)
    battery_wh = c.START_BATTERY_WH
    rows = []
    solar_w = c.IRRADIANCE_W_M2 * c.ARRAY_AREA_M2 * c.SOLAR_EFFICIENCY
    for start, end in zip(route, route[1:]):
        length_km = end["distance_km"] - start["distance_km"]
        speed_kmh = min(target_kmh, start["speed_limit_kmh"])
        duration_h = length_km / speed_kmh
        slope_sine = (end["elevation_m"] - start["elevation_m"]) / (length_km * 1000)
        motor_w = motor_power_w(speed_kmh / 3.6, start["heading_deg"], slope_sine)
        if motor_w > c.MAX_MOTOR_ELECTRICAL_POWER_W:
            return None
        net_battery_w = solar_w - motor_w - c.AUXILIARY_POWER_W
        battery_wh = min(c.BATTERY_CAPACITY_WH, battery_wh + net_battery_w * duration_h)
        if battery_wh < c.MIN_BATTERY_WH:
            return None
        timestamp += timedelta(hours=duration_h)
        rows.append({
            "segment_start_km": start["distance_km"],
            "segment_end_km": end["distance_km"],
            "speed_kmh": speed_kmh,
            "arrival_time": timestamp.isoformat(),
            "battery_wh": round(battery_wh, 1),
            "solar_w": round(solar_w, 1),
            "motor_w": round(motor_w, 1),
            "net_battery_w": round(net_battery_w, 1),
        })
    if battery_wh < c.FINISH_RESERVE_WH:
        return None
    elapsed_h = (timestamp - datetime.fromisoformat(c.START_TIME)).total_seconds() / 3600
    return elapsed_h, rows


def main():
    print("DEMO ONLY — all car, weather, and route inputs are placeholders.")
    if not 0 <= c.MIN_BATTERY_WH <= c.START_BATTERY_WH <= c.BATTERY_CAPACITY_WH:
        raise ValueError("Check battery minimum, starting energy, and capacity.")
    if not c.MIN_BATTERY_WH <= c.FINISH_RESERVE_WH <= c.BATTERY_CAPACITY_WH:
        raise ValueError("Finish reserve must be between battery minimum and capacity.")
    if not 0 < c.DRIVETRAIN_EFFICIENCY <= 1 or not 0 < c.SOLAR_EFFICIENCY <= 1:
        raise ValueError("Efficiencies must be greater than zero and at most one.")
    if c.MIN_TARGET_KMH <= 0 or c.MAX_TARGET_KMH < c.MIN_TARGET_KMH or c.SPEED_STEP_KMH <= 0:
        raise ValueError("Check the positive integer speed search settings.")
    if datetime.fromisoformat(c.START_TIME).utcoffset() is None:
        raise ValueError("START_TIME must include a UTC offset.")
    route = load_route()
    best = None
    unsupported = 0
    for target_kmh in range(c.MIN_TARGET_KMH, c.MAX_TARGET_KMH + 1, c.SPEED_STEP_KMH):
        try:
            result = simulate(route, target_kmh)
        except ValueError as error:
            if str(error) != "Apparent wind yaw exceeds the demo CFD table.":
                raise
            unsupported += 1
            continue
        if result is not None and (best is None or result[0] < best[1][0]):
            best = target_kmh, result
    if unsupported:
        print(f"Skipped {unsupported} target speeds outside the CFD yaw coverage.")
    if best is None:
        print("No feasible speed found in the search range. No output written.")
        print("Any existing strategy.csv belongs to a previous run.")
        return
    target_kmh, (elapsed_h, rows) = best
    output = PROJECT_DIR / "strategy.csv"
    with output.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    print(f"Best feasible constant target on this grid: {target_kmh} km/h")
    print(f"Travel time: {elapsed_h:.2f} h")
    print(f"Finish battery: {rows[-1]['battery_wh']:.1f} Wh")
    print(f"Arrival: {rows[-1]['arrival_time']}")
    print(f"Saved: {output}")


if __name__ == "__main__":
    main()
