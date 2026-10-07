"""File tools shared by the worker and Autopilot: OpenAI function specs + sandboxed dispatch.

Every path goes through `Sandbox` (working copy only, no credential files, scope for writes).
"""

from __future__ import annotations

import re
from typing import Any

from studio.sandbox import Denied, Sandbox, clip

MAX_GREP_HITS = 60


def spec(name: str, description: str, props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": props, "required": required},
        },
    }


_S = {"type": "string"}

LIST_FILES = spec(
    "list_files",
    "List files under a directory of the repository (relative path).",
    {"path": {"type": "string", "description": "e.g. '.' or 'src'"}},
    ["path"],
)
READ_FILE = spec(
    "read_file", "Read a UTF-8 text file (relative path, forward slashes).", {"path": _S}, ["path"]
)
GREP = spec(
    "grep",
    "Search repository text files for a regular expression; returns path:line: text.",
    {"pattern": _S, "path": {"type": "string", "description": "directory, default '.'"}},
    ["pattern"],
)
WRITE_FILE = spec(
    "write_file",
    "Create or overwrite a file with the complete new content.",
    {"path": _S, "content": _S},
    ["path", "content"],
)
REPLACE_IN_FILE = spec(
    "replace_in_file",
    "Replace one exact, unique occurrence of old_text with new_text.",
    {"path": _S, "old_text": _S, "new_text": _S},
    ["path", "old_text", "new_text"],
)
RUN_COMMAND = spec(
    "run_command",
    "Run one allowlisted command (no shell, no pipes), e.g. "
    "'python -m unittest -q'. Returns exit code and output.",
    {"command": _S},
    ["command"],
)
FINISH = spec(
    "finish", "Call when the change is complete and checks pass.", {"summary": _S}, ["summary"]
)

READ_TOOLS = [LIST_FILES, READ_FILE, GREP]
WRITE_TOOLS = [WRITE_FILE, REPLACE_IN_FILE]


class FileTools:
    """Dispatch for the file tools. Unknown names return None so callers add their own."""

    def __init__(self, sandbox: Sandbox) -> None:
        self.sb = sandbox
        self.files_written: list[str] = []

    def call(self, name: str, args: dict[str, Any]) -> str | None:
        sb = self.sb
        try:
            if name == "list_files":
                return "\n".join(sb.list_files(str(args.get("path") or "."))) or "(empty)"
            if name == "read_file":
                return clip(sb.read_text(str(args["path"])))
            if name == "grep":
                return self._grep(str(args["pattern"]), str(args.get("path") or "."))
            if name == "write_file":
                path = sb.check_writable(str(args["path"]))
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(str(args["content"]), encoding="utf-8", newline="")
                self.files_written.append(sb.rel(path))
                return f"wrote {sb.rel(path)} ({len(str(args['content']))} chars)"
            if name == "replace_in_file":
                path = sb.check_writable(str(args["path"]))
                text = sb.read_text(str(args["path"]))
                old = str(args["old_text"])
                count = text.count(old) if old else 0
                if count != 1:
                    return f"error: old_text found {count} times; it must match exactly once"
                path.write_text(
                    text.replace(old, str(args["new_text"])), encoding="utf-8", newline=""
                )
                self.files_written.append(sb.rel(path))
                return f"replaced in {sb.rel(path)}"
            return None
        except Denied as exc:
            return f"denied: {exc}"
        except KeyError as exc:
            return f"error: missing argument {exc}"
        except OSError as exc:
            return f"error: {exc}"

    def _grep(self, pattern: str, path: str) -> str:
        try:
            rx = re.compile(pattern)
        except re.error as exc:
            return f"error: bad pattern: {exc}"
        hits: list[str] = []
        for rel in self.sb.list_files(path):
            try:
                text = self.sb.read_text(rel)
            except (Denied, OSError):
                continue
            for no, line in enumerate(text.splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{rel}:{no}: {line.strip()[:200]}")
                    if len(hits) >= MAX_GREP_HITS:
                        return "\n".join(hits) + "\n[more hits clipped]"
        return "\n".join(hits) or "(no matches)"
