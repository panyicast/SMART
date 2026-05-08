# SMART Agent Notes

## STK Help KB

- For STK command/API questions, query this global KB first:
  - `C:\Users\panyi\.codex\kb\stk11_help.sqlite3`
- Use command:
  - `stkhelp "<query>"`
- Rebuild/update index when STK docs change:
  - `stkhelp-rebuild`
- This machine has both STK 11.6 and STK 12.2 installed.
- The local STK 11.6 runtime root on this machine is:
  - `D:\Program Files\AGI\STK 116`
- The local STK 11.6 general help system root on this machine is:
  - `D:\Program Files\AGI\STK 116\Help`
- Prefer these local STK 11.6 help entry points when checking docs:
  - General help: `D:\Program Files\AGI\STK 116\Help\index.htm`
  - Programming help: `D:\Program Files\AGI\STK 116\Help\Programming\index.htm`
  - Connect help: `D:\Program Files\AGI\STK 116\Help\Programming\Subsystems\connect\connect.htm`
  - Connect command help: `D:\Program Files\AGI\STK 116\Help\Programming\Subsystems\connectCmds\connectCmds.htm`
  - Release notes: `D:\Program Files\AGI\STK 116\Help\releaseNotes.chm`
- Unless the user explicitly asks for STK 12.2, default all STK operations, automation, compatibility assumptions, and help/doc lookups to STK 11.6.
- Do not assume `C:\Program Files\AGI\STK 11` is the active STK 11.6 runtime path.
- When checking command syntax or API behavior, prefer STK 11.6 help content first and avoid mixing in 12.2-only behavior by default.

## Cesium / Qt WebEngine Pitfalls

- Do not depend on the public Cesium CDN inside SMART.
  - On this machine family, Qt WebEngine may fail TLS handshake with `net_error -101`.
  - The runtime is vendored locally under `src/smart/assets/cesium/vendor/Build/Cesium`.
  - To refresh or upgrade the local runtime, run `.\scripts\vendor-cesium.ps1`.

- Do not tighten the mission-view CSP without keeping Cesium workers working.
  - Local Cesium uses `blob:` workers.
  - `mission_view.html` and `cesium_probe.html` must allow `worker-src blob:` and `script-src blob:`.
  - If workers are blocked, the page can remain stuck on `Loading...` even though the runtime loaded.

- Keep Qt Quick and the QWidget shell on the same graphics API.
  - SMART mixes `pyqtgraph.opengl.GLViewWidget` with Qt WebEngine / Qt Quick composition.
  - `smart.app_runtime.configure_graphics_backend()` must keep:
    - `QSG_RHI_BACKEND=opengl`
    - `QT_OPENGL=desktop`
    - `QQuickWindow.setGraphicsApi(OpenGL)`
  - If this drifts, expect errors like:
    - `QQuickWidget: Failed to get a QRhi from the top-level widget's window`
    - `The top-level window is not using the expected graphics API for composition`

- If the standalone scene opens blank on the first load but works after clicking reload, suspect scene-publish ordering.
  - The first `requestScene()` from the page can arrive before `_page_loaded` becomes `True`.
  - `CesiumMissionView._on_load_finished()` must republish the scene after successful load.

- Prefer using the standalone `3D 场景测试` page first when debugging.
  - It removes dashboard scroll/card layout variables.
  - If the test page works and the dashboard does not, investigate embedding/layout differences next.

## Recommended Cesium Debug Order

1. Run `smart-webengine-diagnostics` and confirm:
   - `WebGL Probe` renders.
   - `Cesium Probe` renders.
2. Open SMART `3D 场景测试`.
3. Only after that, debug the dashboard-embedded Cesium card.

## Documentation

- Detailed Cesium troubleshooting notes live in:
  - `doc/cesium_troubleshooting.md`
- Detailed SPICE usage requirements and examples live in:
  - `doc/spice_usage.md`
- Detailed AI project analysis page usage and API configuration notes live in:
  - `doc/ai_project_analysis.md`
- Detailed launch-window workflow, cache files, result CSV, and Gantt rendering notes live in:
  - `doc/launch_window_workflow.md`
- Detailed launch-window angle definitions, formulas, and visibility rules live in:
  - `doc/launch_window_angle_reference.md`
- Lightweight file-based planning guidance for complex tasks lives in:
  - `doc/planning_workflow.md`
- Planning templates for complex tasks live in:
  - `doc/planning_templates/`
