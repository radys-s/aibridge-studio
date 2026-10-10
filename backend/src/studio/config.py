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
    # no_train | unknown | may_train; set from PROVIDER_PRIVACY (see below).
    privacy: Privacy = "unknown"
    paid: bool = False

    @property
    def provider(self) -> str:
        return self.id.split("/", 1)[0]


_LEAD = frozenset({"coding", "tools", "lead"})
_CODE = frozenset({"coding", "tools"})

# Explicit model ids only; never `auto/*` combos (see docs/ARCHITECTURE.md). OmniRoute lists
# models under the provider's *alias* (`cf/...` for cloudflare-ai, `hf/...` for
# huggingface), so ids here use the alias too.
# Order and strength: strongest no-train lead first. Models OmniRoute does not expose (a
# provider not connected yet) are skipped by the router, so listing them is harmless.
# The provider set mirrors the keys Prizma (`../multiagent`) already uses; Tomáš adds the
# same keys in the OmniRoute dashboard (ADR 0005). Privacy comes from PROVIDER_PRIVACY.
# Verified live through OmniRoute on 2026-10-08: groq/openai/gpt-oss-120b (tool calling).
_CATALOG: tuple[ModelSpec, ...] = (
    ModelSpec("groq/openai/gpt-oss-120b", _LEAD, 6),
    ModelSpec("cerebras/gpt-oss-120b", _LEAD, 6),
    ModelSpec("cf/@cf/moonshotai/kimi-k2.6", _LEAD, 5),
    ModelSpec("cerebras/zai-glm-4.7", _LEAD, 5),
    # OmniRoute's Cloudflare list has no gpt-oss-120b; glm-4.7-flash is the next lead there.
    ModelSpec("cf/@cf/zai-org/glm-4.7-flash", _LEAD, 4),
    # Ollama Cloud free plan (v0.6): "light usage" quota, one model at a time; key from
    # ollama.com/settings/keys. OmniRoute alias `ollamacloud`. Ranked below the providers
    # verified live so a hard task does not spend its first calls on a small quota.
    ModelSpec("ollamacloud/deepseek-v4-pro", _LEAD, 4),
    ModelSpec("ollamacloud/kimi-k2.6", _LEAD, 4),
    ModelSpec("ollamacloud/glm-5.1", _LEAD, 4),
    ModelSpec("ollamacloud/qwen3.5:397b", _LEAD, 3),
    # OVHcloud AI Endpoints answers without a key: 2 requests/min per model and IP (400
    # requests/min with a key). OmniRoute lists a fixed set of OVH models; of those,
    # Mistral Small 3.2 returned a well-typed tool call live on 2026-10-10 (Llama 3.3 70B
    # sent "true" as a string). Lowest rank: a different-family fallback, not a worker.
    ModelSpec("ovh/Mistral-Small-3.2-24B-Instruct-2506", _LEAD, 2),
    # GitHub Models is gone: GitHub retired it on 2026-07-30 and models.github.ai now answers
    # every request with a plain "OK" (seen 2026-10-08), so it has no entry.
    ModelSpec("groq/openai/gpt-oss-20b", _LEAD, 3),
    # unknown: no clear public no-training commitment for free/trial keys.
    ModelSpec("zai/glm-4.7-flash", _LEAD, 3),
    ModelSpec("cohere/command-a-03-2025", frozenset({"tools", "lead"}), 3),
    ModelSpec("hf/deepseek-ai/DeepSeek-V3", _LEAD, 3),
    ModelSpec("openrouter/poolside/laguna-s-2.1:free", _LEAD, 3),
    ModelSpec("openrouter/cohere/north-mini-code:free", _CODE, 2),
    ModelSpec("openrouter/nvidia/nemotron-3-ultra-550b-a55b:free", frozenset({"tools", "lead"}), 4),
    ModelSpec("openrouter/google/gemma-4-31b-it:free", frozenset({"tools", "lead"}), 2),
    # may_train: free tiers whose terms allow training on inputs. Never routed unless Tomáš
    # opts out with the provider and lists it in STUDIO_NO_TRAIN_PROVIDERS.
    ModelSpec("nvidia/moonshotai/kimi-k2.6", _LEAD, 6),
    ModelSpec("nvidia/deepseek-ai/deepseek-v4-pro", _LEAD, 6),
    ModelSpec("gemini/gemini-3.5-flash", _LEAD, 5),
    ModelSpec("mistral/devstral-latest", _LEAD, 4),
    ModelSpec("mistral/codestral-latest", _CODE, 3),
)

# Training on inputs by provider alias, checked 2026-10-08 (sources in ADR 0004/0005):
# - no_train: Groq (no retention beyond abuse logs, ZDR toggle), Cloudflare Workers AI (no
#   training, no sharing), Cerebras (inputs not retained; free tier needs a card on file).
#   Added 2026-10-10 from the providers' published statements (ADR 0005, v0.6 update):
#   Ollama Cloud (registry.ollama.ai/privacy: cloud prompts processed transiently and never
#   used for training) and OVHcloud AI Endpoints (ovhcloud.com AI Endpoints / Free AI API
#   pages: data never stored or used for training). Not independently verified; the OVH
#   statement is not specific to the keyless tier, and OmniRoute's own free catalog marks
#   Ollama Cloud's terms "ambiguous".
# - may_train: NVIDIA API trial (content may improve NVIDIA products and models), Gemini free
#   tier, Mistral free tier.
# - unknown: OpenRouter :free (depends on the upstream), Cohere trial, Z.ai, Hugging Face
#   inference providers.
PROVIDER_PRIVACY: dict[str, Privacy] = {
    "groq": "no_train",
    "cloudflare-ai": "no_train",
    "cf": "no_train",
    "cerebras": "no_train",
    "ollamacloud": "no_train",
    "ollama-cloud": "no_train",
    "ovh": "no_train",
    "ovhcloud": "no_train",
    "nvidia": "may_train",
    "gemini": "may_train",
    "mistral": "may_train",
}
DEFAULT_MODELS: tuple[ModelSpec, ...] = tuple(
    replace(m, privacy=PROVIDER_PRIVACY.get(m.provider, "unknown")) for m in _CATALOG
)
# Known free-tier requests per day (per model on Groq; the whole :free pool on OpenRouter
# without credits). Cloudflare counts neurons and Cerebras tokens, so they have no entry.
DAILY_REQUEST_CAPS: dict[str, int] = {"groq": 1000, "openrouter": 50}


def _privacy(model_id: str) -> Privacy:
    provider = model_id.split("/", 1)[0]
    # Tomáš opted out of training with these providers (e.g. Mistral admin console).
    if provider in _ids("STUDIO_NO_TRAIN_PROVIDERS"):
        return "no_train"
    if provider == "openrouter" and os.environ.get("STUDIO_OPENROUTER_NO_TRAIN") == "1":
        return "no_train"  # Tomáš turned off "may train" for free models in his account
    return PROVIDER_PRIVACY.get(provider, "unknown")


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
    # End-to-end UI tests only: a scripted gateway instead of OmniRoute (studio/testing.py).
    fake_gateway: bool = False
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
            fake_gateway=os.environ.get("STUDIO_FAKE_GATEWAY") == "1",
            gateway_timeout_s=float(os.environ.get("STUDIO_GATEWAY_TIMEOUT", "180")),
            port=int(os.environ.get("STUDIO_PORT", "8765")),
            vault_path=(
                Path(os.environ["STUDIO_VAULT"])
                if os.environ.get("STUDIO_VAULT")
                else _default_vault()
            ),
        )
