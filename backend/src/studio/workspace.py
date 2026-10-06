"""Isolated working copy per run.

A `git clone --no-hardlinks` of the source repository's committed HEAD into Studio's data
directory. The user's checkout, its index and its uncommitted changes are never touched;
the clone has no push remote. This is isolation of *work*, not a security boundary (see
`sandbox.py`).
"""

from __future__ import annotations

import shutil
import stat
import subprocess
from pathlib import Path

from studio.sandbox import scrubbed_env


class WorkspaceError(RuntimeError):
    pass


def _git(cwd: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", "-c", "core.autocrlf=false", "-c", "core.quotepath=false", *args],
        cwd=cwd,
        env=scrubbed_env({"GIT_CONFIG_NOSYSTEM": "1"}),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.DEVNULL,
        check=False,
    )
    if check and proc.returncode != 0:
        raise WorkspaceError(f"git {' '.join(args)} failed: {proc.stderr.strip()[:500]}")
    return proc.stdout


def source_head(repo: Path) -> str:
    if not (repo / ".git").exists():
        raise WorkspaceError(f"{repo} is not a git repository")
    return _git(repo, "rev-parse", "HEAD").strip()


def create(repo: Path, dest: Path) -> str:
    """Clone `repo` into `dest`. Returns the commit the clone is based on."""
    head = source_head(repo)
    if dest.exists():
        raise WorkspaceError(f"workspace {dest} already exists")
    dest.parent.mkdir(parents=True, exist_ok=True)
    _git(dest.parent, "clone", "--quiet", "--no-hardlinks", "--no-local", str(repo), dest.name)
    _git(dest, "checkout", "--quiet", "-B", "studio-work", head)
    _git(dest, "remote", "remove", "origin", check=False)
    _git(dest, "config", "user.name", "AI Bridge Studio")
    _git(dest, "config", "user.email", "studio@localhost")
    return head


def reset_to(dest: Path, commit: str) -> None:
    _git(dest, "reset", "--quiet", "--hard", commit)
    _git(dest, "clean", "-fdq")


def commit_all(dest: Path, message: str) -> str:
    _git(dest, "add", "-A")
    if not _git(dest, "status", "--porcelain").strip():
        return _git(dest, "rev-parse", "HEAD").strip()
    _git(dest, "commit", "--quiet", "--no-verify", "-m", message)
    return _git(dest, "rev-parse", "HEAD").strip()


def diff(dest: Path, base: str) -> str:
    """Unified diff of everything (committed + uncommitted + untracked) since `base`."""
    _git(dest, "add", "-A")
    return _git(dest, "diff", "--cached", "--no-color", "--no-ext-diff", base)


def changed_files(dest: Path, base: str) -> list[str]:
    _git(dest, "add", "-A")
    out = _git(dest, "diff", "--cached", "--name-only", "--no-renames", base)
    return [line for line in out.splitlines() if line.strip()]


def remove(dest: Path) -> None:
    def _onerror(func, path, _exc):  # type: ignore[no-untyped-def]
        Path(path).chmod(stat.S_IWRITE)
        func(path)

    if dest.exists():
        shutil.rmtree(dest, onexc=_onerror)
