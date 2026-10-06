"""Stand-in for `claude -p --output-format stream-json`. Event shapes copied from a real
claude 2.1.269 run (system/init, assistant, user tool_result, result).

FAKE_MODE: ok (default) | auth (expired login) | slow. The structured output echoes the
argv so tests can check model, effort and permission flags.
"""

import json
import os
import sys
import time

mode = os.environ.get("FAKE_MODE", "ok")
prompt = sys.stdin.read()
argv = sys.argv[1:]


def emit(obj: dict) -> None:
    print(json.dumps(obj), flush=True)


model = argv[argv.index("--model") + 1] if "--model" in argv else "claude-default"
emit({"type": "system", "subtype": "init", "model": f"resolved-{model}", "session_id": "s"})
if mode == "slow":
    time.sleep(30)
if mode == "auth":
    msg = "Failed to authenticate: OAuth session expired and could not be refreshed"
    emit({"type": "assistant", "message": {"content": [{"type": "text", "text": msg}]}})
    emit({"type": "result", "subtype": "success", "is_error": True, "result": msg})
    sys.exit(1)
emit(
    {
        "type": "assistant",
        "message": {
            "content": [
                {"type": "text", "text": "Looking at the repo."},
                {"type": "tool_use", "name": "Read", "input": {"file_path": "calc/stats.py"}},
            ]
        },
    }
)
emit({"type": "user", "message": {"content": [{"type": "tool_result", "content": "def mean"}]}})
emit(
    {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "done",
        "structured_output": {"ok": True, "argv": argv, "prompt_len": len(prompt)},
        "usage": {
            "input_tokens": 10,
            "cache_creation_input_tokens": 5,
            "cache_read_input_tokens": 100,
            "output_tokens": 7,
        },
        "total_cost_usd": 0.0123,
        "modelUsage": {f"resolved-{model}": {}},
    }
)
