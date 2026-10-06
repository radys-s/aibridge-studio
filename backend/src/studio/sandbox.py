"""Policy for what a model-driven worker may touch: paths, file content, commands, environment.

Provenance: path and command policy derived from Prizma `backend/app/tools/sandbox.py`
(commit 49022f7, same author). Changes: the only root is the run's working copy (Prizma used
global user-configured roots), scope globs per task, `.git` writes denied, minimal
subprocess environment, secret scanning of file content (from AI Bridge
`adapters/security.py`, commit 953e0b9).

This module does not isolate processes. An allowed `python` can run arbitrary code as the
user. The protection is: the working copy is a disposable clone, commands run without a
shell and with a scrubbed environment, and only commands in the allowlist start at all.
"""

from __future__ import annotations

import fnmatch
import os
import re
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final

MAX_OUTPUT_CHARS = 12_000
MAX_READ_BYTES = 200_000

DENIED_NAMES = frozenset(
    {
        ".env",
        ".env.local",
        ".env.production",
        ".env.development",
        "id_rsa",
        "id_ed25519",
        "credentials.json",
        ".npmrc",
        ".pypirc",
        ".git-credentials",
        ".netrc",
    }
)
DENIED_DIRS = frozenset({".git", ".venv", "venv", "node_modules", "__pycache__", ".ssh"})
DENIED_SUFFIXES = frozenset({".key", ".pem", ".pfx", ".p12", ".keystore", ".sqlite", ".db"})

ALLOWED_COMMANDS = frozenset(
    {"python", "python3", "py", "pytest", "ruff", "mypy", "node", "npm", "npx", "tsc", "git"}
)
# git is allowed read-only: these subcommands only.
ALLOWED_GIT = frozenset({"status", "diff", "log", "show", "ls-files", "grep"})

SECRET_PATTERNS: Final = (
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("openai_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{16,}\b")),
    ("api_secret_key", re.compile(r"\bsk_[a-zA-Z0-9_]{16,}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    (
        "credential_assignment",
        re.compile(
            r"(?i)\b(?:api[_-]?key|access[_-]?token|secret|password|passwd)\b\s*[:=]\s*"
            r"['\"]?[A-Za-z0-9_./+\-=]{8,}"
        ),
    ),
)

_ENV_ALLOW = (
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "WINDIR",
    "COMSPEC",
    "TEMP",
    "TMP",
    "HOMEDRIVE",
    "HOMEPATH",
    "USERPROFILE",
    "LOCALAPPDATA",
    "APPDATA",
    "PROGRAMDATA",
    "PROGRAMFILES",
    "PROGRAMFILES(X86)",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
    "LANG",
    "LC_ALL",
    "HOME",
)
_ENV_SECRET = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|AUTH|CREDENTIAL|COOKIE)", re.I)


class Denied(Exception):
    """The worker tried something the policy forbids. Returned to the model as text."""


def secret_findings(text: str) -> tuple[str, ...]:
    return tuple(code for code, pattern in SECRET_PATTERNS if pattern.search(text))


def scrubbed_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k.upper() in _ENV_ALLOW}
    env = {k: v for k, v in env.items() if not _ENV_SECRET.search(k)}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["GIT_TERMINAL_PROMPT"] = "0"
    if extra:
        env.update(extra)
    return env


