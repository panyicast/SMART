# HANDOFF

## Current State

- Current continuous-thrust implementation task is complete. Added `src/smart/services/design_continuous_thrust_optimizer.py` as the standalone implementation of the fixed 5-burn algorithm: MV1-MV4 fixed chain yaw/start-offset solution, MV5 semi-major-axis cutoff, and MV5 cutoff longitude target at `120 degE`. `optimize_continuous_thrust_model_parameters()` now delegates to this module, so the design maneuver page button displays 5 continuous-thrust rows. Profiling on the current F4 config shows the chain optimizer completes in about `0.245 s`, dominated by RK4 low-thrust integration, and returns hard constraints passed with total propellant `2605.002392 kg`.
- Current continuous-thrust algorithm documentation task is complete. Added `doc/design_continuous_thrust_parameter_optimization_algorithm.md`, documenting the fixed 5-burn continuous-thrust optimization flow: MV1-MV4 integrated yaw optimization with MV4 inclination as a hard target, MV5 near-perigee semi-major-axis cutoff, and MV1-MV4 start-time micro-adjustment to drive MV5 cutoff longitude to `120 degE`. The document includes the current fixed parameter table and risk notes.
- Current continuous-thrust table update is complete. The continuous-thrust result table now includes `控后半长轴/km` between cutoff eccentricity and yaw angle; the table has 16 columns and the propellant-objective tooltip moved with the propellant column. Focused design-maneuver regression passes.
- Current first-burn continuous-thrust optimization task is complete. `optimize_continuous_thrust_model_parameters()` now only optimizes MV1 and returns two rows: fixed ignition start time with yaw optimization, and ignition start time plus yaw optimization. The ignition seed is pulse MV1 epoch minus half the pulse burn duration, cutoff is the pulse MV1 post-burn perigee-height target, integration uses the existing RK4/J2 low-thrust dynamics, and the objective is `m + mA + mP`: MV1 continuous propellant plus the next apogee impulse to raise perigee to synchronous radius while controlling inclination plus the next perigee impulse to lower apogee to synchronous radius without inclination control. F4 current config result: fixed-time objective `2558.734041 kg`, optimized-time+yaw objective `2558.691020 kg`; optimized-time row starts at `2026-05-15 17:27:54` Beijing time, yaw `1.676210 deg`, cutoff `2026-05-15 18:33:01`, post-MV1 perigee height `3933.000029 km`.
- Current continuous-thrust perigee-burn alignment fix: the final perigee burn no longer uses an enlarged day-scale time search window, so its finite burn arc is forced to cover the pulse-planning perigee epoch. Perigee-burn yaw is fixed to the pulse-planning retrograde direction and no inclination trim is applied there; inclination control is handled by the final apogee burn. The perigee-burn cutoff target is now post-burn apogee radius rather than semi-major axis, so the local smoke reaches target eccentricity and matching post-burn perigee/apogee heights, with only terminal longitude still failing for the current low-thrust sequence. Focused design-maneuver regression passes.
- Current continuous-thrust yaw optimization check: removed the stale regression-test assumption that continuous-thrust yaw must equal pulse-plan yaw. Tests no longer impose an artificial yaw-difference threshold; yaw is left to the optimizer and existing constraints/objective. Focused design-maneuver regression passes.
- Current continuous-thrust apogee-height stabilization: apogee-burn continuous-thrust search now penalizes post-burn apogee-radius drift from the pulse plan while still using the pulse post-burn perigee radius as the cutoff target. Local smoke keeps MV1-MV4 post-burn apogee heights near the 46000 km pulse target (about 46012/46016/46046/46059 km) instead of drifting to 48235 km. Focused design-maneuver regression passes.
- Current continuous-thrust apogee-burn control fix: continuous-thrust optimization now uses the pulse-planning post-burn perigee radius as the cutoff control target for each apogee burn instead of using post-burn semi-major axis. In local smoke, MV1-MV4 continuous post-burn perigee heights match the pulse targets within display precision; the final perigee burn still correctly reports terminal longitude/eccentricity hard-constraint failures when not met. Focused design-maneuver regression passes.
- Current altitude-column update: both the pulse burn table and continuous-thrust result table now show post-control apogee altitude alongside post-control perigee altitude for every maneuver. Focused design-maneuver regression passes.
- Current continuous-thrust eccentricity check update: both the pulse burn table and continuous-thrust result table now show post-control eccentricity for every maneuver. Continuous-thrust terminal eccentricity is now a hard constraint; the current default/local case explicitly reports `终端偏心率误差` because the final continuous arc reaches target semi-major axis/longitude but remains at about `e=0.2409`, outside the `0.0001` tolerance. Focused design-maneuver regression passes.
- Current continuous-thrust terminal-inclination fix: the continuous-thrust optimizer now applies the final `m3` inclination trim to the displayed/output final maneuver values. The last row's velocity increment, propellant consumption, post-burn mass, and inclination now include the trim already counted in the objective, so the final inclination displays the 6 deg target instead of the pre-trim cutoff value (e.g. 4.87 deg). Focused design-maneuver regression passes.
- Current continuous-thrust UI output update: moved the "连续推力模型参数" card out of the top-left config area and into the design maneuver result card below the pulse burn table controls. The continuous-thrust result table now uses 14 columns: maneuver/revolution, position, burn start/end flight time, total duration, start/end longitude, post-control inclination/eccentricity, yaw angle, total velocity increment, propellant, post-burn mass, and post-burn perigee altitude. Added flight-revolution and position metadata to `ContinuousThrustManeuverParameter`; focused `tests/test_design_maneuver_strategy.py` passes.
- Current small task: design-maneuver perigee target entry moved out of the burn table. The burn table is read-only again, and MV1/MV2 target perigee-height inputs now live under the table. Applying those inputs updates `hard_constraint_planner.fixed_hp_targets_km`, clears the legacy `distribution.first_post_a_control_km`, and replans so values such as 3933 km/8360 km remain hard constraints instead of snapping back to the old 4132 km control.
- Current follow-up: fixed V5.1 free perigee-target search when only the first target is set. The optimizer now keeps template candidates and prioritizes mission-specific perigee templates before generic starts, so a blank second target can still choose a duration-feasible MV2/MV3 sequence instead of drifting to a 131 min second burn.
- Current performance follow-up: profiled the one-fixed-perigee case (`MV1=3940`, MV2 blank). Runtime was dominated by Powell refinement (15 Powell runs, 1567 full candidate simulations, about 99.9 s under cProfile). Added a template-only fast path for the common `front_count=3`, only-MV1-fixed case; the same F4 profile now runs about 0.96 s and evaluates 15 template candidates.
- Current longitude follow-up: `MV1=3400`, MV2 blank failed terminal longitude because the fast path kept the template `MV3=17680` and skipped the scalar refinement that would move MV3 to about `18404.296 km`. Added a light `template_x3_scalar` refinement that holds the best template MV2 target and optimizes MV3; F4 now ends at `119.99 degE` with all hard constraints feasible in about 1.18 s.
- Current hard-constraint policy update: V5.1 no longer ships hidden default MV1/MV2 perigee targets or a fixed q sequence. Auto mode enumerates q candidates; `q_AA_user` only constrains q when `apsis.pattern_mode=user`. If no candidate satisfies hard constraints, planning now fails instead of displaying the least-violating strategy. Absolute F4 perigee template constants were replaced with dynamic fractions of the initial-to-target perigee span.
- Current phase-chain update: V5.1 now seeds candidates from the Earth-fixed longitude/period/q relationship. It subtracts Earth rotation (`lon_next = lon_current - omega_earth * q * period`) to derive post-burn period/semi-major-axis/perigee targets, then uses existing J2 propagation and scalar refinement to remove residual error. Default free planning now chooses q `3,3,3,0` with about `2596.116 kg` propellant; `MV1=3400` chooses q `3,3,2,0` with hp targets about `[3400, 8544, 18146] km`.
- Current phase-chain performance update: profiled the new algorithm and found most time in repeated phase-chain template generation. q ranking now uses a cheap longitude-window score, selected q values build full templates once, and scalar clamps avoid NumPy overhead in the hot loop. Local smoke timing dropped default free planning from about `21.15 s` to `4.07 s`, and `MV1=3400` from about `24.86 s` to `2.95 s`, with unchanged q, propellant, and terminal longitude error.
- Current q-sequence UI update: design maneuver results now show feasible/top q-sequence alternatives below the burn table. Selecting a row fills the q input; applying it switches `apsis.pattern_mode` to `user`, writes `hard_constraint_planner.q_AA_user/q_AP_user`, and reruns optimization. A clear button returns to automatic q search.
- Current q-feasibility scan update: the q alternatives table is now driven by a fast hard-constraint feasibility scan (`phase_diagnostics.feasible_q_sequences`) rather than propellant-ranked optimization records. It only lists q sequences satisfying all hard constraints and shows q, maximum burn duration, terminal longitude error, and target perigee heights.
- Current independent q-scan UI update: the design-maneuver page now has a separate "查找全部可行q" button. It saves the current config, calls `find_feasible_q_sequences()`, scans all configured q candidates with terminal inclination relaxed, and refreshes only the q-candidate table without overwriting archived pulse-planning results. Risk: for very large q limits/counts this can still be expensive because "all q" disables the old prefilter cap; next minimum task is adding progress/cancel if users raise the q search space.
- Current q-scan correction: independent "查找全部可行q" no longer inherits the currently applied user q sequence. It preserves the maneuver count, clears `apsis.pattern_mode=user` and `q_AA_user/q_AP_user` inside the scan copy, and now returns both expected F4 candidates `3,3,2,0` and `3,3,3,0` when the page currently has `3,3,3,0` applied. Regression test added.
- Current q-selection UI simplification: the design-maneuver q candidate table and manual q text/apply/clear controls were replaced with one no-wheel combo box. The first item is blank and clears q constraints/replans; each computed feasible q sequence appears as one selectable option, and selecting one applies the matching user q sequence before replanning. Candidate details are kept in item tooltips rather than table columns.
- Current q-selection apply update: the q combo now sits on the same row as MV1/MV2 target perigee-height inputs and the orange apply button. Changing the combo no longer runs planning; clicking "应用并重算" reads both target perigee heights and the selected q sequence together, then replans once. The combo uses a custom painted cyan down-arrow so the dropdown affordance remains visible under the dark theme.
- Current compact layout update: the design-maneuver page no longer uses the tall horizontal splitter. The top row is now a compact action card plus compact current-parameter card; the pulse burn table is a full-width compressed card; summary, checks, and warnings sit in short lower cards with remaining page height left open. Button heights, table row/header heights, and card margins were reduced to match the reference screenshot and leave space below.
- Current design-page compression update: the action card buttons are arranged as a compact two-row grid, the current-parameter table shows only the first four rows, and the old maneuver-count recommendation plus constraint-check tables were removed. Planning/check results now stay in the status bar: all checks passing shows `硬约束全部通过` with ready styling; failed checks are listed as red `未通过硬约束：...`. Archived-result loading preserves the constraint status instead of replacing it with a generic load message.
- Current continuous-thrust parameter update: added a lower-left "连续推力模型参数" card with an "优化连续推力模型参数" button and compact MV table. The service now uses the pulse plan only as the initial seed, then performs sequential local grid search for each maneuver. The seed burn start is pulse event time minus estimated burn duration, yaw seed is the pulse `alpha_deg`, and the search uses a coarse window followed by final `t=10 s` / `δ=0.05 deg` refinement. Each candidate iterates burn duration and solves the cutoff-point control so the semi-major axis reaches the pulse target at shutdown; non-final objectives use `m + m1 + m2 + m3`, final uses `m + m3`. F4 local smoke: pulse planning about `1.76 s`, continuous optimization about `3.45 s`, hard constraints pass, final cutoff longitude `120.000 degE`, `ΔG=6706.031 kg`.
- Current low-thrust integrator update: continuous-thrust optimization now uses an RK4 low-thrust state integrator during candidate evaluation instead of cutoff-equivalent impulse. Dynamics include two-body gravity, optional J2, thrust acceleration, mass flow, settling thrust, main thrust, and constant yaw angle per maneuver in the local-horizontal frame. Search uses coarse/fine grids around pulse seeds, with coarse integration steps for candidate ranking and 10 s integration for final replay. The UI exports complete numerical orbit history to `data/design_continuous_thrust_orbit_history.csv` with the same core columns as `full_orbit_history.csv`. F4 smoke: pulse planning about `1.69 s`, low-thrust continuous optimization about `36.7 s`, 10001 orbit-history rows, hard constraints pass, final cutoff longitude `120.000691 degE`, final semi-major axis `42164199.901 m`, `ΔG=6803.583 kg`.
- Current low-thrust performance update: profiled the continuous-thrust optimizer and found hot spots in repeated full `_rv_to_coe()` calls and NumPy `cross`/`norm` allocations inside local-horizontal thrust direction. Search now uses a scalar semi-major-axis helper for crossing checks, scalar local-horizontal direction for RK4 thrust acceleration, and a scalar `_rv_to_coe()` implementation. F4 continuous optimization dropped from about `36.7 s` to about `6.1 s` with unchanged hard-constraint result, final cutoff longitude, `ΔG`, and 10001 exported history rows; full design-maneuver regression now runs in `57.54 s`.
- Current parameter-dialog update: `target.dv_lon_margin_mps` was renamed to `Δv估算裕度 (m/s)` and moved out of the basic parameter dialog into Advanced > 估算参数. Basic parameters now include `用户指定变轨次数 (0=自动)`, which writes `maneuver_count.user` and `planner.maneuver_count_user`.
- Current geometry warning fix: design maneuver result panel now lives in a scroll area, preventing the q-candidate table and result cards from raising the main-window minimum height above the available display height. Short `showMaximized()` smoke now reports `minimumSizeHint 1367x942` with no Qt geometry warning.
- Recovered from compact failure by re-reading current repo state only.
- No prior hidden context assumed.
- Root `HANDOFF.md` / `NOTES.md` did not exist before this file.
- `git status` shows WIP across project config, UI, STK link service/page, tests, and F4 generated data.
- Added persistent small-task checkpoint rules to `AGENTS.md`.
- Implemented STK scene time sync from the flight-program page when an existing STK scenario is available.
- Fixed flight-program timeline/playhead sync so dragging or jumping the playhead sends STK `SetAnimation * CurrentTime`.
- Fixed STK page sync failure caused by reusing a COM executor across Qt worker threads; STK operations now refresh the executor per background operation.
- Improved flight-program playhead/STK sync performance: drag/slider movement is debounced and coalesced, while explicit jumps still sync STK immediately.
- Fixed STK link resource sync: the STK link page now previews the same tracking-arc assets that `StkLinkService` imports into STK, and default tracking ground-station names are English.
- Added STK 3D flight-event annotations: each flight-program event becomes a `VO * Annotation` text label with a display interval matching the event start/end time. STK-visible text is forced to English/ASCII.
- Fixed STK sync crash from an invalid English-label regex character range when sanitizing STK-visible text.
- Updated STK 3D event annotations to show only spacecraft attitude mode text (`SPM`, `EPM`, `AFM`, `TRM`) as large Pixel annotations at the 3D view upper-left; `Transition` events display as `TRM`, and non-attitude events are not annotated.
- Added a new "设计变轨策略" page below "导入变轨策略". It uses independent `config/design_maneuver_strategy.json` and implements V4.2 simplified impulse initial planning outputs: count recommendation, A/P pulse table, and constraint checks.
- Updated the design maneuver strategy planner against the user-provided V4.2 reference package. Defaults now match the reference mission, and planning uses J2 secular apsis events, q-sequence search, local-horizontal impulses, fixed-tail semi-major-axis Δv solving, and reference-shape config import.
- Redesigned the "设计变轨策略" page parameter editing flow: the page now shows a compact current-config summary and opens two frameless dialogs, "参数配置" for initial orbit, target orbit, and the first 7 engine/burn-limit fields, and "高级设置" for the remaining parameters.
- Fixed a Qt startup/maximize geometry warning caused by oversized page minimum-height hints. Flight-program splitters/3D preview and design-maneuver result tables now use smaller minimum heights and rely on scroll areas for overflow.
- Moved the "变轨次数推荐" result card from the design-maneuver right result column to the lower-left parameter column.
- Added automatic design-maneuver result archiving. Clicking "生成脉冲规划" now saves `data/design_maneuver_results.json`, and the page auto-loads archived planning results on refresh/open.
- Fixed design-maneuver final-burn longitude selection so the last burn searches for the candidate closest to `target.lon_degE` instead of accepting the first in-window P event. Terminal longitude error is now shown in constraint checks.
- Tightened design-maneuver terminal longitude tolerance from `0.05 deg` to `0.01 deg` in defaults, F4 config, and local algorithm docs. Optimization research shows target longitude is a phasing/period-chain problem controlled mainly by q/event choices and early-burn Δv/period, not by alpha alone.
- Added automatic design-maneuver phase-chain q selection. The first implementation used q sequence `3,6,3,1` to move the F4 final longitude near 120 degE; this was superseded by the q-limited continuous phasing step below.
- Revised design-maneuver phase-chain optimization to enforce per-leg q <= `q_AA_default` (3 by default) and optimize early Δv/period continuously under the max burn-time constraint. For the local F4 design config, the planner now uses `3,3,2,1`, final longitude ~119.9934 degE, and terminal longitude error ~-0.00656 deg.
- Updated design-maneuver result output to match the requested table shape: separation point plus MV rows with flight time, flight revolution, apsis position, subsatellite longitude, post-burn semi-major axis, orbit period, inclination, Δv, burn time, propellant, post-burn mass, and semi-major-axis control amount. MV1 semi-major-axis control amount is editable in the UI and triggers replanning.
- Added terminal inclination trim for design-maneuver planning. The last burn now includes an equivalent inclination-correction Δv when needed so final inclination is locked to `target.i_deg` and the terminal inclination check passes the `0.01 deg` tolerance.
- Added design-maneuver calculation busy UI. Clicking "生成脉冲规划" or editing MV1 semi-major-axis control now shows an indeterminate progress bar/status, disables controls and result tables during calculation, and restores interaction after results are saved. MV1 editable cell is highlighted with a distinct background/foreground and tooltip.
- Optimized design-maneuver planning performance without changing the calculation path by caching Earth-orientation SPICE manager construction and Greenwich angle evaluations. F4 single-plan profiling dropped from about 64 s to about 2 s cold / 4 s profiled hot in the local run; focused tests dropped from ~110 s to ~8 s.
- Removed the design-maneuver "带入当前任务基线" button and all baseline-import logic. Satellite-status page configuration is now treated as local to that page only; Dashboard no longer reads or displays `satellite_status.json` details, and MainWindow no longer forwards satellite settings into Dashboard.
- Restored the design-maneuver initial-orbit reference values to the user-provided table: `a=29478.137 km`, `e=0.77684692`, `i=16.5 deg`, `omega=200 deg`, `Omega=8.53237 deg`, and `M=1.85437 deg`; the F4 design config eccentricity now matches the same value.
- Updated the design-maneuver pulse table display: headers include units, all displayed burn-table numeric values use two decimals, the separation-point subsatellite longitude is calculated from the initial state, MV1 editable semi-major-axis-control cell has delegate-backed highlight, and a calculated thrust yaw-angle column is shown.
- Reworked design-maneuver yaw-angle optimization so longitude phasing first establishes the post-burn semi-major-axis chain, then alpha/yaw is optimized with those semi-major axes locked. The score now treats terminal longitude, terminal inclination, terminal semi-major axis, duration, and warnings as constraints before minimizing propellant. For supersynchronous transfers, the final perigee burn is kept tangential and no longer performs hidden inclination trim.
- Added `doc/design_maneuver_pulse_planning_algorithm.md`, a full Markdown description of the current design-maneuver pulse planning algorithm, including inputs, q sequence, longitude phasing, fixed semi-major-axis chain, yaw/inclination optimization, scoring, propellant calculation, UI output, archive behavior, and limitations.
- Added SciPy as a runtime dependency and installed it in the project venv so future design-maneuver SLSQP continuous optimization can use `scipy.optimize.minimize(method="SLSQP")`.
- Implemented the design-maneuver hybrid phase optimizer: q sequences are fully enumerated up to the q limit, quick-screened, optimized by coordinate search for the top candidates, refined with SciPy SLSQP for the best candidates, and finally ranked with hard-constraint-first tuple scoring. Summary diagnostics now report q candidate counts, SLSQP attempts, feasibility counts, active constraints, terminal margins, and fallback state.
- Fixed the hybrid optimizer evaluation path so the final supersynchronous perigee burn no longer uses a hard-coded `alpha=-180 deg`; it now computes the alpha angle from the local-horizontal projection of the retrograde velocity direction while keeping `alpha=0 deg` defined as local east.
- Fixed design-maneuver apogee yaw search sign handling. For inclination-reduction cases, apogee alpha search bounds are clipped to nonnegative values so the optimizer cannot choose inefficient negative yaw for apogee inclination lowering; the opposite sign is clipped for inclination-raising cases.
- Changed the design-maneuver burn table final column from semi-major-axis control amount to post-burn perigee altitude. Values use `a*(1-e)-Re`; the table no longer exposes MV1 semi-major-axis control as an editable highlighted cell.
- Implemented the V5.1 hard-constrained phase-search planner for supersynchronous design maneuvers. The current page now searches q_AP and post-burn perigee-height targets under hard window, duration, and terminal a/e/i/lon constraints before ranking feasible candidates by propellant; standard-transfer and V5.1 failure paths still fall back to the existing V4.2 flow.

