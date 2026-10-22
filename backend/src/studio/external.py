"""Open a terminal for an external agent the user drives by hand.

Studio only opens the window in the working copy. The user starts the session, pastes the
brief and stays present: Freebuff's terms forbid scripts or wrappers operating it, so
Studio never types into it or reads its output.
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from studio.sandbox import scrubbed_env

AGENT_COMMANDS = {"freebuff": "freebuff"}


class TerminalError(RuntimeError):
    pass


def terminal_argv(workspace: Path, agent: str, platform: str = sys.platform) -> list[str]:
    command = AGENT_COMMANDS[agent]
    title = f"{agent} - Studio"
    if platform == "win32":
        wt = shutil.which("wt.exe") or shutil.which("wt")
        if wt:
            return [wt, "-d", str(workspace), "--title", title, "cmd", "/k", command]
        comspec = shutil.which("cmd.exe")
        if not comspec:
            raise TerminalError("no terminal found (wt.exe or cmd.exe)")
        # `start` opens a new console window; its first quoted argument is the window title.
        return [comspec, "/c", "start", title, "/D", str(workspace), "cmd", "/k", command]
    if platform == "darwin":
        script = f"cd {shlex.quote(str(workspace))} && {command}"
        return [
            "osascript",
            "-e",
            f'tell application "Terminal" to do script {_applescript(script)}',
        ]
    for term, args in (
        ("x-terminal-emulator", ["-e"]),
        ("gnome-terminal", ["--working-directory", str(workspace), "--"]),
        ("konsole", ["--workdir", str(workspace), "-e"]),
        ("xterm", ["-e"]),
    ):
        found = shutil.which(term)
        if found:
            inner = f"cd {shlex.quote(str(workspace))} && {command}; exec $SHELL"
            return [found, *args, "sh", "-c", inner]
    raise TerminalError(f"no terminal found; run `{command}` in {workspace} yourself")


def _applescript(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def open_terminal(workspace: Path, agent: str) -> list[str]:
    if not workspace.is_dir():
        raise TerminalError(f"working copy {workspace} does not exist")
    argv = terminal_argv(workspace, agent)
    try:
        # Scrubbed env: Studio's own keys (OmniRoute) must not reach the external agent.
        subprocess.Popen(argv, cwd=workspace, env=scrubbed_env(), stdin=subprocess.DEVNULL)
    except OSError as exc:
        raise TerminalError(f"terminal could not start: {exc}") from exc
    return argv
