"""
PayGentic — Autonomous B2B Sourcing Agent
FastAPI Backend
"""

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import asyncio
import json
import uuid
from datetime import datetime
from typing import Optional

from models import (
    SourcingRequest, SupplierResponse, EscrowRequest,
    ApprovalRequest, EmailRequest, AuditEntry
)
from services.parser import parse_request
from services.supplier_search import search_suppliers, enrich_suppliers, rank_suppliers
from services.email_service import send_quote_emails
from services.escrow_service import (
    create_escrow, approve_escrow, reject_escrow,
    release_escrow, refund_escrow
)
from services.audit_service import log_action, get_audit_trail
from database.db import init_db
from websocket.manager import ConnectionManager

app = FastAPI(title="PayGentic API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

manager = ConnectionManager()

@app.on_event("startup")
async def startup():
    init_db()

# ── WebSocket ──────────────────────────────────────────────
@app.websocket("/ws/logs")
async def websocket_logs(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            # Echo or process incoming messages
            await manager.broadcast(json.dumps({"type": "ping", "ts": datetime.now().isoformat()}))
    except WebSocketDisconnect:
        manager.disconnect(websocket)

# ── Main Agent Orchestration ───────────────────────────────
@app.post("/api/request-source")
async def request_source(req: SourcingRequest):
    """Main entry point: triggers full agent pipeline."""
    session_id = str(uuid.uuid4())[:8]

    async def stream_pipeline():
        # Step 1: Parse
        await manager.broadcast(json.dumps({
            "type": "log", "step": "parse",
            "action": "Parsing sourcing request",
            "detail": f"Input: {req.query}",
            "ts": now()
        }))
        parsed = parse_request(req.query)
        await asyncio.sleep(0.5)

        # Step 2: Search
        await manager.broadcast(json.dumps({
            "type": "log", "step": "search",
            "action": "Searching suppliers via Exa API",
            "detail": f"Query: {parsed.material} suppliers in India",
            "ts": now()
        }))
        suppliers = search_suppliers(parsed)
        await asyncio.sleep(1.0)

        # Step 3: Enrich
        await manager.broadcast(json.dumps({
            "type": "log", "step": "enrich",
            "action": "Enriching supplier data",
            "detail": f"GSTIN validation + Apollo scores for {len(suppliers)} suppliers",
            "ts": now()
        }))
        enriched = enrich_suppliers(suppliers)
        await asyncio.sleep(0.8)

        # Step 4: Rank
        ranked = rank_suppliers(enriched, parsed)
        await manager.broadcast(json.dumps({
            "type": "suppliers",
            "data": [s.dict() for s in ranked],
            "ts": now()
        }))
        await asyncio.sleep(0.5)

        # Step 5: Email
        await manager.broadcast(json.dumps({
            "type": "log", "step": "email",
            "action": "Sending quote request emails",
            "detail": f"Dispatching to {len(ranked)} suppliers via Resend",
            "ts": now()
        }))
        email_results = send_quote_emails(ranked, parsed)
        await asyncio.sleep(0.8)

        # Step 6: Select best
        best = ranked[0]
        total_cost = best.price_per_kg * parsed.quantity_kg
        await manager.broadcast(json.dumps({
            "type": "log", "step": "select",
            "action": f"Best supplier selected: {best.company_name}",
            "detail": f"Score {best.score} | ₹{best.price_per_kg}/kg | {best.delivery_days} days",
            "ts": now()
        }))

        # Step 7: Escrow
        escrow = create_escrow(best, total_cost, session_id)
        await manager.broadcast(json.dumps({
            "type": "escrow",
            "data": escrow.dict(),
            "ts": now()
        }))

        # Step 8: Threshold check
        APPROVAL_THRESHOLD = 2000
        if total_cost > APPROVAL_THRESHOLD:
            await manager.broadcast(json.dumps({
                "type": "approval_required",
                "supplier": best.company_name,
                "amount": total_cost,
                "excess": total_cost - APPROVAL_THRESHOLD,
                "escrow_id": escrow.id,
                "ts": now()
            }))
        else:
            await manager.broadcast(json.dumps({
                "type": "log", "step": "escrow",
                "action": "Auto-approved: below threshold",
                "detail": f"₹{total_cost} < ₹{APPROVAL_THRESHOLD} limit",
                "ts": now()
            }))

        # Log to audit
        log_action("sourcing_complete", best.company_name, total_cost, "pending", session_id)

    asyncio.create_task(stream_pipeline())
    return {"status": "started", "session_id": session_id}


# ── Supplier Routes ────────────────────────────────────────
@app.get("/api/suppliers")
async def get_suppliers(material: Optional[str] = None):
    from services.supplier_search import get_mock_suppliers
    suppliers = get_mock_suppliers()
    if material:
        suppliers = [s for s in suppliers if material.lower() in s.category.lower()]
    return suppliers


# ── Email Routes ───────────────────────────────────────────
@app.post("/api/send-email")
async def send_email(req: EmailRequest):
    result = send_quote_emails([req.supplier_id], req)
    log_action("email_sent", req.supplier_id, 0, "sent", req.session_id or "manual")
    return {"status": "sent", "result": result}


# ── Escrow Routes ──────────────────────────────────────────
@app.post("/api/create-escrow")
async def api_create_escrow(req: EscrowRequest):
    escrow = create_escrow(req.supplier, req.amount, req.session_id)
    log_action("escrow_created", req.supplier, req.amount, "locked", req.session_id)
    return escrow


@app.post("/api/approve-payment")
async def api_approve(req: ApprovalRequest):
    result = approve_escrow(req.escrow_id)
    log_action("payment_approved", req.escrow_id, result.get("amount", 0), "approved", req.session_id)
    await manager.broadcast(json.dumps({"type": "approval_result", "approved": True, "escrow_id": req.escrow_id}))
    return result


@app.post("/api/reject-payment")
async def api_reject(req: ApprovalRequest):
    result = reject_escrow(req.escrow_id)
    log_action("payment_rejected", req.escrow_id, 0, "rejected", req.session_id)
    await manager.broadcast(json.dumps({"type": "approval_result", "approved": False, "escrow_id": req.escrow_id}))
    return result


@app.post("/api/release-escrow")
async def api_release(req: ApprovalRequest):
    result = release_escrow(req.escrow_id)
    log_action("escrow_released", req.escrow_id, result.get("amount", 0), "released", req.session_id)
    return result


@app.post("/api/refund-escrow")
async def api_refund(req: ApprovalRequest):
    result = refund_escrow(req.escrow_id)
    log_action("escrow_refunded", req.escrow_id, result.get("amount", 0), "refunded", req.session_id)
    return result


# ── Audit Trail ────────────────────────────────────────────
@app.get("/api/audit-trail")
async def get_audit(session_id: Optional[str] = None):
    return get_audit_trail(session_id)


# ── Health ─────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "version": "1.0.0"}


def now():
    return datetime.now().isoformat()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
