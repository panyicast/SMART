from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

AUTOGEN_NOTE = (
    "> 此文件由 `scripts/update_updates_md.py` 自动维护。"
    "正常执行 `git commit` 时会通过 `.githooks/post-commit` 自动刷新并补进同一提交。"
)
EXCLUDED_PREFIXES = (
    ".venv/",
    ".tmp/",
)
NOISY_PREFIXES = (
    ".venv/",
    ".tmp/",
)
PREFERRED_PREFIXES = (
    "src/",
    "tests/",
    "scripts/",
    "README.md",
    "AGENTS.md",
    "pyproject.toml",
    "doc/",
    "data/",
)
TEXT_SUFFIXES = {
    ".md",
    ".py",
    ".ps1",
    ".toml",
    ".json",
    ".html",
    ".js",
    ".svg",
    ".txt",
    ".yml",
    ".yaml",
}


@dataclass(frozen=True)
class UpdateEntry:
    timestamp: str
    title: str
    files: tuple[str, ...]
    commit_hash: str | None = None


def git(repo_root: Path, *args: str, check: bool = True) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if check and completed.returncode != 0:
        stderr = completed.stderr.strip()
        stdout = completed.stdout.strip()
        detail = stderr or stdout or f"exit code {completed.returncode}"
        raise RuntimeError(f"git {' '.join(args)} failed: {detail}")
    return completed.stdout


def normalize_path(value: str) -> str:
    return value.replace("\\", "/").strip()


def should_log_path(path: str) -> bool:
    normalized = normalize_path(path)
    if not normalized or normalized == "updates.md":
        return False
    return not any(normalized.startswith(prefix) for prefix in EXCLUDED_PREFIXES)


def repo_has_commits(repo_root: Path) -> bool:
    completed = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    return completed.returncode == 0


def parse_commit_subject(message_text: str) -> str:
    for raw_line in message_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        return line
    return "Update"


def collect_commit_entries(repo_root: Path, *, head_as_current: bool = False) -> list[UpdateEntry]:
    if not repo_has_commits(repo_root):
        return []

    revisions = [line.strip() for line in git(repo_root, "rev-list", "--reverse", "HEAD").splitlines() if line.strip()]
    entries: list[UpdateEntry] = []
    for index, revision in enumerate(revisions):
        metadata = git(repo_root, "show", "--quiet", "--date=iso-strict", "--format=%H%n%cI%n%s", revision).splitlines()
        if len(metadata) < 3:
            raise RuntimeError(f"Unexpected git show metadata for revision {revision}")
        commit_hash, timestamp, subject = metadata[0].strip(), metadata[1].strip(), metadata[2].strip()
        file_lines = git(
            repo_root,
            "show",
            "--pretty=format:",
            "--name-only",
            "--diff-filter=ACDMRT",
            revision,
        ).splitlines()
        files = tuple(normalize_path(line) for line in file_lines if should_log_path(line))
        rendered_hash = "本次提交" if head_as_current and index == len(revisions) - 1 else commit_hash[:7]
        entries.append(UpdateEntry(timestamp=timestamp, title=subject, files=files, commit_hash=rendered_hash))
    return entries


def collect_pending_entry(repo_root: Path, commit_message_path: Path) -> UpdateEntry | None:
    if not commit_message_path.exists():
        return None

    subject = parse_commit_subject(commit_message_path.read_text(encoding="utf-8"))
    staged_lines = git(repo_root, "diff", "--cached", "--name-only", "--diff-filter=ACDMRT").splitlines()
    files = tuple(normalize_path(line) for line in staged_lines if should_log_path(line))
    timestamp = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    return UpdateEntry(timestamp=timestamp, title=subject, files=files, commit_hash=None)


def score_path(path: str) -> tuple[int, int, int, str]:
    penalty = 1 if any(path.startswith(prefix) for prefix in NOISY_PREFIXES) else 0
    preferred = len(PREFERRED_PREFIXES)
    for index, prefix in enumerate(PREFERRED_PREFIXES):
        if path.startswith(prefix):
            preferred = index
            break
    suffix = Path(path).suffix.lower()
    text_bias = 0 if suffix in TEXT_SUFFIXES or not suffix else 1
    return (penalty, text_bias, preferred, path)


def summarize_files(files: Sequence[str], limit: int = 8) -> str:
    normalized = [normalize_path(path) for path in files if should_log_path(path)]
    if not normalized:
        return "自动刷新记录，无额外文件。"

    unique_files = list(dict.fromkeys(normalized))
    highlights = sorted(unique_files, key=score_path)[:limit]
    rendered = "、".join(f"`{path}`" for path in highlights)
    if len(unique_files) > len(highlights):
        return f"{rendered} 等 {len(unique_files)} 个文件。"
    return f"{rendered}。"


def render_updates(entries: Sequence[UpdateEntry]) -> str:
    lines = ["# 更新记录", "", AUTOGEN_NOTE]
    if not entries:
        lines.extend(["", "_当前还没有可记录的提交。_"])
        return "\n".join(lines) + "\n"

    for entry in entries:
        lines.extend(["", f"## {entry.timestamp} | {entry.title}"])
        if entry.commit_hash:
            lines.append(f"- 提交：`{entry.commit_hash}`")
        else:
            lines.append("- 提交：`待写入本次提交`")
        lines.append(f"- 影响文件：{summarize_files(entry.files)}")
    return "\n".join(lines) + "\n"


def build_updates(repo_root: Path, commit_message_path: Path | None, *, head_as_current: bool = False) -> str:
    entries = collect_commit_entries(repo_root, head_as_current=head_as_current)
    pending_entry = collect_pending_entry(repo_root, commit_message_path) if commit_message_path else None
    if pending_entry is not None:
        entries.append(pending_entry)
    return render_updates(entries)


def write_updates(repo_root: Path, content: str) -> Path:
    updates_path = repo_root / "updates.md"
    updates_path.write_text(content, encoding="utf-8")
    return updates_path


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rebuild SMART updates.md from git history.")
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--commit-message-file", type=Path)
    parser.add_argument(
        "--head-as-current",
        action="store_true",
        help="Render HEAD with a stable current-commit marker for post-commit auto-amend hooks.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    repo_root = args.repo_root.resolve()
    content = build_updates(
        repo_root,
        args.commit_message_file.resolve() if args.commit_message_file else None,
        head_as_current=args.head_as_current,
    )
    write_updates(repo_root, content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
