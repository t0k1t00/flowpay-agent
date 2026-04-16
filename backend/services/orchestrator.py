"""Agent orchestration framework for autonomous sourcing workflows."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import os
from typing import Awaitable, Callable, Dict, List, Optional, Protocol

import httpx

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
    executed_tools: List[str] = field(default_factory=list)


class AgentTool(Protocol):
    name: str

    async def run(self, context: AgentContext, emit: EventEmitter) -> AgentContext:
        ...


class AgentDecisionEngine:
    """Chooses the next tool using an LLM when available, with safe heuristic fallback."""

    def __init__(self) -> None:
        self.openai_api_key = os.getenv("OPENAI_API_KEY", "").strip()
        self.model = os.getenv("AGENT_MODEL", "gpt-4o-mini").strip()

    def _heuristic_choice(self, context: AgentContext, available_tools: List[str]) -> str:
        preferred = [
            (context.parsed is None, "parse_intent"),
            (context.parsed is not None and not context.suppliers, "supplier_discovery"),
            (context.suppliers and context.selected_supplier is None and context.escrow is None, "supplier_enrichment"),
            (context.suppliers and context.email_dispatch_count == 0, "quote_dispatch"),
            (context.suppliers and context.escrow is None, "escrow_management"),
        ]
        for condition, tool_name in preferred:
            if condition and tool_name in available_tools:
                return tool_name
        return available_tools[0]

    async def decide(
        self,
        context: AgentContext,
        available_tools: List[str],
        observation: str,
    ) -> Dict[str, str]:
        heuristic = self._heuristic_choice(context, available_tools)
        if not self.openai_api_key:
            return {
                "tool": heuristic,
                "source": "heuristic",
                "reason": "OPENAI_API_KEY not configured",
            }

        prompt = (
            "You are a procurement agent controller. Choose exactly one tool name from the provided list. "
            "Return only the tool name, no extra text.\n"
            f"available_tools={available_tools}\n"
            f"context={{parsed:{context.parsed is not None}, suppliers:{len(context.suppliers)}, "
            f"emails:{context.email_dispatch_count}, escrow_created:{context.escrow is not None}}}\n"
            f"observation={observation}"
        )
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "You choose the next tool in an autonomous workflow."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
        }
        headers = {
            "Authorization": f"Bearer {self.openai_api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post("https://api.openai.com/v1/chat/completions", json=payload, headers=headers)
            response.raise_for_status()
            body = response.json()
            content = (
                body.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
                .strip()
            )
            for tool_name in available_tools:
                if tool_name == content:
                    return {
                        "tool": tool_name,
                        "source": "llm",
                        "reason": "Selected by LLM planner",
                    }
        except Exception:
            pass

        return {
            "tool": heuristic,
            "source": "heuristic_fallback",
            "reason": "LLM planner unavailable or returned invalid tool",
        }


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
    """Agent-loop orchestrator with dynamic tool selection."""

    def __init__(self, tools: Optional[List[AgentTool]] = None, max_steps: int = 12):
        self.tools: List[AgentTool] = tools or [
            ParseIntentTool(),
            SupplierDiscoveryTool(),
            SupplierEnrichmentTool(),
            QuoteDispatchTool(),
            EscrowTool(),
        ]
        self.tool_map: Dict[str, AgentTool] = {tool.name: tool for tool in self.tools}
        self.decision_engine = AgentDecisionEngine()
        self.max_steps = max_steps

    @staticmethod
    def _is_complete(context: AgentContext) -> bool:
        return context.escrow is not None

    @staticmethod
    def _tool_ready(tool_name: str, context: AgentContext) -> bool:
        if tool_name == "parse_intent":
            return context.parsed is None
        if tool_name == "supplier_discovery":
            return context.parsed is not None and not context.suppliers
        if tool_name == "supplier_enrichment":
            return context.parsed is not None and bool(context.suppliers)
        if tool_name == "quote_dispatch":
            return context.parsed is not None and bool(context.suppliers) and context.email_dispatch_count == 0
        if tool_name == "escrow_management":
            return context.parsed is not None and bool(context.suppliers) and context.escrow is None
        return True

    def _eligible_tools(self, context: AgentContext) -> List[str]:
        return [name for name in self.tool_map if self._tool_ready(name, context)]

    async def run(self, query: str, session_id: str, emit: EventEmitter) -> AgentContext:
        context = AgentContext(session_id=session_id, query=query)
        observation = "Workflow initialized"

        try:
            for step in range(1, self.max_steps + 1):
                if self._is_complete(context):
                    break

                available_tools = self._eligible_tools(context)
                if not available_tools:
                    raise RuntimeError("No eligible tools remain for agent progression")

                decision = await self.decision_engine.decide(context, available_tools, observation)
                chosen = decision["tool"]
                if chosen not in available_tools:
                    chosen = available_tools[0]

                await emit(
                    {
                        "type": "agent_reasoning",
                        "session_id": session_id,
                        "step": step,
                        "available_tools": available_tools,
                        "decision": decision,
                        "ts": _now(),
                    }
                )

                await emit(
                    {
                        "type": "tool_call",
                        "session_id": session_id,
                        "tool": chosen,
                        "step": step,
                        "ts": _now(),
                    }
                )

                tool = self.tool_map[chosen]
                context = await tool.run(context, emit)
                context.executed_tools.append(chosen)

                observation = (
                    f"Executed {chosen}; suppliers={len(context.suppliers)}, "
                    f"emails={context.email_dispatch_count}, escrow={context.escrow.id if context.escrow else 'none'}"
                )
                await emit(
                    {
                        "type": "tool_result",
                        "session_id": session_id,
                        "tool": chosen,
                        "step": step,
                        "status": "ok",
                        "observation": observation,
                        "ts": _now(),
                    }
                )

            if not self._is_complete(context):
                raise RuntimeError("Agent loop ended before escrow creation")

            await emit(
                {
                    "type": "pipeline_done",
                    "session_id": session_id,
                    "summary": {
                        "suppliers": len(context.suppliers),
                        "emails": context.email_dispatch_count,
                        "selected_supplier": context.selected_supplier.company_name if context.selected_supplier else None,
                        "escrow_id": context.escrow.id if context.escrow else None,
                        "executed_tools": context.executed_tools,
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
                    "error_type": exc.__class__.__name__,
                    "ts": _now(),
                }
            )
            log_action("sourcing_failed", "pipeline", 0, "error", session_id)
            raise
