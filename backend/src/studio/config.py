"""Runtime configuration. Everything comes from environment variables with safe defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal

from studio.vault import Vault


def _default_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / "aibridge-studio"


def _default_vault() -> Path:
    return Path.home() / "Documents" / "Obsidian Vault" / "AI paměť"


Privacy = Literal["no_train", "unknown", "may_train"]
PRIVACY_RANK: dict[str, int] = {"no_train": 2, "unknown": 1, "may_train": 0}


@dataclass(frozen=True)
class ModelSpec:
    """One explicit OmniRoute model id the router may choose."""

    id: str
    capabilities: frozenset[str]
    strength: int = 1  # higher = stronger; used to order candidates for hard tasks
    free: bool = True
    # no_train: the provider contractually does not train on inputs (checked 2026-10-08:
    # Groq "does not retain customer data", ZDR available; Cloudflare Workers AI does not use
    # customer content to train). unknown: OpenRouter :free endpoints are often providers
    # that may train, unless the account toggle for them is off.
    privacy: Privacy = "unknown"
    paid: bool = False

    @property
    def provider(self) -> str:
        return self.id.split("/", 1)[0]


_LEAD = frozenset({"coding", "tools", "lead"})

# Explicit model ids only; never `auto/*` combos (see docs/ARCHITECTURE.md).
# Order and strength: strongest no-train lead first. Models OmniRoute does not expose (a
# provider not connected yet) are skipped by the router, so listing them is harmless.
# Verified live through OmniRoute on 2026-10-08: groq/openai/gpt-oss-120b (tool calling).
DEFAULT_MODELS: tuple[ModelSpec, ...] = (
    ModelSpec("groq/openai/gpt-oss-120b", _LEAD, 6, privacy="no_train"),
    ModelSpec("cloudflare-ai/@cf/moonshotai/kimi-k2.6", _LEAD, 5, privacy="no_train"),
    ModelSpec("cloudflare-ai/@cf/openai/gpt-oss-120b", _LEAD, 5, privacy="no_train"),
    ModelSpec("groq/openai/gpt-oss-20b", _LEAD, 3, privacy="no_train"),
    ModelSpec("openrouter/poolside/laguna-s-2.1:free", _LEAD, 3),
    ModelSpec("openrouter/cohere/north-mini-code:free", frozenset({"coding", "tools"}), 2),
    ModelSpec("openrouter/nvidia/nemotron-3-ultra-550b-a55b:free", frozenset({"tools", "lead"}), 4),
    ModelSpec("openrouter/google/gemma-4-31b-it:free", frozenset({"tools", "lead"}), 2),
)
PRIVATE_PROVIDERS = ("groq", "cloudflare-ai", "cf", "cerebras")


def _privacy(model_id: str) -> Privacy:
    provider = model_id.split("/", 1)[0]
    if provider in PRIVATE_PROVIDERS:
        return "no_train"
    if provider == "openrouter" and os.environ.get("STUDIO_OPENROUTER_NO_TRAIN") == "1":
        return "no_train"  # Tomáš turned off "may train" for free models in his account
    return "unknown"


def _ids(name: str) -> list[str]:
    return [item.strip() for item in os.environ.get(name, "").split(",") if item.strip()]


def _models_from_env() -> tuple[ModelSpec, ...]:
    """STUDIO_WORKER_MODELS replaces the coding models; STUDIO_LEAD_MODELS the lead ones.

    Order in each variable is preference order (strongest first for lead models).
    """
    workers, leads = _ids("STUDIO_WORKER_MODELS"), _ids("STUDIO_LEAD_MODELS")
    paid = set(_ids("STUDIO_PAID_MODELS"))
    if not workers and not leads:
        return tuple(replace(m, privacy=_privacy(m.id)) for m in DEFAULT_MODELS)
    base = (
        [ModelSpec(m, frozenset({"coding", "tools"}), 1, privacy=_privacy(m)) for m in workers]
        if workers
        else [m for m in DEFAULT_MODELS if "coding" in m.capabilities]
    )
    if leads:
        base = [replace(m, capabilities=m.capabilities - {"lead"}) for m in base]
    else:
        leads = [m.id for m in DEFAULT_MODELS if "lead" in m.capabilities]
    by_id = {m.id: m for m in base}
    for rank, model_id in enumerate(leads):
        strength = 10 - rank  # keeps the given order for hard tasks
        old = by_id.get(model_id)
        caps = (old.capabilities if old else frozenset({"tools"})) | {"lead"}
        by_id[model_id] = ModelSpec(
            model_id,
            caps,
            max(strength, old.strength if old else 0),
            privacy=_privacy(model_id),
        )
    return tuple(
        replace(m, privacy=_privacy(m.id), paid=m.id in paid, free=m.id not in paid)
        for m in by_id.values()
    )


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(default_factory=_default_data_dir)
    omniroute_url: str = "http://127.0.0.1:20128"
    omniroute_api_key: str | None = None
    worker_models: tuple[ModelSpec, ...] = DEFAULT_MODELS
    # Lowest privacy class a model may have to receive code or notes (ADR 0004).
    privacy_floor: Privacy = "no_train"
    # Paid models (STUDIO_PAID_MODELS) are only used when this is true.
    allow_paid: bool = False
    # Seconds a run waits for a briefly cooling model instead of stopping (0 = never wait).
    route_wait_s: float = 0.0
    gateway_timeout_s: float = 180.0
    # Shared Obsidian memory (Autopilot reads non-personal notes; see vault.py).
    vault_path: Path | None = field(default_factory=lambda: _default_vault())
    host: str = "127.0.0.1"
    port: int = 8765

    def vault(self) -> Vault | None:
        if self.vault_path is None or not self.vault_path.is_dir():
            return None
        return Vault(self.vault_path)

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
            privacy_floor=(
                "unknown" if os.environ.get("STUDIO_PRIVACY") == "unknown" else "no_train"
            ),
            allow_paid=os.environ.get("STUDIO_ALLOW_PAID") == "1",
            route_wait_s=float(os.environ.get("STUDIO_ROUTE_WAIT", "45")),
            gateway_timeout_s=float(os.environ.get("STUDIO_GATEWAY_TIMEOUT", "180")),
            port=int(os.environ.get("STUDIO_PORT", "8765")),
            vault_path=(
                Path(os.environ["STUDIO_VAULT"])
                if os.environ.get("STUDIO_VAULT")
                else _default_vault()
            ),
        )
