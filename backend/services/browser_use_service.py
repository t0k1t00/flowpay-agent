"""Browser Use GST automation service with optional Locus wrapped API support."""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from services.laso_service import create_virtual_card, debit_virtual_card
from services.reliability import post_json_with_retries
from services.runtime_config import strict_integrations, use_live_apis, use_locus_wrapped_apis
from services.spending_controls import charge_api_usage


_GST_RUNS: List[Dict[str, Any]] = []
logger = logging.getLogger("flowpay.browser_use")


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
        timeout=20,
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


def run_gst_automation(
    gstin: str,
    filing_period: str,
    tax_amount: float,
    session_id: str = "manual",
    card_id: Optional[str] = None,
    portal: str = "GSTN",
    notes: Optional[str] = None,
    auto_pay: bool = True,
) -> Dict[str, Any]:
    if tax_amount <= 0:
        raise ValueError("tax_amount must be greater than zero")
    if not gstin.strip():
        raise ValueError("gstin is required")

    charge_api_usage(
        provider="browser_use",
        amount=0.11,
        session_id=session_id,
        metadata={"event": "gstn_automation", "portal": portal},
    )

    selected_card_id = card_id
    card_txn: Optional[Dict[str, Any]] = None

    steps = [
        {"step": "launch_browser", "status": "completed", "ts": _now()},
        {"step": "login_gstn", "status": "completed", "ts": _now()},
        {"step": "open_return_filing", "status": "completed", "ts": _now()},
        {"step": "prefill_tax_values", "status": "completed", "ts": _now()},
    ]

    if auto_pay:
        if not selected_card_id:
            auto_card = create_virtual_card(
                spend_limit=round(tax_amount * 1.2, 2),
                session_id=session_id,
                alias="Auto GSTN Card",
                merchant_lock=portal,
                purpose="gst_payment",
            )
            selected_card_id = auto_card["id"]

        card_txn = debit_virtual_card(
            card_id=selected_card_id,
            amount=tax_amount,
            session_id=session_id,
            reason="gst_tax_payment",
        )
        steps.append({"step": "virtual_card_payment", "status": "completed", "ts": _now()})

    run_id = f"gst_{uuid.uuid4().hex[:10]}"
    receipt_ref = f"GSTN-{uuid.uuid4().hex[:8].upper()}"
    integration_mode = "simulated"

    if use_live_apis() and use_locus_wrapped_apis():
        payload = {
            "gstin": gstin,
            "filing_period": filing_period,
            "tax_amount": tax_amount,
            "portal": portal,
            "card_id": selected_card_id,
            "notes": notes,
            "auto_pay": auto_pay,
        }
        try:
            wrapped = _locus_wrapped_request("browser-use", "gstn/automate", payload)
            run_id = str(wrapped.get("run_id") or wrapped.get("id") or run_id)
            receipt_ref = str(wrapped.get("receipt_ref") or wrapped.get("acknowledgement_no") or receipt_ref)
            integration_mode = "live_wrapped"
        except Exception as exc:
            if strict_integrations():
                raise RuntimeError("GST automation wrapped call failed") from exc
            logger.warning("degraded to local GST automation mode: %s", str(exc))
            integration_mode = "degraded_fallback"

    steps.append({"step": "download_receipt", "status": "completed", "ts": _now()})

    result = {
        "run_id": run_id,
        "status": "completed",
        "portal": portal,
        "gstin": gstin,
        "filing_period": filing_period,
        "tax_amount": round(tax_amount, 2),
        "notes": notes,
        "session_id": session_id,
        "card_id": selected_card_id,
        "card_transaction": card_txn,
        "integration_mode": integration_mode,
        "receipt_ref": receipt_ref,
        "receipt_url": f"https://gstn.example/receipts/{receipt_ref}",
        "steps": steps,
        "completed_at": _now(),
    }
    _GST_RUNS.append(result)
    return result


def list_gst_automation_runs(limit: int = 50) -> List[Dict[str, Any]]:
    if limit <= 0:
        return []
    return _GST_RUNS[-limit:]
