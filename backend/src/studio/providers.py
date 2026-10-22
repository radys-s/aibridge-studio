"""Direct OpenAI-compatible providers, so Studio also works without OmniRoute.

A provider is a name (the model id prefix the router already uses, e.g. `groq`), a base URL
and where its key comes from. Keys are read from the environment or the OS keyring at call
time and never stored in Studio's database, config or logs.

Built-in presets cover the free no-train providers in the catalog. A preset is active when
its key is available (or it needs no key and the user enabled it). `providers.toml` in the
data dir adds providers or overrides presets:

    [providers.mylab]
    base_url = "http://10.0.0.5:8000/v1"
    api_key_env = "MYLAB_KEY"      # optional
    privacy = "no_train"           # no_train | unknown | may_train
    models = ["qwen3-coder"]       # ids without the prefix; empty = ask /models
"""

from __future__ import annotations

import contextlib
import os
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from studio.config import ModelSpec

KEYRING_SERVICE = "aibridge-studio"


@dataclass(frozen=True)
class Provider:
    name: str
    base_url: str
    label: str = ""
    api_key_env: str | None = None
    keyless: bool = False  # works without a key (OVH anonymous tier, local Ollama)
    privacy: str = "unknown"
    models: tuple[str, ...] = ()  # ids without the `name/` prefix; empty = ask /models
    # URL placeholders filled from the environment, e.g. Cloudflare's {CLOUDFLARE_ACCOUNT_ID}.
    url_env: tuple[str, ...] = ()
    signup: str = ""
    local: bool = False
    enabled_by_default: bool = False

    def url(self, env: Callable[[str], str | None]) -> str | None:
        url = self.base_url
        for name in self.url_env:
            value = env(name)
            if not value:
                return None
            url = url.replace("{" + name + "}", value)
        return url.rstrip("/") or None


PRESETS: tuple[Provider, ...] = (
    Provider(
        "groq",
        "https://api.groq.com/openai/v1",
        "Groq",
        api_key_env="GROQ_API_KEY",
        privacy="no_train",
        signup="https://console.groq.com/keys",
    ),
    Provider(
        "cf",
        "https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/ai/v1",
        "Cloudflare Workers AI",
        api_key_env="CLOUDFLARE_API_TOKEN",
        privacy="no_train",
        url_env=("CLOUDFLARE_ACCOUNT_ID",),
        # Cloudflare's OpenAI-compatible API has no /models listing.
        models=("@cf/moonshotai/kimi-k2.6", "@cf/zai-org/glm-4.7-flash", "@cf/openai/gpt-oss-120b"),
        signup="https://dash.cloudflare.com/profile/api-tokens",
    ),
    Provider(
        "cerebras",
        "https://api.cerebras.ai/v1",
        "Cerebras",
        api_key_env="CEREBRAS_API_KEY",
        privacy="no_train",
        signup="https://cloud.cerebras.ai",
    ),
    Provider(
        "ollamacloud",
        "https://ollama.com/v1",
        "Ollama Cloud",
        api_key_env="OLLAMA_API_KEY",
        privacy="no_train",
        signup="https://ollama.com/settings/keys",
    ),
    Provider(
        "ovh",
        "https://oai.endpoints.kepler.ai.cloud.ovh.net/v1",
        "OVHcloud AI Endpoints",
        api_key_env="OVH_AI_ENDPOINTS_ACCESS_TOKEN",
        keyless=True,
        privacy="no_train",
        signup="https://www.ovhcloud.com/en/public-cloud/ai-endpoints/catalog/",
    ),
    Provider(
        "openrouter",
        "https://openrouter.ai/api/v1",
        "OpenRouter",
        api_key_env="OPENROUTER_API_KEY",
        privacy="unknown",
        signup="https://openrouter.ai/settings/keys",
    ),
    Provider(
        "ollama",
        "http://127.0.0.1:11434/v1",
        "Ollama (local)",
        keyless=True,
        privacy="no_train",
        local=True,
        enabled_by_default=True,
        signup="https://ollama.com/download",
    ),
)


def keyring_get(name: str) -> str | None:
    """A key saved from Studio's Settings, in the OS keyring. None when unavailable."""
    try:
        import keyring  # optional dependency
        from keyring.errors import KeyringError
    except ImportError:
        return None
    try:
        return keyring.get_password(KEYRING_SERVICE, name)
    except (KeyringError, RuntimeError, OSError):
        return None


def default_env(name: str) -> str | None:
    return os.environ.get(name) or keyring_get(name)


