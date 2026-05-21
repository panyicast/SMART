from __future__ import annotations

import math

import numpy as np


def local_horizontal_yaw_direction(position: np.ndarray, yaw_angle_deg: float) -> np.ndarray:
    """Return fixed-yaw thrust direction in the local horizontal east/south basis."""

    r = np.asarray(position, dtype=float)
    x, y, z = float(r[0]), float(r[1]), float(r[2])
    xy_norm = math.hypot(x, y)
    if xy_norm <= 1.0e-12:
        east = np.asarray([0.0, 1.0, 0.0], dtype=float)
    else:
        east = np.asarray([-y / xy_norm, x / xy_norm, 0.0], dtype=float)

    r_norm = math.sqrt(x * x + y * y + z * z)
    inv_r2 = 1.0 / max(1.0e-24, r_norm * r_norm)
    north = np.asarray(
        [
            -z * x * inv_r2,
            -z * y * inv_r2,
            1.0 - z * z * inv_r2,
        ],
        dtype=float,
    )
    north_norm = float(np.linalg.norm(north))
    if north_norm <= 1.0e-12:
        north = np.asarray([-east[1], east[0], 0.0], dtype=float)
        north_norm = float(np.linalg.norm(north))
    north = north / north_norm

    yaw_rad = math.radians(float(yaw_angle_deg))
    return math.cos(yaw_rad) * east - math.sin(yaw_rad) * north
