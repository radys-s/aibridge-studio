"""Open a terminal for an external agent the user drives by hand.

Studio only opens the window in the working copy. The user starts the session, pastes the
brief and stays present: Freebuff's terms forbid scripts or wrappers operating it, so
Studio never types into it or reads its output.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from studio.sandbox import scrubbed_env

AGENT_COMMANDS = {"freebuff": "freebuff"}


class TerminalError(RuntimeError):
    pass


def terminal_argv(workspace: Path, agent: str) -> list[str]:
    command = AGENT_COMMANDS[agent]
    wt = shutil.which("wt.exe") or shutil.which("wt")
    if wt:
        return [wt, "-d", str(workspace), "--title", f"{agent} - Studio", "cmd", "/k", command]
    comspec = shutil.which("cmd.exe")
    if not comspec:
        raise TerminalError("no terminal found (wt.exe or cmd.exe)")
    # `start` opens a new console window; its first quoted argument is the window title.
    return [comspec, "/c", "start", f"{agent} - Studio", "/D", str(workspace), "cmd", "/k", command]


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
