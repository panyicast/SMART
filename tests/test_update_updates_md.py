from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path


def _load_module():
    module_path = Path(__file__).resolve().parents[1] / "scripts" / "update_updates_md.py"
    spec = importlib.util.spec_from_file_location("update_updates_md", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _git(repo_root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return completed.stdout


def _init_repo(repo_root: Path) -> None:
    _git(repo_root, "init")
    _git(repo_root, "config", "user.name", "SMART Test")
    _git(repo_root, "config", "user.email", "smart@example.com")


def test_rebuild_updates_md_from_git_history(tmp_path: Path) -> None:
    module = _load_module()
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _init_repo(repo_root)

    (repo_root / "README.md").write_text("# Demo\n", encoding="utf-8")
    _git(repo_root, "add", "README.md")
    _git(repo_root, "commit", "-m", "Initial commit")

    src_dir = repo_root / "src"
    src_dir.mkdir()
    (src_dir / "feature.py").write_text("print('ok')\n", encoding="utf-8")
    _git(repo_root, "add", "src/feature.py")
    _git(repo_root, "commit", "-m", "Add feature module")

    assert module.main(["--repo-root", str(repo_root)]) == 0

    updates_text = (repo_root / "updates.md").read_text(encoding="utf-8")
    latest_hash = _git(repo_root, "rev-parse", "--short", "HEAD").strip()

    assert "# 更新记录" in updates_text
    assert "Initial commit" in updates_text
    assert "Add feature module" in updates_text
    assert latest_hash in updates_text
    assert "`src/feature.py`" in updates_text


def test_commit_message_file_adds_pending_entry(tmp_path: Path) -> None:
    module = _load_module()
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _init_repo(repo_root)

    (repo_root / "README.md").write_text("# Demo\n", encoding="utf-8")
    _git(repo_root, "add", "README.md")
    _git(repo_root, "commit", "-m", "Initial commit")

    scripts_dir = repo_root / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "task.ps1").write_text("Write-Host 'ok'\n", encoding="utf-8")
    _git(repo_root, "add", "scripts/task.ps1")

    message_file = repo_root / ".git" / "COMMIT_EDITMSG"
    message_file.write_text("Add automation hook\n\nbody\n", encoding="utf-8")

    assert module.main(["--repo-root", str(repo_root), "--commit-message-file", str(message_file)]) == 0

    updates_text = (repo_root / "updates.md").read_text(encoding="utf-8")

    assert "Add automation hook" in updates_text
    assert "待写入本次提交" in updates_text
    assert "`scripts/task.ps1`" in updates_text


def test_summarize_files_filters_updates_md_and_vendor_noise() -> None:
    module = _load_module()

    summary = module.summarize_files(
        [
            "updates.md",
            ".tmp/Cesium-1.140/Specs/demo.js",
            "src/smart/assets/cesium/vendor/Build/Cesium/index.js",
            "src/smart/services/project_workspace.py",
            "README.md",
        ]
    )

    assert "updates.md" not in summary
    assert ".tmp/" not in summary
    assert "`README.md`" in summary
    assert "`src/smart/services/project_workspace.py`" in summary
