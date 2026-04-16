"""Agent-to-agent service marketplace with micropayment-backed task execution."""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from database.db import (
    get_a2a_service_record,
    list_a2a_service_records,
    list_a2a_task_records,
    upsert_a2a_service,
    upsert_a2a_task,
)
from services.payment_required import post_json_with_402_retry
from services.runtime_config import strict_integrations
from services.spending_controls import charge_api_usage


logger = logging.getLogger("flowpay.a2a")

_SERVICES: Dict[str, Dict[str, Any]] = {}
_TASKS: Dict[str, Dict[str, Any]] = {}
_LOADED_FROM_DB = False


def _now() -> str:
    return datetime.now().isoformat()


def _ensure_loaded() -> None:
    global _LOADED_FROM_DB
    if _LOADED_FROM_DB:
        return

    try:
        for record in list_a2a_service_records():
            sid = str(record.get("id") or "")
            if sid:
                _SERVICES[sid] = record

        for task in list_a2a_task_records(limit=5000):
            tid = str(task.get("id") or "")
            if tid:
                _TASKS[tid] = task
    except Exception:
        pass

    _LOADED_FROM_DB = True


def register_service(
    *,
    name: str,
    capability: str,
    price_per_unit: float,
    seller_agent_id: str,
    session_id: str,
    endpoint_url: Optional[str] = None,
    currency: str = "USD",
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    _ensure_loaded()

    if price_per_unit <= 0:
        raise ValueError("price_per_unit must be greater than zero")

    service_id = f"svc_{uuid.uuid4().hex[:10]}"
    record = {
        "id": service_id,
        "name": name.strip(),
        "capability": capability.strip().lower(),
        "endpoint_url": endpoint_url.strip() if endpoint_url else None,
        "price_per_unit": round(float(price_per_unit), 6),
        "currency": currency.strip().upper() or "USD",
        "seller_agent_id": seller_agent_id.strip(),
        "status": "active",
        "session_id": session_id,
        "metadata": metadata or {},
        "created_at": _now(),
        "updated_at": _now(),
    }

    _SERVICES[service_id] = record
    try:
        upsert_a2a_service(record)
    except Exception:
        if strict_integrations():
            raise RuntimeError("Failed to persist A2A service registration")

    return record


def list_services(
    *,
    capability: Optional[str] = None,
    max_price: Optional[float] = None,
    status: str = "active",
) -> List[Dict[str, Any]]:
    _ensure_loaded()

    try:
        records = list_a2a_service_records(
            capability=capability,
            max_price=max_price,
            status=status,
        )
        if records:
            return records
    except Exception:
        if strict_integrations():
            raise RuntimeError("Failed to list A2A services from persistence layer")

    rows = list(_SERVICES.values())
    if capability:
        rows = [row for row in rows if str(row.get("capability", "")).lower() == capability.lower()]
    if status:
        rows = [row for row in rows if str(row.get("status", "")).lower() == status.lower()]
    if max_price is not None:
        rows = [row for row in rows if float(row.get("price_per_unit", 0.0)) <= max_price]
    return sorted(rows, key=lambda row: float(row.get("price_per_unit", 0.0)))


def _execute_remote_task(service: Dict[str, Any], task: Dict[str, Any]) -> Dict[str, Any]:
    endpoint = str(service.get("endpoint_url") or "").strip()
    if not endpoint:
        return {
            "mode": "simulated",
            "status": "completed",
            "output": {
                "message": "No endpoint configured; returning simulated execution output",
                "service": service.get("name"),
            },
        }

    headers = {
        "Content-Type": "application/json",
    }
    shared_token = os.getenv("A2A_SHARED_TOKEN", "").strip()
    if shared_token:
        headers["Authorization"] = f"Bearer {shared_token}"

    payload = {
        "task_id": task["id"],
        "service_id": task["service_id"],
        "units": task["units"],
        "requester_agent_id": task["requester_agent_id"],
        "payload": task.get("payload", {}),
    }

    response = post_json_with_402_retry(
        url=endpoint,
        payload=payload,
        headers=headers,
        provider="a2a_service",
        session_id=task["session_id"],
        timeout=20,
        circuit_key="a2a_service",
    ).response

    body = response.json() if response.content else {}
    return {
        "mode": "remote",
        "status": "completed",
        "output": body if isinstance(body, dict) else {"raw": body},
    }


def execute_task(
    *,
    service_id: str,
    requester_agent_id: str,
    units: float,
    payload: Optional[Dict[str, Any]],
    session_id: str,
) -> Dict[str, Any]:
    _ensure_loaded()

    if units <= 0:
        raise ValueError("units must be greater than zero")

    service = _SERVICES.get(service_id)
    if not service:
        service = get_a2a_service_record(service_id)
        if service:
            _SERVICES[service_id] = service

    if not service:
        raise KeyError(f"A2A service {service_id} not found")

    if str(service.get("status", "")).lower() != "active":
        raise ValueError("A2A service is not active")

    unit_price = round(float(service.get("price_per_unit", 0.0)), 6)
    total_amount = round(unit_price * float(units), 6)
    if total_amount <= 0:
        raise ValueError("Computed total amount must be greater than zero")

    charge_api_usage(
        provider="a2a_marketplace",
        amount=total_amount,
        session_id=session_id,
        metadata={
            "service_id": service_id,
            "capability": service.get("capability"),
            "units": units,
            "unit_price": unit_price,
        },
    )

    task_id = f"a2a_{uuid.uuid4().hex[:10]}"
    task = {
        "id": task_id,
        "service_id": service_id,
        "requester_agent_id": requester_agent_id,
        "seller_agent_id": service.get("seller_agent_id"),
        "units": float(units),
        "unit_price": unit_price,
        "total_amount": total_amount,
        "currency": service.get("currency", "USD"),
        "status": "running",
        "payload": payload or {},
        "result": None,
        "session_id": session_id,
        "created_at": _now(),
        "completed_at": None,
    }

    _TASKS[task_id] = task
    try:
        upsert_a2a_task(task)
    except Exception:
        if strict_integrations():
            raise RuntimeError("Failed to persist A2A task before execution")

    try:
        remote_result = _execute_remote_task(service, task)
        task["status"] = "completed"
        task["result"] = remote_result
    except Exception as exc:
        if strict_integrations():
            task["status"] = "failed"
            task["result"] = {"error": str(exc), "mode": "strict_failure"}
            task["completed_at"] = _now()
            upsert_a2a_task(task)
            raise RuntimeError("A2A task execution failed") from exc

        logger.warning("a2a remote execution degraded: %s", str(exc))
        task["status"] = "completed"
        task["result"] = {
            "mode": "degraded_fallback",
            "status": "completed",
            "output": {
                "message": "Remote call failed; returned deterministic local fallback",
                "error": str(exc),
            },
        }

    task["completed_at"] = _now()
    _TASKS[task_id] = task
    try:
        upsert_a2a_task(task)
    except Exception:
        if strict_integrations():
            raise RuntimeError("Failed to persist final A2A task state")

    return task


def list_tasks(limit: int = 100, session_id: Optional[str] = None) -> List[Dict[str, Any]]:
    _ensure_loaded()
    if limit <= 0:
        return []

    try:
        rows = list_a2a_task_records(limit=limit, session_id=session_id)
        if rows:
            return rows
    except Exception:
        if strict_integrations():
            raise RuntimeError("Failed to list A2A tasks from persistence layer")

    rows = list(_TASKS.values())
    if session_id:
        rows = [row for row in rows if row.get("session_id") == session_id]
    return rows[-limit:]
