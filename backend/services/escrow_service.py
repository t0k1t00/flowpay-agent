"""
Escrow Service — Locus Paygentic Escrow (simulated)
In production: integrate with Locus payment APIs.
"""

import uuid
from datetime import datetime
from typing import Dict, Union

from models import Supplier, EscrowRecord
from services.spending_controls import (
    approve_reserved_escrow,
    cancel_reserved_escrow,
    refund_spent_escrow,
    requires_human_approval,
    reserve_escrow,
)

# In-memory store (SQLite in production)
_escrows: Dict[str, dict] = {}


def _supplier_name(supplier: Union[Supplier, str]) -> str:
    if isinstance(supplier, Supplier):
        return supplier.company_name
    return str(supplier)


def create_escrow(supplier: Union[Supplier, str], amount: float, session_id: str, category: str = "general") -> EscrowRecord:
    eid = f"esc_{uuid.uuid4().hex[:8]}"
    reserve_escrow(amount, session_id, category)

    requires_approval = requires_human_approval(amount)
    status = "pending_approval" if requires_approval else "locked"

    if not requires_approval:
        approve_reserved_escrow(amount, session_id)

    record = EscrowRecord(
        id=eid,
        supplier=_supplier_name(supplier),
        amount=amount,
        status=status,
        created_at=datetime.now().isoformat(),
        session_id=session_id,
        requires_approval=requires_approval
    )
    _escrows[eid] = record.model_dump()
    return record


def approve_escrow(escrow_id: str) -> dict:
    if escrow_id not in _escrows:
        raise KeyError(f"Escrow {escrow_id} not found")

    escrow = _escrows[escrow_id]
    if escrow["status"] != "pending_approval":
        return escrow

    approve_reserved_escrow(escrow["amount"], escrow["session_id"])
    _escrows[escrow_id]["status"] = "locked"
    _escrows[escrow_id]["approved_at"] = datetime.now().isoformat()
    return _escrows[escrow_id]


def reject_escrow(escrow_id: str) -> dict:
    if escrow_id not in _escrows:
        raise KeyError(f"Escrow {escrow_id} not found")

    escrow = _escrows[escrow_id]
    if escrow["status"] == "pending_approval":
        cancel_reserved_escrow(escrow["amount"], escrow["session_id"])

    _escrows[escrow_id]["status"] = "rejected"
    _escrows[escrow_id]["rejected_at"] = datetime.now().isoformat()
    return _escrows[escrow_id]


def release_escrow(escrow_id: str) -> dict:
    if escrow_id not in _escrows:
        raise KeyError(f"Escrow {escrow_id} not found")

    if _escrows[escrow_id]["status"] not in {"locked", "approved"}:
        raise ValueError("Escrow can only be released from locked status")

    _escrows[escrow_id]["status"] = "released"
    _escrows[escrow_id]["released_at"] = datetime.now().isoformat()
    return _escrows[escrow_id]


def refund_escrow(escrow_id: str) -> dict:
    if escrow_id not in _escrows:
        raise KeyError(f"Escrow {escrow_id} not found")

    escrow = _escrows[escrow_id]
    status = escrow["status"]

    if status == "pending_approval":
        cancel_reserved_escrow(escrow["amount"], escrow["session_id"])
    elif status in {"locked", "approved", "released"}:
        refund_spent_escrow(escrow["amount"], escrow["session_id"])
    elif status == "refunded":
        return escrow
    else:
        raise ValueError("Escrow cannot be refunded in its current status")

    _escrows[escrow_id]["status"] = "refunded"
    _escrows[escrow_id]["refunded_at"] = datetime.now().isoformat()
    return _escrows[escrow_id]


def get_escrow(escrow_id: str) -> dict:
    return _escrows.get(escrow_id)


def list_escrows(session_id: str = None) -> list:
    records = list(_escrows.values())
    if session_id:
        records = [item for item in records if item.get("session_id") == session_id]
    return sorted(records, key=lambda item: item.get("created_at", ""), reverse=True)


def list_pending_approvals(session_id: str = None) -> list:
    return [item for item in list_escrows(session_id) if item.get("status") == "pending_approval"]
