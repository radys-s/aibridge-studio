"""Read-only access to the shared Obsidian memory, minus anything personal.

The vault is where Claude, Codex and Tomáš keep project state and handoffs. Autopilot reads
it to continue a project after the subscription limits run out. Notes go to external free
models, so the policy is strict:

- only notes under the include list and not under the exclude list (folder or file prefix);
- never notes marked `private: true` or tagged `osobni`/`private`, nor notes whose path
  names an excluded word (Recorder);
- never notes with secrets; emails and phone numbers are redacted before anything leaves;
- writes only as approved proposals: append to a project note, or a new handoff note.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path, PurePosixPath

from studio.sandbox import Denied, secret_findings

MAX_NOTE_CHARS = 40_000

DEFAULT_INCLUDE = (
    "00 Rozcestník.md",
    "Pravidla pro asistenta.md",
    "02 Projekty",
    "05 Učení",
    "06 Zdroje",
    "08 Předávání",
    "09 Šablony",
    "11 Technologie",
    "12 Architektura",
)
DEFAULT_EXCLUDE = (
    "01 Profil",
    "03 Počítač",
    "04 Hardware",
    "07 Claude",
    "10 Chaty",
    "13 Časová osa",
)
# Notes whose path names these never leave (Recorder data must not reach models).
DEFAULT_EXCLUDE_WORDS = ("recorder",)
PROJECTS = "02 Projekty"
HANDOFFS = "08 Předávání"
WRITABLE = (PROJECTS, HANDOFFS)

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"(?<![\w.:/-])(?:\+\d{3} ?)?\d{3} ?\d{3} ?\d{3}(?![\w.:/-])")
_PRIVATE_FLAG = re.compile(r"(?im)^\s*private\s*:\s*(true|yes|ano)\s*$")
_PRIVATE_TAG = re.compile(r"(?i)(?:^|[\s,\[\-#])(osobni|osobní|private)(?:$|[\s,\]])")
_STATUS_OPEN = re.compile(r"(?im)^\s*status\s*:\s*open\s*$")
_REPO_PATH = re.compile(r"(?i)\b([A-Z]:[\\/][^\s`'\"<>|]+)")


@dataclass(frozen=True)
class Note:
    path: str  # relative, forward slashes
    title: str
    text: str


@dataclass
class Vault:
    root: Path
    include: tuple[str, ...] = DEFAULT_INCLUDE
    exclude: tuple[str, ...] = DEFAULT_EXCLUDE
    exclude_words: tuple[str, ...] = DEFAULT_EXCLUDE_WORDS
    reads: list[str] = field(default_factory=list)

    # --- policy -----------------------------------------------------------------------

    def available(self) -> bool:
        return self.root.is_dir()

    def allowed(self, rel: str) -> bool:
        rel = rel.replace("\\", "/").strip("/")
        if not rel.lower().endswith(".md"):
            return False
        if any(_under(rel, p) for p in self.exclude):
            return False
        if any(word in rel.lower() for word in self.exclude_words):
            return False
        return any(_under(rel, p) for p in self.include)

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

    def read(self, raw: str) -> Note:
        path, rel = self._resolve(raw)
        if not self.allowed(rel):
            raise Denied(f"{rel} is not shared with models (personal or outside the allowlist).")
        if not path.is_file():
            raise Denied(f"{rel} does not exist.")
        text = path.read_text(encoding="utf-8", errors="replace")[:MAX_NOTE_CHARS]
        if _is_private(text):
            raise Denied(f"{rel} is marked private.")
        found = secret_findings(text)
        if found:
            raise Denied(f"{rel} appears to contain secrets ({', '.join(found)}); not sent.")
        self.reads.append(rel)
        return Note(rel, PurePosixPath(rel).stem, redact(text))

    def notes(self, folder: str = "") -> list[str]:
        base = self.root / folder if folder else self.root
        if not base.is_dir():
            return []
        root = self.root.resolve()
        out = []
        for path in sorted(base.rglob("*.md")):
            rel = PurePosixPath(path.resolve().relative_to(root)).as_posix()
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
                text = (self.root / rel).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if _is_private(text) or secret_findings(text[:MAX_NOTE_CHARS]):
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
            path, rel = self._resolve(rel)
            if not self.allowed(rel):
                return False
            text = path.read_text(encoding="utf-8", errors="replace")
        except (Denied, OSError):
            return False
        return not _is_private(text) and not secret_findings(text)

    def projects(self) -> list[str]:
        return [rel for rel in self.notes(PROJECTS) if self.shareable(rel)]

    def open_handoffs(self) -> list[str]:
        out = []
        for rel in self.notes(HANDOFFS):
            try:
                head = (self.root / rel).read_text(encoding="utf-8", errors="replace")[:1500]
            except OSError:
                continue
            if _STATUS_OPEN.search(_frontmatter(head)):
                out.append(rel)
        return out

    def repo_hint(self, note: Note) -> str | None:
        """First Windows path in a project note that is an existing git repository."""
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

    # --- write (only after the user approved a proposal) ------------------------------

    def write(self, rel: str, content: str, kind: str) -> str:
        path, rel = self._resolve(rel)
        if not rel.lower().endswith(".md") or not any(_under(rel, w) for w in WRITABLE):
            raise Denied(f"Studio writes only into {', '.join(WRITABLE)}.")
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


def handoff_name(topic: str, to: str = "claude") -> str:
    safe = " ".join(re.sub(r'[\\/:*?"<>|]+', " ", topic).split())[:80] or "pokračování"
    return f"{HANDOFFS}/{date.today().isoformat()} studio → {to} - {safe}.md"


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
