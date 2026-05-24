from __future__ import annotations

import csv
import math
import re
import socket
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from smart.services.earth_orientation import format_utc, parse_utc
from smart.services.flight_program import normalize_flight_program_payload, sample_flight_program_states
from smart.services.launch_window import (
    TrackingAsset,
    config_from_payload,
    default_launch_window_config,
    load_orbit_history_rows,
    tracking_assets_from_config,
)
from smart.services.project_workspace import ProjectInfo, ProjectWorkspace
from smart.services.stk_ephemeris import StkEphemerisMetadata, derive_scenario_epoch_utc, write_stk_ephemeris

STK_116_APP_PATH = Path(r"D:\Program Files\AGI\STK 116\bin\AgUiApplication.exe")
GEO_RADIUS_M = 42_164_000.0
DEFAULT_CONNECT_HOST = "127.0.0.1"
DEFAULT_CONNECT_PORT = 5001
_STK_SCENARIO_ESTABLISHED = False


@dataclass(frozen=True, slots=True)
class StkLinkArtifacts:
    orbit_ephemeris_path: Path | None = None
    attitude_path: Path | None = None
    relay_ephemeris_paths: tuple[Path, ...] = ()


@dataclass(frozen=True, slots=True)
class StkLinkResult:
    scenario_name: str
    satellite_name: str
    ground_station_count: int
    relay_satellite_count: int
    artifacts: StkLinkArtifacts
    commands: tuple[str, ...]


class StkCommandExecutor(Protocol):
    def execute(self, command: str, *, ignore_failure: bool = False) -> list[str]:
        ...


class StkComExecutor:
    def __init__(self, root: Any) -> None:
        self._root = root

    @property
    def root(self) -> Any:
        return self._root

    def execute(self, command: str, *, ignore_failure: bool = False) -> list[str]:
        try:
            result = self._root.ExecuteCommand(command)
        except Exception as exc:
            if ignore_failure:
                return []
            raise RuntimeError(f"STK command failed: {command}") from exc
        return _command_result_values(result)


class StkSocketExecutor:
    def __init__(self, host: str = DEFAULT_CONNECT_HOST, port: int = DEFAULT_CONNECT_PORT) -> None:
        self._host = host
        self._port = int(port)

    def execute(self, command: str, *, ignore_failure: bool = False) -> list[str]:
        payload = (command.rstrip() + "\n").encode("utf-8")
        chunks: list[bytes] = []
        try:
            with socket.create_connection((self._host, self._port), timeout=3.0) as sock:
                sock.settimeout(3.0)
                sock.sendall(payload)
                while True:
                    try:
                        data = sock.recv(4096)
                    except socket.timeout:
                        break
                    if not data:
                        break
                    chunks.append(data)
        except OSError as exc:
            if ignore_failure:
                return []
            raise RuntimeError(
                f"STK socket Connect failed at {self._host}:{self._port}: {exc}"
            ) from exc

        raw_text = b"".join(chunks).decode("utf-8", errors="replace").replace("\r", "")
        if raw_text.startswith("NACK") and not ignore_failure:
            raise RuntimeError(f"STK command failed: {command}\n{raw_text.strip()}")
        if not raw_text.startswith("ACK") and raw_text.strip() and not ignore_failure:
            raise RuntimeError(f"Unexpected STK Connect response: {raw_text.strip()}")
        body = raw_text[3:] if raw_text.startswith("ACK") else raw_text[4:] if raw_text.startswith("NACK") else raw_text
        return [line.rstrip() for line in body.splitlines() if line.strip()]


