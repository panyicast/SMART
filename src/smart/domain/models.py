from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np
from numpy.typing import NDArray

EARTH_MU_KM3_S2 = 398600.4418
EARTH_RADIUS_KM = 6378.1363


def _default_epoch_utc() -> str:
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(slots=True)
class OrbitalElements:
    semi_major_axis_km: float = 7000.0
    eccentricity: float = 0.05
    inclination_deg: float = 28.5
    raan_deg: float = 40.0
    argument_of_periapsis_deg: float = 10.0
    true_anomaly_deg: float = 0.0
    mu_km3_s2: float = EARTH_MU_KM3_S2
    central_body_radius_km: float = EARTH_RADIUS_KM
    central_body_name: str = "Earth"

    def validate(self) -> "OrbitalElements":
        if self.semi_major_axis_km <= self.central_body_radius_km:
            raise ValueError("Semi-major axis must be larger than the central-body radius.")
        if not 0.0 <= self.eccentricity < 1.0:
            raise ValueError("Eccentricity must satisfy 0 <= e < 1 for an elliptical orbit.")
        if self.semi_major_axis_km * (1.0 - self.eccentricity) <= self.central_body_radius_km:
            raise ValueError("Periapsis must remain above the central-body surface.")
        return self

    @property
    def period_seconds(self) -> float:
        return float(2.0 * np.pi * np.sqrt(self.semi_major_axis_km**3 / self.mu_km3_s2))

    @property
    def perigee_radius_km(self) -> float:
        return self.semi_major_axis_km * (1.0 - self.eccentricity)

    @property
    def apogee_radius_km(self) -> float:
        return self.semi_major_axis_km * (1.0 + self.eccentricity)


@dataclass(slots=True)
class OrbitInitializationSettings:
    mode: str = "classical"
    epoch_utc: str = field(default_factory=_default_epoch_utc)
    elements: OrbitalElements = field(default_factory=OrbitalElements)
    tle_line1: str = ""
    tle_line2: str = ""
    ephemeris_file_path: str = ""

    def validate(self) -> "OrbitInitializationSettings":
        if self.mode not in {"classical", "tle", "stk_ephemeris"}:
            raise ValueError("Orbit initialization mode is not supported.")
        self.elements.validate()
        if not self.epoch_utc.strip():
            raise ValueError("Orbit epoch is required.")
        return self


@dataclass(slots=True)
class OrbitTrajectory:
    positions_km: NDArray[np.float64]
    velocities_km_s: NDArray[np.float64]
    radii_km: NDArray[np.float64]
    speeds_km_s: NDArray[np.float64]
    elapsed_seconds: NDArray[np.float64]
    current_position_km: NDArray[np.float64]
    current_velocity_km_s: NDArray[np.float64]


@dataclass(slots=True)
class HohmannTransferResult:
    initial_radius_km: float
    target_radius_km: float
    delta_v1_km_s: float
    delta_v2_km_s: float
    total_delta_v_km_s: float
    transfer_time_s: float
    transfer_semi_major_axis_km: float


@dataclass(slots=True)
class CoplanarHohmannEstimate:
    transfer: HohmannTransferResult
    transfer_kind: str
    initial_circular_speed_km_s: float
    target_circular_speed_km_s: float
    transfer_departure_speed_km_s: float
    transfer_arrival_speed_km_s: float
    departure_burn_direction: str
    arrival_burn_direction: str
    required_target_lead_angle_deg: float
    current_target_lead_angle_deg: float
    wait_time_s: float | None
    relative_phase_rate_deg_s: float


@dataclass(slots=True)
class CircularOrbitMetrics:
    altitude_km: float
    radius_km: float
    period_s: float
    circular_speed_km_s: float
    escape_speed_km_s: float
    mean_motion_rad_s: float


@dataclass(slots=True)
class ApsisOrbitMetrics:
    perigee_altitude_km: float
    apogee_altitude_km: float
    perigee_radius_km: float
    apogee_radius_km: float
    semi_major_axis_km: float
    eccentricity: float
    period_s: float


