"""Provider readiness checks for live integration mode."""

from __future__ import annotations

import os
from typing import Any, Dict

import httpx

from services.runtime_config import load_runtime_config


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


def get_provider_health() -> Dict[str, Any]:
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

    locus_base = os.getenv("LOCUS_API_BASE", "https://api.paywithlocus.com/api").strip().rstrip("/")
    apollo_endpoint = os.getenv("APOLLO_ENRICH_ENDPOINT", "").strip() or "https://api.apollo.io"

    providers = {
        "locus": _provider_status(
            "locus",
            _configured("LOCUS_API_KEY"),
            _reachable(locus_base),
            live_mode,
        ),
        "exa": _provider_status(
            "exa",
            _configured("EXA_API_KEY"),
            _reachable("https://api.exa.ai"),
            live_mode,
        ),
        "firecrawl": _provider_status(
            "firecrawl",
            _configured("FIRECRAWL_API_KEY"),
            _reachable("https://api.firecrawl.dev"),
            live_mode,
        ),
        "resend": _provider_status(
            "resend",
            _configured("RESEND_API_KEY"),
            _reachable("https://api.resend.com"),
            live_mode,
        ),
        "apollo": _provider_status(
            "apollo",
            _configured("APOLLO_API_KEY"),
            _reachable(apollo_endpoint),
            live_mode,
        ),
    }

    required = ["locus", "exa", "firecrawl", "resend", "apollo"]
    required_ok = all(providers[name]["status"] == "healthy" for name in required)

    return {
        "live_mode": live_mode,
        "providers": providers,
        "required_live_providers_healthy": required_ok if live_mode else True,
    }