- A helper script to initialize a local planning session lives in:
  - `scripts/init-planning-session.ps1`

## Planning Workflow

- For complex tasks, prefer the lightweight file-based planning workflow described in `doc/planning_workflow.md`.
- Use it for multi-step debugging, cross-module changes, research-heavy work, or tasks likely to span multiple sessions.
- Keep planning artifacts local under `.planning/`; do not commit them unless the user explicitly asks.
- Use planning files as working memory. Promote only stable, reusable conclusions into `AGENTS.md` or the relevant permanent doc.
- Do not store secrets, full kernel contents, or large CSV dumps in planning files; record concise summaries and paths instead.

## UI Rules

- UI layer time fields should be shown and edited in Beijing Time (UTC+8). Convert to UTC only at service/config boundaries.
- Avoid mouse-wheel-driven parameter edits. Numeric, datetime, and parameter combo-box controls should use no-wheel widgets so scrolling a page/table does not silently modify values.

## Launch Window Rules

- Launch-window analysis reuses the maneuver page output `data/full_orbit_history.csv`; it does not reintegrate the orbit during scanning.
- UI datetime fields and result tables are Beijing Time. Service/config boundaries store UTC strings.
- Project load may display cached `data/launch_window_samples.csv` directly. When changing launch-window constraints, sampling step, visibility thresholds, or calculation logic, regenerate the sample CSV before trusting cached tables or Gantt output.
- Result CSV export writes the current result table to `data/launch_window_results.csv` with UTF-8 BOM for spreadsheet compatibility.
- The Gantt chart renders the merged launch-window result as the first row in red. Each enabled/disabled constraint row gets its own row; passing intervals are green and failing intervals remain blank.
- The launch-window result table currently keeps: window start, window end, duration, leading T0, first-orbit shadow, leading-edge longest shadow, trailing-edge longest shadow, leading constraint, and trailing constraint.
- Keep performance-sensitive loops in `smart.services.launch_window` vectorized with NumPy where practical. Avoid per-sample Qt `processEvents()` calls; progress updates should stay throttled.
- Default relay satellite longitude presets are `77`, `171`, `10.6`, `80`, and `20.4` degrees for TL2-1 through TL2-5. Keep project templates and F4 config aligned unless the user explicitly changes mission data.

## AI Project Analysis Rules

- The AI project analysis page must remain optional. Missing API credentials should not affect normal SMART startup or mission calculations.
- API keys may be read from `SMART_LLM_API_KEY` or `DEEPSEEK_API_KEY`, or saved in local `QSettings`; never write API keys to project `config/*.json`, `data/*.csv`, docs, or tests.
- Only send summarized project context to the model. Do not upload full binary files, SPICE kernels, charts, `.tmp` artifacts, or full large CSV contents.
- Keep LLM HTTP calls out of the UI thread. Use a worker thread and report failures in the page status area.
- Treat model output as advisory. Do not let model responses directly mutate project configuration without an explicit user confirmation flow.

## SPICE Rules

- Prefer SPICE first for orbit, epoch/time, frame, and state-vector related processing.
- Default to local kernels; do not assume online kernel access.
- Default kernel search order is:
  - project `data/kernels/`
  - repo-root `data/kernels/`
- Reuse `smart.services.spice_service.SpiceKernelManager` for kernel loading, UTC/ET conversion, frame conversion, and state queries.
- Reuse `smart.services.orbital_mechanics` for SPICE-first orbital element/state conversions instead of reimplementing formulas in UI code.
- For STK `.e` ephemeris import, only Earth-centered files are supported right now. Inertial frames pass through directly; `Fixed` / `ITRF93` / `IAU_EARTH` require local SPICE kernels for conversion to `J2000`.
- Keep manual math only as a fallback when SpiceyPy or the required kernels are unavailable.

## Testing

- In this repo, do not assume `pytest` is available on `PATH`.
- Prefer running tests with the project virtual environment explicitly:
  - `D:\Spark\SMART\.venv\Scripts\python.exe -m pytest`
- Prefer this explicit interpreter form over bare `pytest` to avoid mixing Conda/system Python with the repo venv.

## Update Log

- Root `updates.md` is auto-maintained by `.githooks/commit-msg` via `scripts/update_updates_md.py`.
- `.\scripts\setup.ps1` installs the hook automatically. If needed, rerun `.\scripts\install-git-hooks.ps1`.
- Prefer letting the hook refresh `updates.md` during `git commit` instead of editing the file by hand.
