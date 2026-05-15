# HANDOFF

## Current State

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
- `tests/test_design_maneuver_strategy.py`: covers supersynchronous fixed-tail planning, standard transfer user count, page config independence, and config normalization.
- `tests/test_design_maneuver_strategy.py`: now also checks reference default output landmarks: 5 burns, 1539 m/s estimate, 312.123864 m/s design burn, first apogee event longitude/time, and final fixed-tail Δv.
- `src/smart/ui/widgets/design_maneuver_strategy_page.py`: current parameter settings moved out of the left scroll form into two dedicated dialogs with Beijing-time epoch editing, no-wheel controls, and the same dark/cyan/orange dialog style used by maneuver configuration.
- `src/smart/ui/i18n.py`, `tests/test_design_maneuver_strategy.py`: added labels/status text and regression coverage for the two-dialog editing flow.
- `src/smart/ui/widgets/flight_program_page.py`, `src/smart/ui/widgets/design_maneuver_strategy_page.py`: reduced oversized minimum heights that inflated `MainWindow` minimum geometry on scaled Windows displays.
- `tests/test_sidebar_navigation.py`: adds a main-window minimum-height regression check.
- `src/smart/ui/widgets/design_maneuver_strategy_page.py`: places the recommendation summary card at the bottom of the left config column; right column now starts with the pulse burn table.
- `tests/test_design_maneuver_strategy.py`: verifies the recommendation card lives in the left config panel.

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
- SciPy is not installed in the repo venv, so the planner currently evaluates the reference initial template and fixed-tail solve without Powell optimization. Optional optimizer fields are preserved in config for future SciPy-backed optimization.

## Next Minimum Task

Current STK tracking-resource sync fix is complete. STK link preview and STK import both use tracking-arc assets, and default tracking station names are English.
Current STK flight-event annotation fix is complete. STK import creates English-only `VO Annotation` labels for all flight-program events using their computed start/end intervals.
Current STK label regex crash fix is complete. `_english_stk_label()` now accepts ASCII `/` and `-` without forming an invalid regex range.
Current STK attitude-mode annotation update is complete. STK import now labels only attitude modes with `SPM`/`EPM`/`AFM`/`TRM` in the 3D upper-left corner.
Current design maneuver strategy page task is complete. The page has its own config file and does not mutate the import maneuver strategy config.
Current reference-alignment task is complete. The default design planner now matches the provided V4.2 package shape and major default-output landmarks while staying runnable without SciPy.
Current design maneuver dialog split task is complete. Parameter configuration and advanced settings are separated into two dialogs, and planning still uses the independent design config.
Current Qt geometry warning fix is complete. Main window `minimumSizeHint().height()` now measures 942 in the local check, down from the warning-producing oversized layout.
Current design maneuver summary placement task is complete. The recommendation card is now in the page lower-left.

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
```

Result: 63 passed for the previous playhead checkpoint; 41 passed for STK/launch-window focused tests; 26 passed for project/tracking/page regression tests; 13 passed for STK annotation tests; 54 passed for STK/flight-program regression tests; 14 passed for the STK label regex fix; 14 passed for the attitude-mode Pixel annotation test; 55 passed for STK/flight-program regression tests; 16 passed for design maneuver focused tests; 182 passed for full suite; after reference alignment, 16 focused tests passed and 183 full tests passed; after dialog split, 16 focused project/design tests passed; after geometry fix, 58 focused UI/project tests passed; after summary-card move, 11 focused design/sidebar tests passed.

Next minimum task: visually smoke-test maximize/restore and the two design-maneuver dialogs in the running Qt app, then if continuing design strategy work add an explicit user-confirmed export/mapping from pulse output into finite-thrust `maneuver_strategy.json`.

## Working Rule

After each small task:

1. Summarize current changes.
2. Update this `HANDOFF.md` or `NOTES.md`.
3. Create a git checkpoint commit.
4. Continue in a new session when requested.
