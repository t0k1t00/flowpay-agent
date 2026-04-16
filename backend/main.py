"""
PayGentic — Autonomous B2B Sourcing Agent
FastAPI Backend
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from database.db import init_db, insert_agent_event, is_db_ready, list_sessions, upsert_session
from models import (
    A2AServiceRegisterRequest,
    A2ATaskExecuteRequest,
    ApprovalRequest,
    EmailRequest,
    EscrowRequest,
    GSTAutomationRequest,
    SourcingRequest,
    SpendingControlsResponse,
    SpendingControlsUpdate,
    VirtualCardCreateRequest,
    VirtualCardDebitRequest,
    WalletTopUpRequest,
    Supplier,
)
from services.a2a_marketplace_service import (
    execute_task as execute_a2a_task,
    list_services as list_a2a_services,
    list_tasks as list_a2a_tasks,
    register_service as register_a2a_service,
)
from services.audit_service import get_audit_trail, log_action
from services.browser_use_service import list_gst_automation_runs, run_gst_automation
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
from services.laso_service import (
    create_virtual_card,
    debit_virtual_card,
    get_virtual_card,
    list_virtual_card_transactions,
    list_virtual_cards,
)
from services.orchestrator import ProcurementOrchestrator
from services.parser import parse_request
from services.provider_health import get_provider_health
from services.runtime_config import load_runtime_config
from services.security import ApiKeyMiddleware
from services.spending_controls import (
    get_spending_controls,
    get_wallet_ledger,
    get_wallet_state,
    hydrate_wallet_state,
    set_payment_event_hook,
    topup_wallet,
    update_spending_controls,
)
from services.supplier_search import get_mock_suppliers
from websocket.manager import ConnectionManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("flowpay")

runtime_cfg = load_runtime_config()
app = FastAPI(
    title="PayGentic API",
    version="1.1.0",
    docs_url="/docs" if runtime_cfg.docs_enabled else None,
    redoc_url="/redoc" if runtime_cfg.docs_enabled else None,
)

app.add_middleware(ApiKeyMiddleware)

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
    hydrate_wallet_state()
    for saved in list_sessions():
        sid = str(saved.get("id"))
        sessions[sid] = {
            "session_id": sid,
            "query": saved.get("query"),
            "status": saved.get("status"),
            "created_at": saved.get("created_at"),
            "restored": True,
        }

    def _payment_hook(entry: Dict[str, Any]) -> None:
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(
                broadcast_event(
                    {
                        "type": "payment_event",
                        "session_id": entry.get("session_id", "manual"),
                        "data": entry,
                    }
                )
            )
        except RuntimeError:
            return

    set_payment_event_hook(_payment_hook)

    logger.info(
        "startup completed",
        extra={
            "use_live_apis": runtime_cfg.use_live_apis,
            "strict_integrations": runtime_cfg.strict_integrations,
            "api_key_required": runtime_cfg.require_api_key,
        },
    )


def now() -> str:
    return datetime.now().isoformat()


async def broadcast_event(payload: Dict[str, Any]) -> None:
    payload.setdefault("ts", now())
    try:
        insert_agent_event(
            session_id=str(payload.get("session_id") or "system"),
            event_type=str(payload.get("type") or "event"),
            payload=payload,
        )
    except Exception:
        pass
    await manager.broadcast(json.dumps(payload))


def _to_float(raw: str, fallback: float) -> float:
    cleaned = "".join(ch for ch in str(raw) if ch.isdigit() or ch == ".")
    if not cleaned:
        return fallback
    try:
        return float(cleaned)
    except ValueError:
        return fallback


def _extract_bearer_token(auth_header: str) -> str:
    prefix = "bearer "
    if not auth_header:
        return ""
    if auth_header.lower().startswith(prefix):
        return auth_header[len(prefix):].strip()
    return ""


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
        upsert_session(sessions[session_id])
        context = await orchestrator.run(query=query, session_id=session_id, emit=broadcast_event)
        sessions[session_id].update(
            {
                "status": "completed",
                "completed_at": now(),
                "selected_supplier": context.selected_supplier.company_name if context.selected_supplier else None,
                "escrow_id": context.escrow.id if context.escrow else None,
            }
        )
        upsert_session(sessions[session_id])
        await emit_wallet_state(session_id)
    except Exception as exc:
        sessions[session_id]["status"] = "failed"
        sessions[session_id]["error"] = str(exc)
        upsert_session(sessions[session_id])
        log_action("pipeline_failed", "orchestrator", 0, "error", session_id)


# ── WebSocket ──────────────────────────────────────────────
@app.websocket("/ws/logs")
async def websocket_logs(websocket: WebSocket) -> None:
    if runtime_cfg.require_api_key:
        supplied = websocket.headers.get("x-api-key", "").strip()
        if not supplied:
            supplied = _extract_bearer_token(websocket.headers.get("authorization", ""))
        if not supplied:
            supplied = websocket.query_params.get("api_key", "").strip()

        if not runtime_cfg.api_key or supplied != runtime_cfg.api_key:
            await websocket.close(code=1008, reason="Unauthorized")
            return

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
    upsert_session(sessions[session_id])

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
    supplier = next(
        (
            item for item in get_mock_suppliers()
            if item.id == req.supplier_id
            or item.company_name.lower() == req.supplier_id.lower()
        ),
        None,
    )

    if not supplier:
        supplier = Supplier(
            id=req.supplier_id.lower().replace(" ", "_"),
            company_name=req.supplier_id,
            price_per_kg=0,
            delivery_days=req.delivery_days,
            verified=False,
            gstin="",
            email=f"sales@{req.supplier_id.lower().replace(' ', '')}.com",
            phone=None,
            location="India",
            website=None,
            score=75,
            category=req.material,
    )
        
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


# ── Browser Use + Laso Integrations ───────────────────────
@app.post("/api/virtual-cards")
async def api_create_virtual_card(payload: VirtualCardCreateRequest):
    try:
        card = create_virtual_card(
            spend_limit=payload.spend_limit,
            session_id=payload.session_id,
            alias=payload.alias,
            merchant_lock=payload.merchant_lock,
            purpose=payload.purpose,
            currency=payload.currency,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    log_action("virtual_card_provisioned", card["id"], payload.spend_limit, "active", payload.session_id)
    await broadcast_event({"type": "virtual_card", "session_id": payload.session_id, "data": card})
    await emit_wallet_state(payload.session_id)
    return card


@app.get("/api/virtual-cards")
async def api_list_virtual_cards(status: Optional[str] = None):
    return list_virtual_cards(status=status)


@app.get("/api/virtual-cards/{card_id}")
async def api_get_virtual_card(card_id: str):
    card = get_virtual_card(card_id)
    if not card:
        raise HTTPException(status_code=404, detail="Virtual card not found")
    return card


@app.post("/api/virtual-cards/debit")
async def api_debit_virtual_card(payload: VirtualCardDebitRequest):
    try:
        txn = debit_virtual_card(
            card_id=payload.card_id,
            amount=payload.amount,
            session_id=payload.session_id,
            reason=payload.reason,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    log_action("virtual_card_debit", payload.card_id, payload.amount, "charged", payload.session_id)
    await broadcast_event({"type": "virtual_card_txn", "session_id": payload.session_id, "data": txn})
    await emit_wallet_state(payload.session_id)
    return txn


@app.get("/api/virtual-cards/{card_id}/transactions")
async def api_virtual_card_transactions(card_id: str, limit: int = 100):
    card = get_virtual_card(card_id)
    if not card:
        raise HTTPException(status_code=404, detail="Virtual card not found")
    return list_virtual_card_transactions(card_id=card_id, limit=limit)


@app.post("/api/gstn/automate")
async def api_gstn_automate(payload: GSTAutomationRequest):
    try:
        result = run_gst_automation(
            gstin=payload.gstin,
            filing_period=payload.filing_period,
            tax_amount=payload.tax_amount,
            session_id=payload.session_id,
            card_id=payload.card_id,
            portal=payload.portal,
            notes=payload.notes,
            auto_pay=payload.auto_pay,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    log_action("gstn_automation_run", payload.gstin, payload.tax_amount, result["status"], payload.session_id)
    await broadcast_event({"type": "gstn_automation", "session_id": payload.session_id, "data": result})
    await emit_wallet_state(payload.session_id)
    return result


@app.get("/api/gstn/runs")
async def api_list_gstn_runs(limit: int = 50):
    return list_gst_automation_runs(limit=limit)


# ── A2A Marketplace ───────────────────────────────────────
@app.post("/api/a2a/services/register")
async def api_register_a2a_service(payload: A2AServiceRegisterRequest):
    try:
        service = register_a2a_service(
            name=payload.name,
            capability=payload.capability,
            price_per_unit=payload.price_per_unit,
            seller_agent_id=payload.seller_agent_id,
            session_id=payload.session_id,
            endpoint_url=payload.endpoint_url,
            currency=payload.currency,
            metadata=payload.metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    log_action("a2a_service_registered", service["id"], service["price_per_unit"], "active", payload.session_id)
    await broadcast_event({"type": "a2a_service_registered", "session_id": payload.session_id, "data": service})
    return service


@app.get("/api/a2a/services")
async def api_list_a2a_services(capability: Optional[str] = None, max_price: Optional[float] = None, status: str = "active"):
    if max_price is not None and max_price <= 0:
        raise HTTPException(status_code=400, detail="max_price must be greater than zero")

    return list_a2a_services(capability=capability, max_price=max_price, status=status)


@app.post("/api/a2a/tasks/execute")
async def api_execute_a2a_task(payload: A2ATaskExecuteRequest):
    try:
        task = execute_a2a_task(
            service_id=payload.service_id,
            requester_agent_id=payload.requester_agent_id,
            units=payload.units,
            payload=payload.payload,
            session_id=payload.session_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    log_action(
        "a2a_task_executed",
        payload.service_id,
        float(task.get("total_amount", 0)),
        str(task.get("status", "completed")),
        payload.session_id,
    )
    await broadcast_event({"type": "a2a_task", "session_id": payload.session_id, "data": task})
    await emit_wallet_state(payload.session_id)
    return task


@app.get("/api/a2a/tasks")
async def api_list_a2a_tasks(limit: int = 100, session_id: Optional[str] = None):
    return list_a2a_tasks(limit=limit, session_id=session_id)


# ── Audit Trail ────────────────────────────────────────────
@app.get("/api/audit-trail")
async def get_audit(session_id: Optional[str] = None):
    return get_audit_trail(session_id)


# ── Health ─────────────────────────────────────────────────
@app.get("/health")
async def health() -> Dict[str, Any]:
    db_ready = is_db_ready()
    provider_health = get_provider_health()
    ready_for_live = db_ready and provider_health.get("required_live_providers_healthy", True)
    return {
        "status": "ok" if ready_for_live else "degraded",
        "version": "1.1.0",
        "active_sessions": len([s for s in sessions.values() if s.get("status") == "running"]),
        "auth_enabled": runtime_cfg.require_api_key,
        "live_integrations": runtime_cfg.use_live_apis,
        "db_ready": db_ready,
        "providers": provider_health,
    }


@app.get("/health/ready")
async def health_ready() -> Dict[str, Any]:
    db_ready = is_db_ready()
    provider_health = get_provider_health()
    ready = db_ready and provider_health.get("required_live_providers_healthy", True)
    return {
        "status": "ready" if ready else "not_ready",
        "ready": ready,
        "db_ready": db_ready,
        "providers": provider_health,
        "active_sessions": len([s for s in sessions.values() if s.get("status") == "running"]),
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
