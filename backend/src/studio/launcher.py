"""Start a local OmniRoute on the user's click.

OmniRoute stays an external install; Studio only runs its fixed `omniroute serve` command,
detached, when the gateway URL is local and nothing answers there. No shell, no user input
in the command line, at most one start per minute.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
READY_TIMEOUT_S = 60.0


def which_on_path(name: str) -> str | None:
    """`shutil.which` limited to absolute PATH entries: on Windows it would try the current
    directory first, so a planted `omniroute.cmd` next to Studio could run on the click."""
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if entry and Path(entry).is_absolute():
            found = shutil.which(name, path=entry)
            if found and Path(found).is_absolute():
                return found
    return None


class LaunchError(Exception):
    pass


class OmniRouteLauncher:
    def __init__(
        self,
        url: str,
        log_dir: Path,
        reachable: Callable[[], bool],
        which: Callable[[str], str | None] = which_on_path,
        popen: Callable[..., object] = subprocess.Popen,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.url = url
        self.log_dir = log_dir
        self.reachable = reachable
        self.which = which
        self.popen = popen
        self.sleep = sleep
        self._lock = threading.Lock()
        self._proc: object | None = None  # the server Studio started, while it lives

    def binary(self) -> str | None:
        if (urlparse(self.url).hostname or "") not in LOCAL_HOSTS:
            return None  # a remote gateway is not Studio's to start
        return self.which("omniroute")

    def start(self) -> dict[str, object]:
        """Start OmniRoute and wait until it answers. Returns {"started", "reachable"}."""
        if self.reachable():
            return {"started": False, "reachable": True}
        binary = self.binary()
        if not binary:
            raise LaunchError("omniroute is not installed here or the gateway URL is not local")
        try:
            port = str(urlparse(self.url).port or 20128)
        except ValueError as exc:
            raise LaunchError(f"invalid gateway URL: {self.url}") from exc
        with self._lock:
            poll = getattr(self._proc, "poll", None)
            if poll is not None and poll() is None:
                started = False  # the server we started is still booting; wait for it
            else:
                self._proc = self._spawn(binary, port)
                started = True
        deadline = time.monotonic() + READY_TIMEOUT_S
        while time.monotonic() < deadline:
            if self.reachable():
                return {"started": started, "reachable": True}
            self.sleep(2.0)
        return {"started": started, "reachable": False}

    def _spawn(self, binary: str, port: str) -> object:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        log = (self.log_dir / "omniroute.log").open("ab")
        flags = 0
        if os.name == "nt":
            # Not DETACHED_PROCESS: npm installs `omniroute.cmd`, and cmd.exe without a
            # console exits at once without a word (seen live 2026-10-10).
            flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        try:
            # OmniRoute is the user's own gateway and reads its own config: it gets their env.
            # Studio's own PORT (8765 under a dev server) must not leak into OmniRoute,
            # which reads PORT too (seen live: it came up on Studio's port).
            env = {k: v for k, v in os.environ.items() if k.upper() != "PORT"}
            return self.popen(
                [binary, "serve", "--port", port, "--no-open"],
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=flags,
                start_new_session=os.name != "nt",
            )
        except OSError as exc:
            raise LaunchError(f"failed to start omniroute: {exc}") from exc
        finally:
            log.close()
