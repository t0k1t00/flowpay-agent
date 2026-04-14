"""
PayGentic — Autonomous B2B Sourcing Agent
FastAPI Backend
"""

import asyncio
import json
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from database.db import init_db
from models import (
    ApprovalRequest,
    EmailRequest,
    EscrowRequest,
    SourcingRequest,
    SpendingControlsResponse,
    SpendingControlsUpdate,
    WalletTopUpRequest,
)
from services.audit_service import get_audit_trail, log_action
from services.email_service import send_quote_emails
from services.escrow_service import (
    approve_escrow,
    create_escrow,
    list_escrows,
    list_pending_approvals,
    refund_escrow,
    reject_escrow,
    release_escrow,
)
from services.orchestrator import ProcurementOrchestrator
from services.parser import parse_request
from services.spending_controls import (
    get_spending_controls,
    get_wallet_ledger,
    get_wallet_state,
    topup_wallet,
    update_spending_controls,
)
from services.supplier_search import get_mock_suppliers
from websocket.manager import ConnectionManager

app = FastAPI(title="PayGentic API", version="1.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

manager = ConnectionManager()
orchestrator = ProcurementOrchestrator()
sessions: Dict[str, Dict[str, Any]] = {}


@app.on_event("startup")
async def startup() -> None:
    init_db()


def now() -> str:
    return datetime.now().isoformat()


async def broadcast_event(payload: Dict[str, Any]) -> None:
    payload.setdefault("ts", now())
    await manager.broadcast(json.dumps(payload))


def _to_float(raw: str, fallback: float) -> float:
    cleaned = "".join(ch for ch in str(raw) if ch.isdigit() or ch == ".")
    if not cleaned:
        return fallback
    try:
        return float(cleaned)
    except ValueError:
        return fallback


async def emit_wallet_state(session_id: str) -> None:
    await broadcast_event(
        {
            "type": "wallet",
            "session_id": session_id,
            "data": get_wallet_state().model_dump(),
        }
    )


async def run_pipeline(session_id: str, query: str) -> None:
    try:
        sessions[session_id]["status"] = "running"
        context = await orchestrator.run(query=query, session_id=session_id, emit=broadcast_event)
        sessions[session_id].update(
            {
                "status": "completed",
                "completed_at": now(),
                "selected_supplier": context.selected_supplier.company_name if context.selected_supplier else None,
                "escrow_id": context.escrow.id if context.escrow else None,
            }
        )
        await emit_wallet_state(session_id)
    except Exception as exc:
        sessions[session_id]["status"] = "failed"
        sessions[session_id]["error"] = str(exc)
        log_action("pipeline_failed", "orchestrator", 0, "error", session_id)


# ── WebSocket ──────────────────────────────────────────────
@app.websocket("/ws/logs")
async def websocket_logs(websocket: WebSocket) -> None:
    await manager.connect(websocket)
    await manager.send_to(websocket, json.dumps({"type": "connected", "ts": now()}))
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)


# ── Main Agent Orchestration ───────────────────────────────
@app.post("/api/request-source")
async def request_source(req: SourcingRequest) -> Dict[str, str]:
    """Start a sourcing session and stream execution events over WebSocket."""
    session_id = req.session_id or str(uuid.uuid4())[:8]
    sessions[session_id] = {
        "session_id": session_id,
        "query": req.query,
        "status": "queued",
        "created_at": now(),
    }

    asyncio.create_task(run_pipeline(session_id, req.query))
    return {"status": "started", "session_id": session_id}


@app.get("/api/session/{session_id}")
async def get_session(session_id: str) -> Dict[str, Any]:
    data = sessions.get(session_id)
    if not data:
        raise HTTPException(status_code=404, detail="Session not found")
    return data


# ── Supplier Routes ────────────────────────────────────────
@app.get("/api/suppliers")
async def get_suppliers(material: Optional[str] = None):
    suppliers = get_mock_suppliers()
    if material:
        query = material.lower()
        suppliers = [
            supplier
            for supplier in suppliers
            if query in supplier.category.lower() or query in supplier.company_name.lower()
        ]
    return [supplier.model_dump() for supplier in suppliers]


# ── Email Routes ───────────────────────────────────────────
@app.post("/api/send-email")
async def send_email(req: EmailRequest):
    supplier = next((item for item in get_mock_suppliers() if item.id == req.supplier_id), None)
    if not supplier:
        raise HTTPException(status_code=404, detail="Supplier not found")

    qty = _to_float(req.quantity, 1000.0)
    max_budget = _to_float(req.max_budget, 300.0)
    parsed = parse_request(f"{qty}kg {req.material} under {max_budget}/kg within {req.delivery_days} days")
    parsed.quantity_kg = qty
    parsed.max_budget_per_kg = max_budget
    parsed.material = req.material.lower().strip() or parsed.material

    result = send_quote_emails([supplier], parsed, req.session_id or "manual")
    log_action("email_sent", supplier.company_name, 0, "sent", req.session_id or "manual")
    return {"status": "sent", "result": result[0] if result else None}


