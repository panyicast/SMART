from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from smart.domain.models import (
    AntennaConfig,
    GroundAssetConfig,
    OrbitInitializationSettings,
    OrbitalElements,
    RelaySatelliteConfig,
    SatelliteStructureConfig,
    SatelliteStatusSettings,
)
from smart.services.earth_orientation import format_utc, parse_utc, utc_now_iso_z
from smart.services.design_maneuver_strategy import (
    ContinuousThrustOptimizationResult,
    DesignManeuverResult,
    continuous_thrust_result_from_payload,
    continuous_thrust_result_to_maneuver_strategy_payload,
    continuous_thrust_result_to_payload,
    design_maneuver_result_from_payload,
    design_maneuver_result_to_payload,
    default_design_maneuver_strategy_payload,
    normalize_design_maneuver_strategy_payload,
)
from smart.services.launch_window import default_launch_window_config, normalize_launch_window_config
from smart.services.flight_program import default_flight_program_payload, normalize_flight_program_payload

PROJECT_META_FILE = "smart_project.json"
PROJECTS_DIR_NAME = "projects"
DATA_DIR_NAME = "data"
KERNELS_DIR_NAME = "kernels"
CHARTS_DIR_NAME = "charts"
CONFIG_DIR_NAME = "config"

ORBIT_ELEMENTS_FILE = "orbit_elements.json"
MANEUVER_SNAPSHOT_FILE = "maneuver_snapshot.json"
SATELLITE_SETTINGS_FILE = "satellite_status.json"
SATELLITE_3D_MODEL_FILE = "satellite_3d_model.json"
ORBIT_INITIALIZATION_FILE = "orbit_initialization.json"
MANEUVER_STRATEGY_FILE = "maneuver_strategy.json"
DESIGN_MANEUVER_STRATEGY_FILE = "design_maneuver_strategy.json"
DESIGN_IMPORT_MANEUVER_STRATEGY_FILE = "design_import_maneuver_strategy.json"
DESIGN_MANEUVER_RESULTS_FILE = "design_maneuver_results.json"
DESIGN_CONTINUOUS_THRUST_RESULTS_FILE = "design_continuous_thrust_results.json"
LAUNCH_WINDOW_FILE = "launch_window.json"
TRACKING_ARC_FILE = "tracking_arc.json"
TRACKING_ARC_RESULTS_FILE = "tracking_arc_results.json"
FLIGHT_PROGRAM_FILE = "flight_program.json"
FLIGHT_PROGRAM_REFERENCE_RESULTS_FILE = "flight_program_reference_results.json"


@dataclass(slots=True, frozen=True)
class ProjectInfo:
    name: str
    root_dir: Path
    created_utc: str
    updated_utc: str
    version: int = 1


