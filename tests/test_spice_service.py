from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pytest

from smart.services import spice_service
from smart.services.spice_service import (
    COMMON_KERNEL_PRESETS,
    SpiceKernelManager,
    default_local_kernel_roots,
    discover_kernel_files,
    download_kernel_file,
    infer_kernel_filename,
)


def test_discover_kernel_files_orders_supported_suffixes(tmp_path: Path) -> None:
    (tmp_path / "zeta.bsp").write_text("bsp", encoding="utf-8")
    (tmp_path / "alpha.tls").write_text("tls", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "gamma.bc").write_text("bc", encoding="utf-8")
    (tmp_path / "sub" / "beta.tf").write_text("tf", encoding="utf-8")
    (tmp_path / "ignore.txt").write_text("txt", encoding="utf-8")

    discovered = discover_kernel_files(tmp_path)

    assert [path.name for path in discovered] == ["alpha.tls", "beta.tf", "zeta.bsp", "gamma.bc"]


def test_load_kernel_deduplicates_existing_entry(monkeypatch, tmp_path: Path) -> None:
    calls: list[str] = []

    class _FakeSpice:
        @staticmethod
        def furnsh(path: str) -> None:
            calls.append(path)

        @staticmethod
        def kclear() -> None:
            calls.append("kclear")

    kernel_path = tmp_path / "mission.bsp"
    kernel_path.write_text("bsp", encoding="utf-8")
    monkeypatch.setattr(spice_service, "spice", _FakeSpice())

    manager = SpiceKernelManager()
    manager.load_kernel(kernel_path)
    manager.load_kernel(kernel_path)

    assert calls == [str(kernel_path.resolve())]
    assert manager.loaded_kernels == [kernel_path.resolve()]


def test_utc_to_et_auto_loads_local_kernels(monkeypatch, tmp_path: Path) -> None:
    calls: list[object] = []

    class _FakeSpice:
        @staticmethod
        def furnsh(path: str) -> None:
            calls.append(("furnsh", Path(path).name))

        @staticmethod
        def kclear() -> None:
            calls.append(("kclear",))

        @staticmethod
        def str2et(utc: str) -> float:
            calls.append(("str2et", utc))
            return 123.0

        @staticmethod
        def et2utc(et: float, fmt: str, precision: int) -> str:
            calls.append(("et2utc", et, fmt, precision))
            return "2026-04-18T10:45:00.000"

    kernel_dir = tmp_path / "kernels"
    kernel_dir.mkdir()
    (kernel_dir / "naif0012.tls").write_text("tls", encoding="utf-8")
    (kernel_dir / "pck00011.tpc").write_text("tpc", encoding="utf-8")
    monkeypatch.setattr(spice_service, "spice", _FakeSpice())
    monkeypatch.setattr(spice_service, "_DEFAULT_LOCAL_KERNEL_DIR", tmp_path / "missing")

    manager = SpiceKernelManager(local_kernel_roots=[kernel_dir])

    assert manager.utc_to_et("2026-04-18T10:45:00") == 123.0
    assert manager.et_to_utc(123.0, precision=3) == "2026-04-18T10:45:00.000"
    assert calls == [
        ("furnsh", "naif0012.tls"),
        ("furnsh", "pck00011.tpc"),
        ("str2et", "2026-04-18T10:45:00"),
        ("et2utc", 123.0, "ISOC", 3),
    ]


def test_transform_state_uses_sxform(monkeypatch, tmp_path: Path) -> None:
    class _FakeSpice:
        @staticmethod
        def furnsh(path: str) -> None:
            raise AssertionError("No local kernels should be loaded for this test.")

        @staticmethod
        def str2et(utc: str) -> float:
            assert utc == "2026-04-18T10:45:00Z"
            return 10.0

        @staticmethod
        def sxform(from_frame: str, to_frame: str, et: float) -> list[list[float]]:
            assert from_frame == "ITRF93"
            assert to_frame == "J2000"
            assert et == 10.0
            transform = np.eye(6)
            transform[0, 3] = 1.0
            transform[1, 4] = 1.0
            transform[2, 5] = 1.0
            return transform.tolist()

        @staticmethod
        def pxform(from_frame: str, to_frame: str, et: float) -> list[list[float]]:
            return np.eye(3).tolist()

    monkeypatch.setattr(spice_service, "spice", _FakeSpice())
    monkeypatch.setattr(spice_service, "_DEFAULT_LOCAL_KERNEL_DIR", tmp_path / "missing")

    manager = SpiceKernelManager(local_kernel_roots=[])
    position, velocity = manager.transform_state(
        [1.0, 2.0, 3.0],
        [4.0, 5.0, 6.0],
        from_frame="ITRF93",
        to_frame="J2000",
        utc="2026-04-18T10:45:00Z",
    )

    assert position.tolist() == [5.0, 7.0, 9.0]
    assert velocity.tolist() == [4.0, 5.0, 6.0]


def test_infer_kernel_filename_uses_explicit_filename_and_validates_suffix() -> None:
    assert infer_kernel_filename(
        "https://naif.jpl.nasa.gov/pub/naif/generic_kernels/lsk/latest_leapseconds.tls",
        "mission_clock.tls",
    ) == "mission_clock.tls"

    with pytest.raises(ValueError):
        infer_kernel_filename("https://example.com/download", "kernel.txt")


def test_download_kernel_file_writes_response_content(monkeypatch, tmp_path: Path) -> None:
    payload = b"kernel payload"

    class _FakeResponse(io.BytesIO):
        def __enter__(self) -> "_FakeResponse":
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            self.close()

    def _fake_urlopen(request_obj: object, timeout: float) -> _FakeResponse:
        assert timeout == 60.0
        assert request_obj.full_url == "https://example.com/kernels/de440s.bsp"
        return _FakeResponse(payload)

    monkeypatch.setattr(spice_service.request, "urlopen", _fake_urlopen)

    downloaded = download_kernel_file("https://example.com/kernels/de440s.bsp", tmp_path)

    assert downloaded == (tmp_path / "de440s.bsp").resolve()
    assert downloaded.read_bytes() == payload


def test_download_kernel_file_rejects_existing_file_without_overwrite(tmp_path: Path) -> None:
    existing = tmp_path / "earth_attitude.bc"
    existing.write_text("old", encoding="utf-8")

    with pytest.raises(FileExistsError):
        download_kernel_file("https://example.com/kernels/earth_attitude.bc", tmp_path)


def test_common_kernel_presets_use_full_https_urls_and_supported_suffixes() -> None:
    assert COMMON_KERNEL_PRESETS
    assert len({preset.key for preset in COMMON_KERNEL_PRESETS}) == len(COMMON_KERNEL_PRESETS)
    assert len({preset.filename for preset in COMMON_KERNEL_PRESETS}) == len(COMMON_KERNEL_PRESETS)
    assert all(preset.url.startswith("https://") for preset in COMMON_KERNEL_PRESETS)
    assert all(preset.selected_by_default for preset in COMMON_KERNEL_PRESETS)

    for preset in COMMON_KERNEL_PRESETS:
        assert infer_kernel_filename(preset.url) == preset.filename
        assert preset.kernel_type


def test_default_local_kernel_roots_preserves_preferred_order(tmp_path: Path) -> None:
    project_root = tmp_path / "projects" / "mission" / "data" / "kernels"
    roots = default_local_kernel_roots([project_root, project_root])

    assert roots[0] == project_root.resolve()
    assert roots[-1].name == "kernels"
