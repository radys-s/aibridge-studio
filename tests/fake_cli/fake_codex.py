"""Stand-in for `codex exec --json`. Event shapes copied from a real codex 0.144.6 run
(thread.started, item.completed agent_message/error, turn.completed with usage, turn.failed).

FAKE_MODE: ok (default) | badeffort (API rejects the reasoning effort).
"""

import json
import os
import sys

mode = os.environ.get("FAKE_MODE", "ok")
prompt = sys.stdin.read()
argv = sys.argv[1:]


def emit(obj: dict) -> None:
    print(json.dumps(obj), flush=True)


emit({"type": "thread.started", "thread_id": "t"})
emit({"type": "turn.started"})
emit(
    {
        "type": "item.completed",
        "item": {"id": "i0", "type": "error", "message": "Skill list trimmed"},
    }
)
if mode == "badeffort":
    err = json.dumps(
        {
            "type": "error",
            "error": {"message": "Invalid value: 'bogus'. Supported values are: 'low'."},
        }
    )
    emit({"type": "error", "message": err})
    emit({"type": "turn.failed", "error": {"message": err}})
    sys.exit(1)
emit({"type": "item.started", "item": {"id": "i1", "type": "command_execution", "command": "ls"}})
emit(
    {
        "type": "item.completed",
        "item": {
            "id": "i1",
            "type": "command_execution",
            "aggregated_output": "calc",
            "exit_code": 0,
        },
    }
)
emit({"type": "item.completed", "item": {"id": "i2", "type": "agent_message", "text": "{...}"}})
emit(
    {
        "type": "turn.completed",
        "usage": {
            "input_tokens": 200,
            "cached_input_tokens": 50,
            "output_tokens": 9,
            "reasoning_output_tokens": 3,
        },
    }
)
out = argv[argv.index("--output-last-message") + 1]
with open(out, "w", encoding="utf-8") as f:
    json.dump({"ok": True, "argv": argv, "prompt_len": len(prompt)}, f)
