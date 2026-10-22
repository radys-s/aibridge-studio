"""Read-only access to the memory folder (Markdown notes), minus anything personal.

The memory folder is where the user and their coding agents keep project state and handoffs
(an Obsidian vault works; so does any folder of Markdown files). Autopilot reads it to
continue a project. Notes may go to external models, so the policy is strict:

- only notes under the include list and not under the exclude list (folder or file prefix);
- never notes marked `private: true` or tagged `private`, nor notes whose path names an
  excluded word;
- never notes with secrets; emails and phone numbers are redacted before anything leaves;
- writes only as approved proposals: append to a project note, or a new handoff note.

Folder names come from `Layout`, configurable in `config.toml` (`[memory]`).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path, PurePosixPath

from studio.sandbox import Denied, secret_findings

MAX_NOTE_CHARS = 40_000
MAX_NOTE_BYTES = 1024 * 1024


@dataclass(frozen=True)
class Layout:
    """Which folders hold projects and handoffs, and which notes may be shared."""

    projects: str = "projects"
    handoffs: str = "handoffs"
    # Shared with models: these files/folders (prefixes), minus `exclude` and `exclude_words`.
    include: tuple[str, ...] = ("README.md", "index.md", "projects", "handoffs", "notes")
    exclude: tuple[str, ...] = ("private",)
    exclude_words: tuple[str, ...] = ("private", "password", "secret")

    @property
    def writable(self) -> tuple[str, str]:
        return (self.projects, self.handoffs)


_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"(?<![\w.:/-])(?:\+\d{3} ?)?\d{3} ?\d{3} ?\d{3}(?![\w.:/-])")
_PRIVATE_FLAG = re.compile(r"(?im)^\s*private\s*:\s*(true|yes|ano)\s*$")
_PRIVATE_TAG = re.compile(r"(?i)(?:^|[\s,\[\-#])(osobni|osobní|private)(?:$|[\s,\]])")
_STATUS_OPEN = re.compile(r"(?im)^\s*status\s*:\s*open\s*$")
_REPO_PATH = re.compile(r"(?i)(\b[A-Z]:[\\/][^\s`'\"<>|]+|(?<![\w.:/~-])/[^\s`'\"<>|]+)")


@dataclass(frozen=True)
class Note:
    path: str  # relative, forward slashes
    title: str
    text: str


@dataclass
class Vault:
    root: Path
    layout: Layout = field(default_factory=Layout)
    reads: list[str] = field(default_factory=list)

    # --- policy -----------------------------------------------------------------------

    def available(self) -> bool:
        return self.root.is_dir()

    def allowed(self, rel: str) -> bool:
        rel = rel.replace("\\", "/").strip("/")
        if not rel.lower().endswith(".md"):
            return False
        if any(_under(rel, p) for p in self.layout.exclude):
            return False
        if any(word.lower() in rel.lower() for word in self.layout.exclude_words):
            return False
        return any(_under(rel, p) for p in self.layout.include)

    def _resolve(self, raw: str) -> tuple[Path, str]:
        if not raw or not raw.strip():
            raise Denied("Missing note path.")
        candidate = Path(raw.strip().strip('"'))
        if candidate.is_absolute():
            raise Denied("Use a path relative to the vault.")
        root = self.root.resolve()
        try:
            path = (root / candidate).resolve()
            rel = PurePosixPath(path.relative_to(root)).as_posix()
        except (OSError, ValueError) as exc:
            raise Denied(f"{raw!r} is outside the vault.") from exc
        if any(part.startswith(".") for part in PurePosixPath(rel).parts):
            raise Denied(f"{raw!r} is not a note.")
        return path, rel

    # --- read -------------------------------------------------------------------------

    def _load(self, raw: str) -> tuple[str, str]:
        path, rel = self._resolve(raw)
        if not self.allowed(rel):
            raise Denied(f"{rel} is not shared with models (personal or outside the allowlist).")
        if not path.is_file():
            raise Denied(f"{rel} does not exist.")
        try:
            with path.open("rb") as fh:
                data = fh.read(MAX_NOTE_BYTES + 1)
        except OSError as exc:
            raise Denied(f"{rel} is unavailable.") from exc
        if len(data) > MAX_NOTE_BYTES:
            raise Denied(f"{rel} exceeds the 1 MiB note limit.")
        text = data.decode("utf-8", errors="replace")
        if _is_private(text):
            raise Denied(f"{rel} is marked private.")
        found = secret_findings(text)
        if found:
            raise Denied(f"{rel} appears to contain secrets ({', '.join(found)}); not sent.")
        return rel, redact(text)

    def read(self, raw: str) -> Note:
        rel, text = self._load(raw)
        self.reads.append(rel)
        return Note(rel, PurePosixPath(rel).stem, text[:MAX_NOTE_CHARS])

    def notes(self, folder: str = "") -> list[str]:
        base = self.root / folder if folder else self.root
        if not base.is_dir():
            return []
        root = self.root.resolve()
        try:
            base.resolve().relative_to(root)
        except (OSError, ValueError):
            return []
        out = []
        paths = (
            Path(directory) / name
            for directory, _, names in os.walk(base, followlinks=False)
            for name in names
            if name.lower().endswith(".md")
        )
        for path in sorted(paths):
            try:
                rel = PurePosixPath(path.resolve().relative_to(root)).as_posix()
                if not path.is_file():
                    continue
            except (OSError, ValueError):
                continue
            if not any(p.startswith(".") for p in PurePosixPath(rel).parts) and self.allowed(rel):
                out.append(rel)
        return out

    def search(self, query: str, limit: int = 15) -> list[dict[str, str]]:
        words = [w.lower() for w in query.split() if w.strip()]
        if not words:
            return []
        scored: list[tuple[int, str, str]] = []
        for rel in self.notes():
            try:
                _, text = self._load(rel)
            except (Denied, OSError):
                continue
            hay = (rel + "\n" + text).lower()
            score = sum(hay.count(w) for w in words) + 5 * sum(w in rel.lower() for w in words)
            if score and all(w in hay for w in words):
                scored.append((score, rel, _snippet(text, words[0])))
        scored.sort(key=lambda s: (-s[0], s[1]))
        return [{"path": r, "snippet": redact(s)} for _, r, s in scored[:limit]]

    def shareable(self, rel: str) -> bool:
        """Allowed by folder and not private or secret by content (names alone stay local)."""
        try:
            self._load(rel)
        except (Denied, OSError):
            return False
        return True

    def projects(self) -> list[str]:
        return [rel for rel in self.notes(self.layout.projects) if self.shareable(rel)]

    def open_handoffs(self) -> list[str]:
        out = []
        for rel in self.notes(self.layout.handoffs):
            try:
                _, head = self._load(rel)
            except (Denied, OSError):
                continue
            if _STATUS_OPEN.search(_frontmatter(head)):
                out.append(rel)
        return out

    def repo_hint(self, note: Note) -> str | None:
        """First absolute path in a project note that is an existing git repository."""
        for match in _REPO_PATH.finditer(note.text):
            candidate = Path(match.group(1).rstrip(".,;:)`"))
            if (candidate / ".git").exists():
                return str(candidate)
        return None

    def folder_counts(self) -> list[dict[str, object]]:
        if not self.available():
            return []
        out: list[dict[str, object]] = []
        for entry in sorted(self.root.iterdir()):
            if entry.name.startswith(".") or entry.name.startswith("_"):
                continue
            if entry.is_dir():
                count = sum(1 for _ in entry.rglob("*.md"))
            elif entry.suffix.lower() == ".md":
                count = 1
            else:
                continue
            out.append(
                {
                    "name": entry.name,
                    "notes": count,
                    "shared": self.allowed(f"{entry.name}/x.md")
                    if entry.is_dir()
                    else self.allowed(entry.name),
                }
            )
        return out

    def handoff_name(self, topic: str, to: str = "claude", day: str | None = None) -> str:
        safe = " ".join(re.sub(r'[\\/:*?"<>|]+', " ", topic).split())[:80] or "follow-up"
        day = day or date.today().isoformat()
        return f"{self.layout.handoffs}/{day} studio → {to} - {safe}.md"

    # --- write (only after the user approved a proposal) ------------------------------

    def write(self, rel: str, content: str, kind: str) -> str:
        path, rel = self._resolve(rel)
        writable = self.layout.writable
        if not rel.lower().endswith(".md") or not any(_under(rel, w) for w in writable):
            raise Denied(f"Studio writes only into {', '.join(writable)}.")
        found = secret_findings(content)
        if found:
            raise Denied(f"The proposed text contains secrets ({', '.join(found)}).")
        if kind == "append":
            if not path.is_file():
                raise Denied(f"{rel} does not exist; appending needs an existing note.")
            with path.open("a", encoding="utf-8", newline="\n") as fh:
                fh.write("\n\n" + content.strip() + "\n")
        elif kind == "new":
            if path.exists():
                raise Denied(f"{rel} already exists; Studio never overwrites notes.")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content.strip() + "\n", encoding="utf-8", newline="\n")
        else:
            raise Denied(f"Unknown write kind {kind!r}.")
        return rel


def redact(text: str) -> str:
    return _PHONE.sub("[telefon]", _EMAIL.sub("[e-mail]", text))


def _under(rel: str, prefix: str) -> bool:
    prefix = prefix.replace("\\", "/").strip("/")
    return rel == prefix or rel.startswith(prefix + "/")


def _frontmatter(text: str) -> str:
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    return text[3:end] if end != -1 else ""


def _is_private(text: str) -> bool:
    fm = _frontmatter(text)
    if _PRIVATE_FLAG.search(fm):
        return True
    tag_lines = []
    in_tags = False
    for line in fm.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("tags:"):
            tag_lines.append(stripped[5:])
            in_tags = True
        elif in_tags and stripped.startswith("-"):
            tag_lines.append(stripped)
        else:
            in_tags = False
    body = text.lower()
    return any(_PRIVATE_TAG.search(t) for t in tag_lines) or "#osobni" in body or "#osobní" in body


def _snippet(text: str, word: str) -> str:
    i = text.lower().find(word)
    start = max(0, i - 80) if i >= 0 else 0
    return " ".join(text[start : start + 240].split())
