"""Console entry for ``smart-cesium-diagnostics``.

把 ``scripts/cesium_diagnostics.py`` 的逻辑暴露成一个安装后可调用的
console script。脚本本体保留在 ``scripts/`` 目录便于源码状态下直接运行；
此模块仅做轻量包装，便于通过 ``pip install`` 后从任意目录调用。
"""

from __future__ import annotations

from pathlib import Path
import importlib.util
import sys


def _load_script_module():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "cesium_diagnostics.py"
    if not script_path.exists():
        raise RuntimeError(
            "cesium_diagnostics 脚本未找到，请确认 SMART 仓库源码完整安装。"
        )
    spec = importlib.util.spec_from_file_location("smart_cesium_diagnostics_script", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载脚本：{script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main(argv: list[str] | None = None) -> int:
    module = _load_script_module()
    return int(module.main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
