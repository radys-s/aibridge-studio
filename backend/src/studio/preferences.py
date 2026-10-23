"""User preferences stored in Studio's own database: defaults for new tasks, recent repos.

The router picks Autopilot's model from the configured pool (`config.py`).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, ValidationError

from studio.db import Database
from studio.domain import AGENT_ROLES, CLI_LEADS, Escalation, Implementer, Orchestration
from studio.messages import Language


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
        try:
            return Defaults.model_validate(raw)
        except ValidationError:  # saved by an older version with other rules
            return Defaults()

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

    def language(self) -> Language | None:
        """Language for new event text; None until the user or the first browser picks one."""
        value = self.db.setting("language")
        return "cs" if value == "cs" else "en" if value == "en" else None

    def remember_language(self, language: Language) -> None:
        self.db.set_setting("language", language)

    def enabled_providers(self) -> list[str]:
        return [str(n) for n in self.db.setting("enabled_providers") or []]

    def remember_enabled_providers(self, names: list[str]) -> None:
        self.db.set_setting("enabled_providers", names)

    def agents(self) -> dict[str, dict[str, bool]]:
        """Which roles each CLI lead may take. Everything is off until the user turns it on."""
        stored = self.db.setting("agents") or {}
        return {
            name: {role: bool((stored.get(name) or {}).get(role)) for role in AGENT_ROLES}
            for name in CLI_LEADS
        }

    def remember_agent(self, name: str, roles: dict[str, bool]) -> dict[str, dict[str, bool]]:
        agents = self.agents()
        agents[name].update({r: bool(v) for r, v in roles.items() if r in AGENT_ROLES})
        self.db.set_setting("agents", agents)
        return agents

    def snapshot(self) -> dict[str, Any]:
        return {
            "defaults": self.defaults().model_dump(mode="json"),
            "recent_repos": self.recent_repos(),
            "last_project": self.last_project(),
            "memory_path": self.memory_path(),
            "language": self.language(),
        }
