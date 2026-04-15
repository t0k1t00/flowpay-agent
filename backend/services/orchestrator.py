"""Agent orchestration framework for autonomous sourcing workflows."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Awaitable, Callable, Dict, List, Optional, Protocol

from models import EscrowRecord, ParsedRequest, Supplier
from services.audit_service import log_action
from services.email_service import send_quote_emails
from services.escrow_service import create_escrow
from services.parser import parse_request
from services.spending_controls import get_spending_controls
from services.supplier_search import enrich_suppliers, rank_suppliers, search_suppliers


EventEmitter = Callable[[dict], Awaitable[None]]


def _now() -> str:
    return datetime.now().isoformat()


@dataclass
class AgentContext:
    session_id: str
    query: str
    parsed: Optional[ParsedRequest] = None
    suppliers: List[Supplier] = field(default_factory=list)
    selected_supplier: Optional[Supplier] = None
    escrow: Optional[EscrowRecord] = None
    email_dispatch_count: int = 0


class AgentTool(Protocol):
    name: str

    async def run(self, context: AgentContext, emit: EventEmitter) -> AgentContext:
        ...


class ParseIntentTool:
    name = "parse_intent"

    async def run(self, context: AgentContext, emit: EventEmitter) -> AgentContext:
        parsed = parse_request(context.query)
        context.parsed = parsed

        await emit(
            {
                "type": "log",
                "step": "parse",
                "action": "Parsed sourcing intent",
                "detail": (
                    f"Material={parsed.material}, Qty={int(parsed.quantity_kg)}kg, "
                    f"Budget={parsed.max_budget_per_kg}/kg, Delivery={parsed.delivery_days}d"
                ),
                "ts": _now(),
                "session_id": context.session_id,
            }
        )
        log_action("request_parsed", parsed.material, 0, "ok", context.session_id)
        return context


class SupplierDiscoveryTool:
    name = "supplier_discovery"

    async def run(self, context: AgentContext, emit: EventEmitter) -> AgentContext:
        if context.parsed is None:
            raise ValueError("Parsed request is required before supplier discovery")

        await emit(
            {
                "type": "log",
                "step": "search",
                "action": "Searching suppliers via Exa",
                "detail": f"Querying suppliers for '{context.parsed.material}'",
                "ts": _now(),
                "session_id": context.session_id,
            }
        )

        suppliers = search_suppliers(context.parsed, context.session_id)
        context.suppliers = suppliers

        await emit(
            {
                "type": "log",
                "step": "search",
                "action": "Supplier search completed",
                "detail": f"{len(suppliers)} candidates discovered",
                "ts": _now(),
                "session_id": context.session_id,
            }
        )
        log_action("supplier_search", context.parsed.material, 0, "ok", context.session_id)
        return context


class SupplierEnrichmentTool:
    name = "supplier_enrichment"

    async def run(self, context: AgentContext, emit: EventEmitter) -> AgentContext:
        if context.parsed is None:
            raise ValueError("Parsed request is required before enrichment")

        await emit(
            {
                "type": "log",
                "step": "enrich",
                "action": "Enriching with Firecrawl + Apollo",
                "detail": f"Validating GSTIN and scoring {len(context.suppliers)} suppliers",
                "ts": _now(),
                "session_id": context.session_id,
            }
        )

        enriched = enrich_suppliers(context.suppliers, context.session_id)
        ranked = rank_suppliers(enriched, context.parsed)
        context.suppliers = ranked

        await emit(
            {
                "type": "suppliers",
                "data": [supplier.model_dump() for supplier in ranked],
                "ts": _now(),
                "session_id": context.session_id,
            }
        )
        log_action("supplier_enriched", context.parsed.material, 0, "ok", context.session_id)
        return context


class QuoteDispatchTool:
    name = "quote_dispatch"

    async def run(self, context: AgentContext, emit: EventEmitter) -> AgentContext:
        if context.parsed is None:
            raise ValueError("Parsed request is required before quote dispatch")

        if not context.suppliers:
            raise ValueError("No suppliers available for quote dispatch")

        top_suppliers = context.suppliers[:3]
        results = send_quote_emails(top_suppliers, context.parsed, context.session_id)
        context.email_dispatch_count = len(results)

        await emit(
            {
                "type": "log",
                "step": "email",
                "action": "Quote requests dispatched",
                "detail": f"Sent {len(results)} emails via Resend",
                "ts": _now(),
                "session_id": context.session_id,
            }
        )
        log_action("quote_requests_sent", "resend", 0, "sent", context.session_id)
        return context


class EscrowTool:
    name = "escrow_management"

    async def run(self, context: AgentContext, emit: EventEmitter) -> AgentContext:
        if context.parsed is None:
            raise ValueError("Parsed request is required before escrow")

        if not context.suppliers:
            raise ValueError("No suppliers available for escrow creation")

        best = context.suppliers[0]
        context.selected_supplier = best
        amount = round(best.price_per_kg * context.parsed.quantity_kg, 2)

        await emit(
            {
                "type": "log",
                "step": "select",
                "action": f"Selected supplier: {best.company_name}",
                "detail": f"Score {best.score}, {best.price_per_kg}/kg, {best.delivery_days} days",
                "ts": _now(),
                "session_id": context.session_id,
            }
        )

        escrow = create_escrow(best, amount, context.session_id, context.parsed.material)
        context.escrow = escrow

        await emit(
            {
                "type": "escrow",
                "data": escrow.model_dump(),
                "ts": _now(),
                "session_id": context.session_id,
            }
        )

        if escrow.requires_approval:
            controls = get_spending_controls()
            await emit(
                {
                    "type": "approval_required",
                    "escrow_id": escrow.id,
                    "supplier": escrow.supplier,
                    "amount": escrow.amount,
                    "threshold": controls["auto_approve_threshold"],
                    "ts": _now(),
                    "session_id": context.session_id,
                }
            )
        else:
            await emit(
                {
                    "type": "log",
                    "step": "escrow",
                    "action": "Escrow auto-approved",
                    "detail": "Amount is below auto-approve threshold",
                    "ts": _now(),
                    "session_id": context.session_id,
                }
            )
            await emit(
                {
                    "type": "approval_result",
                    "approved": True,
                    "escrow_id": escrow.id,
                    "ts": _now(),
                    "session_id": context.session_id,
                }
            )

        log_action("escrow_created", escrow.supplier, escrow.amount, escrow.status, context.session_id)
        return context


class ProcurementOrchestrator:
    """Simple tool-chain orchestrator with swappable tool interfaces."""

    def __init__(self, tools: Optional[List[AgentTool]] = None):
        self.tools: List[AgentTool] = tools or [
            ParseIntentTool(),
            SupplierDiscoveryTool(),
            SupplierEnrichmentTool(),
            QuoteDispatchTool(),
            EscrowTool(),
        ]

    async def run(self, query: str, session_id: str, emit: EventEmitter) -> AgentContext:
        context = AgentContext(session_id=session_id, query=query)

        try:
            for tool in self.tools:
                context = await tool.run(context, emit)

            await emit(
                {
                    "type": "pipeline_done",
                    "session_id": session_id,
                    "summary": {
                        "suppliers": len(context.suppliers),
                        "emails": context.email_dispatch_count,
                        "selected_supplier": context.selected_supplier.company_name if context.selected_supplier else None,
                        "escrow_id": context.escrow.id if context.escrow else None,
                    },
                    "ts": _now(),
                }
            )
            log_action("sourcing_complete", "pipeline", 0, "done", session_id)
            return context
        except Exception as exc:
            await emit(
                {
                    "type": "pipeline_error",
                    "session_id": session_id,
                    "message": str(exc),
                    "ts": _now(),
                }
            )
            log_action("sourcing_failed", "pipeline", 0, "error", session_id)
            raise