@dataclass(slots=True)
class PlaneChangeResult:
    initial_speed_km_s: float
    target_speed_km_s: float
    angle_deg: float
    pure_plane_change_delta_v_km_s: float
    combined_delta_v_km_s: float


@dataclass(slots=True)
class OrbitalAnomalySet:
    eccentricity: float
    true_anomaly_deg: float
    eccentric_anomaly_deg: float
    mean_anomaly_deg: float


@dataclass(slots=True)
class LambertTransferResult:
    departure_velocity_km_s: NDArray[np.float64]
    arrival_velocity_km_s: NDArray[np.float64]
    time_of_flight_s: float
    transfer_angle_deg: float
    path: str


@dataclass(slots=True)
class TwoBodyPropagationResult:
    position_km: NDArray[np.float64]
    velocity_km_s: NDArray[np.float64]
    true_anomaly_deg: float
    elapsed_s: float


@dataclass(slots=True)
class AntennaConfig:
    name: str
    band: str
    gain_dbi: float
    beamwidth_deg: float


@dataclass(slots=True)
class GroundAssetConfig:
    name: str
    asset_type: str
    longitude_deg: float
    latitude_deg: float
    altitude_m: float


@dataclass(slots=True)
class RelaySatelliteConfig:
    name: str
    orbital_slot_orbit: str
    band: str
    note: str


@dataclass(slots=True)
class SatelliteStructureConfig:
    body_size_x_m: float = 2.40
    body_size_y_m: float = 2.05
    body_size_z_m: float = 1.65
    model_path: str = ""
    antenna_major_axis_m: float = 1.20
    antenna_minor_axis_m: float = 0.78
    antenna_depth_m: float = 0.18
    east_antenna_count: int = 2
    west_antenna_count: int = 1
    north_wing_count: int = 1
    south_wing_count: int = 1
    solar_panels_per_wing: int = 5
    solar_panel_span_m: float = 1.35
    solar_panel_width_m: float = 1.05
    solar_panel_gap_m: float = 0.06


@dataclass(slots=True)
class SatelliteStatusSettings:
    launch_mass_kg: float = 5200.0
    fuel_load_kg: float = 1850.0
    helium_load_kg: float = 62.0
    orbit_engine_thrust_n: float = 490.0
    orbit_engine_isp_s: float = 314.1
    settle_engine_thrust_n: float = 10.0
    settle_engine_isp_s: float = 290.0
    structure: SatelliteStructureConfig = field(default_factory=SatelliteStructureConfig)
    ttc_antennas: list[AntennaConfig] = field(
        default_factory=lambda: [
            AntennaConfig(name="TTC-A", band="S", gain_dbi=11.5, beamwidth_deg=42.0),
            AntennaConfig(name="TTC-B", band="S", gain_dbi=11.5, beamwidth_deg=42.0),
        ]
    )
    relay_antennas: list[AntennaConfig] = field(
        default_factory=lambda: [
            AntennaConfig(name="Relay-1", band="Ka", gain_dbi=26.0, beamwidth_deg=2.8),
        ]
    )
    ground_assets: list[GroundAssetConfig] = field(
        default_factory=lambda: [
            GroundAssetConfig(
                name="Sanya Ground Station",
                asset_type="Ground",
                longitude_deg=109.6,
                latitude_deg=18.3,
                altitude_m=15.0,
            ),
            GroundAssetConfig(
                name="Yuanwang Tracking Ship",
                asset_type="Ship",
                longitude_deg=154.0,
                latitude_deg=-18.5,
                altitude_m=12.0,
            ),
        ]
    )
    relay_satellites: list[RelaySatelliteConfig] = field(
        default_factory=lambda: [
            RelaySatelliteConfig(
                name="Tianlian-1",
                orbital_slot_orbit="GEO 77E",
                band="S/Ka",
                note="Primary relay",
            ),
        ]
    )
