"""User preferences stored in Studio's own database: defaults for new tasks, recent repos.

The router picks Autopilot's model from the configured pool (`config.py`).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from studio.db import Database
from studio.domain import Escalation, Implementer, Orchestration


class Defaults(BaseModel):
    orchestration: Orchestration = Field(default_factory=lambda: Orchestration())
    implementer: Implementer = "auto"
    escalation: Escalation = "ask"
    auto_approve: bool = False


class Preferences:
    def __init__(self, db: Database) -> None:
        self.db = db

    def defaults(self) -> Defaults:
        raw = self.db.setting("defaults")
        if not raw:
            return Defaults()
        raw = dict(raw)
        if raw.get("implementer") in ("claude", "codex"):  # stored before ADR 0004
            raw["implementer"] = "auto"
        return Defaults.model_validate(raw)

    def save(self, defaults: Defaults) -> None:
        self.db.set_setting("defaults", defaults.model_dump(mode="json"))

    def recent_repos(self) -> list[str]:
        return list(self.db.setting("recent_repos") or [])

    def remember_repo(self, path: str) -> None:
        repos = [path, *[r for r in self.recent_repos() if r.lower() != path.lower()]]
        self.db.set_setting("recent_repos", repos[:12])

    def last_project(self) -> str | None:
        value = self.db.setting("last_project")
        return str(value) if value else None

    def remember_project(self, note: str) -> None:
        self.db.set_setting("last_project", note)

    def memory_path(self) -> str | None:
        value = self.db.setting("memory_path")
        return str(value) if value else None

    def remember_memory_path(self, path: str | None) -> None:
        self.db.set_setting("memory_path", path)

    def enabled_providers(self) -> list[str]:
        return [str(n) for n in self.db.setting("enabled_providers") or []]

    def remember_enabled_providers(self, names: list[str]) -> None:
        self.db.set_setting("enabled_providers", names)

    def snapshot(self) -> dict[str, Any]:
        return {
            "defaults": self.defaults().model_dump(mode="json"),
            "recent_repos": self.recent_repos(),
            "last_project": self.last_project(),
            "memory_path": self.memory_path(),
        }