def launch_or_attach_stk_116(*, app_path: str | Path = STK_116_APP_PATH, timeout_s: float = 45.0) -> StkComExecutor:
    target_app = Path(app_path).expanduser().resolve()
    if not target_app.exists() and target_app == STK_116_APP_PATH:
        raise FileNotFoundError(f"STK 11.6 application not found: {STK_116_APP_PATH}")

    try:
        import win32com.client  # type: ignore[import-not-found]
    except Exception as exc:  # pragma: no cover - depends on local Windows COM runtime
        return _launch_and_attach_socket(target_app, timeout_s=timeout_s, reason=str(exc))  # type: ignore[return-value]

    app = _active_stk_app(win32com.client)
    if app is None:
        if target_app.exists():
            subprocess.Popen([str(target_app)], cwd=str(target_app.parent))  # noqa: S603
        else:
            app = win32com.client.Dispatch("STK11.Application")

    deadline = time.monotonic() + float(timeout_s)
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            app = app or _active_stk_app(win32com.client) or win32com.client.Dispatch("STK11.Application")
            app.Visible = True
            app.UserControl = True
            root = app.Personality2
            if root is not None:
                return StkComExecutor(root)
        except Exception as exc:  # pragma: no cover - depends on local STK startup timing
            last_error = exc
        time.sleep(0.5)
    raise RuntimeError(f"Unable to attach to STK 11.6 COM application: {last_error}")


def attach_to_running_stk_116_scenario() -> StkCommandExecutor | None:
    try:
        import win32com.client  # type: ignore[import-not-found]
    except Exception:
        win32com_client = None
    else:
        win32com_client = win32com.client

    if win32com_client is not None:
        app = _active_stk_app(win32com_client)
        if app is not None:
            try:
                root = app.Personality2
                if root is not None and getattr(root, "CurrentScenario", None) is not None:
                    _mark_stk_scenario_established()
                    return StkComExecutor(root)
            except Exception:
                return None

    if _STK_SCENARIO_ESTABLISHED and _socket_ready():
        return StkSocketExecutor()
    if _socket_ready():
        return StkSocketExecutor()
    return None


def _launch_and_attach_socket(
    app_path: Path,
    *,
    timeout_s: float,
    reason: str,
) -> StkSocketExecutor:
    if _socket_ready():
        return StkSocketExecutor()
    if app_path.exists():
        subprocess.Popen([str(app_path)], cwd=str(app_path.parent))  # noqa: S603
    deadline = time.monotonic() + float(timeout_s)
    while time.monotonic() < deadline:
        if _socket_ready():
            return StkSocketExecutor()
        time.sleep(0.5)
    raise RuntimeError(
        "pywin32/win32com is unavailable and STK socket Connect did not become ready "
        f"at {DEFAULT_CONNECT_HOST}:{DEFAULT_CONNECT_PORT}. Import error: {reason}"
    )


def _socket_ready(host: str = DEFAULT_CONNECT_HOST, port: int = DEFAULT_CONNECT_PORT) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


