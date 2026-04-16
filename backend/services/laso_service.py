"""Laso virtual card provisioning service with optional Locus wrapped API support."""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from database.db import (
    get_virtual_card_record,
    insert_virtual_card_transaction,
    list_virtual_card_records,
    list_virtual_card_transaction_records,
    upsert_virtual_card,
)
from services.reliability import post_json_with_retries
from services.runtime_config import strict_integrations, use_live_apis, use_locus_wrapped_apis
from services.spending_controls import charge_api_usage


_CARDS: Dict[str, Dict[str, Any]] = {}
_CARD_TXNS: List[Dict[str, Any]] = []
_LOADED_FROM_DB = False
logger = logging.getLogger("flowpay.laso")


def _now() -> str:
    return datetime.now().isoformat()


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

    response = post_json_with_retries(
        url=f"{_locus_api_base()}/wrapped/{provider}/{endpoint}",
        payload=payload,
        headers=headers,
        timeout=15,
        circuit_key=f"wrapped_{provider}",
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


def _ensure_loaded() -> None:
    global _LOADED_FROM_DB
    if _LOADED_FROM_DB:
        return

    try:
        cards = list_virtual_card_records()
        for card in cards:
            card_id = card.get("id")
            if card_id:
                _CARDS[str(card_id)] = card

        _CARD_TXNS.clear()
        _CARD_TXNS.extend(list_virtual_card_transaction_records(limit=5000))
    except Exception:
        pass

    _LOADED_FROM_DB = True


def create_virtual_card(
    spend_limit: float,
    session_id: str = "manual",
    alias: Optional[str] = None,
    merchant_lock: str = "GSTN",
    purpose: str = "gst_payment",
    currency: str = "INR",
) -> Dict[str, Any]:
    _ensure_loaded()
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
    integration_mode = "simulated"

    if use_live_apis() and use_locus_wrapped_apis():
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
            integration_mode = "live_wrapped"
        except Exception as exc:
            if strict_integrations():
                raise RuntimeError("Laso wrapped virtual card create failed") from exc
            logger.warning("degraded to local virtual card mode: %s", str(exc))
            integration_mode = "degraded_fallback"

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
        "integration_mode": integration_mode,
        "created_at": _now(),
        "last_used_at": None,
    }
    _CARDS[card_id] = card
    try:
        upsert_virtual_card(card)
    except Exception:
        pass
    return card


def list_virtual_cards(status: Optional[str] = None) -> List[Dict[str, Any]]:
    _ensure_loaded()
    rows = list(_CARDS.values())
    if status:
        rows = [row for row in rows if str(row.get("status", "")).lower() == status.lower()]
    return sorted(rows, key=lambda row: row.get("created_at", ""), reverse=True)


def get_virtual_card(card_id: str) -> Optional[Dict[str, Any]]:
    _ensure_loaded()
    card = _CARDS.get(card_id)
    if card:
        return card

    try:
        record = get_virtual_card_record(card_id)
        if record:
            _CARDS[card_id] = record
        return record
    except Exception:
        return None


def debit_virtual_card(card_id: str, amount: float, session_id: str = "manual", reason: str = "portal_payment") -> Dict[str, Any]:
    _ensure_loaded()
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

    integration_mode = "simulated"
    provider_txn_id = None
    if use_live_apis() and use_locus_wrapped_apis():
        payload = {
            "card_id": card_id,
            "amount": amount,
            "reason": reason,
            "session_id": session_id,
        }
        try:
            wrapped = _locus_wrapped_request("laso", "virtual-cards/debit", payload)
            provider_txn_id = wrapped.get("id") or wrapped.get("transaction_id")
            integration_mode = "live_wrapped"
        except Exception as exc:
            if strict_integrations():
                raise RuntimeError("Laso wrapped virtual card debit failed") from exc
            logger.warning("degraded to local virtual card debit mode: %s", str(exc))
            integration_mode = "degraded_fallback"

    card["available_limit"] = round(float(card.get("available_limit", 0.0)) - amount, 2)
    card["last_used_at"] = _now()

    txn = {
        "id": f"txn_{uuid.uuid4().hex[:10]}",
        "provider_transaction_id": provider_txn_id,
        "card_id": card_id,
        "amount": round(amount, 2),
        "reason": reason,
        "status": "charged",
        "integration_mode": integration_mode,
        "session_id": session_id,
        "timestamp": _now(),
        "remaining_limit": card["available_limit"],
    }
    _CARD_TXNS.append(txn)

    try:
        upsert_virtual_card(card)
        insert_virtual_card_transaction(txn)
    except Exception:
        pass

    return txn


def list_virtual_card_transactions(card_id: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
    _ensure_loaded()
    if limit <= 0:
        return []

    try:
        return list_virtual_card_transaction_records(card_id=card_id, limit=limit)
    except Exception:
        rows = _CARD_TXNS
        if card_id:
            rows = [row for row in rows if row.get("card_id") == card_id]
        return rows[-limit:]