class ProjectWorkspace:
    def __init__(self) -> None:
        self._project: ProjectInfo | None = None

    @property
    def current_project(self) -> ProjectInfo | None:
        return self._project

    @property
    def root_dir(self) -> Path | None:
        if self._project is None:
            return None
        return self._project.root_dir

    def projects_dir(self, base_dir: str | Path | None = None) -> Path:
        root_dir = Path.cwd() if base_dir is None else Path(base_dir)
        return (root_dir / PROJECTS_DIR_NAME).resolve()

    def create_project(self, name: str, parent_dir: str | Path | None = None) -> ProjectInfo:
        project_name = name.strip()
        if not project_name:
            raise ValueError("Project name cannot be empty.")

        base_dir = self.projects_dir() if parent_dir is None else Path(parent_dir).expanduser().resolve()
        base_dir.mkdir(parents=True, exist_ok=True)
        root_dir = (base_dir / project_name).resolve()
        if root_dir.exists():
            raise FileExistsError(f"Project directory already exists: {root_dir}")

        created_at = _utc_now_iso()
        root_dir.mkdir(parents=True, exist_ok=False)
        (root_dir / DATA_DIR_NAME).mkdir(parents=True, exist_ok=True)
        (root_dir / DATA_DIR_NAME / KERNELS_DIR_NAME).mkdir(parents=True, exist_ok=True)
        (root_dir / CHARTS_DIR_NAME).mkdir(parents=True, exist_ok=True)
        (root_dir / CONFIG_DIR_NAME).mkdir(parents=True, exist_ok=True)

        metadata: dict[str, Any] = {
            "name": project_name,
            "version": 1,
            "created_utc": created_at,
            "updated_utc": created_at,
        }
        _write_json(root_dir / PROJECT_META_FILE, metadata)

        self._project = self._read_project(root_dir)
        self.save_satellite_3d_model_config(SatelliteStructureConfig())
        self.save_orbit_initialization(OrbitInitializationSettings())
        self.save_maneuver_strategy(default_maneuver_strategy_payload())
        self.save_design_maneuver_strategy(default_design_maneuver_strategy_payload())
        launch_window_config = default_launch_window_config()
        self.save_launch_window_config(launch_window_config)
        self.save_tracking_arc_config(launch_window_config)
        self.save_flight_program_config(default_flight_program_payload())
        return self._project

    def open_project(self, project_dir: str | Path) -> ProjectInfo:
        root_dir = Path(project_dir).expanduser().resolve()
        if not root_dir.exists():
            raise FileNotFoundError(f"Project directory not found: {root_dir}")
        self._project = self._read_project(root_dir)
        self.data_dir().mkdir(parents=True, exist_ok=True)
        self.kernels_dir().mkdir(parents=True, exist_ok=True)
        self.charts_dir().mkdir(parents=True, exist_ok=True)
        self.config_dir().mkdir(parents=True, exist_ok=True)
        return self._project

    def close_project(self) -> None:
        self._project = None

    def save_project_as(self, target_dir: str | Path) -> ProjectInfo:
        project = self._require_project()
        source_dir = project.root_dir.resolve()
        destination_dir = Path(target_dir).expanduser().resolve()
        if destination_dir == source_dir:
            raise FileExistsError(f"Target project directory is the current project: {destination_dir}")
        if source_dir in destination_dir.parents:
            raise ValueError("Target project directory cannot be inside the current project.")
        if destination_dir.exists() and not destination_dir.is_dir():
            raise FileExistsError(f"Target project path is not a directory: {destination_dir}")
        if destination_dir.exists() and any(destination_dir.iterdir()):
            raise FileExistsError(f"Target project directory is not empty: {destination_dir}")

        if destination_dir.exists():
            shutil.rmtree(destination_dir)
        shutil.copytree(source_dir, destination_dir)

        metadata_path = destination_dir / PROJECT_META_FILE
        payload = _read_json(metadata_path)
        payload["name"] = destination_dir.name
        payload["updated_utc"] = _utc_now_iso()
        _write_json(metadata_path, payload)
        return self.open_project(destination_dir)

    def data_dir(self) -> Path:
        root_dir = self._require_project().root_dir
        return root_dir / DATA_DIR_NAME

    def charts_dir(self) -> Path:
        root_dir = self._require_project().root_dir
        return root_dir / CHARTS_DIR_NAME

    def kernels_dir(self) -> Path:
        return self.data_dir() / KERNELS_DIR_NAME

    def config_dir(self) -> Path:
        root_dir = self._require_project().root_dir
        return root_dir / CONFIG_DIR_NAME

    def chart_path(self, filename: str) -> Path:
        return self.charts_dir() / filename

    def satellite_status_path(self) -> Path:
        return self.config_dir() / SATELLITE_SETTINGS_FILE

    def legacy_satellite_status_path(self) -> Path:
        return self.data_dir() / SATELLITE_SETTINGS_FILE

    def satellite_3d_model_path(self) -> Path:
        return self.config_dir() / SATELLITE_3D_MODEL_FILE

    def orbit_initialization_path(self) -> Path:
        return self.config_dir() / ORBIT_INITIALIZATION_FILE

    def maneuver_strategy_path(self) -> Path:
        return self.config_dir() / MANEUVER_STRATEGY_FILE

    def design_maneuver_strategy_path(self) -> Path:
        return self.config_dir() / DESIGN_MANEUVER_STRATEGY_FILE

    def design_import_maneuver_strategy_path(self) -> Path:
        return self.config_dir() / DESIGN_IMPORT_MANEUVER_STRATEGY_FILE

    def design_maneuver_results_path(self) -> Path:
        return self.data_dir() / DESIGN_MANEUVER_RESULTS_FILE

    def design_continuous_thrust_results_path(self) -> Path:
        return self.data_dir() / DESIGN_CONTINUOUS_THRUST_RESULTS_FILE

    def launch_window_path(self) -> Path:
        return self.config_dir() / LAUNCH_WINDOW_FILE

    def tracking_arc_path(self) -> Path:
        return self.config_dir() / TRACKING_ARC_FILE

    def tracking_arc_results_path(self) -> Path:
        return self.data_dir() / TRACKING_ARC_RESULTS_FILE

    def flight_program_path(self) -> Path:
        return self.config_dir() / FLIGHT_PROGRAM_FILE

    def flight_program_reference_results_path(self) -> Path:
        return self.data_dir() / FLIGHT_PROGRAM_REFERENCE_RESULTS_FILE

    def save_orbit_elements(self, elements: OrbitalElements) -> Path:
        payload = _orbital_elements_payload(elements)
        file_path = self.data_dir() / ORBIT_ELEMENTS_FILE
        _write_json(file_path, payload)
        self._touch_updated_time()
        return file_path

    def load_orbit_elements(self) -> OrbitalElements | None:
        file_path = self.data_dir() / ORBIT_ELEMENTS_FILE
        if not file_path.exists():
            return None
        payload = _read_json(file_path)
        return _orbital_elements_from_payload(payload)

    def save_orbit_initialization(self, settings: OrbitInitializationSettings) -> Path:
        payload: dict[str, Any] = {
            "mode": settings.mode,
            "epoch_utc": settings.epoch_utc,
            "elements": _orbital_elements_payload(settings.elements),
            "tle_line1": settings.tle_line1,
            "tle_line2": settings.tle_line2,
            "ephemeris_file_path": settings.ephemeris_file_path,
        }
        file_path = self.orbit_initialization_path()
        _write_json(file_path, payload)
        self._touch_updated_time()
        return file_path

    def load_orbit_initialization(self) -> OrbitInitializationSettings | None:
        file_path = self.orbit_initialization_path()
        if not file_path.exists():
            return None
        payload = _read_json(file_path)
        elements_payload = payload.get("elements")
        if not isinstance(elements_payload, dict):
            raise ValueError("Invalid orbit initialization JSON payload.")
        return OrbitInitializationSettings(
            mode=str(payload.get("mode", "classical")),
            epoch_utc=str(payload.get("epoch_utc", "")),
            elements=_orbital_elements_from_payload(elements_payload),
            tle_line1=str(payload.get("tle_line1", "")),
            tle_line2=str(payload.get("tle_line2", "")),
            ephemeris_file_path=str(payload.get("ephemeris_file_path", "")),
        )

    def save_maneuver_snapshot(self, snapshot: dict[str, float]) -> Path:
        payload = {
            "initial_altitude_km": float(snapshot["initial_altitude_km"]),
            "target_altitude_km": float(snapshot["target_altitude_km"]),
            "delta_v1_km_s": float(snapshot["delta_v1_km_s"]),
            "delta_v2_km_s": float(snapshot["delta_v2_km_s"]),
            "total_delta_v_km_s": float(snapshot["total_delta_v_km_s"]),
            "transfer_time_s": float(snapshot["transfer_time_s"]),
        }
        file_path = self.data_dir() / MANEUVER_SNAPSHOT_FILE
        _write_json(file_path, payload)
        self._touch_updated_time()
        return file_path

    def save_maneuver_strategy(self, strategy: dict[str, Any]) -> Path:
        payload = _normalize_maneuver_strategy_payload(strategy)
        file_path = self.maneuver_strategy_path()
        _write_json(file_path, payload)
        self._touch_updated_time()
        return file_path

    def load_maneuver_strategy(self) -> dict[str, Any] | None:
        file_path = self.maneuver_strategy_path()
        if not file_path.exists():
            return None
        payload = _read_json(file_path)
        return _normalize_maneuver_strategy_payload(payload)

    def save_design_maneuver_strategy(self, strategy: dict[str, Any]) -> Path:
        payload = normalize_design_maneuver_strategy_payload(strategy)
        file_path = self.design_maneuver_strategy_path()
        _write_json(file_path, payload)
        self._touch_updated_time()
        return file_path

    def load_design_maneuver_strategy(self) -> dict[str, Any] | None:
        file_path = self.design_maneuver_strategy_path()
        if not file_path.exists():
            return None
        payload = _read_json(file_path)
        return normalize_design_maneuver_strategy_payload(payload)

    def save_design_import_maneuver_strategy(self, strategy: dict[str, Any]) -> Path:
        payload = _normalize_maneuver_strategy_payload(strategy)
        file_path = self.design_import_maneuver_strategy_path()
        _write_json(file_path, payload)
        self._touch_updated_time()
        return file_path

    def load_design_import_maneuver_strategy(self) -> dict[str, Any] | None:
        file_path = self.design_import_maneuver_strategy_path()
        if not file_path.exists():
            return None
        payload = _read_json(file_path)
        return _normalize_maneuver_strategy_payload(payload)

    def save_continuous_thrust_import_maneuver_strategy(
        self,
        result: Any,
        design_config: dict[str, Any],
    ) -> Path:
        payload = continuous_thrust_result_to_maneuver_strategy_payload(result, design_config)
        return self.save_design_import_maneuver_strategy(payload)

    def save_design_maneuver_results(self, result: DesignManeuverResult) -> Path:
        file_path = self.design_maneuver_results_path()
        payload = design_maneuver_result_to_payload(result)
        payload["metadata"] = {
            "config_hash": _stable_hash(payload["config"]),
        }
        _write_json(file_path, payload)
        self._touch_updated_time()
        return file_path

    def load_design_maneuver_results(self, *, require_current_config: bool = False) -> DesignManeuverResult | None:
        file_path = self.design_maneuver_results_path()
        if not file_path.exists():
            return None
        payload = _read_json(file_path)
        if require_current_config:
            metadata = payload.get("metadata")
            current_config = self.load_design_maneuver_strategy()
            current_hash = _stable_hash(current_config) if current_config is not None else ""
            if not isinstance(metadata, dict) or metadata.get("config_hash") != current_hash:
                return None
        return design_maneuver_result_from_payload(payload)

    def save_design_continuous_thrust_results(
        self,
        result: ContinuousThrustOptimizationResult,
        *,
        pulse_result: DesignManeuverResult | None = None,
    ) -> Path:
        file_path = self.design_continuous_thrust_results_path()
        payload = continuous_thrust_result_to_payload(result)
        if pulse_result is not None:
            payload["metadata"] = {
                "pulse_result_hash": _stable_hash(design_maneuver_result_to_payload(pulse_result)),
            }
        _write_json(file_path, payload)
        self._touch_updated_time()
        return file_path

    def load_design_continuous_thrust_results(
        self,
        *,
        pulse_result: DesignManeuverResult | None = None,
    ) -> ContinuousThrustOptimizationResult | None:
        file_path = self.design_continuous_thrust_results_path()
        if not file_path.exists():
            return None
        payload = _read_json(file_path)
        if pulse_result is not None:
            metadata = payload.get("metadata")
            expected_hash = _stable_hash(design_maneuver_result_to_payload(pulse_result))
            if not isinstance(metadata, dict) or metadata.get("pulse_result_hash") != expected_hash:
                return None
        return continuous_thrust_result_from_payload(payload)

    def save_launch_window_config(self, config: dict[str, Any]) -> Path:
        payload = normalize_launch_window_config(config)
        file_path = self.launch_window_path()
        _write_json(file_path, payload)
        self._touch_updated_time()
        return file_path

    def load_launch_window_config(self) -> dict[str, Any] | None:
        file_path = self.launch_window_path()
        if not file_path.exists():
            return None
        payload = _read_json(file_path)
        return normalize_launch_window_config(payload)

    def save_tracking_arc_config(self, config: dict[str, Any]) -> Path:
        payload = normalize_launch_window_config(config)
        file_path = self.tracking_arc_path()
        _write_json(file_path, payload)
        self._touch_updated_time()
        return file_path

    def load_tracking_arc_config(self) -> dict[str, Any] | None:
        file_path = self.tracking_arc_path()
        if not file_path.exists():
            return None
        payload = _read_json(file_path)
        return normalize_launch_window_config(payload)

    def save_tracking_arc_results(self, payload: dict[str, Any]) -> Path:
        file_path = self.tracking_arc_results_path()
        _write_json(file_path, payload)
        self._touch_updated_time()
        return file_path

    def load_tracking_arc_results(self) -> dict[str, Any] | None:
        file_path = self.tracking_arc_results_path()
        if not file_path.exists():
            return None
        return _read_json(file_path)

    def save_flight_program_config(self, config: dict[str, Any]) -> Path:
        payload = normalize_flight_program_payload(config)
        file_path = self.flight_program_path()
        _write_json(file_path, payload)
        self._touch_updated_time()
        return file_path

    def load_flight_program_config(self) -> dict[str, Any] | None:
        file_path = self.flight_program_path()
        if not file_path.exists():
            return None
        payload = _read_json(file_path)
        return normalize_flight_program_payload(payload)

    def save_flight_program_reference_results(self, payload: dict[str, Any]) -> Path:
        file_path = self.flight_program_reference_results_path()
        _write_json(file_path, payload)
        self._touch_updated_time()
        return file_path

    def load_flight_program_reference_results(self) -> dict[str, Any] | None:
        file_path = self.flight_program_reference_results_path()
        if not file_path.exists():
            return None
        return _read_json(file_path)

    def load_maneuver_snapshot(self) -> dict[str, float] | None:
        file_path = self.data_dir() / MANEUVER_SNAPSHOT_FILE
        if not file_path.exists():
            return None
        payload = _read_json(file_path)
        return {
            "initial_altitude_km": float(payload["initial_altitude_km"]),
            "target_altitude_km": float(payload["target_altitude_km"]),
            "delta_v1_km_s": float(payload["delta_v1_km_s"]),
            "delta_v2_km_s": float(payload["delta_v2_km_s"]),
            "total_delta_v_km_s": float(payload["total_delta_v_km_s"]),
            "transfer_time_s": float(payload["transfer_time_s"]),
        }

    def save_satellite_3d_model_config(self, config: SatelliteStructureConfig) -> Path:
        payload = _satellite_structure_payload(config)
        file_path = self.satellite_3d_model_path()
        _write_json(file_path, payload)
        self._delete_legacy_satellite_status_files()
        self._touch_updated_time()
        return file_path

    def load_satellite_3d_model_config(self) -> SatelliteStructureConfig | None:
        file_path = self.satellite_3d_model_path()
        if file_path.exists():
            return _satellite_structure_from_payload(_read_json(file_path))
        legacy = self.load_satellite_status()
        if legacy is not None:
            return legacy.structure
        return None

    def save_satellite_status(self, settings: SatelliteStatusSettings) -> Path:
        payload: dict[str, Any] = {
            "launch_mass_kg": float(settings.launch_mass_kg),
            "fuel_load_kg": float(settings.fuel_load_kg),
            "helium_load_kg": float(settings.helium_load_kg),
            "orbit_engine_thrust_n": float(settings.orbit_engine_thrust_n),
            "orbit_engine_isp_s": float(settings.orbit_engine_isp_s),
            "settle_engine_thrust_n": float(settings.settle_engine_thrust_n),
            "settle_engine_isp_s": float(settings.settle_engine_isp_s),
            "structure": {
                "body_size_x_m": float(settings.structure.body_size_x_m),
                "body_size_y_m": float(settings.structure.body_size_y_m),
                "body_size_z_m": float(settings.structure.body_size_z_m),
                "model_path": settings.structure.model_path,
                "antenna_major_axis_m": float(settings.structure.antenna_major_axis_m),
                "antenna_minor_axis_m": float(settings.structure.antenna_minor_axis_m),
                "antenna_depth_m": float(settings.structure.antenna_depth_m),
                "east_antenna_count": int(settings.structure.east_antenna_count),
                "west_antenna_count": int(settings.structure.west_antenna_count),
                "north_wing_count": int(settings.structure.north_wing_count),
                "south_wing_count": int(settings.structure.south_wing_count),
                "solar_panels_per_wing": int(settings.structure.solar_panels_per_wing),
                "solar_panel_span_m": float(settings.structure.solar_panel_span_m),
                "solar_panel_width_m": float(settings.structure.solar_panel_width_m),
                "solar_panel_gap_m": float(settings.structure.solar_panel_gap_m),
            },
            "ttc_antennas": [
                {
                    "name": item.name,
                    "band": item.band,
                    "gain_dbi": float(item.gain_dbi),
                    "beamwidth_deg": float(item.beamwidth_deg),
                }
                for item in settings.ttc_antennas
            ],
            "relay_antennas": [
                {
                    "name": item.name,
                    "band": item.band,
                    "gain_dbi": float(item.gain_dbi),
                    "beamwidth_deg": float(item.beamwidth_deg),
                }
                for item in settings.relay_antennas
            ],
            "ground_assets": [
                {
                    "name": item.name,
                    "asset_type": item.asset_type,
                    "longitude_deg": float(item.longitude_deg),
                    "latitude_deg": float(item.latitude_deg),
                    "altitude_m": float(item.altitude_m),
                }
                for item in settings.ground_assets
            ],
            "relay_satellites": [
                {
                    "name": item.name,
                    "orbital_slot_orbit": item.orbital_slot_orbit,
                    "band": item.band,
                    "note": item.note,
                }
                for item in settings.relay_satellites
            ],
        }
        file_path = self.satellite_status_path()
        _write_json(file_path, payload)
        self._touch_updated_time()
        return file_path

    def load_satellite_status(self) -> SatelliteStatusSettings | None:
        file_path = self.satellite_status_path()
        if not file_path.exists():
            legacy_path = self.legacy_satellite_status_path()
            if legacy_path.exists():
                file_path = legacy_path
        if not file_path.exists():
            return None

        payload = _read_json(file_path)
        defaults = SatelliteStatusSettings()
        ttc_payload = payload.get("ttc_antennas")
        relay_ant_payload = payload.get("relay_antennas")
        ground_payload = payload.get("ground_assets")
        relay_sat_payload = payload.get("relay_satellites")
        structure_payload = payload.get("structure")
        structure_defaults = defaults.structure
        if structure_payload is None:
            structure_data = structure_defaults
        else:
            structure_map = _ensure_dict(structure_payload)
            structure_data = SatelliteStructureConfig(
                body_size_x_m=float(structure_map.get("body_size_x_m", structure_defaults.body_size_x_m)),
                body_size_y_m=float(structure_map.get("body_size_y_m", structure_defaults.body_size_y_m)),
                body_size_z_m=float(structure_map.get("body_size_z_m", structure_defaults.body_size_z_m)),
                model_path=str(
                    structure_map.get(
                        "model_path",
                        structure_map.get("dae_model_path", structure_defaults.model_path),
                    )
                ),
                antenna_major_axis_m=float(
                    structure_map.get("antenna_major_axis_m", structure_defaults.antenna_major_axis_m)
                ),
                antenna_minor_axis_m=float(
                    structure_map.get("antenna_minor_axis_m", structure_defaults.antenna_minor_axis_m)
                ),
                antenna_depth_m=float(
                    structure_map.get("antenna_depth_m", structure_defaults.antenna_depth_m)
                ),
                east_antenna_count=int(
                    structure_map.get("east_antenna_count", structure_defaults.east_antenna_count)
                ),
                west_antenna_count=int(
                    structure_map.get("west_antenna_count", structure_defaults.west_antenna_count)
                ),
                north_wing_count=int(
                    structure_map.get("north_wing_count", structure_defaults.north_wing_count)
                ),
                south_wing_count=int(
                    structure_map.get("south_wing_count", structure_defaults.south_wing_count)
                ),
                solar_panels_per_wing=int(
                    structure_map.get(
                        "solar_panels_per_wing",
                        structure_defaults.solar_panels_per_wing,
                    )
                ),
                solar_panel_span_m=float(
                    structure_map.get("solar_panel_span_m", structure_defaults.solar_panel_span_m)
                ),
                solar_panel_width_m=float(
                    structure_map.get("solar_panel_width_m", structure_defaults.solar_panel_width_m)
                ),
                solar_panel_gap_m=float(
                    structure_map.get("solar_panel_gap_m", structure_defaults.solar_panel_gap_m)
                ),
            )

        ttc_antennas = (
            list(defaults.ttc_antennas)
            if ttc_payload is None
            else [
                AntennaConfig(
                    name=str(item.get("name", "")),
                    band=str(item.get("band", "")),
                    gain_dbi=float(item.get("gain_dbi", 0.0)),
                    beamwidth_deg=float(item.get("beamwidth_deg", 0.0)),
                )
                for item in _ensure_list(ttc_payload)
            ]
        )
        relay_antennas = (
            list(defaults.relay_antennas)
            if relay_ant_payload is None
            else [
                AntennaConfig(
                    name=str(item.get("name", "")),
                    band=str(item.get("band", "")),
                    gain_dbi=float(item.get("gain_dbi", 0.0)),
                    beamwidth_deg=float(item.get("beamwidth_deg", 0.0)),
                )
                for item in _ensure_list(relay_ant_payload)
            ]
        )
        ground_assets = (
            list(defaults.ground_assets)
            if ground_payload is None
            else [
                GroundAssetConfig(
                    name=str(item.get("name", "")),
                    asset_type=str(item.get("asset_type", "")),
                    longitude_deg=float(item.get("longitude_deg", 0.0)),
                    latitude_deg=float(item.get("latitude_deg", 0.0)),
                    altitude_m=float(item.get("altitude_m", 0.0)),
                )
                for item in _ensure_list(ground_payload)
            ]
        )
        relay_satellites = (
            list(defaults.relay_satellites)
            if relay_sat_payload is None
            else [
                RelaySatelliteConfig(
                    name=str(item.get("name", "")),
                    orbital_slot_orbit=str(item.get("orbital_slot_orbit", "")),
                    band=str(item.get("band", "")),
                    note=str(item.get("note", "")),
                )
                for item in _ensure_list(relay_sat_payload)
            ]
        )

        return SatelliteStatusSettings(
            launch_mass_kg=float(payload.get("launch_mass_kg", defaults.launch_mass_kg)),
            fuel_load_kg=float(payload.get("fuel_load_kg", defaults.fuel_load_kg)),
            helium_load_kg=float(payload.get("helium_load_kg", defaults.helium_load_kg)),
            orbit_engine_thrust_n=float(
                payload.get("orbit_engine_thrust_n", defaults.orbit_engine_thrust_n)
            ),
            orbit_engine_isp_s=float(payload.get("orbit_engine_isp_s", defaults.orbit_engine_isp_s)),
            settle_engine_thrust_n=float(
                payload.get("settle_engine_thrust_n", defaults.settle_engine_thrust_n)
            ),
            settle_engine_isp_s=float(
                payload.get("settle_engine_isp_s", defaults.settle_engine_isp_s)
            ),
            structure=structure_data,
            ttc_antennas=ttc_antennas,
            relay_antennas=relay_antennas,
            ground_assets=ground_assets,
            relay_satellites=relay_satellites,
        )

    def _delete_legacy_satellite_status_files(self) -> None:
        for path in (self.satellite_status_path(), self.legacy_satellite_status_path()):
            try:
                if path.exists():
                    path.unlink()
            except OSError:
                pass

    def _require_project(self) -> ProjectInfo:
        if self._project is None:
            raise RuntimeError("No active project.")
        return self._project

    def _read_project(self, root_dir: Path) -> ProjectInfo:
        metadata_path = root_dir / PROJECT_META_FILE
        if not metadata_path.exists():
            raise FileNotFoundError(f"Project metadata not found: {metadata_path}")
        payload = _read_json(metadata_path)
        return ProjectInfo(
            name=str(payload["name"]),
            root_dir=root_dir,
            created_utc=str(payload["created_utc"]),
            updated_utc=str(payload["updated_utc"]),
            version=int(payload.get("version", 1)),
        )

    def _touch_updated_time(self) -> None:
        project = self._require_project()
        metadata_path = project.root_dir / PROJECT_META_FILE
        payload = _read_json(metadata_path)
        payload["updated_utc"] = _utc_now_iso()
        _write_json(metadata_path, payload)
        self._project = self._read_project(project.root_dir)