class StkLinkService:
    def __init__(
        self,
        workspace: ProjectWorkspace,
        *,
        executor: StkCommandExecutor | None = None,
    ) -> None:
        self._workspace = workspace
        self._executor = executor
        self._scenario_established = False
        self._commands: list[str] = []

    @property
    def executor(self) -> StkCommandExecutor | None:
        return self._executor

    def clear_executor(self) -> None:
        self._executor = None

    def connect(self) -> StkCommandExecutor:
        if self._executor is None:
            self._executor = launch_or_attach_stk_116()
        return self._executor

    def create_new_scenario(self, project: ProjectInfo | None = None) -> str:
        executor = self.connect()
        project = project or self._require_project()
        scenario_name = sanitize_stk_object_name(f"{project.name}_SMART")
        root = getattr(executor, "root", None)
        if root is not None:
            try:
                if getattr(root, "CurrentScenario", None) is not None:
                    root.CloseScenario()
                root.NewScenario(scenario_name)
            except Exception as exc:
                raise RuntimeError(f"Failed to create STK scenario '{scenario_name}': {exc}") from exc
        else:
            self._execute(f"New / Scenario {scenario_name}")
        self._mark_scenario_established()
        self._apply_flight_program_analysis_time(ignore_failure=True)
        return scenario_name

    def has_current_scenario(self) -> bool:
        executor = self._executor
        if executor is None:
            return False
        root = getattr(executor, "root", None)
        if root is None:
            return self._scenario_established or _STK_SCENARIO_ESTABLISHED
        try:
            has_scenario = getattr(root, "CurrentScenario", None) is not None
        except Exception:
            return self._scenario_established or _STK_SCENARIO_ESTABLISHED
        if has_scenario:
            self._mark_scenario_established()
        return has_scenario

    def sync_current_scenario_analysis_time(self) -> bool:
        if not self.has_current_scenario():
            if self._executor is not None:
                return False
            executor = attach_to_running_stk_116_scenario()
            if executor is None:
                return False
            self._executor = executor
            self._mark_scenario_established()
        return self._apply_flight_program_analysis_time(ignore_failure=False)

    def sync_current_scenario_time(self, current_utc: str | datetime) -> bool:
        if not self.has_current_scenario():
            if self._executor is not None:
                return False
            executor = attach_to_running_stk_116_scenario()
            if executor is None:
                return False
            self._executor = executor
            self._mark_scenario_established()
        current = parse_utc(current_utc) if isinstance(current_utc, str) else current_utc
        self._execute(f'SetAnimation * CurrentTime "{_format_stk_epoch(current)}"', ignore_failure=False)
        return True

    def import_project_to_stk(self) -> StkLinkResult:
        project = self._require_project()
        self._commands = []
        scenario_name = self.create_new_scenario(project)
        satellite_name = sanitize_stk_object_name(project.name)
        self._execute("Units_SetConnect / Distance Meter Latitude Degree Longitude Degree Date GregorianUTC Time Seconds")

        rows = load_orbit_history_rows(self._orbit_history_path())
        scenario_epoch_utc = self._scenario_epoch_utc(rows)
        stk_dir = self._stk_output_dir()
        orbit_path = stk_dir / f"{satellite_name}_orbit.e"
        orbit_metadata = write_stk_ephemeris(rows, orbit_path, scenario_epoch_utc=scenario_epoch_utc)
        start_time, stop_time = _stk_time_bounds(rows, scenario_epoch_utc=orbit_metadata.scenario_epoch_utc)

        self._execute(f'SetAnalysisTimePeriod * "{start_time}" "{stop_time}"')
        self._execute("SetAnimation * StartAndCurrentTime UseAnalysisStartTime", ignore_failure=True)
        self._execute("SetAnimation * EndTime UseAnalysisStopTime", ignore_failure=True)
        self._ensure_satellite(satellite_name)
        self._execute(f'SetState */Satellite/{satellite_name} FromFile "{orbit_metadata.output_path}" FileFormat StkPL')
        self._apply_satellite_graphics(satellite_name, label=_english_stk_label(project.name, fallback=satellite_name))
        self._apply_satellite_model(satellite_name)

        attitude_path: Path | None = None
        try:
            attitude_path = self._write_current_attitude_file(
                rows,
                stk_dir / f"{satellite_name}_attitude.a",
                scenario_epoch_utc=orbit_metadata.scenario_epoch_utc,
            )
            self._execute(f'SetAttitude */Satellite/{satellite_name} File Filename "{attitude_path}"')
        except Exception as exc:
            self._execute(f'VO */Satellite/{satellite_name} Model Detail On', ignore_failure=True)
            self._commands.append(f"# attitude import skipped: {exc}")

        assets = self.tracking_assets_for_sync()
        ground_count = self._create_ground_stations([asset for asset in assets if asset.asset_type == "ground"])
        relay_paths = self._create_relay_satellites(
            [asset for asset in assets if asset.asset_type == "relay"],
            stk_dir=stk_dir,
            scenario_epoch_utc=orbit_metadata.scenario_epoch_utc,
            start_time=start_time,
            stop_time=stop_time,
        )
        self._create_flight_event_annotations(rows, scenario_epoch_utc=orbit_metadata.scenario_epoch_utc)

        self._execute("Animate * Reset", ignore_failure=True)
        return StkLinkResult(
            scenario_name=scenario_name,
            satellite_name=satellite_name,
            ground_station_count=ground_count,
            relay_satellite_count=len(relay_paths),
            artifacts=StkLinkArtifacts(
                orbit_ephemeris_path=orbit_metadata.output_path,
                attitude_path=attitude_path,
                relay_ephemeris_paths=tuple(relay_paths),
            ),
            commands=tuple(self._commands),
        )

    def _create_ground_stations(self, ground_assets: list[TrackingAsset]) -> int:
        count = 0
        for asset in ground_assets:
            name = sanitize_stk_object_name(asset.name)
            if not name:
                continue
            self._execute(f"Unload / */Facility/{name}", ignore_failure=True)
            self._execute(f"New / */Facility {name}")
            self._execute(
                " ".join(
                    [
                        f"SetPosition */Facility/{name} Geodetic",
                        f"{float(asset.latitude_deg):.9f}",
                        f"{float(asset.longitude_deg):.9f}",
                        f"{float(asset.altitude_m):.3f}",
                    ]
                )
            )
            count += 1
        return count

    def _create_relay_satellites(
        self,
        relay_satellites: list[TrackingAsset],
        *,
        stk_dir: Path,
        scenario_epoch_utc: str,
        start_time: str,
        stop_time: str,
    ) -> list[Path]:
        output_paths: list[Path] = []
        for relay in relay_satellites:
            longitude = float(relay.longitude_deg)
            name = sanitize_stk_object_name(relay.name or f"Relay_{longitude:g}E")
            self._ensure_satellite(name)
            relay_path = write_geo_relay_ephemeris(
                stk_dir / f"{name}_geo.e",
                longitude_deg=longitude,
                scenario_epoch_utc=scenario_epoch_utc,
                duration_s=max(60.0, (_parse_stk_epoch(stop_time) - _parse_stk_epoch(start_time)).total_seconds()),
            )
            self._execute(f'SetState */Satellite/{name} FromFile "{relay_path}" FileFormat StkPL')
            self._apply_satellite_graphics(name, label=_english_stk_label(relay.name or name, fallback=name), color="#FFB347")
            output_paths.append(relay_path)
        return output_paths

    def _write_current_attitude_file(
        self,
        rows: list[dict[str, float | str]],
        output_path: Path,
        *,
        scenario_epoch_utc: str,
    ) -> Path:
        strategy = self._workspace.load_maneuver_strategy() or {}
        program = self._workspace.load_flight_program_config() or {}
        samples = sample_flight_program_states(
            orbit_history_csv=self._orbit_history_path(),
            maneuver_strategy=strategy,
            payload=program,
            t0_utc=scenario_epoch_utc,
        )
        points = [
            (float(row["elapsed_time_s"]), sample.plus_z_ecef)
            for row, sample in zip(rows, samples, strict=False)
        ]
        return write_stk_attitude_dcm(points, output_path, scenario_epoch_utc=scenario_epoch_utc)

    def _create_flight_event_annotations(
        self,
        rows: list[dict[str, float | str]],
        *,
        scenario_epoch_utc: str,
    ) -> int:
        program = normalize_flight_program_payload(self._workspace.load_flight_program_config() or {})
        events = [
            event
            for event in program.get("events", [])
            if isinstance(event, dict) and str(event.get("kind", "")).strip().lower() == "attitude"
        ]
        if not events:
            return 0
        epoch = parse_utc(scenario_epoch_utc)
        self._execute("VO * Annotation Delete AllAnnotations Text", ignore_failure=True)
        count = 0
        for index, event in enumerate(events, start=1):
            start_min = float(event.get("start_min", 0.0))
            end_min = float(event.get("end_min", start_min))
            if bool(event.get("instant", False)) or end_min <= start_min:
                end_min = start_min + 1.0
            name = f"FP_Event_{index:03d}"
            label = _escape_stk_string(_attitude_mode_label(event, index))
            start_time = _format_stk_epoch(epoch + timedelta(minutes=start_min))
            stop_time = _format_stk_epoch(epoch + timedelta(minutes=end_min))
            self._execute(
                " ".join(
                    [
                        f"VO * Annotation Add {name} Text",
                        f'String "{label}"',
                        "Coord Pixel",
                        "Position 24 32 0",
                        "HorizPixelOrigin Left",
                        "VertPixelOrigin Top",
                        "Color #FFD54A",
                        "FontStyle Large",
                        f'Interval Add 1 "{start_time}" "{stop_time}"',
                    ]
                ),
                ignore_failure=True,
            )
            count += 1
        self._execute("VO * Annotation Declutter On", ignore_failure=True)
        return count

    def _ensure_satellite(self, name: str) -> None:
        self._execute(f"Unload / */Satellite/{name}", ignore_failure=True)
        self._execute(f"New / */Satellite {name}")

    def _apply_satellite_graphics(self, name: str, *, label: str, color: str = "#00E5FF") -> None:
        path = f"*/Satellite/{name}"
        escaped_label = _escape_stk_string(_english_stk_label(label, fallback=name))
        self._execute(f"Graphics {path} SetColor {color} GroundTrack", ignore_failure=True)
        self._execute(f"Graphics {path} SetColor {color} Marker", ignore_failure=True)
        self._execute(f"Graphics {path} Label Show On", ignore_failure=True)
        self._execute(f'Graphics {path} Label LabelText "{escaped_label}"', ignore_failure=True)
        self._execute(
            f"Graphics {path} Pass2D GrndLead All GrndTrail SameAsLead Show All ShowPassLabels Off ShowPathLabels Off",
            ignore_failure=True,
        )
        self._execute(
            f"VO {path} Pass3D Inherit Off GroundLead None GroundTrail SameAsLead OrbitLead All OrbitTrail SameAsLead",
            ignore_failure=True,
        )

    def _apply_satellite_model(self, name: str) -> None:
        config = self._workspace.load_satellite_3d_model_config()
        if config is None:
            return
        raw_path = config.model_path.strip()
        if not raw_path:
            return
        model_path = Path(raw_path).expanduser()
        if not model_path.exists():
            self._commands.append(f"# satellite model skipped: file not found: {raw_path}")
            return
        if model_path.suffix.lower() not in {".mdl", ".dae", ".glb", ".gltf"}:
            self._commands.append(
                f"# satellite model skipped: unsupported model file extension: {model_path}"
            )
            return
        escaped_path = str(model_path.resolve()).replace('"', '\\"')
        self._execute(f'VO */Satellite/{name} Model File "{escaped_path}" Use ModelFile', ignore_failure=True)
        self._execute(f"VO */Satellite/{name} Model Show On", ignore_failure=True)

    def _execute(self, command: str, *, ignore_failure: bool = False) -> list[str]:
        executor = self.connect()
        self._commands.append(command)
        return executor.execute(command, ignore_failure=ignore_failure)

    def _scenario_epoch_utc(self, rows: list[dict[str, float | str]]) -> str:
        program = self._workspace.load_flight_program_config() or {}
        value = str(program.get("selected_t0_utc", "")).strip()
        if value:
            return format_utc(parse_utc(value), timespec="microseconds")
        strategy = self._workspace.load_maneuver_strategy() or {}
        value = str(strategy.get("t0_epoch", "")).strip()
        if value:
            return format_utc(parse_utc(value), timespec="microseconds")
        return derive_scenario_epoch_utc(rows)

    def _apply_flight_program_analysis_time(self, *, ignore_failure: bool) -> bool:
        path = self._orbit_history_path()
        if not path.exists():
            return False
        try:
            rows = load_orbit_history_rows(path)
            scenario_epoch_utc = self._scenario_epoch_utc(rows)
            start_time, stop_time = _stk_time_bounds(rows, scenario_epoch_utc=scenario_epoch_utc)
        except Exception:
            return False
        self._execute(f'SetAnalysisTimePeriod * "{start_time}" "{stop_time}"', ignore_failure=ignore_failure)
        self._execute("SetAnimation * StartAndCurrentTime UseAnalysisStartTime", ignore_failure=True)
        self._execute("SetAnimation * EndTime UseAnalysisStopTime", ignore_failure=True)
        return True

    def tracking_assets_for_sync(self) -> list[TrackingAsset]:
        payload = (
            self._workspace.load_tracking_arc_config()
            or self._workspace.load_launch_window_config()
            or default_launch_window_config()
        )
        return tracking_assets_from_config(config_from_payload(payload))

    def _flight_program_tracking_assets(self) -> list[TrackingAsset]:
        return self.tracking_assets_for_sync()

    def _orbit_history_path(self) -> Path:
        return self._workspace.data_dir() / "full_orbit_history.csv"

    def _stk_output_dir(self) -> Path:
        project = self._require_project()
        stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
        target = project.root_dir / "data" / "stk_link" / stamp
        target.mkdir(parents=True, exist_ok=True)
        return target

    def _require_project(self) -> ProjectInfo:
        project = self._workspace.current_project
        if project is None:
            raise RuntimeError("No active SMART project.")
        return project

    def _mark_scenario_established(self) -> None:
        self._scenario_established = True
        _mark_stk_scenario_established()


