from __future__ import annotations

import argparse
from datetime import timedelta, timezone
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from smart.services.earth_orientation import parse_utc
from smart.services.launch_window import compute_shadow_intervals_for_launch


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compute orbit eclipse intervals from a rocket launch time and SMART full_orbit_history.csv.",
    )
    parser.add_argument(
        "--history",
        default=str(REPO_ROOT / "projects" / "F4" / "data" / "full_orbit_history.csv"),
        help="Path to full_orbit_history.csv.",
    )
    parser.add_argument(
        "--launch",
        required=True,
        help="Rocket launch time. ISO UTC or offset time, e.g. 2026-05-15T15:30:00+08:00.",
    )
    parser.add_argument(
        "--rocket-flight-s",
        type=float,
        default=2134.4121,
        help="Rocket flight time from launch to satellite orbit T0, in seconds.",
    )
    args = parser.parse_args()

    launch_utc = parse_utc(args.launch)
    t0_utc = launch_utc + timedelta(seconds=args.rocket_flight_s)
    beijing = timezone(timedelta(hours=8))
    print(f"Launch (BJT): {launch_utc.astimezone(beijing).strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Orbit T0 (BJT): {t0_utc.astimezone(beijing).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}")
    print("start_min,end_min,duration_min,exact_start_min,exact_end_min")
    for interval in compute_shadow_intervals_for_launch(
        orbit_history_csv=args.history,
        launch_utc=launch_utc,
        rocket_flight_time_s=args.rocket_flight_s,
    ):
        print(
            f"{interval.start_min},"
            f"{interval.end_min},"
            f"{interval.duration_min},"
            f"{interval.exact_start_min:.3f},"
            f"{interval.exact_end_min:.3f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
