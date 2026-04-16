"""Resilience helpers: retries and simple circuit breaker."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, Optional

import httpx


@dataclass
class _Circuit:
    failures: int = 0
    opened_until: float = 0.0


_CIRCUITS: Dict[str, _Circuit] = {}


def _now() -> float:
    return time.time()


def _get_circuit(key: str) -> _Circuit:
    if key not in _CIRCUITS:
        _CIRCUITS[key] = _Circuit()
    return _CIRCUITS[key]


def _assert_circuit_closed(key: Optional[str]) -> None:
    if not key:
        return

    circuit = _get_circuit(key)
    if circuit.opened_until > _now():
        raise RuntimeError(f"Circuit breaker open for '{key}'")


def _register_success(key: Optional[str]) -> None:
    if not key:
        return

    circuit = _get_circuit(key)
    circuit.failures = 0
    circuit.opened_until = 0.0


def _register_failure(key: Optional[str], threshold: int, open_seconds: float) -> None:
    if not key:
        return

    circuit = _get_circuit(key)
    circuit.failures += 1
    if circuit.failures >= threshold:
        circuit.opened_until = _now() + open_seconds


def post_json_with_retries(
    url: str,
    payload: dict,
    headers: dict,
    timeout: float = 15,
    max_retries: int = 2,
    base_backoff_seconds: float = 0.4,
    circuit_key: Optional[str] = None,
    circuit_failure_threshold: int = 3,
    circuit_open_seconds: float = 20.0,
) -> httpx.Response:
    """POST JSON with retry and basic circuit breaker behavior."""
    _assert_circuit_closed(circuit_key)

    last_exc: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        try:
            response = httpx.post(url, json=payload, headers=headers, timeout=timeout)
            if response.status_code >= 500:
                raise httpx.HTTPStatusError(
                    f"Server error {response.status_code} from {url}",
                    request=response.request,
                    response=response,
                )

            _register_success(circuit_key)
            return response
        except (httpx.TimeoutException, httpx.RequestError, httpx.HTTPStatusError) as exc:
            last_exc = exc
            _register_failure(circuit_key, circuit_failure_threshold, circuit_open_seconds)
            if attempt >= max_retries:
                break

            sleep_seconds = base_backoff_seconds * (2 ** attempt)
            time.sleep(sleep_seconds)

    if last_exc:
        raise last_exc
    raise RuntimeError("Unknown retry failure")