def _mark_stk_scenario_established() -> None:
    global _STK_SCENARIO_ESTABLISHED
    _STK_SCENARIO_ESTABLISHED = True


def write_stk_attitude_dcm(
    points: list[tuple[float, tuple[float, float, float]]],
    output_path: str | Path,
    *,
    scenario_epoch_utc: str,
) -> Path:
    if not points:
        raise ValueError("STK attitude export requires at least one attitude point.")
    target = Path(output_path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    epoch_text = _format_stk_epoch(parse_utc(scenario_epoch_utc))
    lines = [
        "stk.v.11.0",
        "",
        "BEGIN Attitude",
        "",
        f"NumberOfAttitudePoints  {len(points)}",
        f"ScenarioEpoch           {epoch_text}",
        "BlockingFactor          20",
        "InterpolationOrder      1",
        "CentralBody             Earth",
        "CoordinateAxes          Fixed",
        "",
        "AttitudeTimeDCM",
        "",
    ]
    for elapsed_s, plus_z in points:
        matrix = _dcm_rows_from_body_z(plus_z)
        flattened = [f"{value:.14e}" for row in matrix for value in row]
        lines.append(" ".join([f"{float(elapsed_s):.14e}", *flattened]))
    lines.extend(["", "END Attitude", ""])
    target.write_text("\n".join(lines), encoding="utf-8")
    return target


def write_geo_relay_ephemeris(
    output_path: str | Path,
    *,
    longitude_deg: float,
    scenario_epoch_utc: str,
    duration_s: float,
) -> Path:
    target = Path(output_path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    lon_rad = math.radians(float(longitude_deg))
    x = GEO_RADIUS_M * math.cos(lon_rad)
    y = GEO_RADIUS_M * math.sin(lon_rad)
    z = 0.0
    stop_s = max(60.0, float(duration_s))
    epoch_text = _format_stk_epoch(parse_utc(scenario_epoch_utc))
    lines = [
        "stk.v.11.0",
        "",
        "BEGIN Ephemeris",
        "",
        "NumberOfEphemerisPoints 2",
        f"ScenarioEpoch           {epoch_text}",
        "InterpolationMethod     Lagrange",
        "InterpolationOrder      1",
        "CentralBody             Earth",
        "CoordinateSystem        Fixed",
        "DistanceUnit            Meters",
        "",
        "EphemerisTimePosVel",
        "",
        f"0.00000000000000e+00 {x:.14e} {y:.14e} {z:.14e} 0.00000000000000e+00 0.00000000000000e+00 0.00000000000000e+00",
        f"{stop_s:.14e} {x:.14e} {y:.14e} {z:.14e} 0.00000000000000e+00 0.00000000000000e+00 0.00000000000000e+00",
        "",
        "END Ephemeris",
        "",
    ]
    target.write_text("\n".join(lines), encoding="utf-8")
    return target


def sanitize_stk_object_name(raw_name: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z_]", "_", str(raw_name).strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    if not cleaned:
        return "SMART_Object"
    if cleaned[0].isdigit():
        cleaned = f"Obj_{cleaned}"
    return cleaned


def parse_relay_longitude_deg(value: str) -> float | None:
    text = str(value).strip()
    if not text:
        return None
    matches = list(re.finditer(r"([+-]?\d+(?:\.\d+)?)\s*([EeWw])?", text))
    if not matches:
        return None
    match = next((item for item in reversed(matches) if item.group(2)), matches[-1])
    longitude = float(match.group(1))
    suffix = (match.group(2) or "E").upper()
    if suffix == "W":
        longitude = -longitude
    while longitude > 180.0:
        longitude -= 360.0
    while longitude < -180.0:
        longitude += 360.0
    return longitude


def _dcm_rows_from_body_z(plus_z: tuple[float, float, float]) -> np.ndarray:
    z_axis = _normalize(np.asarray(plus_z, dtype=np.float64))
    seed = np.asarray([0.0, 0.0, 1.0], dtype=np.float64)
    if abs(float(np.dot(seed, z_axis))) > 0.92:
        seed = np.asarray([0.0, 1.0, 0.0], dtype=np.float64)
    x_axis = _normalize(np.cross(seed, z_axis))
    y_axis = _normalize(np.cross(z_axis, x_axis))
    x_axis = _normalize(np.cross(y_axis, z_axis))
    return np.vstack([x_axis, y_axis, z_axis])


def _normalize(vector: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    if not math.isfinite(norm) or norm <= 1e-12:
        return np.asarray([1.0, 0.0, 0.0], dtype=np.float64)
    return vector / norm


def _stk_time_bounds(
    rows: list[dict[str, float | str]],
    *,
    scenario_epoch_utc: str,
) -> tuple[str, str]:
    start_offset_s = min(float(row["elapsed_time_s"]) for row in rows)
    stop_offset_s = max(float(row["elapsed_time_s"]) for row in rows)
    epoch = parse_utc(scenario_epoch_utc)
    start = epoch + timedelta(seconds=start_offset_s)
    stop = epoch + timedelta(seconds=max(stop_offset_s, start_offset_s + 60.0))
    return _format_stk_epoch(start), _format_stk_epoch(stop)


def _format_stk_epoch(value: datetime) -> str:
    text = value.astimezone(timezone.utc).strftime("%d %b %Y %H:%M:%S.%f")
    return text[1:] if text.startswith("0") else text


def _parse_stk_epoch(value: str) -> datetime:
    return datetime.strptime(value, "%d %b %Y %H:%M:%S.%f").replace(tzinfo=timezone.utc)


def _escape_stk_string(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def _attitude_mode_label(event: dict[str, Any], index: int) -> str:
    mode = str(event.get("mode", "") or "").strip().upper()
    if mode in {"SPM", "EPM", "AFM"}:
        return mode
    if mode in {"TRANSITION", "TRM"}:
        return "TRM"
    return _english_stk_label(mode, fallback=f"ATTITUDE_{index}")


def _english_stk_label(value: str, *, fallback: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z_ .:+\\/()-]", " ", str(value))
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or fallback


def _active_stk_app(win32com_client: Any) -> Any | None:
    try:
        return win32com_client.GetActiveObject("STK11.Application")
    except Exception:
        return None


def _command_result_values(result: Any) -> list[str]:
    if result is None:
        return []
    try:
        count = int(result.Count)
    except Exception:
        return []
    values: list[str] = []
    for index in range(count):
        try:
            values.append(str(result[index]).strip())
        except Exception:
            try:
                values.append(str(result.get_Item(index)).strip())
            except Exception:
                values.append("")
    return values


def read_artifact_summary(path: Path) -> tuple[int, str]:
    if not path.exists():
        return 0, ""
    with path.open("r", encoding="utf-8", newline="") as handle:
        row_count = max(0, sum(1 for _ in csv.reader(handle)) - 1)
    return row_count, str(path)
