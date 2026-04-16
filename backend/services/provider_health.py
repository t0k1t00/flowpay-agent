"""Provider readiness checks for live integration mode."""

from __future__ import annotations

import os
import time
from typing import Any, Dict

import httpx

from services.runtime_config import load_runtime_config


_CACHE_TTL_SECONDS = float(os.getenv("PROVIDER_HEALTH_CACHE_TTL_SECONDS", "20") or "20")
_CACHED_HEALTH: Dict[str, Any] = {}
_CACHED_AT = 0.0


def _configured(key: str) -> bool:
    value = os.getenv(key, "").strip()
    return bool(value) and "your_" not in value.lower()


def _reachable(url: str) -> bool:
    try:
        response = httpx.get(url, timeout=4)
        return response.status_code < 500
    except Exception:
        return False


def _provider_status(name: str, configured: bool, reachable: bool, live_mode: bool) -> Dict[str, Any]:
    if not live_mode:
        return {
            "name": name,
            "status": "disabled",
            "configured": configured,
            "reachable": reachable,
        }
    if not configured:
        return {
            "name": name,
            "status": "not_configured",
            "configured": configured,
            "reachable": reachable,
        }
    return {
        "name": name,
        "status": "healthy" if reachable else "unreachable",
        "configured": configured,
        "reachable": reachable,
    }


def _provider_status_live(name: str, config_key: str, health_url: str) -> Dict[str, Any]:
    configured = _configured(config_key)
    reachable = _reachable(health_url) if configured else False
    return _provider_status(name, configured, reachable, True)


def get_provider_health() -> Dict[str, Any]:
    global _CACHED_HEALTH, _CACHED_AT

    cfg = load_runtime_config()
    live_mode = cfg.use_live_apis

    if not live_mode:
        return {
            "live_mode": False,
            "providers": {
                "locus": {"name": "locus", "status": "disabled", "configured": _configured("LOCUS_API_KEY"), "reachable": False},
                "exa": {"name": "exa", "status": "disabled", "configured": _configured("EXA_API_KEY"), "reachable": False},
                "firecrawl": {"name": "firecrawl", "status": "disabled", "configured": _configured("FIRECRAWL_API_KEY"), "reachable": False},
                "resend": {"name": "resend", "status": "disabled", "configured": _configured("RESEND_API_KEY"), "reachable": False},
                "apollo": {"name": "apollo", "status": "disabled", "configured": _configured("APOLLO_API_KEY"), "reachable": False},
            },
            "required_live_providers_healthy": True,
        }

    now = time.time()
    if _CACHED_HEALTH and (now - _CACHED_AT) < _CACHE_TTL_SECONDS:
        return _CACHED_HEALTH

    locus_base = os.getenv("LOCUS_API_BASE", "https://api.paywithlocus.com/api").strip().rstrip("/")
    apollo_endpoint = os.getenv("APOLLO_ENRICH_ENDPOINT", "").strip() or "https://api.apollo.io"

    providers = {
        "locus": _provider_status_live("locus", "LOCUS_API_KEY", locus_base),
        "exa": _provider_status_live("exa", "EXA_API_KEY", "https://api.exa.ai"),
        "firecrawl": _provider_status_live("firecrawl", "FIRECRAWL_API_KEY", "https://api.firecrawl.dev"),
        "resend": _provider_status_live("resend", "RESEND_API_KEY", "https://api.resend.com"),
        "apollo": _provider_status_live("apollo", "APOLLO_API_KEY", apollo_endpoint),
    }

    required = ["locus", "exa", "firecrawl", "resend", "apollo"]
    required_ok = all(providers[name]["status"] == "healthy" for name in required)

    payload = {
        "live_mode": live_mode,
        "providers": providers,
        "required_live_providers_healthy": required_ok if live_mode else True,
    }

    _CACHED_HEALTH = payload
    _CACHED_AT = now
    return payload
