"""Runtime configuration. Everything comes from environment variables with safe defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _default_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / "aibridge-studio"


@dataclass(frozen=True)
class ModelSpec:
    """One explicit OmniRoute model id the router may choose."""

    id: str
    capabilities: frozenset[str]
    strength: int = 1  # higher = stronger; used to order candidates for hard tasks
    free: bool = True


# Explicit model ids only; never `auto/*` combos (see docs/ARCHITECTURE.md).
# Verified tool-calling through OmniRoute on 2026-10-06: laguna-s-2.1.
# `lead` = may act as Autopilot (draft, plan, review). Review needs a second lead model,
# different from the implementer, so keep at least two.
DEFAULT_MODELS: tuple[ModelSpec, ...] = (
    ModelSpec("openrouter/poolside/laguna-s-2.1:free", frozenset({"coding", "tools", "lead"}), 3),
    ModelSpec("openrouter/cohere/north-mini-code:free", frozenset({"coding", "tools"}), 2),
    ModelSpec("openrouter/nvidia/nemotron-3-ultra-550b-a55b:free", frozenset({"tools", "lead"}), 4),
    ModelSpec("openrouter/google/gemma-4-31b-it:free", frozenset({"tools", "lead"}), 2),
)


def _ids(name: str) -> list[str]:
    return [item.strip() for item in os.environ.get(name, "").split(",") if item.strip()]


def _models_from_env() -> tuple[ModelSpec, ...]:
    """STUDIO_WORKER_MODELS replaces the coding models; STUDIO_LEAD_MODELS the lead ones.

    Order in each variable is preference order (strongest first for lead models).
    """
    workers, leads = _ids("STUDIO_WORKER_MODELS"), _ids("STUDIO_LEAD_MODELS")
    if not workers and not leads:
        return DEFAULT_MODELS
    base = (
        [ModelSpec(m, frozenset({"coding", "tools"}), 1) for m in workers]
        if workers
        else [m for m in DEFAULT_MODELS if "coding" in m.capabilities]
    )
    if leads:
        base = [ModelSpec(m.id, m.capabilities - {"lead"}, m.strength) for m in base]
    else:
        leads = [m.id for m in DEFAULT_MODELS if "lead" in m.capabilities]
    by_id = {m.id: m for m in base}
    for rank, model_id in enumerate(leads):
        strength = 10 - rank  # keeps the given order for hard tasks
        old = by_id.get(model_id)
        caps = (old.capabilities if old else frozenset({"tools"})) | {"lead"}
        by_id[model_id] = ModelSpec(model_id, caps, max(strength, old.strength if old else 0))
    return tuple(by_id.values())


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(default_factory=_default_data_dir)
    omniroute_url: str = "http://127.0.0.1:20128"
    omniroute_api_key: str | None = None
    worker_models: tuple[ModelSpec, ...] = DEFAULT_MODELS
    gateway_timeout_s: float = 180.0
    claude_bin: str = "claude"
    codex_bin: str = "codex"
    codex_model: str | None = "gpt-5.6-sol"
    host: str = "127.0.0.1"
    port: int = 8765

    @property
    def db_path(self) -> Path:
        return self.data_dir / "studio.sqlite"

    @property
    def workspaces_dir(self) -> Path:
        return self.data_dir / "workspaces"

    @staticmethod
    def from_env() -> Settings:
        data_dir = os.environ.get("STUDIO_DATA_DIR")
        return Settings(
            data_dir=Path(data_dir).resolve() if data_dir else _default_data_dir(),
            omniroute_url=os.environ.get("STUDIO_OMNIROUTE_URL", "http://127.0.0.1:20128"),
            omniroute_api_key=os.environ.get("OMNIROUTE_API_KEY") or None,
            worker_models=_models_from_env(),
            gateway_timeout_s=float(os.environ.get("STUDIO_GATEWAY_TIMEOUT", "180")),
            claude_bin=os.environ.get("STUDIO_CLAUDE_BIN", "claude"),
            codex_bin=os.environ.get("STUDIO_CODEX_BIN", "codex"),
            codex_model=os.environ.get("STUDIO_CODEX_MODEL", "gpt-5.6-sol") or None,
            port=int(os.environ.get("STUDIO_PORT", "8765")),
        )
