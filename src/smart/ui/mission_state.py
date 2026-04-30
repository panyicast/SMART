from __future__ import annotations

from dataclasses import replace

from PySide6 import QtCore

from smart.domain.models import OrbitInitializationSettings, OrbitTrajectory, OrbitalElements
from smart.services.orbital_mechanics import sample_orbit


class MissionState(QtCore.QObject):
    initialization_changed = QtCore.Signal(object)
    elements_changed = QtCore.Signal(object)
    trajectory_changed = QtCore.Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self._initialization = OrbitInitializationSettings(elements=OrbitalElements().validate()).validate()
        self._elements = self._initialization.elements
        self._trajectory = sample_orbit(self._elements, sample_count=480)

    @property
    def initialization(self) -> OrbitInitializationSettings:
        return self._initialization

    @property
    def elements(self) -> OrbitalElements:
        return self._elements

    @property
    def trajectory(self) -> OrbitTrajectory:
        return self._trajectory

    def update_elements(self, elements: OrbitalElements) -> None:
        updated = replace(self._initialization, elements=elements.validate())
        self.update_initialization(updated)

    def update_initialization(self, settings: OrbitInitializationSettings) -> None:
        self._initialization = settings.validate()
        self._elements = self._initialization.elements.validate()
        self._trajectory = sample_orbit(self._elements, sample_count=480)
        self.initialization_changed.emit(self._initialization)
        self.elements_changed.emit(self._elements)
        self.trajectory_changed.emit(self._trajectory)
