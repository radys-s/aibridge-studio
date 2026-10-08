"""Worker context trimming: old bulky tool output is shortened before it is re-sent."""

from __future__ import annotations

import json
from typing import Any

from studio.worker.loop import KEEP_RECENT_TOOL_RESULTS, compact


def _turn(i: int, result: str, content: str = "", name: str = "") -> list[dict[str, Any]]:
    args = json.dumps({"path": f"f{i}.py", "content": content} if content else {"path": f"f{i}"})
    name = name or ("write_file" if content else "read_file")
    return [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": f"c{i}", "function": {"name": name, "arguments": args}}],
        },
        {"role": "tool", "tool_call_id": f"c{i}", "content": result},
    ]


def _history(
    turns: int, result: str = "x" * 5000, content: str = "", name: str = "run_command"
) -> list[dict[str, Any]]:
    msgs: list[dict[str, Any]] = [
        {"role": "system", "content": "S" * 3000},
        {"role": "user", "content": "Start."},
    ]
    for i in range(turns):
        msgs += _turn(i, result, content, "" if content else name)
    return msgs


def test_short_history_is_sent_unchanged() -> None:
    msgs = _history(KEEP_RECENT_TOOL_RESULTS)
    assert compact(msgs) is msgs


def test_old_tool_results_are_stubbed_and_recent_ones_kept() -> None:
    msgs = _history(KEEP_RECENT_TOOL_RESULTS + 4)
    sent = compact(msgs)
    tools = [m for m in sent if m["role"] == "tool"]
    assert all("older result trimmed, 5000 chars in total" in m["content"] for m in tools[:4])
    assert all(m["content"] == "x" * 5000 for m in tools[4:])
    # System prompt and goal are never trimmed; ids stay paired; the original is untouched.
    assert sent[0]["content"] == "S" * 3000 and sent[1]["content"] == "Start."
    assert [m["tool_call_id"] for m in tools] == [f"c{i}" for i in range(len(tools))]
    assert all(m["content"] == "x" * 5000 for m in msgs if m["role"] == "tool")
    assert len(json.dumps(sent)) < len(json.dumps(msgs)) * 0.75


def test_old_write_arguments_shrink_but_stay_valid_json() -> None:
    msgs = _history(KEEP_RECENT_TOOL_RESULTS + 1, result="ok", content="y" * 4000)
    sent = compact(msgs)
    first = json.loads(sent[2]["tool_calls"][0]["function"]["arguments"])
    assert first == {"path": "f0.py", "content": "<4000 chars written; read the file to see it>"}
    # The assistant turn that issued the oldest kept result is untouched (boundary).
    boundary = json.loads(sent[4]["tool_calls"][0]["function"]["arguments"])
    assert boundary["content"] == "y" * 4000


def test_small_old_results_are_kept_verbatim() -> None:
    msgs = _history(KEEP_RECENT_TOOL_RESULTS + 3, result="exit 0")
    assert [m["content"] for m in compact(msgs) if m["role"] == "tool"] == ["exit 0"] * 9


def test_failed_command_keeps_its_tail() -> None:
    out = "collecting...\n" + "." * 3000 + "\nFAILED tests/test_x.py::test_y - AssertionError"
    sent = compact(_history(KEEP_RECENT_TOOL_RESULTS + 1, result=out))
    assert sent[3]["content"].endswith("FAILED tests/test_x.py::test_y - AssertionError")
    assert "trimmed" in sent[3]["content"]


def test_latest_read_of_each_file_is_kept() -> None:
    msgs = _history(1, name="read_file")  # read f0, 5000 chars
    for i in range(1, KEEP_RECENT_TOOL_RESULTS + 3):
        msgs += _turn(i, "y" * 5000, name="run_command")
    sent = compact(msgs)
    assert sent[3]["content"] == "x" * 5000  # still the latest read of f0
    assert "trimmed" in sent[5]["content"]  # an old command output is stubbed


def test_replace_in_file_arguments_are_never_trimmed() -> None:
    msgs = _history(KEEP_RECENT_TOOL_RESULTS + 2, result="ok", name="replace_in_file")
    big = json.dumps({"path": "a.py", "old": "o" * 2000, "new": "n" * 2000})
    msgs[2]["tool_calls"][0]["function"]["arguments"] = big
    assert compact(msgs)[2]["tool_calls"][0]["function"]["arguments"] == big