def clip(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    head = limit * 2 // 3
    tail = limit - head
    return f"{text[:head]}\n[... {len(text) - limit} chars clipped ...]\n{text[-tail:]}"


def in_scope(rel_posix: str, scope: list[str]) -> bool:
    return any(fnmatch.fnmatch(rel_posix, pattern) for pattern in scope)


@dataclass
class CommandResult:
    command: str
    exit_code: int | None
    output: str
    timed_out: bool
    seconds: float

    def as_dict(self) -> dict[str, object]:
        return {
            "command": self.command,
            "exit_code": self.exit_code,
            "output": self.output,
            "timed_out": self.timed_out,
            "seconds": round(self.seconds, 2),
        }


class Sandbox:
    """Policy bound to one working copy and one task scope."""

    def __init__(self, root: Path, scope: list[str], command_timeout_s: int = 120) -> None:
        self.root = root.resolve()
        self.scope = scope
        self.command_timeout_s = command_timeout_s

    # --- paths ------------------------------------------------------------------------

    def resolve(self, raw: str, *, must_exist: bool = True) -> Path:
        if not raw or not raw.strip():
            raise Denied("Missing path.")
        candidate = Path(raw.strip().strip('"'))
        if candidate.is_absolute():
            raise Denied("Use a path relative to the repository root.")
        try:
            path = (self.root / candidate).resolve()
            rel = path.relative_to(self.root)
        except (OSError, ValueError) as exc:
            raise Denied(f"Path {raw!r} is outside the repository.") from exc
        parts = {p.lower() for p in rel.parts}
        if parts & DENIED_DIRS:
            raise Denied(f"Path {raw!r} is in a closed directory.")
        if path.name.lower() in DENIED_NAMES or path.name.lower().startswith(".env"):
            raise Denied(f"{path.name} may contain credentials and is not accessible.")
        if path.suffix.lower() in DENIED_SUFFIXES:
            raise Denied(f"{path.name} looks like a key or database and is not accessible.")
        if must_exist and not path.exists():
            raise Denied(f"Path {raw!r} does not exist.")
        return path

    def rel(self, path: Path) -> str:
        return PurePosixPath(path.relative_to(self.root)).as_posix()

    def read_text(self, raw: str) -> str:
        path = self.resolve(raw)
        if not path.is_file():
            raise Denied(f"{raw!r} is not a file.")
        data = path.read_bytes()[:MAX_READ_BYTES]
        text = data.decode("utf-8", errors="replace")
        found = secret_findings(text)
        if found:
            raise Denied(f"{raw!r} appears to contain secrets ({', '.join(found)}); not sent.")
        return text

    def check_writable(self, raw: str) -> Path:
        path = self.resolve(raw, must_exist=False)
        rel = self.rel(path)
        if not in_scope(rel, self.scope):
            raise Denied(
                f"{rel} is outside the task scope. Allowed patterns: {', '.join(self.scope)}"
            )
        return path

    def list_files(self, raw: str = ".") -> list[str]:
        base = self.resolve(raw or ".")
        out: list[str] = []
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if d.lower() not in DENIED_DIRS)
            for name in sorted(filenames):
                p = Path(dirpath) / name
                if name.lower() in DENIED_NAMES or p.suffix.lower() in DENIED_SUFFIXES:
                    continue
                out.append(self.rel(p))
                if len(out) >= 500:
                    return out
        return out

    # --- commands ---------------------------------------------------------------------

    def check_command(self, command: str) -> list[str]:
        text = (command or "").strip()
        if not text:
            raise Denied("Empty command.")
        for operator in ("&&", "||", "|", ";", ">", "<", "`", "$(", "\n"):
            if operator in text:
                raise Denied(f"Command contains {operator!r}. No shell: send one command.")
        try:
            argv = shlex.split(text, posix=True)
        except ValueError as exc:
            raise Denied(f"Could not parse command: {exc}") from exc
        if not argv:
            raise Denied("Empty command.")
        head = Path(argv[0]).name.lower().removesuffix(".exe")
        if head not in ALLOWED_COMMANDS:
            raise Denied(
                f"Command {head!r} is not allowed. Allowed: {', '.join(sorted(ALLOWED_COMMANDS))}"
            )
        if head == "git" and (len(argv) < 2 or argv[1] not in ALLOWED_GIT):
            raise Denied(f"Only read-only git subcommands: {', '.join(sorted(ALLOWED_GIT))}")
        binary = shutil.which(head)
        if not binary:
            raise Denied(f"Command {head!r} is not installed.")
        if binary.lower().endswith((".cmd", ".bat")):
            # Batch files would be interpreted by cmd.exe, i.e. a shell (BatBadBut).
            raise Denied(f"{head!r} is a batch wrapper and cannot run without a shell.")
        return [binary, *argv[1:]]

    def run_command(self, command: str, timeout_s: int | None = None) -> CommandResult:
        argv = self.check_command(command)
        timeout = timeout_s or self.command_timeout_s
        started = time.monotonic()
        try:
            proc = subprocess.run(
                argv,
                cwd=self.root,
                env=scrubbed_env(),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                stdin=subprocess.DEVNULL,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            partial = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
            return CommandResult(
                command,
                None,
                clip(partial + f"\n[timed out after {timeout}s]"),
                True,
                time.monotonic() - started,
            )
        except OSError as exc:
            return CommandResult(command, None, f"failed to start: {exc}", False, 0.0)
        output = (proc.stdout or "") + (proc.stderr or "")
        return CommandResult(
            command, proc.returncode, clip(output), False, time.monotonic() - started
        )
