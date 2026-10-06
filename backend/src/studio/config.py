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
DEFAULT_MODELS: tuple[ModelSpec, ...] = (
    ModelSpec("openrouter/poolside/laguna-s-2.1:free", frozenset({"coding", "tools"}), 3),
    ModelSpec("openrouter/cohere/north-mini-code:free", frozenset({"coding", "tools"}), 2),
    ModelSpec("openrouter/nvidia/nemotron-3-ultra-550b-a55b:free", frozenset({"tools"}), 4),
    ModelSpec("openrouter/google/gemma-4-31b-it:free", frozenset({"tools"}), 2),
)


def _models_from_env() -> tuple[ModelSpec, ...]:
    raw = os.environ.get("STUDIO_WORKER_MODELS", "").strip()
    if not raw:
        return DEFAULT_MODELS
    return tuple(
        ModelSpec(item.strip(), frozenset({"coding", "tools"}), 1)
        for item in raw.split(",")
        if item.strip()
    )


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