## Modified / Added Areas

- `AGENTS.md`: added "Small Task Checkpoints" section requiring summary, handoff update, git checkpoint, and new-session continuation; records STK-visible text must be English/ASCII only.
- `projects/F4/config/flight_program.json`: selected launch/T0 shifted 10 minutes earlier.
- `projects/F4/smart_project.json`: project `updated_utc` refreshed.
- `projects/F4/data/flight_program_reference_results.json`: new cached flight-program reference results.
- `projects/F4/data/stk_link/20260512_114224/`: generated STK export artifacts (`.e`, `.a`).
- `src/smart/services/project_workspace.py`: project close/save-as plus flight-program reference-result paths and load/save.
- `src/smart/ui/main_window.py`: adds STK link page, project save-as/close actions, sidebar simplification.
- `src/smart/ui/i18n.py`: adds Chinese UI text for save-as/close and STK link navigation.
- `src/smart/ui/nav_icons.py`: adds STK link icon.
- `src/smart/ui/theme.py`: adds sidebar project name/path roles.
- `src/smart/ui/widgets/flight_program_page.py`: adds autosave and reference-result cache load/save; launch-source/manual/window/orbit-point T0 changes now try to sync existing STK scenario analysis time; playhead jumps sync STK current animation time immediately, while drag/slider changes use a short trailing debounce.
- `src/smart/ui/widgets/maneuver_page.py`: changes ground-track maneuver-number labels to yellow text with black outline and smaller offset.
- `src/smart/services/stk_link.py`: new STK 11.6 launch/connect/import service; now tracks established scenarios, can attach to a running STK scenario without launching STK, can set STK current animation time, can discard COM executors without losing established-scenario state, and adds English-only 3D Pixel annotations for flight-program attitude modes.
- `tests/test_stk_link.py`: covers English-only label sanitization so path-safe ASCII labels do not trigger regex range errors, and verifies STK event annotations are attitude-only, Pixel upper-left, large text, with `Transition` mapped to `TRM`.
- `src/smart/services/launch_window.py`: tracking ground-station defaults now use English names, and old Chinese default station names migrate to their English equivalents when configs are normalized.
- `src/smart/ui/widgets/stk_link_page.py`: new STK link UI page with worker thread; shares the MainWindow-owned `StkLinkService` so scenario state survives page switches; clears COM executor before and after each worker operation to avoid cross-thread COM reuse; previews tracking-arc assets instead of satellite-status assets.
- `projects/F4/config/launch_window.json`, `projects/F4/config/tracking_arc.json`: default tracking ground stations renamed to English.
- `src/smart/ui/main_window.py`: owns one shared `StkLinkService` for STK link page and flight-program page.
- Tests updated/added for project workspace, flight program page, maneuver page, sidebar navigation, STK link helpers, existing-scenario STK time sync, debounced drag sync, and reference-jump sync.
- `src/smart/services/design_maneuver_strategy.py`: new independent V4.2 simplified impulse planner service and config normalizer.
- `src/smart/ui/widgets/design_maneuver_strategy_page.py`: new Qt page with Beijing-time epoch field, no-wheel numeric controls, independent save/load, current-project baseline import, planning result tables, and reserved future-task area.
- `src/smart/ui/widgets/design_maneuver_strategy_page.py`: MV1/MV2 post-burn perigee-height constraints are now entered through dedicated inputs under the burn table; the table cells are read-only, and applying inputs clears legacy first semi-major-axis control before replanning.
- `src/smart/services/design_maneuver_strategy.py`: V5.1 template starts are retained as ranked candidates, and mission-specific three-front-burn perigee templates are considered before generic power-law templates so unconstrained MV2/MV3 searches preserve burn-duration feasibility.
- `src/smart/services/design_maneuver_strategy.py`: one-fixed-perigee V5.1 search now skips Powell refinement and uses template-only ranking for the common three-front-burn case, cutting the profiled F4 case from about 99.9 s to about 0.96 s.
- `src/smart/services/design_maneuver_strategy.py`: one-fixed-perigee fast path now performs a bounded scalar refinement of MV3 when the best template only misses terminal longitude, fixing the `MV1=3400` case without returning to full Powell search.
- `src/smart/services/design_maneuver_strategy.py`: hard-constraint mode now raises on infeasible results, uses empty default fixed perigee targets, searches q in auto mode, and uses dynamic perigee templates rather than hard-coded `3933/8360/17680` km targets.
- `src/smart/services/design_maneuver_strategy.py`: adds phase-chain q/period/perigee candidate generation in the Earth-fixed frame, accounting for Earth rotation before J2 propagation validates and refines the candidate.
- `src/smart/services/design_maneuver_strategy.py`: optimizes phase-chain candidate generation by separating cheap q ranking from full template generation, caching selected-q templates, and removing scalar `np.clip` calls in hot loops.
- `src/smart/services/design_maneuver_strategy.py`: adds a fast feasible-q scan that enumerates q candidates, uses phase-chain templates plus terminal-longitude scalar correction, and returns only hard-feasible q summaries without propellant ranking fields.
- `src/smart/ui/widgets/design_maneuver_strategy_page.py`: adds a q-candidate table plus manual q input under the burn table; users can apply a candidate or typed q sequence and replan under that hard user q sequence.
- `src/smart/ui/widgets/design_maneuver_strategy_page.py`: simplifies the q-candidate table to q sequence, max burn duration, terminal longitude error, and target perigee heights.
- `src/smart/ui/widgets/design_maneuver_strategy_page.py`: moves the Δv estimate margin to the advanced dialog and adds user maneuver count to the basic parameter dialog.
- `src/smart/ui/widgets/design_maneuver_strategy_page.py`: wraps the result panel in a `QScrollArea` so the main window can maximize on shorter screens without exceeding screen geometry.
- `tests/test_design_maneuver_strategy.py`: covers supersynchronous fixed-tail planning, standard transfer user count, page config independence, and config normalization.
- `tests/test_design_maneuver_strategy.py`: now also checks reference default output landmarks: 5 burns, 1539 m/s estimate, 312.123864 m/s design burn, first apogee event longitude/time, and final fixed-tail Δv.
- `src/smart/ui/widgets/design_maneuver_strategy_page.py`: current parameter settings moved out of the left scroll form into two dedicated dialogs with Beijing-time epoch editing, no-wheel controls, and the same dark/cyan/orange dialog style used by maneuver configuration.
- `src/smart/ui/i18n.py`, `tests/test_design_maneuver_strategy.py`: added labels/status text and regression coverage for the two-dialog editing flow.
- `src/smart/ui/widgets/flight_program_page.py`, `src/smart/ui/widgets/design_maneuver_strategy_page.py`: reduced oversized minimum heights that inflated `MainWindow` minimum geometry on scaled Windows displays.
- `tests/test_sidebar_navigation.py`: adds a main-window minimum-height regression check.
- `src/smart/ui/widgets/design_maneuver_strategy_page.py`: places the recommendation summary card at the bottom of the left config column; right column now starts with the pulse burn table.
- `tests/test_design_maneuver_strategy.py`: verifies the recommendation card lives in the left config panel.
- `src/smart/services/design_maneuver_strategy.py`: adds JSON serialization/deserialization helpers for `DesignManeuverResult`.
- `src/smart/services/project_workspace.py`: adds `design_maneuver_results_path()`, `save_design_maneuver_results()`, and `load_design_maneuver_results()` using `data/design_maneuver_results.json`.
- `src/smart/ui/widgets/design_maneuver_strategy_page.py`: saves result archive after successful planning and loads archived result tables during `refresh_from_workspace()`.
- `src/smart/ui/i18n.py`, `tests/test_design_maneuver_strategy.py`, `tests/test_project_workspace.py`: adds status strings and regression coverage for archive save/load.
- `src/smart/services/design_maneuver_strategy.py`: final burn event search now uses target longitude preference and adds terminal longitude check output.
- `tests/test_design_maneuver_strategy.py`: updates reference expected output for target-longitude-aware final burn selection.
- `src/smart/services/design_maneuver_strategy.py`, `projects/F4/config/design_maneuver_strategy.json`, and V4/V4.2 algorithm docs: terminal longitude tolerance is now `0.01 deg`.
- `src/smart/services/design_maneuver_strategy.py`: adds phase-chain q candidate selection before burn construction and records the selected q sequence in the result summary.
- `src/smart/ui/widgets/design_maneuver_strategy_page.py`: shows the selected q sequence in the recommendation summary card.
- `tests/test_design_maneuver_strategy.py`: covers the F4-like terminal longitude case, requiring final longitude error within `0.01 deg`.
- `src/smart/services/design_maneuver_strategy.py`: q sequences are capped by `q_AA_default`; user-provided q sequences are capped too. Phase optimization now adjusts front Δv values and ignores old uniform-Δv spread checks when this continuous phasing is active.
- `tests/test_design_maneuver_strategy.py`: updates expected F4-like solution to q `3,3,2,1` with optimized front Δv and max-burn-time feasibility.
- `src/smart/services/design_maneuver_strategy.py`: adds result fields for flight revolution, position label, post-burn period, post-burn mass, and semi-major-axis control amount; supports `distribution.first_post_a_control_km` to fix MV1 post-burn semi-major-axis change before re-optimizing later pulses.
- `src/smart/ui/widgets/design_maneuver_strategy_page.py`: replaces the pulse table columns with the requested table format, adds a separation-point row, and makes MV1 semi-major-axis control amount editable.
- `tests/test_design_maneuver_strategy.py`: covers manual MV1 semi-major-axis control and the new table row/editability behavior.
- `src/smart/services/design_maneuver_strategy.py`: adds `_terminal_inclination_trim_delta_v_mps()` and applies it on the final burn before burn-time/propellant output is finalized.
- `tests/test_design_maneuver_strategy.py`: verifies terminal inclination error is zero after planning.
- `src/smart/ui/widgets/design_maneuver_strategy_page.py`: adds a planning busy guard, progress bar, control/table disabling during calculation, and highlighted editable MV1 control cell.
- `tests/test_design_maneuver_strategy.py`: verifies the progress bar starts hidden and the editable MV1 cell has the expected visual highlight.
- `src/smart/services/earth_orientation.py`: adds bounded `lru_cache` layers for `_build_spice_manager()` and Greenwich angle lookups so repeated design-maneuver longitude calculations do not repeatedly scan/load SPICE kernels.
- `src/smart/ui/widgets/design_maneuver_strategy_page.py`: removes the baseline import button and `_load_project_baseline()` flow that previously read orbit initialization and satellite mass.
- `src/smart/ui/widgets/dashboard_page.py`, `src/smart/ui/main_window.py`, `src/smart/ui/i18n.py`: remove Dashboard dependencies on satellite-status config and replace the satellite card body with a note that satellite-status settings are not a cross-page baseline.
- `tests/test_design_maneuver_strategy.py`: asserts the design-maneuver baseline button is absent.
- `.planning/2026-05-15-design-maneuver-longitude-optimization/`: local research notes, not intended for commit, record the phase-chain optimization findings.

