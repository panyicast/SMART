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

## Modified / Added Areas

- `AGENTS.md`: added "Small Task Checkpoints" section requiring summary, handoff update, git checkpoint, and new-session continuation.
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
- `src/smart/services/stk_link.py`: new STK 11.6 launch/connect/import service; now tracks established scenarios, can attach to a running STK scenario without launching STK, can set STK current animation time, and can discard COM executors without losing established-scenario state.
- `src/smart/ui/widgets/stk_link_page.py`: new STK link UI page with worker thread; shares the MainWindow-owned `StkLinkService` so scenario state survives page switches; clears COM executor before and after each worker operation to avoid cross-thread COM reuse.
- `src/smart/ui/main_window.py`: owns one shared `StkLinkService` for STK link page and flight-program page.
- Tests updated/added for project workspace, flight program page, maneuver page, sidebar navigation, STK link helpers, existing-scenario STK time sync, debounced drag sync, and reference-jump sync.

## Risks

- STK Connect commands need real STK 11.6 end-to-end verification.
- STK link UI asset preview reads satellite-status settings, while sync service uses tracking-arc/launch-window assets; visible resources may differ from imported resources.
- Flight-program reference cache can become stale after orbit, tracking, launch-window, or constraint changes.
- `projects/F4/data/stk_link/` contains generated artifacts; decide whether to keep in git.
- Several files show LF-to-CRLF warnings on git diff/status.
- Real STK 11.6 UI/Connect validation is still needed for the automatic time sync path.
- Local F4 project has new STK-generated artifacts under `projects/F4/data/stk_link/20260512_132638/` and an updated `projects/F4/smart_project.json`; include them in the checkpoint only if preserving the real STK validation run is desired.
- Local `projects/F4/smart_project.json` changed again during real STK use and was intentionally left out of this COM-threading checkpoint.
- Local real STK retry also generated `projects/F4/data/stk_link/20260512_134342/`; decide later whether to keep committed, clean, or ignore generated STK exports.

## Next Minimum Task

Current playhead sync performance/jump fix is complete. Dragging no longer calls STK on every mouse move, and explicit jump paths route through immediate STK current-time sync.

Verified:

```powershell
D:\Spark\SMART\.venv\Scripts\python.exe -m pytest tests/test_project_workspace.py tests/test_flight_program_page.py tests/test_maneuver_page.py tests/test_sidebar_navigation.py tests/test_stk_link.py
```

Result: 63 passed.

Next minimum task: retry real STK 11.6 drag and jump behavior. If accepted, decide whether generated STK artifacts under `projects/F4/data/stk_link/` should remain committed or move to ignore/cleanup.

## Working Rule

After each small task:

1. Summarize current changes.
2. Update this `HANDOFF.md` or `NOTES.md`.
3. Create a git checkpoint commit.
4. Continue in a new session when requested.