@dataclass
class ProviderSet:
    """The providers Studio may call directly, with the switches the user set."""

    providers: dict[str, Provider] = field(default_factory=dict)
    # Keyless providers that are not on by default (OVH) are used only when enabled.
    enabled: set[str] = field(default_factory=set)
    env: Callable[[str], str | None] = default_env

    def key(self, provider: Provider) -> str | None:
        return self.env(provider.api_key_env) if provider.api_key_env else None

    def set_enabled(self, name: str, on: bool) -> list[str]:
        """Swap in a new set (never mutate it) so readers on other threads stay safe."""
        enabled = set(self.enabled)
        if on:
            enabled.add(name)
        else:
            enabled.discard(name)
        self.enabled = enabled
        return sorted(enabled)

    def active(self, provider: Provider) -> bool:
        if provider.url(self.env) is None:
            return False
        if self.key(provider):
            return True
        return provider.keyless and (provider.enabled_by_default or provider.name in self.enabled)

    def active_names(self) -> list[str]:
        return [name for name, p in self.providers.items() if self.active(p)]

    def get(self, model_id: str) -> Provider | None:
        """The active provider that serves a `name/model` id, or None (OmniRoute's job)."""
        name = model_id.split("/", 1)[0]
        provider = self.providers.get(name)
        return provider if provider is not None and self.active(provider) else None

    def describe(self) -> list[dict[str, Any]]:
        out = []
        for p in self.providers.values():
            out.append(
                {
                    "name": p.name,
                    "label": p.label or p.name,
                    "active": self.active(p),
                    "has_key": bool(self.key(p)),
                    "keyless": p.keyless,
                    "key_env": p.api_key_env,
                    "missing_env": [n for n in p.url_env if not self.env(n)],
                    "privacy": p.privacy,
                    "local": p.local,
                    "signup": p.signup,
                }
            )
        return out


def _is_loopback(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return host in ("localhost", "127.0.0.1", "::1")


def load_providers(data_dir: Path, enabled: set[str] | None = None) -> ProviderSet:
    """Presets plus `providers.toml`.

    A preset keeps its URL and key variable: the file may only rename it, list models or
    switch it on, so a shared config cannot send a preset's key somewhere else. Custom
    providers need https unless they run on this machine; only those count as local.
    """
    providers = {p.name: p for p in PRESETS}
    path = data_dir / "providers.toml"
    if path.is_file():
        try:
            with path.open("rb") as fh:
                raw = tomllib.load(fh)
        except tomllib.TOMLDecodeError as exc:
            raise SystemExit(f"{path}: invalid TOML ({exc})") from exc
        for name, cfg in (raw.get("providers") or {}).items():
            if not isinstance(cfg, dict):
                continue
            if "api_key" in cfg:
                raise SystemExit(f"{path}: put keys in the environment (api_key_env), not here")
            models = tuple(str(m) for m in cfg.get("models", ()))
            preset = providers.get(name) if name in {p.name for p in PRESETS} else None
            if preset is not None:
                if {"base_url", "api_key_env", "privacy", "keyless"} & set(cfg):
                    raise SystemExit(
                        f"{path}: [{name}] is a built-in provider; only label, models and "
                        "enabled can change. Add your own name for another endpoint."
                    )
                providers[name] = replace(
                    preset,
                    label=str(cfg.get("label", preset.label)),
                    models=models or preset.models,
                    enabled_by_default=bool(cfg.get("enabled", preset.enabled_by_default)),
                )
                continue
            base_url = str(cfg.get("base_url", "")).strip()
            local = _is_loopback(base_url)
            if not base_url.startswith("https://") and not local:
                raise SystemExit(f"{path}: [{name}] base_url must be https (or localhost)")
            privacy = str(cfg.get("privacy", "unknown"))
            if privacy not in ("no_train", "unknown", "may_train"):
                raise SystemExit(f"{path}: [{name}] privacy must be no_train, unknown or may_train")
            key_env = cfg.get("api_key_env")
            if key_env and str(key_env) in {p.api_key_env for p in PRESETS if p.api_key_env}:
                # Otherwise a shared config could send e.g. the Groq key to its own host.
                raise SystemExit(
                    f"{path}: [{name}] api_key_env {key_env} belongs to a built-in provider"
                )
            providers[name] = Provider(
                name,
                base_url,
                str(cfg.get("label", name)),
                api_key_env=str(key_env) if key_env else None,
                keyless=bool(cfg.get("keyless", not key_env)),
                privacy=privacy,
                models=models,
                local=local,
                enabled_by_default=bool(cfg.get("enabled", False)),
            )
    return ProviderSet(providers, set(enabled or ()))


class KeyStoreError(RuntimeError):
    pass


def save_key(name: str, value: str | None) -> None:
    """Save (or with None delete) a provider key in the OS keyring, never anywhere else."""
    try:
        import keyring
        from keyring.errors import KeyringError, PasswordDeleteError
    except ImportError as exc:
        raise KeyStoreError("the OS keyring is not available; set the key as " + name) from exc
    try:
        if value:
            keyring.set_password(KEYRING_SERVICE, name, value)
        else:
            with contextlib.suppress(PasswordDeleteError):
                keyring.delete_password(KEYRING_SERVICE, name)
    except (KeyringError, RuntimeError, OSError) as exc:
        raise KeyStoreError(f"the OS keyring refused the key ({type(exc).__name__})") from exc


def local_specs(providers: ProviderSet, exposed: set[str]) -> list[ModelSpec]:
    """Catalog entries for models of local providers (e.g. whatever Ollama has pulled).

    Local models never leave the machine, so they rank as no_train, but they come last:
    small local models are a fallback, not the first choice for a hard task.
    """
    local = {name for name, p in providers.providers.items() if p.local}
    return [
        ModelSpec(model_id, frozenset({"coding", "tools", "lead"}), 0, privacy="no_train")
        for model_id in sorted(exposed)
        if model_id.split("/", 1)[0] in local
    ]
