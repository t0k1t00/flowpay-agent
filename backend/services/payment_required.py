"""HTTP 402 payment-required handling with settlement + retry."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

from services.reliability import post_json_with_retries
from services.runtime_config import strict_integrations, use_live_apis, use_locus_wrapped_apis
from services.spending_controls import charge_api_usage


def _locus_api_base() -> str:
    raw = os.getenv("LOCUS_API_BASE", "https://api.paywithlocus.com/api").strip()
    return raw[:-1] if raw.endswith("/") else raw


def _float_amount(value: Any, fallback: float = 0.05) -> float:
    try:
        amount = float(value)
        if amount > 0:
            return round(amount, 4)
    except (TypeError, ValueError):
        pass
    return fallback


@dataclass
class PaymentAwareResponse:
    response: Any
    payment_event: Optional[Dict[str, Any]] = None


def _settle_402_via_locus(provider: str, session_id: str, payment_payload: Dict[str, Any]) -> Dict[str, Any]:
    locus_key = os.getenv("LOCUS_API_KEY", "").strip()
    if not locus_key or "your_" in locus_key:
        raise RuntimeError("LOCUS_API_KEY is not configured for 402 settlement")

    endpoint = os.getenv("LOCUS_402_APPROVAL_ENDPOINT", "/payments/approve")
    endpoint = endpoint if endpoint.startswith("/") else f"/{endpoint}"
    url = f"{_locus_api_base()}{endpoint}"

    amount = _float_amount(
        payment_payload.get("amount")
        or payment_payload.get("price")
        or payment_payload.get("required_amount"),
        fallback=0.05,
    )
    currency = str(payment_payload.get("currency") or "USD")

    payload = {
        "provider": provider,
        "session_id": session_id,
        "amount": amount,
        "currency": currency,
        "payment_context": payment_payload,
    }
    headers = {
        "Authorization": f"Bearer {locus_key}",
        "Content-Type": "application/json",
    }

    response = post_json_with_retries(
        url=url,
        payload=payload,
        headers=headers,
        timeout=15,
        circuit_key="locus_402",
    )
    response.raise_for_status()

    body = response.json() if response.content else {}
    return {
        "kind": "http_402_settlement",
        "provider": provider,
        "amount": amount,
        "currency": currency,
        "session_id": session_id,
        "settlement": body,
        "status": "approved",
    }


def _settle_402(provider: str, session_id: str, payment_payload: Dict[str, Any]) -> Dict[str, Any]:
    if use_live_apis() and use_locus_wrapped_apis():
        return _settle_402_via_locus(provider, session_id, payment_payload)

    amount = _float_amount(
        payment_payload.get("amount")
        or payment_payload.get("price")
        or payment_payload.get("required_amount"),
        fallback=0.05,
    )

    charged = charge_api_usage(
        provider=provider,
        amount=amount,
        session_id=session_id,
        metadata={
            "event": "http_402_settlement",
            "payment_payload": payment_payload,
        },
    )
    return charged


def post_json_with_402_retry(
    *,
    url: str,
    payload: Dict[str, Any],
    headers: Dict[str, str],
    provider: str,
    session_id: str,
    timeout: float = 15,
    circuit_key: Optional[str] = None,
) -> PaymentAwareResponse:
    """Send POST request, settle 402 via wallet/Locus, then retry once."""
    first = post_json_with_retries(
        url=url,
        payload=payload,
        headers=headers,
        timeout=timeout,
        circuit_key=circuit_key,
    )

    if first.status_code != 402:
        first.raise_for_status()
        return PaymentAwareResponse(response=first, payment_event=None)

    payment_payload: Dict[str, Any] = {}
    try:
        body = first.json()
        if isinstance(body, dict):
            payment_payload = body
    except Exception:
        payment_payload = {}

    try:
        payment_event = _settle_402(provider, session_id, payment_payload)
    except Exception:
        if strict_integrations():
            raise
        raise RuntimeError("HTTP 402 payment settlement failed")

    second = post_json_with_retries(
        url=url,
        payload=payload,
        headers=headers,
        timeout=timeout,
        circuit_key=circuit_key,
    )
    second.raise_for_status()
    return PaymentAwareResponse(response=second, payment_event=payment_event)
