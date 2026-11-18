"""Read-only local personal search. Nothing here calls a model or writes the vault."""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from studio.sandbox import Denied, secret_findings
from studio.vault import MAX_NOTE_BYTES, _is_private

DEFAULT_PERSONAL_EXCLUSIONS = ("AI paměť/10 Chaty", "10 Chaty", "AI paměť/07 Claude", "07 Claude")
_FORBIDDEN = (
    "recorder",
    "credential",
    "secret",
    "password",
    "hesla",
    "api-key",
    "api_key",
    "token",
    "attachment",
    "příloh",
    "priloh",
)
MAX_INDEX_BYTES = 32 * 1024 * 1024
MAX_INDEX_NOTES = 10_000


@dataclass(frozen=True)
class PersonalNote:
    path: str
    text: str
    digest: str


class PersonalVault:
    def __init__(
        self, root: Path, exclusions: tuple[str, ...] = DEFAULT_PERSONAL_EXCLUSIONS
    ) -> None:
        self.root = root
        self.exclusions = exclusions
        self.enabled = False
        self._lock = threading.RLock()
        self._index = sqlite3.connect(":memory:", check_same_thread=False)
        self._index.execute("PRAGMA temp_store=MEMORY")
        self._index.execute(
            "CREATE VIRTUAL TABLE notes USING fts5(path, text, digest UNINDEXED, "
            "tokenize='unicode61 remove_diacritics 2')"
        )
        self._stamps: dict[str, tuple[int, int]] = {}
        self._bytes: dict[str, int] = {}

    def configure(self, exclusions: tuple[str, ...], *, enabled: bool) -> None:
        if len(exclusions) > 100:
            raise Denied("Too many vault exclusions.")
        if any(not item.strip() or len(item) > 300 for item in exclusions):
            raise Denied("Invalid vault exclusion.")
        with self._lock:
            self.exclusions = exclusions
            self.enabled = enabled
            self._index.execute("DELETE FROM notes")
            self._stamps.clear()
            self._bytes.clear()

    def _allowed(self, rel: str) -> bool:
        parts = PurePosixPath(rel).parts
        folded = rel.casefold()
        if not folded.endswith(".md") or any(p.startswith(".") for p in parts):
            return False
        if any(word in folded for word in _FORBIDDEN) or "10 chaty" in folded:
            return False
        for exclusion in self.exclusions:
            prefix = exclusion.replace("\\", "/").strip("/").casefold()
            if folded == prefix or folded.startswith(prefix + "/"):
                return False
        return True

    def _directory_allowed(self, rel: str) -> bool:
        """Check folders without a suffix; mandatory exclusions cannot be removed."""
        return self._allowed(rel.rstrip("/") + "/placeholder.md")

    def _refresh(self) -> None:
        seen: set[str] = set()
        for directory, folders, names in os.walk(self.root, followlinks=False):
            folders[:] = [
                folder
                for folder in folders
                if self._directory_allowed(
                    (Path(directory) / folder).relative_to(self.root).as_posix()
                )
                and not (Path(directory) / folder).is_symlink()
            ]
            for name in names:
                path = Path(directory) / name
                rel = path.relative_to(self.root).as_posix()
                if not self._allowed(rel):
                    continue
                seen.add(rel)
                try:
                    stat = path.stat()
                    stamp = (stat.st_mtime_ns, stat.st_size)
                    if self._stamps.get(rel) == stamp:
                        continue
                    note = self.read(rel)
                    size = len(note.text.encode("utf-8"))
                    self._index.execute("DELETE FROM notes WHERE path=?", (rel,))
                    self._stamps.pop(rel, None)
                    self._bytes.pop(rel, None)
                    if (
                        len(self._stamps) >= MAX_INDEX_NOTES
                        or sum(self._bytes.values()) + size > MAX_INDEX_BYTES
                    ):
                        continue
                    self._index.execute(
                        "INSERT INTO notes VALUES (?, ?, ?)", (rel, note.text, note.digest)
                    )
                    self._stamps[rel] = stamp
                    self._bytes[rel] = size
                except (Denied, OSError, ValueError):
                    self._index.execute("DELETE FROM notes WHERE path=?", (rel,))
                    self._stamps.pop(rel, None)
                    self._bytes.pop(rel, None)
        for removed in set(self._stamps) - seen:
            self._index.execute("DELETE FROM notes WHERE path=?", (removed,))
            self._stamps.pop(removed, None)
            self._bytes.pop(removed, None)

    def read(self, rel: str) -> PersonalNote:
        if not self.enabled:
            raise Denied("Approve vault exclusions before enabling personal search.")
        raw = Path(rel)
        if raw.is_absolute() or ".." in raw.parts:
            raise Denied("Use a vault-relative note path.")
        try:
            root = self.root.resolve()
            path = (root / raw).resolve()
            normalized = path.relative_to(root).as_posix()
            if not self._allowed(normalized):
                raise Denied("This note is excluded from personal search.")
            with path.open("rb") as stream:
                data = stream.read(MAX_NOTE_BYTES + 1)
        except (OSError, ValueError) as exc:
            raise Denied("Note is unavailable or outside the vault.") from exc
        if len(data) > MAX_NOTE_BYTES:
            raise Denied("Note exceeds the 1 MiB limit.")
        text = data.decode("utf-8", errors="replace")
        if _is_private(text) or "#private" in text.casefold():
            raise Denied("Private-marked notes require separate permission; sharing is disabled.")
        if secret_findings(text):
            raise Denied("Note contains possible secrets and cannot be shared.")
        return PersonalNote(normalized, text, hashlib.sha256(data).hexdigest())

    def search(self, query: str, limit: int = 15) -> list[dict[str, str]]:
        if not self.enabled:
            raise Denied("Approve vault exclusions before enabling personal search.")
        if not query.strip() or len(query) > 2000 or secret_findings(query):
            raise Denied("Search query is empty, too long or contains possible secrets.")
        words = re.findall(r"\w+", query, re.UNICODE)[:30]
        if not words:
            return []
        expression = " AND ".join('"' + word + '"' for word in words)
        with self._lock:
            self._refresh()
            rows = self._index.execute(
                "SELECT path, snippet(notes, 1, '', '', '…', 45), digest "
                "FROM notes WHERE notes MATCH ? ORDER BY bm25(notes), path LIMIT ?",
                (expression, max(0, min(limit, 50))),
            ).fetchall()
            # Re-read only hits so content edits/private flags never expose stale snippets.
            result: list[dict[str, str]] = []
            for path, snippet, digest in rows:
                try:
                    note = self.read(path)
                    if note.digest == digest:
                        result.append({"path": path, "snippet": snippet[:400], "hash": digest})
                except Denied:
                    continue
            return result
