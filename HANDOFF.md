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
- `src/smart/services/stk_link.py`: new STK 11.6 launch/connect/import service; now tracks established scenarios, can attach to a running STK scenario without launching STK, can set STK current animation time, can discard COM executors without losing established-scenario state, and adds English-only 3D annotations for flight-program events.
- `src/smart/services/launch_window.py`: tracking ground-station defaults now use English names, and old Chinese default station names migrate to their English equivalents when configs are normalized.
- `src/smart/ui/widgets/stk_link_page.py`: new STK link UI page with worker thread; shares the MainWindow-owned `StkLinkService` so scenario state survives page switches; clears COM executor before and after each worker operation to avoid cross-thread COM reuse; previews tracking-arc assets instead of satellite-status assets.
- `projects/F4/config/launch_window.json`, `projects/F4/config/tracking_arc.json`: default tracking ground stations renamed to English.
- `src/smart/ui/main_window.py`: owns one shared `StkLinkService` for STK link page and flight-program page.
- Tests updated/added for project workspace, flight program page, maneuver page, sidebar navigation, STK link helpers, existing-scenario STK time sync, debounced drag sync, and reference-jump sync.

## Risks

- STK Connect commands need real STK 11.6 end-to-end verification.
- Flight-program reference cache can become stale after orbit, tracking, launch-window, or constraint changes.
- `projects/F4/data/stk_link/` contains generated artifacts; decide whether to keep in git.
- Several files show LF-to-CRLF warnings on git diff/status.
- Real STK 11.6 UI/Connect validation is still needed for the automatic time sync path.
- Local F4 project has new STK-generated artifacts under `projects/F4/data/stk_link/20260512_132638/` and an updated `projects/F4/smart_project.json`; include them in the checkpoint only if preserving the real STK validation run is desired.
- Local `projects/F4/smart_project.json` changed again during real STK use and was intentionally left out of this COM-threading checkpoint.
- Local real STK retry also generated `projects/F4/data/stk_link/20260512_134342/`; decide later whether to keep committed, clean, or ignore generated STK exports.

## Next Minimum Task

Current STK tracking-resource sync fix is complete. STK link preview and STK import both use tracking-arc assets, and default tracking station names are English.
Current STK flight-event annotation fix is complete. STK import creates English-only `VO Annotation` labels for all flight-program events using their computed start/end intervals.

Verified:

```powershell
D:\Spark\SMART\.venv\Scripts\python.exe -m pytest tests/test_project_workspace.py tests/test_flight_program_page.py tests/test_maneuver_page.py tests/test_sidebar_navigation.py tests/test_stk_link.py
D:\Spark\SMART\.venv\Scripts\python.exe -m pytest tests/test_stk_link.py tests/test_launch_window.py
D:\Spark\SMART\.venv\Scripts\python.exe -m pytest tests/test_project_workspace.py tests/test_tracking_arc.py tests/test_launch_window_page.py
D:\Spark\SMART\.venv\Scripts\python.exe -m pytest tests/test_stk_link.py
D:\Spark\SMART\.venv\Scripts\python.exe -m pytest tests/test_stk_link.py tests/test_flight_program.py tests/test_flight_program_page.py
```

Result: 63 passed for the previous playhead checkpoint; 41 passed for STK/launch-window focused tests; 26 passed for project/tracking/page regression tests; 13 passed for STK annotation tests; 54 passed for STK/flight-program regression tests.

Next minimum task: run one real STK 11.6 sync from the STK link page and confirm STK creates `Xiamen_Station`, `Weinan_Station`, `Kashi_Station`, `TL2_2`, and `TL2_3` from the F4 tracking-arc config, plus `FP_Event_###` 3D annotations with English-only text and correct intervals. If accepted, decide whether generated STK artifacts under `projects/F4/data/stk_link/` should remain committed or move to ignore/cleanup.

## Working Rule

After each small task:

1. Summarize current changes.
2. Update this `HANDOFF.md` or `NOTES.md`.
3. Create a git checkpoint commit.
4. Continue in a new session when requested.
