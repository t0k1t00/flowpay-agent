"""Laso virtual card provisioning service with optional Locus wrapped API support."""

from __future__ import annotations

import os
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx

from services.spending_controls import charge_api_usage


_CARDS: Dict[str, Dict[str, Any]] = {}
_CARD_TXNS: List[Dict[str, Any]] = []


def _now() -> str:
    return datetime.now().isoformat()


def _use_live_apis() -> bool:
    return os.getenv("USE_LIVE_APIS", "false").lower() == "true"


def _use_locus_wrapped_apis() -> bool:
    return os.getenv("USE_LOCUS_WRAPPED_APIS", "false").lower() == "true"


def _locus_api_base() -> str:
    raw = os.getenv("LOCUS_API_BASE", "https://api.paywithlocus.com/api").strip()
    return raw[:-1] if raw.endswith("/") else raw


def _locus_wrapped_request(provider: str, endpoint: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    locus_key = os.getenv("LOCUS_API_KEY")
    if not locus_key or "your_" in locus_key:
        raise RuntimeError("LOCUS_API_KEY is not configured")

    headers = {
        "Authorization": f"Bearer {locus_key}",
        "Content-Type": "application/json",
    }

    response = httpx.post(
        f"{_locus_api_base()}/wrapped/{provider}/{endpoint}",
        json=payload,
        headers=headers,
        timeout=15,
    )
    response.raise_for_status()

    body = response.json()
    if not isinstance(body, dict):
        return {}

    data = body.get("data", body)
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        return data["data"]
    return data if isinstance(data, dict) else {}


def _masked_pan(card_id: str) -> str:
    tail = card_id[-4:].upper()
    return f"5290 11XX XXXX {tail}"


def create_virtual_card(
    spend_limit: float,
    session_id: str = "manual",
    alias: Optional[str] = None,
    merchant_lock: str = "GSTN",
    purpose: str = "gst_payment",
    currency: str = "INR",
) -> Dict[str, Any]:
    if spend_limit <= 0:
        raise ValueError("spend_limit must be greater than zero")

    charge_api_usage(
        provider="laso",
        amount=0.09,
        session_id=session_id,
        metadata={"event": "virtual_card_provision", "purpose": purpose},
    )

    card_id = f"card_{uuid.uuid4().hex[:10]}"
    status = "active"

    if _use_live_apis() and _use_locus_wrapped_apis():
        payload = {
            "spend_limit": spend_limit,
            "merchant_lock": merchant_lock,
            "purpose": purpose,
            "currency": currency,
            "alias": alias,
        }
        try:
            wrapped = _locus_wrapped_request("laso", "virtual-cards/create", payload)
            card_id = str(wrapped.get("id") or wrapped.get("card_id") or card_id)
            status = str(wrapped.get("status") or status)
        except Exception:
            # Fallback to deterministic local mode when live endpoint is unreachable.
            pass

    card = {
        "id": card_id,
        "provider": "laso",
        "alias": alias or "GST Compliance Card",
        "merchant_lock": merchant_lock,
        "purpose": purpose,
        "currency": currency,
        "spend_limit": round(spend_limit, 2),
        "available_limit": round(spend_limit, 2),
        "status": status,
        "network": "VISA",
        "masked_pan": _masked_pan(card_id),
        "session_id": session_id,
        "created_at": _now(),
        "last_used_at": None,
    }
    _CARDS[card_id] = card
    return card


def list_virtual_cards(status: Optional[str] = None) -> List[Dict[str, Any]]:
    rows = list(_CARDS.values())
    if status:
        rows = [row for row in rows if str(row.get("status", "")).lower() == status.lower()]
    return sorted(rows, key=lambda row: row.get("created_at", ""), reverse=True)


def get_virtual_card(card_id: str) -> Optional[Dict[str, Any]]:
    return _CARDS.get(card_id)


def debit_virtual_card(card_id: str, amount: float, session_id: str = "manual", reason: str = "portal_payment") -> Dict[str, Any]:
    if amount <= 0:
        raise ValueError("amount must be greater than zero")

    card = _CARDS.get(card_id)
    if not card:
        raise KeyError(f"Virtual card {card_id} not found")

    if card.get("status") != "active":
        raise ValueError("Virtual card is not active")

    if amount > float(card.get("available_limit", 0.0)):
        raise ValueError("Insufficient virtual card available limit")

    charge_api_usage(
        provider="laso",
        amount=0.03,
        session_id=session_id,
        metadata={"event": "virtual_card_debit_fee", "card_id": card_id},
    )

    card["available_limit"] = round(float(card.get("available_limit", 0.0)) - amount, 2)
    card["last_used_at"] = _now()

    txn = {
        "id": f"txn_{uuid.uuid4().hex[:10]}",
        "card_id": card_id,
        "amount": round(amount, 2),
        "reason": reason,
        "status": "charged",
        "session_id": session_id,
        "timestamp": _now(),
        "remaining_limit": card["available_limit"],
    }
    _CARD_TXNS.append(txn)
    return txn


def list_virtual_card_transactions(card_id: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
    if limit <= 0:
        return []

    rows = _CARD_TXNS
    if card_id:
        rows = [row for row in rows if row.get("card_id") == card_id]
    return rows[-limit:]
