from pathlib import Path
from typing import Any

import pytest

from studio.launcher import LaunchError, OmniRouteLauncher


class Child:
    def __init__(self) -> None:
        self.code: int | None = None

    def poll(self) -> int | None:
        return self.code


class FakeProcess:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict[str, Any]]] = []
        self.children: list[Child] = []

    def __call__(self, args: list[str], **kw: Any) -> Child:
        self.calls.append((args, kw))
        self.children.append(Child())
        return self.children[-1]


def launcher(
    tmp_path: Path,
    up: list[bool],
    url: str = "http://127.0.0.1:20128",
    binary: str | None = "C:/npm/omniroute.cmd",
) -> tuple[OmniRouteLauncher, FakeProcess]:
    proc = FakeProcess()
    answers = iter(up)
    return (
        OmniRouteLauncher(
            url,
            tmp_path,
            reachable=lambda: next(answers, up[-1]),
            which=lambda _: binary,
            popen=proc,
            sleep=lambda _: None,
        ),
        proc,
    )


def test_starts_the_fixed_command_once_and_waits_until_it_answers(tmp_path: Path) -> None:
    lch, proc = launcher(tmp_path, [False, False, True])
    assert lch.start() == {"started": True, "reachable": True}
    assert [args for args, _ in proc.calls] == [
        ["C:/npm/omniroute.cmd", "serve", "--port", "20128", "--no-open"]
    ]
    assert "PORT" not in proc.calls[0][1]["env"]
    assert "shell" not in proc.calls[0][1]
    assert (tmp_path / "omniroute.log").exists()
    # A second click while that server still boots does not spawn another one.
    lch2_answers = [False, True]
    lch.reachable = lambda: lch2_answers.pop(0) if lch2_answers else True
    assert lch.start() == {"started": False, "reachable": True}
    assert len(proc.calls) == 1


def test_nothing_starts_when_it_already_runs(tmp_path: Path) -> None:
    lch, proc = launcher(tmp_path, [True])
    assert lch.start() == {"started": False, "reachable": True}
    assert not proc.calls


def test_remote_gateway_or_missing_binary_is_not_started(tmp_path: Path) -> None:
    lch, proc = launcher(tmp_path, [False], url="http://10.0.0.5:20128")
    assert lch.binary() is None
    with pytest.raises(LaunchError):
        lch.start()
    lch, proc = launcher(tmp_path, [False], binary=None)
    with pytest.raises(LaunchError):
        lch.start()
    assert not proc.calls


def test_a_server_that_exited_is_started_again(tmp_path: Path) -> None:
    lch, proc = launcher(tmp_path, [False, True])
    lch.start()
    proc.children[0].code = 1  # crashed
    answers = [False, True]
    lch.reachable = lambda: answers.pop(0) if answers else True
    assert lch.start()["started"] is True
    assert len(proc.calls) == 2


def test_bad_port_is_a_launch_error(tmp_path: Path) -> None:
    lch, proc = launcher(tmp_path, [False], url="http://127.0.0.1:99999")
    with pytest.raises(LaunchError):
        lch.start()
    assert not proc.calls


def test_binary_lookup_ignores_relative_path_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from studio.launcher import which_on_path

    (tmp_path / "omniroute.cmd").write_text("@echo planted", encoding="utf-8")
    (tmp_path / "omniroute").write_text("planted", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", "." + __import__("os").pathsep)
    assert which_on_path("omniroute") is None
