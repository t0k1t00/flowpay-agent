"""
Escrow Service — Locus Paygentic Escrow (simulated)
In production: integrate with Locus payment APIs.
"""

import uuid
from datetime import datetime
from typing import Dict
from models import Supplier, EscrowRecord

# In-memory store (SQLite in production)
_escrows: Dict[str, dict] = {}
APPROVAL_THRESHOLD = 2000.0


def create_escrow(supplier: Supplier, amount: float, session_id: str) -> EscrowRecord:
    eid = f"esc_{uuid.uuid4().hex[:8]}"
    requires_approval = amount > APPROVAL_THRESHOLD
    record = EscrowRecord(
        id=eid,
        supplier=supplier.company_name if hasattr(supplier, 'company_name') else str(supplier),
        amount=amount,
        status="locked" if not requires_approval else "pending_approval",
        created_at=datetime.now().isoformat(),
        session_id=session_id,
        requires_approval=requires_approval
    )
    _escrows[eid] = record.dict()
    return record


def approve_escrow(escrow_id: str) -> dict:
    if escrow_id not in _escrows:
        raise KeyError(f"Escrow {escrow_id} not found")
    _escrows[escrow_id]["status"] = "approved"
    return _escrows[escrow_id]


def reject_escrow(escrow_id: str) -> dict:
    if escrow_id not in _escrows:
        raise KeyError(f"Escrow {escrow_id} not found")
    _escrows[escrow_id]["status"] = "rejected"
    return _escrows[escrow_id]


def release_escrow(escrow_id: str) -> dict:
    if escrow_id not in _escrows:
        raise KeyError(f"Escrow {escrow_id} not found")
    _escrows[escrow_id]["status"] = "released"
    _escrows[escrow_id]["released_at"] = datetime.now().isoformat()
    return _escrows[escrow_id]


def refund_escrow(escrow_id: str) -> dict:
    if escrow_id not in _escrows:
        raise KeyError(f"Escrow {escrow_id} not found")
    _escrows[escrow_id]["status"] = "refunded"
    _escrows[escrow_id]["refunded_at"] = datetime.now().isoformat()
    return _escrows[escrow_id]


def get_escrow(escrow_id: str) -> dict:
    return _escrows.get(escrow_id)
