from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import tempfile
from typing import Iterable
from urllib import parse, request

import numpy as np
from numpy.typing import NDArray

try:
    import spiceypy as spice
except ImportError:  # pragma: no cover - optional at import time
    spice = None


SUPPORTED_KERNEL_PATTERNS: tuple[str, ...] = ("*.tls", "*.tpc", "*.tf", "*.bsp", "*.bpc", "*.bc")
SUPPORTED_KERNEL_SUFFIXES: tuple[str, ...] = tuple(pattern[1:] for pattern in SUPPORTED_KERNEL_PATTERNS)
_DEFAULT_LOCAL_KERNEL_DIR = Path.cwd() / "data" / "kernels"


class SpiceUnavailableError(RuntimeError):
    """Raised when a SPICE operation is requested without SpiceyPy."""


@dataclass(slots=True)
class BodyState:
    position_km: NDArray[np.float64]
    velocity_km_s: NDArray[np.float64]
    light_time_s: float


@dataclass(frozen=True, slots=True)
class KernelDownloadPreset:
    key: str
    url: str
    selected_by_default: bool = False

    @property
    def filename(self) -> str:
        return Path(parse.urlparse(self.url).path).name

    @property
    def kernel_type(self) -> str:
        return Path(self.filename).suffix.lstrip(".").upper()


COMMON_KERNEL_PRESETS: tuple[KernelDownloadPreset, ...] = (
    KernelDownloadPreset(
        key="naif0012_lsk",
        url="https://naif.jpl.nasa.gov/pub/naif/generic_kernels/lsk/naif0012.tls",
        selected_by_default=True,
    ),
    KernelDownloadPreset(
        key="pck00011_pck",
        url="https://naif.jpl.nasa.gov/pub/naif/generic_kernels/pck/pck00011.tpc",
        selected_by_default=True,
    ),
    KernelDownloadPreset(
        key="earth_assoc_itrf93_fk",
        url="https://naif.jpl.nasa.gov/pub/naif/generic_kernels/fk/planets/earth_assoc_itrf93.tf",
        selected_by_default=True,
    ),
    KernelDownloadPreset(
        key="earth_latest_high_prec_bpc",
        url="https://naif.jpl.nasa.gov/pub/naif/generic_kernels/pck/earth_latest_high_prec.bpc",
        selected_by_default=True,
    ),
    KernelDownloadPreset(
        key="de440s_spk",
        url="https://naif.jpl.nasa.gov/pub/naif/generic_kernels/spk/planets/de440s.bsp",
        selected_by_default=True,
    ),
)


def runtime_summary() -> tuple[str, str]:
    if spice is None:
        return (
            "Disconnected",
            "SpiceyPy is not installed in the active environment. Install dependencies to enable ephemeris queries.",
        )
    return (
        "Ready",
        "SpiceyPy import succeeded. Load kernels from data/kernels before requesting state vectors.",
    )


def discover_kernel_files(directory: str | Path) -> list[Path]:
    root = Path(directory).expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(f"Kernel directory not found: {root}")

    ordered_paths: list[Path] = []
    for pattern in SUPPORTED_KERNEL_PATTERNS:
        ordered_paths.extend(sorted(root.rglob(pattern)))
    return ordered_paths


def default_local_kernel_roots(preferred_roots: Iterable[str | Path] | None = None) -> list[Path]:
    if preferred_roots is None:
        preferred_items: list[str | Path] = []
    elif isinstance(preferred_roots, (str, Path)):
        preferred_items = [preferred_roots]
    else:
        preferred_items = list(preferred_roots)
    candidates: list[Path] = []
    seen: set[Path] = set()
    for raw_root in [*preferred_items, _DEFAULT_LOCAL_KERNEL_DIR]:
        root = Path(raw_root).expanduser().resolve()
        if root in seen:
            continue
        seen.add(root)
        candidates.append(root)
    return candidates


def infer_kernel_filename(url: str, filename: str | None = None) -> str:
    candidate = (filename or Path(parse.urlparse(url).path).name).strip()
    if not candidate:
        raise ValueError("Unable to determine a kernel filename from the download URL.")
    if any(sep in candidate for sep in ("/", "\\")) or Path(candidate).name != candidate:
        raise ValueError("Kernel filename must not contain directory separators.")
    suffix = Path(candidate).suffix.lower()
    if suffix not in SUPPORTED_KERNEL_SUFFIXES:
        supported = ", ".join(SUPPORTED_KERNEL_SUFFIXES)
        raise ValueError(f"Unsupported kernel file suffix '{suffix}'. Supported suffixes: {supported}")
    return candidate


