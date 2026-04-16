"""Locus API client helpers for wallet and escrow operations."""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from services.reliability import post_json_with_retries
from services.runtime_config import strict_integrations, use_live_apis, use_locus_wrapped_apis


def _locus_api_base() -> str:
    raw = os.getenv("LOCUS_API_BASE", "https://api.paywithlocus.com/api").strip()
    return raw[:-1] if raw.endswith("/") else raw


def locus_live_enabled() -> bool:
    return use_live_apis() and use_locus_wrapped_apis()


def _auth_headers() -> Dict[str, str]:
    api_key = os.getenv("LOCUS_API_KEY", "").strip()
    if not api_key or "your_" in api_key:
        raise RuntimeError("LOCUS_API_KEY is not configured")
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def locus_post(endpoint: str, payload: Dict[str, Any], circuit_key: str = "locus") -> Dict[str, Any]:
    endpoint = endpoint if endpoint.startswith("/") else f"/{endpoint}"
    response = post_json_with_retries(
        url=f"{_locus_api_base()}{endpoint}",
        payload=payload,
        headers=_auth_headers(),
        timeout=15,
        circuit_key=circuit_key,
    )
    response.raise_for_status()
    body = response.json() if response.content else {}
    if not isinstance(body, dict):
        return {}
    data = body.get("data", body)
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        return data["data"]
    return data if isinstance(data, dict) else {}


def maybe_locus_post(endpoint: str, payload: Dict[str, Any], circuit_key: str = "locus") -> Optional[Dict[str, Any]]:
    if not locus_live_enabled():
        return None
    try:
        return locus_post(endpoint=endpoint, payload=payload, circuit_key=circuit_key)
    except Exception:
        if strict_integrations():
            raise
        return None


def wallet_debit(amount: float, session_id: str, reason: str, metadata: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    endpoint = os.getenv("LOCUS_WALLET_DEBIT_ENDPOINT", "/wallet/debit")
    payload = {
        "amount": amount,
        "session_id": session_id,
        "reason": reason,
        "metadata": metadata,
    }
    return maybe_locus_post(endpoint=endpoint, payload=payload, circuit_key="locus_wallet")


def wallet_credit(amount: float, session_id: str, reason: str, metadata: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    endpoint = os.getenv("LOCUS_WALLET_CREDIT_ENDPOINT", "/wallet/credit")
    payload = {
        "amount": amount,
        "session_id": session_id,
        "reason": reason,
        "metadata": metadata,
    }
    return maybe_locus_post(endpoint=endpoint, payload=payload, circuit_key="locus_wallet")


def escrow_create(
    supplier: str,
    amount: float,
    session_id: str,
    category: str,
    requires_approval: bool,
) -> Optional[Dict[str, Any]]:
    endpoint = os.getenv("LOCUS_ESCROW_CREATE_ENDPOINT", "/escrow/create")
    payload = {
        "supplier": supplier,
        "amount": amount,
        "session_id": session_id,
        "category": category,
        "requires_approval": requires_approval,
    }
    return maybe_locus_post(endpoint=endpoint, payload=payload, circuit_key="locus_escrow")


def escrow_transition(action: str, escrow_id: str, amount: float, session_id: str) -> Optional[Dict[str, Any]]:
    endpoint = os.getenv("LOCUS_ESCROW_TRANSITION_ENDPOINT", "/escrow/transition")
    payload = {
        "action": action,
        "escrow_id": escrow_id,
        "amount": amount,
        "session_id": session_id,
    }
    return maybe_locus_post(endpoint=endpoint, payload=payload, circuit_key="locus_escrow")
