# HANDOFF

## Current State

- Recovered from compact failure by re-reading current repo state only.
- No prior hidden context assumed.
- Root `HANDOFF.md` / `NOTES.md` did not exist before this file.
- `git status` shows WIP across project config, UI, STK link service/page, tests, and F4 generated data.
- Added persistent small-task checkpoint rules to `AGENTS.md`.
- Implemented STK scene time sync from the flight-program page when an existing STK scenario is available.

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
- `src/smart/ui/widgets/flight_program_page.py`: adds autosave and reference-result cache load/save; launch-source/manual/window/orbit-point T0 changes now try to sync existing STK scenario analysis time.
- `src/smart/ui/widgets/maneuver_page.py`: changes ground-track maneuver-number labels to yellow text with black outline and smaller offset.
- `src/smart/services/stk_link.py`: new STK 11.6 launch/connect/import service; now tracks established scenarios and can attach to a running STK scenario without launching STK to update analysis time.
- `src/smart/ui/widgets/stk_link_page.py`: new STK link UI page with worker thread.
- Tests updated/added for project workspace, flight program page, maneuver page, sidebar navigation, STK link helpers, and existing-scenario STK time sync.

## Risks

- STK Connect commands need real STK 11.6 end-to-end verification.
- STK link UI asset preview reads satellite-status settings, while sync service uses tracking-arc/launch-window assets; visible resources may differ from imported resources.
- Flight-program reference cache can become stale after orbit, tracking, launch-window, or constraint changes.
- `projects/F4/data/stk_link/` contains generated artifacts; decide whether to keep in git.
- Several files show LF-to-CRLF warnings on git diff/status.
- Real STK 11.6 UI/Connect validation is still needed for the automatic time sync path.

## Next Minimum Task

Current STK time-sync task is complete.

Verified:

```powershell
D:\Spark\SMART\.venv\Scripts\python.exe -m pytest tests/test_project_workspace.py tests/test_flight_program_page.py tests/test_maneuver_page.py tests/test_sidebar_navigation.py tests/test_stk_link.py
```

Result: 58 passed.

Next minimum task: validate the STK time-sync behavior against a real STK 11.6 scenario, then decide whether `projects/F4/data/stk_link/` generated artifacts should remain committed or move to ignore/cleanup.

## Working Rule

After each small task:

1. Summarize current changes.
2. Update this `HANDOFF.md` or `NOTES.md`.
3. Create a git checkpoint commit.
4. Continue in a new session when requested.