def _utc_now_iso() -> str:
    return utc_now_iso_z()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def _stable_hash(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Invalid JSON object in {path}")
    return data


def _ensure_list(value: Any) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError("Expected list value in project JSON payload.")
    return value


def _ensure_dict(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Expected object value in project JSON payload.")
    return value


def _orbital_elements_payload(elements: OrbitalElements) -> dict[str, Any]:
    return {
        "semi_major_axis_km": elements.semi_major_axis_km,
        "eccentricity": elements.eccentricity,
        "inclination_deg": elements.inclination_deg,
        "raan_deg": elements.raan_deg,
        "argument_of_periapsis_deg": elements.argument_of_periapsis_deg,
        "true_anomaly_deg": elements.true_anomaly_deg,
        "mu_km3_s2": elements.mu_km3_s2,
        "central_body_radius_km": elements.central_body_radius_km,
        "central_body_name": elements.central_body_name,
    }


def _orbital_elements_from_payload(payload: dict[str, Any]) -> OrbitalElements:
    return OrbitalElements(
        semi_major_axis_km=float(payload["semi_major_axis_km"]),
        eccentricity=float(payload["eccentricity"]),
        inclination_deg=float(payload["inclination_deg"]),
        raan_deg=float(payload["raan_deg"]),
        argument_of_periapsis_deg=float(payload["argument_of_periapsis_deg"]),
        true_anomaly_deg=float(payload["true_anomaly_deg"]),
        mu_km3_s2=float(payload["mu_km3_s2"]),
        central_body_radius_km=float(payload["central_body_radius_km"]),
        central_body_name=str(payload["central_body_name"]),
    )


def _satellite_structure_payload(config: SatelliteStructureConfig) -> dict[str, Any]:
    return {
        "body_size_x_m": float(config.body_size_x_m),
        "body_size_y_m": float(config.body_size_y_m),
        "body_size_z_m": float(config.body_size_z_m),
        "model_path": config.model_path,
        "antenna_major_axis_m": float(config.antenna_major_axis_m),
        "antenna_minor_axis_m": float(config.antenna_minor_axis_m),
        "antenna_depth_m": float(config.antenna_depth_m),
        "east_antenna_count": int(config.east_antenna_count),
        "west_antenna_count": int(config.west_antenna_count),
        "north_wing_count": int(config.north_wing_count),
        "south_wing_count": int(config.south_wing_count),
        "solar_panels_per_wing": int(config.solar_panels_per_wing),
        "solar_panel_span_m": float(config.solar_panel_span_m),
        "solar_panel_width_m": float(config.solar_panel_width_m),
        "solar_panel_gap_m": float(config.solar_panel_gap_m),
    }


def _satellite_structure_from_payload(payload: dict[str, Any]) -> SatelliteStructureConfig:
    defaults = SatelliteStructureConfig()
    return SatelliteStructureConfig(
        body_size_x_m=float(payload.get("body_size_x_m", defaults.body_size_x_m)),
        body_size_y_m=float(payload.get("body_size_y_m", defaults.body_size_y_m)),
        body_size_z_m=float(payload.get("body_size_z_m", defaults.body_size_z_m)),
        model_path=str(payload.get("model_path", payload.get("dae_model_path", defaults.model_path))),
        antenna_major_axis_m=float(payload.get("antenna_major_axis_m", defaults.antenna_major_axis_m)),
        antenna_minor_axis_m=float(payload.get("antenna_minor_axis_m", defaults.antenna_minor_axis_m)),
        antenna_depth_m=float(payload.get("antenna_depth_m", defaults.antenna_depth_m)),
        east_antenna_count=int(payload.get("east_antenna_count", defaults.east_antenna_count)),
        west_antenna_count=int(payload.get("west_antenna_count", defaults.west_antenna_count)),
        north_wing_count=int(payload.get("north_wing_count", defaults.north_wing_count)),
        south_wing_count=int(payload.get("south_wing_count", defaults.south_wing_count)),
        solar_panels_per_wing=int(payload.get("solar_panels_per_wing", defaults.solar_panels_per_wing)),
        solar_panel_span_m=float(payload.get("solar_panel_span_m", defaults.solar_panel_span_m)),
        solar_panel_width_m=float(payload.get("solar_panel_width_m", defaults.solar_panel_width_m)),
        solar_panel_gap_m=float(payload.get("solar_panel_gap_m", defaults.solar_panel_gap_m)),
    )


def default_maneuver_strategy_payload(maneuver_count: int = 1) -> dict[str, Any]:
    count = max(0, int(maneuver_count))
    return {
        "launch_mass_kg": 5200.0,
        "t0_epoch": utc_now_iso_z(),
        "t0_orbit": {
            "semi_major_axis_m": 29_478_137.0,
            "eccentricity": 0.7768460924,
            "inclination_deg": 16.5,
            "argument_of_perigee_deg": 200.0,
            "raan_deg": 8.53237,
            "mean_anomaly_deg": 1.85437,
        },
        "maneuver_count": count,
        "maneuvers": [_default_maneuver_step_payload(index + 1) for index in range(count)],
    }


def _default_maneuver_step_payload(maneuver_index: int) -> dict[str, Any]:
    return {
        "maneuver_index": int(maneuver_index),
        "Tn_start_min": 0.0,
        "burn_duration_min": 0.0,
        "control_fuel_%": 0.0,
        "settle_duration_s": 240.0,
        "direction_mode": "delta_tangent",
        "yaw_angle_deg": 0.0,
        "delta_deg": 0.0,
        "dv_direction": 1,
        "orbit_control_thrust_n": 490.0,
        "orbit_control_isp_s": 314.1,
        "settle_thrust_n": 20.0,
        "settle_isp_s": 290.0,
    }


def _normalize_maneuver_step_payload(step: dict[str, Any], fallback_index: int) -> dict[str, Any]:
    default = _default_maneuver_step_payload(fallback_index)
    maneuver_index = int(step.get("maneuver_index", default["maneuver_index"]))
    if maneuver_index <= 0:
        maneuver_index = int(default["maneuver_index"])
    direction_mode = str(step.get("direction_mode", default["direction_mode"]))
    if direction_mode not in {"delta_tangent", "local_horizontal_yaw"}:
        raise ValueError(
            "Invalid maneuver strategy JSON payload: 'direction_mode' must be "
            "'delta_tangent' or 'local_horizontal_yaw'."
        )
    return {
        "maneuver_index": maneuver_index,
        "Tn_start_min": _coerce_minutes_value(
            step=step,
            default_minutes=default["Tn_start_min"],
            key_minutes="Tn_start_min",
            key_seconds="Tn_start_s",
            legacy_minute_keys=("Tn_start", "t_start_min", "t_start"),
            legacy_second_keys=("t_start_s",),
        ),
        "burn_duration_min": _coerce_minutes_value(
            step=step,
            default_minutes=default["burn_duration_min"],
            key_minutes="burn_duration_min",
            key_seconds="burn_duration_s",
            legacy_minute_keys=("burn_duration",),
            legacy_second_keys=(),
        ),
        "control_fuel_%": float(
            step.get("control_fuel_%", step.get("control_fuel_percent", default["control_fuel_%"]))
        ),
        "settle_duration_s": float(step.get("settle_duration_s", default["settle_duration_s"])),
        "direction_mode": direction_mode,
        "yaw_angle_deg": float(step.get("yaw_angle_deg", default["yaw_angle_deg"])),
        "delta_deg": float(step.get("delta_deg", step.get("delta", default["delta_deg"]))),
        "dv_direction": _coerce_dv_direction(step.get("dv_direction", default["dv_direction"])),
        "orbit_control_thrust_n": float(step.get("orbit_control_thrust_n", default["orbit_control_thrust_n"])),
        "orbit_control_isp_s": float(step.get("orbit_control_isp_s", default["orbit_control_isp_s"])),
        "settle_thrust_n": float(step.get("settle_thrust_n", default["settle_thrust_n"])),
        "settle_isp_s": float(step.get("settle_isp_s", default["settle_isp_s"])),
    }


def _coerce_dv_direction(value: object) -> int:
    dv_direction = int(float(value))
    if dv_direction not in {-1, 1}:
        raise ValueError("Invalid maneuver strategy JSON payload: 'dv_direction' must be 1 or -1.")
    return dv_direction


def _coerce_minutes_value(
    *,
    step: dict[str, Any],
    default_minutes: float,
    key_minutes: str,
    key_seconds: str,
    legacy_minute_keys: tuple[str, ...],
    legacy_second_keys: tuple[str, ...],
) -> float:
    if key_minutes in step:
        return float(step[key_minutes])
    for key in legacy_minute_keys:
        if key in step:
            return float(step[key])
    if key_seconds in step:
        return float(step[key_seconds]) / 60.0
    for key in legacy_second_keys:
        if key in step:
            return float(step[key]) / 60.0
    return float(default_minutes)


def _normalize_maneuver_strategy_payload(payload: dict[str, Any]) -> dict[str, Any]:
    default_payload = default_maneuver_strategy_payload(0)
    maneuvers_raw = payload.get("maneuvers", [])
    if maneuvers_raw is None:
        maneuvers_raw = []
    if not isinstance(maneuvers_raw, list):
        raise ValueError("Invalid maneuver strategy JSON payload: 'maneuvers' must be a list.")

    parsed_steps = [
        _normalize_maneuver_step_payload(_ensure_dict(item), index + 1)
        for index, item in enumerate(maneuvers_raw)
    ]

    count_raw = payload.get("maneuver_count", len(parsed_steps) if parsed_steps else 1)
    count = int(count_raw)
    if count < 0:
        raise ValueError("Invalid maneuver strategy JSON payload: 'maneuver_count' must be >= 0.")

    if len(parsed_steps) < count:
        parsed_steps.extend(_default_maneuver_step_payload(index + 1) for index in range(len(parsed_steps), count))
    elif len(parsed_steps) > count:
        parsed_steps = parsed_steps[:count]

    t0_orbit_raw = payload.get("t0_orbit", default_payload["t0_orbit"])
    if not isinstance(t0_orbit_raw, dict):
        t0_orbit_raw = default_payload["t0_orbit"]
    default_t0_orbit = default_payload["t0_orbit"]

    normalized = dict(payload)
    normalized["launch_mass_kg"] = float(payload.get("launch_mass_kg", default_payload["launch_mass_kg"]))
    epoch_value = payload.get("t0_epoch")
    if epoch_value in (None, ""):
        epoch_value = payload.get("to_epoch")
    if epoch_value in (None, ""):
        epoch_value = default_payload["t0_epoch"]
    normalized["t0_epoch"] = format_utc(parse_utc(str(epoch_value)))
    normalized["t0_orbit"] = {
        key: float(t0_orbit_raw.get(key, default_t0_orbit[key]))
        for key in default_t0_orbit
    }
    normalized["maneuver_count"] = count
    normalized["maneuvers"] = parsed_steps
    normalized.pop("to_epoch", None)
    return normalized
