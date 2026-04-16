"""Central runtime configuration helpers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class RuntimeConfig:
    use_live_apis: bool
    use_locus_wrapped_apis: bool
    strict_integrations: bool
    require_api_key: bool
    api_key: str
    docs_enabled: bool


@lru_cache(maxsize=1)
def load_runtime_config() -> RuntimeConfig:
    api_key = os.getenv("FLOWPAY_API_KEY", "").strip()
    require_api_key = _env_bool("REQUIRE_API_KEY", False)

    return RuntimeConfig(
        use_live_apis=_env_bool("USE_LIVE_APIS", False),
        use_locus_wrapped_apis=_env_bool("USE_LOCUS_WRAPPED_APIS", False),
        strict_integrations=_env_bool("STRICT_INTEGRATIONS", False),
        require_api_key=require_api_key,
        api_key=api_key,
        docs_enabled=_env_bool("ENABLE_API_DOCS", True),
    )


def use_live_apis() -> bool:
    return load_runtime_config().use_live_apis


def use_locus_wrapped_apis() -> bool:
    return load_runtime_config().use_locus_wrapped_apis


def strict_integrations() -> bool:
    return load_runtime_config().strict_integrations