## Risks

- STK Connect commands need real STK 11.6 end-to-end verification.
- Flight-program reference cache can become stale after orbit, tracking, launch-window, or constraint changes.
- `projects/F4/data/stk_link/` contains generated artifacts; decide whether to keep in git.
- Several files show LF-to-CRLF warnings on git diff/status.
- Real STK 11.6 UI/Connect validation is still needed for the automatic time sync path.
- Local F4 project has new STK-generated artifacts under `projects/F4/data/stk_link/20260512_132638/` and an updated `projects/F4/smart_project.json`; include them in the checkpoint only if preserving the real STK validation run is desired.
- Local `projects/F4/smart_project.json` changed again during real STK use and was intentionally left out of this COM-threading checkpoint.
- Local real STK retry also generated `projects/F4/data/stk_link/20260512_134342/`; decide later whether to keep committed, clean, or ignore generated STK exports.
- Design maneuver page currently produces simplified impulse initial-planning tables only. Direction-angle optimization, J2 high-fidelity propagation, finite-thrust expansion, and explicit export into `maneuver_strategy.json` remain future work.
- SciPy is installed in the repo venv and the design-maneuver planner can use SLSQP. If SciPy import fails at runtime, the planner falls back to the coordinate-search path.

## Next Minimum Task

Current STK tracking-resource sync fix is complete. STK link preview and STK import both use tracking-arc assets, and default tracking station names are English.
Current STK flight-event annotation fix is complete. STK import creates English-only `VO Annotation` labels for all flight-program events using their computed start/end intervals.
Current STK label regex crash fix is complete. `_english_stk_label()` now accepts ASCII `/` and `-` without forming an invalid regex range.
Current STK attitude-mode annotation update is complete. STK import now labels only attitude modes with `SPM`/`EPM`/`AFM`/`TRM` in the 3D upper-left corner.
Current design maneuver yaw-sign fix is complete. Apogee alpha is constrained to the correct inclination-control sign, and the focused design-maneuver tests pass.
Current design maneuver burn-table final-column change is complete. The last column now shows post-burn perigee altitude and focused tests pass.
Current V5.1 design maneuver planner integration is complete. Supersynchronous planning uses hard constraints and propellant ranking, preserves the legacy MV1 first semi-major-axis control config by mapping it to a V5.1 perigee target, and focused tests pass.
Current design maneuver strategy page task is complete. The page has its own config file and does not mutate the import maneuver strategy config.
Current reference-alignment task is complete. The default design planner now matches the provided V4.2 package shape and major default-output landmarks while staying runnable without SciPy.
Current design maneuver dialog split task is complete. Parameter configuration and advanced settings are separated into two dialogs, and planning still uses the independent design config.
Current Qt geometry warning fix is complete. Main window `minimumSizeHint().height()` now measures 942 in the local check, down from the warning-producing oversized layout.
Current design maneuver summary placement task is complete. The recommendation card is now in the page lower-left.
Current design maneuver result archive task is complete. Planning result tables persist to project data and auto-load when the page opens.
Current final-burn longitude candidate-selection fix is complete. For the F4 config seen locally, that earlier step moved final burn longitude from ~82.0006 degE to ~128.7545 degE before phase-chain q optimization.
Current longitude optimization research step is complete. User-provided phase-chain logic is accepted as the right method: first-burn longitude, post-burn orbital periods, integer regression counts, and Earth rotation close the longitude chain.
Current phase-chain q optimization step is complete but superseded by q-limited continuous phasing. The earlier unconstrained q sequence `3,6,3,1` is no longer accepted because per-leg q must be <= 3.
Current q-limited continuous phasing step is complete. q is capped at 3; for the F4-like design config the selected sequence is `3,3,2,1`, final longitude is `119.993441 degE`, terminal longitude error is `-0.006559 deg`, and max burn time is `71.950448 min`.
Current result-table/manual-control step is complete. The UI output table now follows the requested columns, includes a separation point row, and replans when the user edits MV1 semi-major-axis control amount. Service verification with `first_post_a_control_km=1000.0` keeps q `3,3,2,1`, MV1 control at 1000 km, and terminal longitude within tolerance.
Current terminal inclination-control step is complete. F4 verification shows final inclination error `0.000000 deg`, final longitude error remains within `0.01 deg`, and max burn time remains below limit.
Current design-maneuver busy UI step is complete. Generate and MV1-edit replanning paths both show a busy progress bar and disable controls/tables while synchronous planning runs; MV1 editable control cell is visibly highlighted.
Current design-maneuver performance step is complete. The major bottleneck was repeated SPICE kernel manager construction inside `greenwich_angle_at_utc()` during thousands of longitude evaluations; cached Earth-orientation calls preserve outputs while reducing runtime.
Current satellite-status isolation step is complete. The design-maneuver page no longer imports current-task baseline from satellite-status data, and Dashboard no longer reads satellite-status configuration.