def download_kernel_file(
    url: str,
    destination_dir: str | Path,
    filename: str | None = None,
    *,
    overwrite: bool = False,
    timeout_s: float = 60.0,
) -> Path:
    parsed_url = parse.urlparse(url.strip())
    if parsed_url.scheme.lower() not in {"http", "https"}:
        raise ValueError("Kernel download URL must start with http:// or https://")

    destination_root = Path(destination_dir).expanduser().resolve()
    destination_root.mkdir(parents=True, exist_ok=True)
    target_name = infer_kernel_filename(url, filename)
    target_path = (destination_root / target_name).resolve()
    if target_path.parent != destination_root:
        raise ValueError("Kernel download target must stay within the selected kernel directory.")
    if target_path.exists() and not overwrite:
        raise FileExistsError(f"Kernel file already exists: {target_path}")

    request_headers = {"User-Agent": "SMART-SPICE-Kernel-Downloader/0.1"}
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f"{target_path.stem}_",
            suffix=".part",
            dir=destination_root,
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            with request.urlopen(request.Request(url, headers=request_headers), timeout=timeout_s) as response:
                shutil.copyfileobj(response, temp_file)
        temp_path.replace(target_path)
        return target_path
    except Exception:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink(missing_ok=True)
        raise


class SpiceKernelManager:
    def __init__(self, local_kernel_roots: Iterable[str | Path] | None = None) -> None:
        self.loaded_kernels: list[Path] = []
        self._local_kernel_roots = default_local_kernel_roots(local_kernel_roots)
        self._local_kernels_loaded = False

    @property
    def available(self) -> bool:
        return spice is not None

    @property
    def local_kernel_roots(self) -> tuple[Path, ...]:
        return tuple(self._local_kernel_roots)

    def _require_runtime(self) -> None:
        if spice is None:
            raise SpiceUnavailableError(
                "SpiceyPy is unavailable. Install project dependencies before using SPICE features."
            )

    def clear(self) -> None:
        self._require_runtime()
        spice.kclear()
        self.loaded_kernels.clear()
        self._local_kernels_loaded = False

    def configure_local_kernel_roots(self, kernel_roots: Iterable[str | Path]) -> tuple[Path, ...]:
        self._local_kernel_roots = default_local_kernel_roots(kernel_roots)
        self._local_kernels_loaded = False
        return self.local_kernel_roots

    def ensure_local_kernels_loaded(self) -> list[Path]:
        self._require_runtime()
        if self._local_kernels_loaded:
            return list(self.loaded_kernels)
        loaded: list[Path] = []
        seen_filenames: set[str] = set()
        for root in self._local_kernel_roots:
            if not root.exists():
                continue
            for kernel_path in discover_kernel_files(root):
                filename_key = kernel_path.name.lower()
                if filename_key in seen_filenames:
                    continue
                seen_filenames.add(filename_key)
                loaded.append(self.load_kernel(kernel_path))
        self._local_kernels_loaded = True
        return loaded

    def load_kernel(self, kernel_path: str | Path) -> Path:
        self._require_runtime()
        resolved = Path(kernel_path).expanduser().resolve()
        if not resolved.exists():
            raise FileNotFoundError(f"Kernel not found: {resolved}")
        if resolved in self.loaded_kernels:
            return resolved
        spice.furnsh(str(resolved))
        self.loaded_kernels.append(resolved)
        return resolved

    def load_kernels(self, kernel_paths: Iterable[str | Path]) -> list[Path]:
        return [self.load_kernel(path) for path in kernel_paths]

    def load_directory(self, directory: str | Path) -> list[Path]:
        self._require_runtime()
        return self.load_kernels(discover_kernel_files(directory))

    def utc_to_et(self, utc: str) -> float:
        self._require_runtime()
        self.ensure_local_kernels_loaded()
        return float(spice.str2et(utc))

    def et_to_utc(self, et: float, precision: int = 3) -> str:
        self._require_runtime()
        self.ensure_local_kernels_loaded()
        return str(spice.et2utc(float(et), "ISOC", precision))

    def transform_position(
        self,
        position_km: Iterable[float],
        *,
        from_frame: str,
        to_frame: str,
        utc: str,
    ) -> NDArray[np.float64]:
        self._require_runtime()
        self.ensure_local_kernels_loaded()
        et = self.utc_to_et(utc)
        rotation = np.asarray(spice.pxform(from_frame, to_frame, et), dtype=np.float64)
        return rotation @ np.asarray(list(position_km), dtype=np.float64)

    def transform_state(
        self,
        position_km: Iterable[float],
        velocity_km_s: Iterable[float],
        *,
        from_frame: str,
        to_frame: str,
        utc: str,
    ) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
        self._require_runtime()
        self.ensure_local_kernels_loaded()
        et = self.utc_to_et(utc)
        transform = np.asarray(spice.sxform(from_frame, to_frame, et), dtype=np.float64)
        state = np.concatenate(
            [
                np.asarray(list(position_km), dtype=np.float64),
                np.asarray(list(velocity_km_s), dtype=np.float64),
            ]
        )
        transformed = transform @ state
        return transformed[:3], transformed[3:]

    def state(
        self,
        target: str,
        observer: str,
        utc: str,
        frame: str = "J2000",
        aberration: str = "NONE",
    ) -> BodyState:
        self._require_runtime()
        self.ensure_local_kernels_loaded()
        et = self.utc_to_et(utc)
        state_vector, light_time = spice.spkezr(target, et, frame, aberration, observer)
        state_array = np.asarray(state_vector, dtype=np.float64)
        return BodyState(
            position_km=state_array[:3],
            velocity_km_s=state_array[3:],
            light_time_s=float(light_time),
        )
