"""`studio app`: Studio in its own window instead of a browser tab.

The window is Edge (or Chrome) in app mode with a dedicated profile under the data dir, so it
has its own taskbar entry, no tabs or address bar, and none of the user's browser extensions
or cookies. The server lives as long as the window: closing it stops Studio. If Studio is
already running, only a window is opened.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import socket
import subprocess
import threading
import time
import webbrowser
from pathlib import Path

import httpx
import uvicorn

from studio.api import create_app
from studio.config import Settings


def find_browser() -> str | None:
    """A Chromium browser that supports --app windows. Edge ships with Windows."""
    names = ("msedge", "chrome", "chromium", "google-chrome", "chromium-browser", "microsoft-edge")
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    roots = [os.environ.get(v) for v in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")]
    tails = (
        Path("Microsoft/Edge/Application/msedge.exe"),
        Path("Google/Chrome/Application/chrome.exe"),
    )
    for root in filter(None, roots):
        for tail in tails:
            candidate = Path(root) / tail
            if candidate.is_file():
                return str(candidate)
    for app in (
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ):
        if Path(app).is_file():
            return app
    return None


def window_argv(browser: str, url: str, profile: Path) -> list[str]:
    return [
        browser,
        f"--app={url}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        "--window-size=1440,900",
    ]


def _studio_running(url: str) -> bool:
    try:
        return httpx.get(f"{url}/api/health", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


def _port_taken(host: str, port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(1)
        return probe.connect_ex((host, port)) == 0


def run_app(settings: Settings) -> None:
    url = f"http://{settings.host}:{settings.port}"
    browser = find_browser()
    profile = settings.data_dir / "ui-profile"
    profile.mkdir(parents=True, exist_ok=True)

    if _port_taken(settings.host, settings.port):
        if not _studio_running(url):
            raise SystemExit(f"port {settings.port} is used by another program")
        print(f"Studio already runs at {url}; opening a window")
        if browser:
            subprocess.Popen(window_argv(browser, url, profile))
        else:
            webbrowser.open(url)
        return

    server = uvicorn.Server(
        uvicorn.Config(
            create_app(settings), host=settings.host, port=settings.port, log_level="warning"
        )
    )
    thread = threading.Thread(target=server.run, name="studio-server", daemon=True)
    thread.start()
    deadline = time.monotonic() + 20
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            raise SystemExit("Studio server did not start")
        time.sleep(0.1)
    print(f"AI Bridge Studio: {url}  data: {settings.data_dir}")

    if not browser:
        print("No Edge/Chrome found; opening the default browser. Ctrl+C stops Studio.")
        webbrowser.open(url)
        try:
            thread.join()
        except KeyboardInterrupt:
            server.should_exit = True
        return

    window = subprocess.Popen(window_argv(browser, url, profile))
    try:
        window.wait()
    except KeyboardInterrupt:
        window.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            window.wait(timeout=5)
    # A dedicated profile keeps the browser process alive exactly as long as the window.
    server.should_exit = True
    thread.join(timeout=15)
