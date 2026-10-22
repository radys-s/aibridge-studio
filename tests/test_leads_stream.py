"""Lead CLI adapters against stand-in CLIs that emit real-shaped streams."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from studio.agents.leads import (
    AgentEvent,
    CallContext,
    ClaudeLead,
    CodexLead,
    LeadError,
    cli_version,
    lead_env,
)
from studio.domain import LeadConfig

FAKE = Path(__file__).parent / "fake_cli"
PROBE_SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}


def shim(tmp_path: Path, name: str) -> str:
    """A launcher so shutil.which and Popen treat the fake like the real CLI."""
    script = FAKE / (name + ".py")
    if os.name == "nt":
        path = tmp_path / f"{name}.cmd"
        path.write_text(f'@"{sys.executable}" "{script}" %*\n', encoding="utf-8")
    else:
        path = tmp_path / name
        path.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
        path.chmod(0o755)
    return str(path)


def ctx(config: LeadConfig, timeout_s: int = 60, stop: bool = False) -> tuple[CallContext, list]:
    events: list[AgentEvent] = []
    return CallContext(timeout_s, lambda: stop, events.append, config=config), events


@pytest.fixture
def fake_mode(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    def set_mode(mode: str) -> None:
        monkeypatch.setenv("FAKE_MODE", mode)

    return set_mode


def test_claude_stream_usage_and_flags(tmp_path: Path) -> None:
    lead = ClaudeLead(shim(tmp_path, "fake_claude"))
    c, events = ctx(LeadConfig(model="opus", effort="xhigh"))
    out = lead.ask("plan this", PROBE_SCHEMA, tmp_path, c)
    argv = out["argv"]
    assert argv[argv.index("--model") + 1] == "opus"
    assert argv[argv.index("--effort") + 1] == "xhigh"
    assert argv[argv.index("--permission-mode") + 1] == "plan"
    assert "Edit" not in argv and "Bash" not in argv
    assert out["prompt_len"] == len("plan this")
    assert c.usage.actual_model == "resolved-opus"
    assert (c.usage.tokens_in, c.usage.tokens_out) == (115, 7)
    assert c.usage.cost_usd == pytest.approx(0.0123)
    kinds = [e.kind for e in events]
    assert kinds == ["status", "text", "tool", "tool_result"]
    assert events[2].text == "Read calc/stats.py"


def test_claude_write_mode_allows_edits_but_never_bash(tmp_path: Path) -> None:
    lead = ClaudeLead(shim(tmp_path, "fake_claude"))
    c, _ = ctx(LeadConfig())
    argv = lead.ask("do it", PROBE_SCHEMA, tmp_path, c, mode="write")["argv"]
    assert argv[argv.index("--permission-mode") + 1] == "acceptEdits"
    assert "Edit" in argv and "Write" in argv and "Bash" not in argv
    assert "--model" not in argv and "--effort" not in argv


def test_claude_expired_login_is_reported_as_such(tmp_path: Path, fake_mode) -> None:  # type: ignore[no-untyped-def]
    fake_mode("auth")
    lead = ClaudeLead(shim(tmp_path, "fake_claude"))
    with pytest.raises(LeadError) as err:
        lead.ask("x", PROBE_SCHEMA, tmp_path, ctx(LeadConfig())[0])
    assert err.value.kind == "not_logged_in"


def test_claude_cancel_kills_streaming_process(tmp_path: Path, fake_mode) -> None:  # type: ignore[no-untyped-def]
    fake_mode("slow")
    lead = ClaudeLead(shim(tmp_path, "fake_claude"))
    with pytest.raises(LeadError) as err:
        lead.ask("x", PROBE_SCHEMA, tmp_path, ctx(LeadConfig(), stop=True)[0])
    assert err.value.kind == "cancelled"
    with pytest.raises(LeadError) as err:
        lead.ask("x", PROBE_SCHEMA, tmp_path, ctx(LeadConfig(), timeout_s=2)[0])
    assert err.value.kind == "timeout"


def test_codex_stream_usage_effort_and_sandbox(tmp_path: Path) -> None:
    lead = CodexLead(shim(tmp_path, "fake_codex"))
    c, events = ctx(LeadConfig(model="gpt-5.6-sol", effort="max"))
    out = lead.ask("review", PROBE_SCHEMA, tmp_path, c)
    argv = out["argv"]
    assert argv[argv.index("-m") + 1] == "gpt-5.6-sol"
    assert "model_reasoning_effort=max" in argv
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    assert "--json" in argv and "--ignore-user-config" in argv
    assert (c.usage.tokens_in, c.usage.tokens_out) == (200, 12)
    assert c.usage.actual_model == "gpt-5.6-sol"
    assert [e.kind for e in events] == ["status", "tool", "tool_result", "text"]

    c2, _ = ctx(LeadConfig())
    argv = lead.ask("impl", PROBE_SCHEMA, tmp_path, c2, mode="write")["argv"]
    assert argv[argv.index("--sandbox") + 1] == "workspace-write"
    if os.name == "nt":  # without it Codex silently writes nothing on Windows
        assert 'windows.sandbox="elevated"' in argv


def test_codex_rejected_effort_surfaces_api_message(tmp_path: Path, fake_mode) -> None:  # type: ignore[no-untyped-def]
    fake_mode("badeffort")
    lead = CodexLead(shim(tmp_path, "fake_codex"))
    with pytest.raises(LeadError) as err:
        lead.ask("x", PROBE_SCHEMA, tmp_path, ctx(LeadConfig(effort="low"))[0])
    assert "Supported values are" in str(err.value)


def test_lead_env_drops_host_session_and_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "x")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://proxy")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    env = lead_env()
    assert not {
        "CLAUDE_CODE_SESSION_ID",
        "ANTHROPIC_BASE_URL",
        "OPENAI_API_KEY",
        "GITHUB_TOKEN",
    } & set(env)
    assert "PATH" in env or "Path" in env
    assert env.get("USERPROFILE") == os.environ.get("USERPROFILE")  # CLIs need it for login


def test_cli_version_reads_the_first_line(tmp_path: Path) -> None:
    assert cli_version(shim(tmp_path, "fake_claude")) == "2.1.0 (Claude Code)"
    assert cli_version(str(tmp_path / "missing-cli")) is None


def test_lead_env_drops_more_secret_names(monkeypatch: pytest.MonkeyPatch) -> None:
    from studio.agents.leads import lead_env

    for name in ("AWS_ACCESS_KEY_ID", "GITHUB_PAT", "DATABASE_URL", "XAI_KEY", "SENTRY_DSN"):
        monkeypatch.setenv(name, "x")
    monkeypatch.setenv("STUDIO_HARMLESS", "y")
    env = lead_env()
    assert not {"AWS_ACCESS_KEY_ID", "GITHUB_PAT", "DATABASE_URL", "XAI_KEY", "SENTRY_DSN"} & {
        k.upper() for k in env
    }
    assert env.get("STUDIO_HARMLESS") == "y" and "PATH" in {k.upper() for k in env}


def test_windows_shims_refuse_cmd_metacharacters(monkeypatch: pytest.MonkeyPatch) -> None:
    from studio.agents import leads

    monkeypatch.setattr(leads.os, "name", "nt")
    leads.check_shim_args(["claude.exe", "a&b"])  # a real executable gets argv as is
    leads.check_shim_args(["C:/npm/claude.cmd", "-p", '{"type": "object"}'])
    with pytest.raises(leads.LeadError, match="refusing"):
        leads.check_shim_args(["C:/npm/codex.CMD", "-C", "C:/x & calc"])
