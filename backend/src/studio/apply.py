"""Bring an accepted change into the user's repository as a new branch, nothing more.

The accepted working copy is squashed into one commit on top of the run's source commit and
fetched into the source repository as `studio/<run_id>`. Studio never checks the branch out,
never merges, never pushes and never touches the user's current branch, index or working
tree. It refuses when the user has uncommitted changes in a file the change touches (they
would collide when the user merges) or when the branch already exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from studio import workspace
from studio.workspace import WorkspaceError, _git


class ApplyError(RuntimeError):
    pass


@dataclass(frozen=True)
class Applied:
    branch: str
    commit: str
    files: list[str]


def branch_name(run_id: str) -> str:
    return f"studio/{run_id}"


def apply_run(source: Path, work: Path, source_commit: str, run_id: str, title: str) -> Applied:
    if not (source / ".git").exists():
        raise ApplyError(f"{source} is not a git repository")
    branch = branch_name(run_id)
    try:
        if _git(source, "branch", "--list", branch).strip():
            raise ApplyError(f"branch {branch} already exists in {source}")
        if not _git(source, "cat-file", "-t", source_commit, check=False).strip():
            raise ApplyError(f"source commit {source_commit[:10]} is not in {source}")
        workspace.commit_all(work, "studio: accepted state")
        files = workspace.changed_files(work, source_commit)
        if not files:
            raise ApplyError("the accepted change is empty")
        dirty = _dirty(source)
        clash = sorted(set(files) & dirty)
        if clash:
            raise ApplyError(
                "uncommitted changes in files the change touches: " + ", ".join(clash[:10])
            )
        name = _git(source, "config", "user.name", check=False).strip()
        email = _git(source, "config", "user.email", check=False).strip()
        if not name or not email:
            raise ApplyError("git user.name / user.email is not set for the repository")
        tree = _git(work, "rev-parse", "HEAD^{tree}").strip()
        message = f"{title}\n\nZměna z AI Bridge Studio, běh {run_id} (ověřeno a zrevidováno)."
        commit = _git(
            work,
            "-c",
            f"user.name={name}",
            "-c",
            f"user.email={email}",
            "commit-tree",
            tree,
            "-p",
            source_commit,
            "-m",
            message,
        ).strip()
        _git(work, "branch", "-f", "studio-apply", commit)
        try:
            _git(
                source,
                "fetch",
                "--quiet",
                "--no-tags",
                "--no-recurse-submodules",
                str(work),
                f"studio-apply:refs/heads/{branch}",
            )
        finally:
            _git(work, "branch", "-D", "studio-apply", check=False)
    except WorkspaceError as exc:
        raise ApplyError(str(exc)) from exc
    return Applied(branch, commit, files)


def _dirty(repo: Path) -> set[str]:
    # -z: NUL-separated and never quoted, so names with quotes or backslashes match too.
    out = _git(repo, "status", "--porcelain", "-z", "--untracked-files=all", "--no-renames")
    return {entry[3:] for entry in out.split("\0") if len(entry) > 3}