# ── Escrow Routes ──────────────────────────────────────────
@app.post("/api/create-escrow")
async def api_create_escrow(req: EscrowRequest):
    try:
        escrow = create_escrow(req.supplier, req.amount, req.session_id, req.category)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    log_action("escrow_created", req.supplier, req.amount, escrow.status, req.session_id)
    await broadcast_event({"type": "escrow", "session_id": req.session_id, "data": escrow.model_dump()})
    await emit_wallet_state(req.session_id)
    return escrow


@app.get("/api/escrows")
async def api_list_escrows(session_id: Optional[str] = None):
    return list_escrows(session_id)


@app.get("/api/approvals")
async def api_list_approvals(session_id: Optional[str] = None):
    controls = get_spending_controls()
    all_escrows = list_escrows(session_id)
    pending = list_pending_approvals(session_id)
    history = [
        item
        for item in all_escrows
        if item.get("status") in {"locked", "released", "refunded", "rejected"}
    ]

    return {
        "approval_threshold": controls["auto_approve_threshold"],
        "pending": pending,
        "history": history,
        "pending_count": len(pending),
    }


@app.post("/api/approve-payment")
async def api_approve(req: ApprovalRequest):
    try:
        result = approve_escrow(req.escrow_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    session_id = result.get("session_id", req.session_id)
    log_action("payment_approved", req.escrow_id, result.get("amount", 0), "approved", session_id)
    await broadcast_event({"type": "approval_result", "approved": True, "escrow_id": req.escrow_id, "session_id": session_id})
    await broadcast_event({"type": "escrow", "data": result, "session_id": session_id})
    await emit_wallet_state(session_id)
    return result


@app.post("/api/reject-payment")
async def api_reject(req: ApprovalRequest):
    try:
        result = reject_escrow(req.escrow_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    session_id = result.get("session_id", req.session_id)
    log_action("payment_rejected", req.escrow_id, 0, "rejected", session_id)
    await broadcast_event({"type": "approval_result", "approved": False, "escrow_id": req.escrow_id, "session_id": session_id})
    await broadcast_event({"type": "escrow", "data": result, "session_id": session_id})
    await emit_wallet_state(session_id)
    return result


@app.post("/api/release-escrow")
async def api_release(req: ApprovalRequest):
    try:
        result = release_escrow(req.escrow_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    session_id = result.get("session_id", req.session_id)
    log_action("escrow_released", req.escrow_id, result.get("amount", 0), "released", session_id)
    await broadcast_event({"type": "escrow", "data": result, "session_id": session_id})
    return result


@app.post("/api/refund-escrow")
async def api_refund(req: ApprovalRequest):
    try:
        result = refund_escrow(req.escrow_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    session_id = result.get("session_id", req.session_id)
    log_action("escrow_refunded", req.escrow_id, result.get("amount", 0), "refunded", session_id)
    await broadcast_event({"type": "escrow", "data": result, "session_id": session_id})
    await emit_wallet_state(session_id)
    return result


# ── Spending Controls / Wallet ─────────────────────────────
@app.get("/api/wallet-state")
async def wallet_state():
    return get_wallet_state().model_dump()


@app.post("/api/wallet-topup")
async def wallet_topup(payload: WalletTopUpRequest):
    try:
        entry = topup_wallet(
            amount=payload.amount,
            session_id=payload.session_id,
            reason=payload.reason or "manual_topup",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await emit_wallet_state(payload.session_id)
    return {
        "status": "credited",
        "entry": entry,
        "wallet": get_wallet_state().model_dump(),
    }


@app.get("/api/wallet-ledger")
async def wallet_ledger(limit: int = 100):
    return get_wallet_ledger(limit)


@app.get("/api/spending-controls", response_model=SpendingControlsResponse)
async def api_get_spending_controls():
    return get_spending_controls()


@app.put("/api/spending-controls", response_model=SpendingControlsResponse)
async def api_update_spending_controls(payload: SpendingControlsUpdate):
    try:
        return update_spending_controls(payload.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ── Audit Trail ────────────────────────────────────────────
@app.get("/api/audit-trail")
async def get_audit(session_id: Optional[str] = None):
    return get_audit_trail(session_id)


# ── Health ─────────────────────────────────────────────────
@app.get("/health")
async def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "version": "1.1.0",
        "active_sessions": len([s for s in sessions.values() if s.get("status") == "running"]),
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
