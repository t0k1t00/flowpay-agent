"""Runtime spending controls and Locus wallet simulation."""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime
from threading import Lock
from typing import Any, Callable, Dict, List, Optional

from database.db import (
    init_db,
    get_wallet_state_record,
    insert_wallet_ledger_entry,
    list_wallet_ledger_entries,
    upsert_wallet_state,
)
from models import WalletState
from services.locus_client import wallet_credit, wallet_debit
from services.runtime_config import strict_integrations


logger = logging.getLogger("flowpay.wallet")


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return float(default)
    try:
        return float(raw)
    except ValueError:
        return float(default)


def _env_categories(name: str, default: str) -> List[str]:
    raw = os.getenv(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


_LOCK = Lock()

_CONTROLS: Dict[str, Any] = {
    "auto_approve_threshold": _env_float("AUTO_APPROVE_THRESHOLD", 2000.0),
    "monthly_spend_limit": _env_float("MONTHLY_SPEND_LIMIT", 30000.0),
    "daily_spend_limit": _env_float("DAILY_SPEND_LIMIT", 10000.0),
    "allowed_categories": _env_categories(
        "ALLOWED_VENDOR_CATEGORIES",
        "cotton yarn,textile dye,steel rod,machine parts",
    ),
}

_STATE: Dict[str, Any] = {
    "balance": _env_float("WALLET_BALANCE", 50000.0),
    "spent": 0.0,
    "escrow_locked": 0.0,
    "spent_today": 0.0,
    "day_key": datetime.now().strftime("%Y-%m-%d"),
}

_LEDGER: List[Dict[str, Any]] = []
_PAYMENT_EVENT_HOOK: Optional[Callable[[Dict[str, Any]], None]] = None
_DB_INIT_DONE = False


def _ensure_db_schema() -> None:
    global _DB_INIT_DONE
    if _DB_INIT_DONE:
        return
    init_db()
    _DB_INIT_DONE = True


def _now_iso() -> str:
    return datetime.now().isoformat()


def _rollover_if_needed() -> None:
    today = datetime.now().strftime("%Y-%m-%d")
    if _STATE["day_key"] != today:
        _STATE["day_key"] = today
        _STATE["spent_today"] = 0.0


def _record(kind: str, amount: float, session_id: str, status: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
    entry = {
        "id": f"wtx_{uuid.uuid4().hex[:8]}",
        "kind": kind,
        "amount": round(amount, 2),
        "session_id": session_id,
        "status": status,
        "metadata": metadata,
        "timestamp": _now_iso(),
    }
    _LEDGER.append(entry)

    try:
        insert_wallet_ledger_entry(entry)
    except Exception as exc:
        if "no such table" in str(exc).lower():
            _ensure_db_schema()
            try:
                insert_wallet_ledger_entry(entry)
            except Exception as retry_exc:
                logger.warning("wallet ledger persistence failed after retry: %s", str(retry_exc))
                if strict_integrations():
                    raise RuntimeError("Wallet ledger persistence failed") from retry_exc
        else:
            logger.warning("wallet ledger persistence failed: %s", str(exc))
            if strict_integrations():
                raise RuntimeError("Wallet ledger persistence failed") from exc

    try:
        upsert_wallet_state(_STATE)
    except Exception as exc:
        if "no such table" in str(exc).lower():
            _ensure_db_schema()
            try:
                upsert_wallet_state(_STATE)
            except Exception as retry_exc:
                logger.warning("wallet state persistence failed after retry: %s", str(retry_exc))
                if strict_integrations():
                    raise RuntimeError("Wallet state persistence failed") from retry_exc
        else:
            logger.warning("wallet state persistence failed: %s", str(exc))
            if strict_integrations():
                raise RuntimeError("Wallet state persistence failed") from exc

    hook = _PAYMENT_EVENT_HOOK
    if hook:
        try:
            hook(entry)
        except Exception as exc:
            logger.warning("payment event hook failed: %s", str(exc))
            if strict_integrations():
                raise RuntimeError("Payment event hook failed") from exc

    return entry


def set_payment_event_hook(hook: Optional[Callable[[Dict[str, Any]], None]]) -> None:
    global _PAYMENT_EVENT_HOOK
    _PAYMENT_EVENT_HOOK = hook


def hydrate_wallet_state() -> None:
    with _LOCK:
        _ensure_db_schema()
        persisted = get_wallet_state_record()
        if persisted:
            _STATE["balance"] = float(persisted.get("balance", _STATE["balance"]))
            _STATE["spent"] = float(persisted.get("spent", _STATE["spent"]))
            _STATE["escrow_locked"] = float(persisted.get("escrow_locked", _STATE["escrow_locked"]))
            _STATE["spent_today"] = float(persisted.get("spent_today", _STATE["spent_today"]))
            _STATE["day_key"] = str(persisted.get("day_key") or _STATE["day_key"])
        else:
            upsert_wallet_state(_STATE)

        _LEDGER.clear()
        _LEDGER.extend(list_wallet_ledger_entries(limit=1000))


def _available_balance() -> float:
    return max(0.0, _STATE["balance"] - _STATE["escrow_locked"])


def _assert_positive(amount: float) -> None:
    if amount <= 0:
        raise ValueError("Amount must be greater than zero")


def _assert_procurement_allowed(category: str) -> None:
    if category.lower() == "general":
        return

    allowed = [item.lower() for item in _CONTROLS["allowed_categories"]]
    if category.lower() not in allowed:
        raise ValueError(f"Vendor category '{category}' is not allowed by spending controls")


def _assert_spend_limits(amount: float) -> None:
    projected_monthly = _STATE["spent"] + amount
    if projected_monthly > _CONTROLS["monthly_spend_limit"]:
        raise ValueError("Monthly spending limit exceeded")

    projected_daily = _STATE["spent_today"] + amount
    if projected_daily > _CONTROLS["daily_spend_limit"]:
        raise ValueError("Daily spending limit exceeded")

    if amount > _available_balance():
        raise ValueError("Insufficient available wallet balance")


def requires_human_approval(amount: float) -> bool:
    return amount > _CONTROLS["auto_approve_threshold"]


def get_wallet_state() -> WalletState:
    with _LOCK:
        _rollover_if_needed()
        return WalletState(
            balance=round(_STATE["balance"], 2),
            spent=round(_STATE["spent"], 2),
            limit=round(_CONTROLS["monthly_spend_limit"], 2),
            escrow_locked=round(_STATE["escrow_locked"], 2),
        )


def get_spending_controls() -> Dict[str, Any]:
    with _LOCK:
        return {
            "auto_approve_threshold": _CONTROLS["auto_approve_threshold"],
            "monthly_spend_limit": _CONTROLS["monthly_spend_limit"],
            "daily_spend_limit": _CONTROLS["daily_spend_limit"],
            "allowed_categories": list(_CONTROLS["allowed_categories"]),
        }


def update_spending_controls(payload: Dict[str, Any]) -> Dict[str, Any]:
    with _LOCK:
        if payload.get("auto_approve_threshold") is not None:
            _CONTROLS["auto_approve_threshold"] = float(payload["auto_approve_threshold"])
        if payload.get("monthly_spend_limit") is not None:
            _CONTROLS["monthly_spend_limit"] = float(payload["monthly_spend_limit"])
        if payload.get("daily_spend_limit") is not None:
            _CONTROLS["daily_spend_limit"] = float(payload["daily_spend_limit"])
        if payload.get("allowed_categories") is not None:
            cleaned = [str(item).strip() for item in payload["allowed_categories"] if str(item).strip()]
            if not cleaned:
                raise ValueError("allowed_categories cannot be empty")
            _CONTROLS["allowed_categories"] = cleaned
        return {
            "auto_approve_threshold": _CONTROLS["auto_approve_threshold"],
            "monthly_spend_limit": _CONTROLS["monthly_spend_limit"],
            "daily_spend_limit": _CONTROLS["daily_spend_limit"],
            "allowed_categories": list(_CONTROLS["allowed_categories"]),
        }


def charge_api_usage(provider: str, amount: float, session_id: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
    with _LOCK:
        _rollover_if_needed()
        _assert_positive(amount)
        _assert_spend_limits(amount)

        wallet_debit(
            amount=amount,
            session_id=session_id,
            reason="api_micropayment",
            metadata={"provider": provider, **metadata},
        )

        _STATE["balance"] -= amount
        _STATE["spent"] += amount
        _STATE["spent_today"] += amount

        return _record(
            kind="api_micropayment",
            amount=amount,
            session_id=session_id,
            status="charged",
            metadata={"provider": provider, **metadata},
        )


def reserve_escrow(amount: float, session_id: str, category: str) -> Dict[str, Any]:
    with _LOCK:
        _rollover_if_needed()
        _assert_positive(amount)
        _assert_procurement_allowed(category)
        _assert_spend_limits(amount)

        _STATE["escrow_locked"] += amount
        return _record(
            kind="escrow_reserve",
            amount=amount,
            session_id=session_id,
            status="reserved",
            metadata={"category": category},
        )


def approve_reserved_escrow(amount: float, session_id: str) -> Dict[str, Any]:
    with _LOCK:
        _rollover_if_needed()
        _assert_positive(amount)
        if amount > _STATE["escrow_locked"]:
            raise ValueError("Escrow approval failed: reserved amount not found")

        wallet_debit(
            amount=amount,
            session_id=session_id,
            reason="escrow_approve",
            metadata={},
        )

        _STATE["escrow_locked"] -= amount
        _STATE["balance"] -= amount
        _STATE["spent"] += amount
        _STATE["spent_today"] += amount

        return _record(
            kind="escrow_approve",
            amount=amount,
            session_id=session_id,
            status="approved",
            metadata={},
        )


def cancel_reserved_escrow(amount: float, session_id: str) -> Dict[str, Any]:
    with _LOCK:
        _assert_positive(amount)
        _STATE["escrow_locked"] = max(0.0, _STATE["escrow_locked"] - amount)
        return _record(
            kind="escrow_cancel",
            amount=amount,
            session_id=session_id,
            status="cancelled",
            metadata={},
        )


def refund_spent_escrow(amount: float, session_id: str) -> Dict[str, Any]:
    with _LOCK:
        _assert_positive(amount)
        wallet_credit(
            amount=amount,
            session_id=session_id,
            reason="escrow_refund",
            metadata={},
        )
        _STATE["balance"] += amount
        _STATE["spent"] = max(0.0, _STATE["spent"] - amount)
        _STATE["spent_today"] = max(0.0, _STATE["spent_today"] - amount)
        return _record(
            kind="escrow_refund",
            amount=amount,
            session_id=session_id,
            status="refunded",
            metadata={},
        )


def topup_wallet(amount: float, session_id: str = "manual", reason: str = "manual_topup") -> Dict[str, Any]:
    with _LOCK:
        _assert_positive(amount)
        wallet_credit(
            amount=amount,
            session_id=session_id,
            reason=reason,
            metadata={},
        )
        _STATE["balance"] += amount
        return _record(
            kind="wallet_topup",
            amount=amount,
            session_id=session_id,
            status="credited",
            metadata={"reason": reason},
        )


def get_wallet_ledger(limit: int = 100) -> List[Dict[str, Any]]:
    if limit <= 0:
        return []
    try:
        return list_wallet_ledger_entries(limit=limit)
    except Exception:
        return _LEDGER[-limit:]
