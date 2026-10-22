"""The external-agent terminal opens on every platform, or says what to run."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from studio.external import TerminalError, terminal_argv


def test_macos_uses_terminal_app(tmp_path: Path) -> None:
    argv = terminal_argv(tmp_path / 'my "repo"', "freebuff", platform="darwin")
    assert argv[0] == "osascript" and "freebuff" in argv[2] and '\\"repo\\"' in argv[2]


def test_linux_uses_an_installed_terminal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda n: "/usr/bin/xterm" if n == "xterm" else None)
    argv = terminal_argv(tmp_path, "freebuff", platform="linux")
    assert argv[0] == "/usr/bin/xterm" and argv[-1].endswith("freebuff; exec $SHELL")


def test_no_terminal_names_the_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda n: None)
    with pytest.raises(TerminalError, match="freebuff"):
        terminal_argv(tmp_path, "freebuff", platform="linux")
