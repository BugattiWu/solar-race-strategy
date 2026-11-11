# Solar race strategy benchmark

Run in the PyCharm terminal from this folder:

```sh
.venv/bin/python -m streamlit run app.py
```

Open http://localhost:8501. Select Historical race replay and fetch weather. Use a short finish distance for quick experiments. Forecast scenario and Live telemetry use forecast weather; live telemetry reads telemetry.json (see telemetry.example.json).

## Latest controls

- Race overview: Dark (default) / Light theme, scroll-wheel zoom and optional cloud, wind and mapped traffic-control overlays. Completed runs retain final segment target, battery percentage/Wh, race time, map and elevation together.
- Target finish SOC and tolerance: a strict target ± percentage-point band, clipped to usable battery bounds. The fastest retained plan must finish inside this band. If no plan survives, widen the band or revise the target; speed/power caps or approximate-search pruning can prevent meeting it. No artificial energy dumping is modeled.
- What-if parameters: regenerative efficiency, regen electrical power limit and total battery charge power limit.
- Restart race appears after completion. Reset replay returns to the initial state paused. Both discard the old trajectory and history.

## Physics and placeholders

Only array area (6 square meters) is confirmed. Vehicle, battery and CFD constants in config.py are placeholders. Regen defaults to 65% wheel-to-battery electrical efficiency, 2000 W regen limit and 3000 W total charge limit. Acceleration and deceleration limits default to 0.4 and 1.0 m/s². Kinetic energy changes, road load and drivetrain efficiency are integrated with finite speed transitions at up to 50 m intervals. Known stops include braking, dwell and restart. Regen cannot exceed charge power or battery capacity. Detailed efficiency maps and thermal effects remain unmodeled.

Each candidate speed trajectory samples weather at its predicted arrival times. Historical weather is reanalysis, not an archived forecast as known on race day. The approximate beam search can miss feasible plans.

## Road data review

The current OpenStreetMap download is partial because public query endpoints timed out. speed_limits_review.csv and traffic_controls_review.csv are candidate matches, not verified driving instructions. Unknown sections remain unknown. Directional, conditional and intersection-approach applicability require review; current map edits may differ from July 2026 conditions. Candidates do not automatically constrain the optimizer. Use the global speed-cap assumption and enter verified stops in the sidebar.

To retry missing download sections, then rebuild review files:

```sh
.venv/bin/python fetch_road_data.py
.venv/bin/python road_review.py
```

Downloads reuse road_cache. Metadata and missing sections are in road_data_osm.json. Reliable full-route coverage requires reconciliation with current road-authority information and route reconnaissance. No traffic-signal phase prediction is included.

Run checks:

```sh
.venv/bin/python -m unittest test_planner test_replay test_dynamics test_roads
```

## Full race analysis tab

Full race analysis is a dashboard tab, containing entire battery (Wh and percent) and target-speed curves with map and elevation. Completed history is retained across replans.

Display units switches distance, speed, elevation, wind and mass between metric and imperial. Internal calculations, telemetry, detailed tables, stop entry and CSV exports retain explicitly labeled SI/km/km/h units. Switching display units preserves the physical settings and replay state.

Minimum planning speed constrains cruising targets, not acceleration ramps or stops. Planned acceleration/deceleration limits are adjustable placeholders. Five-second guidance follows the integrated velocity ramp; the segment target is the cruising objective. Regenerative electrical limits are modeled, but mechanical brake authority is still assumed sufficient within the configured deceleration envelope. Full coasting/torque optimization, speed-dependent braking torque and jerk limits are not yet modeled.

For calibration provide total loaded mass, effective tire radius, gear ratio, motor drive and regen torque versus RPM, motor/inverter efficiency maps, DC voltage and current/power limits, battery charge acceptance versus SOC and temperature, regen cutoff speed, friction-brake/coast-down measurements, and telemetry from controlled acceleration/coasting/braking tests.

## Route imports, car profiles and charging ceiling

Load a single continuous GPX track or route (with elevation) in the sidebar. Loading different bytes resets route state and requires fresh weather; benchmark road-feature candidates are hidden for uploaded routes. Choose an IANA race time zone for scheduling.

The Route planning tab is available before fetching weather. Create equal legs, edit names, kilometre boundaries, dates and start times, then Use selected leg. Validation requires an ordered, contiguous split covering the whole route. Each selected leg is simulated independently with the chosen initial SOC; no automatic overnight charging or day-to-day energy transfer is modeled. Download the plan to preserve it outside the session.

Car parameters include area (at most 6 m²), efficiency, usable battery capacity, minimum SOC, mass, rolling resistance, drivetrain efficiency and the existing power/dynamics controls. Sunstruck resets area/efficiency to 6 m²/28%, Azimuth to 4 m²/23%. Other values are repeatable, arbitrary placeholders in car_settings.py. Changing capacity resets replay so stored Wh cannot silently be reinterpreted.

Maximum charging SOC curtails positive net battery charging, including at stops, without deleting stored charge when starting above the limit. Below the ceiling, both regen and solar charging are capped. This assumes the electrical system can curtail solar/regen and use friction braking; it is not an implemented hardware BMS command.

## Aligned full-race profiles

Battery (Wh and %), target speed, elevation and predicted solar irradiance are stacked vertically with a common distance axis. Hover any graph to align a cursor through all profiles and move the orange marker on the map. Solar values are recorded from the optimizer's predicted encounter times and retained through replanning.

The solar quantity is W/m² irradiance. Open-Meteo shortwave radiation is horizontal global irradiance, already containing solar-angle projection and modeled cloud effects. The model assumes a horizontal array and does not apply a second cosine or cloud reduction. Road pitch, array curvature, horizon shading and cell temperature are not modeled. Historical data is reanalysis, not a forecast known at the time. Source: https://open-meteo.com/en/docs (solar radiation variables).

Optimization minimizes arrival time among candidates retained by bounded beam search. It does not certify the globally fastest route or infeasibility. Higher search widths may find a different feasible or faster profile.
