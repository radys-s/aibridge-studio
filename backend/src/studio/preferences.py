"""User preferences stored in Studio's own database: defaults for new tasks and model catalog.

The catalog lists what the UI offers. It is not proof that a model works with the user's
login; `probe` results (recorded per model) are.
"""

from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, Field

from studio.db import Database
from studio.domain import (
    EFFORTS,
    HEAVY_EFFORTS,
    Escalation,
    Implementer,
    LeadConfig,
    Orchestration,
)


class CatalogModel(BaseModel):
    id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9._:/\[\]-]+$")
    label: str = ""


class Defaults(BaseModel):
    orchestration: Orchestration = Field(
        default_factory=lambda: Orchestration(
            mode="cross",
            primary="claude",
            claude=LeadConfig(model="opus", effort="high"),
            codex=LeadConfig(model="gpt-5.6-sol", effort="medium"),
        )
    )
    implementer: Implementer = "auto"
    escalation: Escalation = "ask"
    auto_approve: bool = False


class Catalog(BaseModel):
    claude: list[CatalogModel] = Field(
        default_factory=lambda: [
            CatalogModel(id="opus", label="Opus — nejsilnější"),
            CatalogModel(id="sonnet", label="Sonnet — rychlý a silný"),
            CatalogModel(id="fable", label="Fable"),
            CatalogModel(id="haiku", label="Haiku — nejlevnější"),
        ]
    )
    codex: list[CatalogModel] = Field(
        # gpt-5.6-sol: verified with the ChatGPT login in `codex exec` on 2026-10-06.
        default_factory=lambda: [CatalogModel(id="gpt-5.6-sol", label="GPT-5.6 Sol")]
    )


class Preferences:
    def __init__(self, db: Database, codex_model: str | None = None) -> None:
        self.db = db
        self.codex_model = codex_model

    def defaults(self) -> Defaults:
        raw = self.db.setting("defaults")
        if raw:
            return Defaults.model_validate(raw)
        d = Defaults()
        if self.codex_model:
            d.orchestration.codex.model = self.codex_model
        return d

    def catalog(self) -> Catalog:
        raw = self.db.setting("catalog")
        return Catalog.model_validate(raw) if raw else Catalog()

    def save(self, defaults: Defaults | None, catalog: Catalog | None) -> None:
        if defaults is not None:
            self.db.set_setting("defaults", defaults.model_dump(mode="json"))
        if catalog is not None:
            self.db.set_setting("catalog", catalog.model_dump(mode="json"))

    def recent_repos(self) -> list[str]:
        return list(self.db.setting("recent_repos") or [])

    def remember_repo(self, path: str) -> None:
        repos = [path, *[r for r in self.recent_repos() if r.lower() != path.lower()]]
        self.db.set_setting("recent_repos", repos[:12])

    def probes(self) -> dict[str, Any]:
        return dict(self.db.setting("probes") or {})

    def record_probe(self, key: str, result: dict[str, Any]) -> None:
        probes = self.probes()
        probes[key] = {**result, "at": time.time()}
        self.db.set_setting("probes", probes)

    def snapshot(self) -> dict[str, Any]:
        return {
            "defaults": self.defaults().model_dump(mode="json"),
            "catalog": self.catalog().model_dump(mode="json"),
            "efforts": {
                lead: [{"id": e, "heavy": e in HEAVY_EFFORTS} for e in values]
                for lead, values in EFFORTS.items()
            },
            "recent_repos": self.recent_repos(),
            "probes": self.probes(),
        }
