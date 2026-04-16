"""Central runtime configuration helpers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import List


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_list(name: str, default: str) -> List[str]:
    raw = os.getenv(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass(frozen=True)
class RuntimeConfig:
    use_live_apis: bool
    use_locus_wrapped_apis: bool
    strict_integrations: bool
    require_api_key: bool
    api_key: str
    docs_enabled: bool
    cors_allow_origins: List[str]
    cors_allow_methods: List[str]
    cors_allow_headers: List[str]


@lru_cache(maxsize=1)
def load_runtime_config() -> RuntimeConfig:
    api_key = os.getenv("FLOWPAY_API_KEY", "").strip()
    require_api_key = _env_bool("REQUIRE_API_KEY", bool(api_key))

    return RuntimeConfig(
        use_live_apis=_env_bool("USE_LIVE_APIS", False),
        use_locus_wrapped_apis=_env_bool("USE_LOCUS_WRAPPED_APIS", False),
        strict_integrations=_env_bool("STRICT_INTEGRATIONS", False),
        require_api_key=require_api_key,
        api_key=api_key,
        docs_enabled=_env_bool("ENABLE_API_DOCS", False),
        cors_allow_origins=_env_list(
            "CORS_ALLOW_ORIGINS",
            "http://localhost:3000,http://127.0.0.1:3000,http://localhost:8080,http://127.0.0.1:8080",
        ),
        cors_allow_methods=_env_list("CORS_ALLOW_METHODS", "GET,POST,PUT,OPTIONS"),
        cors_allow_headers=_env_list("CORS_ALLOW_HEADERS", "Content-Type,Authorization,X-Api-Key"),
    )


def use_live_apis() -> bool:
    return load_runtime_config().use_live_apis


def use_locus_wrapped_apis() -> bool:
    return load_runtime_config().use_locus_wrapped_apis


def strict_integrations() -> bool:
    return load_runtime_config().strict_integrations
