"""Quick cProfile harness for compute_launch_windows.

生成一个合成的轨道历史 CSV，然后用一个有代表性步长的扫描跑一遍
``compute_launch_windows``，把热点函数打印出来。
"""

from __future__ import annotations

import cProfile
import csv
import io
import math
import pstats
import tempfile
from pathlib import Path

from smart.services.launch_window import (
    compute_launch_windows,
    config_from_payload,
    default_launch_window_config,
)


def _synthetic_orbit_history_csv(target: Path, *, sample_count: int = 1200) -> Path:
    """生成一段合成 LEO 轨道历史，用于性能测试。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "elapsed_time_s",
        "elapsed_time_min",
        "phase",
        "is_event_point",
        "subsatellite_longitude_deg",
        "subsatellite_latitude_deg",
        "subsatellite_altitude_m",
        "inclination_deg",
        "position_x_m",
        "position_y_m",
        "position_z_m",
        "velocity_x_m_s",
        "velocity_y_m_s",
        "velocity_z_m_s",
    ]
    period_min = 90.0
    radius = 6_771_000.0
    speed = 7_500.0
    inclination_deg = 30.0
    inc = math.radians(inclination_deg)
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for index in range(sample_count):
            elapsed_min = index * 0.5  # 30 s step → 600 min total
            phase_angle = 2.0 * math.pi * (elapsed_min / period_min)
            x = radius * math.cos(phase_angle)
            y = radius * math.sin(phase_angle) * math.cos(inc)
            z = radius * math.sin(phase_angle) * math.sin(inc)
            vx = -speed * math.sin(phase_angle)
            vy = speed * math.cos(phase_angle) * math.cos(inc)
            vz = speed * math.cos(phase_angle) * math.sin(inc)
            longitude_deg = math.degrees(math.atan2(y, x))
            latitude_deg = math.degrees(math.asin(z / radius))
            writer.writerow(
                {
                    "elapsed_time_s": elapsed_min * 60.0,
                    "elapsed_time_min": elapsed_min,
                    "phase": "coast",
                    "is_event_point": 0,
                    "subsatellite_longitude_deg": longitude_deg,
                    "subsatellite_latitude_deg": latitude_deg,
                    "subsatellite_altitude_m": radius - 6_378_137.0,
                    "inclination_deg": inclination_deg,
                    "position_x_m": x,
                    "position_y_m": y,
                    "position_z_m": z,
                    "velocity_x_m_s": vx,
                    "velocity_y_m_s": vy,
                    "velocity_z_m_s": vz,
                }
            )
    return target


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        csv_path = _synthetic_orbit_history_csv(Path(tmp) / "history.csv")
        config_payload = default_launch_window_config()
        # 强制扫描时长足够长，触发明显工作量。
        config_payload["start_utc"] = "2026-05-01T00:00:00Z"
        config_payload["end_utc"] = "2026-05-08T00:00:00Z"
        config_payload["sample_step_min"] = 15.0
        config = config_from_payload(config_payload)
        maneuver_strategy = {
            "reference_t0_utc": "2026-05-01T00:35:00Z",
            "t0_offset_s": 0.0,
            "maneuvers": [],
        }

        profiler = cProfile.Profile()
        profiler.enable()
        windows, samples = compute_launch_windows(
            orbit_history_csv=csv_path,
            maneuver_strategy=maneuver_strategy,
            config=config,
        )
        profiler.disable()

        print(f"samples={len(samples)} windows={len(windows)}")
        buffer = io.StringIO()
        stats = pstats.Stats(profiler, stream=buffer).strip_dirs().sort_stats("cumulative")
        stats.print_stats(20)
        print(buffer.getvalue())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