Verified:

```powershell
D:\Spark\SMART\.venv\Scripts\python.exe -m pytest tests/test_project_workspace.py tests/test_flight_program_page.py tests/test_maneuver_page.py tests/test_sidebar_navigation.py tests/test_stk_link.py
D:\Spark\SMART\.venv\Scripts\python.exe -m pytest tests/test_stk_link.py tests/test_launch_window.py
D:\Spark\SMART\.venv\Scripts\python.exe -m pytest tests/test_project_workspace.py tests/test_tracking_arc.py tests/test_launch_window_page.py
D:\Spark\SMART\.venv\Scripts\python.exe -m pytest tests/test_stk_link.py
D:\Spark\SMART\.venv\Scripts\python.exe -m pytest tests/test_stk_link.py tests/test_flight_program.py tests/test_flight_program_page.py
D:\Spark\SMART\.venv\Scripts\python.exe -m pytest tests/test_stk_link.py
D:\Spark\SMART\.venv\Scripts\python.exe -m pytest tests/test_stk_link.py
D:\Spark\SMART\.venv\Scripts\python.exe -m pytest tests/test_stk_link.py tests/test_flight_program.py tests/test_flight_program_page.py
D:\Spark\SMART\.venv\Scripts\python.exe -m pytest tests/test_design_maneuver_strategy.py tests/test_project_workspace.py tests/test_maneuver_page.py
D:\Spark\SMART\.venv\Scripts\python.exe -m pytest
D:\Spark\SMART\.venv\Scripts\python.exe -m pytest tests/test_design_maneuver_strategy.py tests/test_project_workspace.py
D:\Spark\SMART\.venv\Scripts\python.exe -m pytest
D:\Spark\SMART\.venv\Scripts\python.exe -m pytest tests/test_design_maneuver_strategy.py tests/test_project_workspace.py
D:\Spark\SMART\.venv\Scripts\python.exe -m pytest tests/test_sidebar_navigation.py tests/test_flight_program_page.py tests/test_design_maneuver_strategy.py tests/test_project_workspace.py
D:\Spark\SMART\.venv\Scripts\python.exe -m pytest tests/test_design_maneuver_strategy.py tests/test_sidebar_navigation.py
D:\Spark\SMART\.venv\Scripts\python.exe -m pytest tests/test_design_maneuver_strategy.py tests/test_project_workspace.py tests/test_sidebar_navigation.py
D:\Spark\SMART\.venv\Scripts\python.exe -m pytest tests/test_design_maneuver_strategy.py tests/test_project_workspace.py
D:\Spark\SMART\.venv\Scripts\python.exe -m pytest tests/test_design_maneuver_strategy.py tests/test_project_workspace.py -q
D:\Spark\SMART\.venv\Scripts\python.exe -m pytest tests/test_design_maneuver_strategy.py -q
D:\Spark\SMART\.venv\Scripts\python.exe -m pytest tests/test_project_workspace.py -q
D:\Spark\SMART\.venv\Scripts\python.exe -m pytest tests/test_design_maneuver_strategy.py -q
D:\Spark\SMART\.venv\Scripts\python.exe -m pytest tests/test_project_workspace.py -q
D:\Spark\SMART\.venv\Scripts\python.exe -m pytest tests/test_design_maneuver_strategy.py -q
D:\Spark\SMART\.venv\Scripts\python.exe -m pytest tests/test_project_workspace.py -q
D:\Spark\SMART\.venv\Scripts\python.exe -m py_compile src\smart\ui\widgets\design_maneuver_strategy_page.py src\smart\services\design_maneuver_strategy.py
D:\Spark\SMART\.venv\Scripts\python.exe -m pytest tests\test_design_maneuver_strategy.py -q
D:\Spark\SMART\.venv\Scripts\python.exe -m pytest tests\test_project_workspace.py -q
D:\Spark\SMART\.venv\Scripts\python.exe -m py_compile src\smart\services\earth_orientation.py src\smart\services\design_maneuver_strategy.py
D:\Spark\SMART\.venv\Scripts\python.exe -m pytest tests\test_design_maneuver_strategy.py -q
D:\Spark\SMART\.venv\Scripts\python.exe -m pytest tests\test_project_workspace.py -q
D:\Spark\SMART\.venv\Scripts\python.exe -m py_compile src\smart\ui\widgets\design_maneuver_strategy_page.py src\smart\ui\widgets\dashboard_page.py src\smart\ui\main_window.py src\smart\ui\i18n.py
D:\Spark\SMART\.venv\Scripts\python.exe -m pytest tests\test_design_maneuver_strategy.py -q
D:\Spark\SMART\.venv\Scripts\python.exe -m pytest tests\test_sidebar_navigation.py -q
```

