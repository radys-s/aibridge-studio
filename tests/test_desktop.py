"""`studio app` window helpers (no window is opened here)."""

from __future__ import annotations

from pathlib import Path

from studio.desktop import find_browser, window_argv


def test_window_is_an_isolated_app_window(tmp_path: Path) -> None:
    argv = window_argv("msedge.exe", "http://127.0.0.1:8765", tmp_path / "ui-profile")
    assert argv[0] == "msedge.exe"
    assert "--app=http://127.0.0.1:8765" in argv
    # Own profile: no user cookies or extensions, and the process lives as long as the window.
    assert f"--user-data-dir={tmp_path / 'ui-profile'}" in argv
    assert "--disable-extensions" in argv


def test_find_browser_returns_an_existing_file_or_none() -> None:
    found = find_browser()
    assert found is None or Path(found).is_file()
