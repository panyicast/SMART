from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ModuleDescriptor:
    key: str
    status: str


def build_module_catalog() -> list[ModuleDescriptor]:
    return [
        ModuleDescriptor(
            key="orbit_design",
            status="Operational",
        ),
        ModuleDescriptor(
            key="maneuver_strategy",
            status="Operational",
        ),
        ModuleDescriptor(
            key="launch_window",
            status="Planned",
        ),
        ModuleDescriptor(
            key="tracking_arc",
            status="Planned",
        ),
        ModuleDescriptor(
            key="flight_program",
            status="Planned",
        ),
        ModuleDescriptor(
            key="science_visualization",
            status="Operational",
        ),
    ]