Result: 63 passed for the previous playhead checkpoint; 41 passed for STK/launch-window focused tests; 26 passed for project/tracking/page regression tests; 13 passed for STK annotation tests; 54 passed for STK/flight-program regression tests; 14 passed for the STK label regex fix; 14 passed for the attitude-mode Pixel annotation test; 55 passed for STK/flight-program regression tests; 16 passed for design maneuver focused tests; 182 passed for full suite; after reference alignment, 16 focused tests passed and 183 full tests passed; after dialog split, 16 focused project/design tests passed; after geometry fix, 58 focused UI/project tests passed; after summary-card move, 11 focused design/sidebar tests passed; after archive save/load, 23 focused tests passed; after target-longitude final-burn fix, 17 focused tests passed.
Latest phase-chain q optimization focused run: 18 passed.
Latest q-limited continuous phasing runs: 6 design tests passed; 12 project workspace tests passed.
Latest result-table/manual-control runs: 6 design tests passed; 12 project workspace tests passed.
Latest terminal inclination-control runs: 6 design tests passed; 12 project workspace tests passed.
Latest busy-UI runs: py_compile passed; 6 design tests passed; 12 project workspace tests passed.
Latest performance runs: py_compile passed; 6 design tests passed in 7.97 s; 12 project workspace tests passed in 3.19 s.
Latest satellite-status isolation runs: py_compile passed; 6 design tests passed; 6 sidebar/navigation tests passed.
Latest initial-orbit restore runs: py_compile passed; 18 design/project tests passed.
Latest pulse-table display runs: py_compile passed; 6 design tests passed; 12 project workspace tests passed.
Latest merge-readiness fix runs: 13 project workspace tests passed; 6 design maneuver tests passed.
Latest yaw-angle optimization runs: py_compile passed; 19 design/project tests passed.
Latest algorithm-documentation task: documentation-only change; no code tests required.
Latest SciPy dependency task: SciPy 1.17.1 installed; SLSQP smoke test passed; 19 design/project tests passed.
Latest hybrid optimizer task: py_compile passed; default and F4-like planning smoke runs completed in about 18-20 s with terminal longitude/inclination inside 0.01 deg; 6 design tests passed; 13 project workspace tests passed; 19 combined design/project tests passed.
Latest runtime/result evaluation: default SLSQP run took about 18.8 s, selected q `1,1,1,1`, terminal lon error `-0.000367 deg`, terminal i error `0.003748 deg`, propellant `2599.177443 kg`, max burn `75.900735 min`; F4-like SLSQP run took about 18.5 s, selected q `1,3,2,1`, terminal lon error `0.007849 deg`, terminal i error `-0.009279 deg`, propellant `2097.137869 kg`, max burn `72.942825 min`; final P alpha fixed at `-180 deg`; 19 combined design/project tests passed.
Latest dynamic final-P alpha fix: default SLSQP run took about 20.3 s, selected q `1,1,1,1`, final P alpha `-178.578786 deg`, terminal lon error `-0.001199 deg`, terminal i error `0.002253 deg`, propellant `2598.321563 kg`; F4-like SLSQP run took about 19.1 s, selected q `1,3,2,1`, final P alpha `179.293510 deg`, terminal lon error `0.007897 deg`, terminal i error `-0.009535 deg`, propellant `2097.622147 kg`; 19 combined design/project tests passed.
Current V5.1 user-input task is complete. The design maneuver advanced page now exposes q_AA sequence, q_AP/q_AP candidates, fixed post-burn perigee-height targets, and hard-window options. When `apsis.pattern_mode=user`, the q_AA sequence drives the V5.1 burn count; fixed `index:hp_km` targets replace defaults and feed the optimizer.
Latest V5.1 user-input run: 7 design maneuver tests passed.
Current V5.1 page-UI task is superseded. The left-side `V5.1 用户约束` card was removed. MV1/MV2 front-burn cells in the result table `控后近地点高度/km` column are now highlighted and editable; edits write `hard_constraint_planner.fixed_hp_targets_km[1/2]` and trigger replanning.
Latest V5.1 perigee-table UI run: 7 design maneuver tests passed.
Current perigee-table editability fix is complete. The burn table now overrides its readonly edit triggers, uses cell selection, and paints MV1/MV2 editable perigee-height cells with a delegate so the orange highlight survives the global table stylesheet.
Latest perigee-table editability run: 7 design maneuver tests passed.
Current editable-cell affordance fix is complete. The MV1/MV2 perigee-height delegate now draws an orange border, corner marker, and `EDIT` badge after the default table paint so editable cells are visibly distinct from normal cells.
Latest editable-cell affordance run: 7 design maneuver tests passed.
Current V5.1 performance optimization is complete. Profiling showed the default one-free-variable perigee search ran bounded scalar optimization and then repeated Powell multi-starts; the Powell pass was redundant for 1D. `_v51_optimize_sequence()` now returns after bounded scalar optimization when there is exactly one variable. Default planning smoke dropped from about 37.7 s under cProfile / 1.83 s wall after the change; design maneuver tests dropped from about 76-84 s to 7.70 s while preserving the default result (`q=3,3,2,0`, propellant `2596.404635868783 kg`, hard constraints feasible).
Latest V5.1 performance run: cProfile completed; 7 design maneuver tests passed in 7.70 s.

Next minimum task: run the page on the real F4 project and confirm the operator-facing advanced settings values match the desired mission sequence and perigee-height targets.

## Working Rule

After each small task:

1. Summarize current changes.
2. Update this `HANDOFF.md` or `NOTES.md`.
3. Create a git checkpoint commit.
4. Continue in a new session when requested.
