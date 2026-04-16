"""
Request Parser Service
Extracts structured procurement data from natural language queries.
In production: use LangChain + OpenAI function calling.
For demo: regex + heuristics with smart defaults.
"""

from __future__ import annotations

import json
import os
import re

import httpx

from models import ParsedRequest


def _llm_parse_request(query: str) -> ParsedRequest | None:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None

    model = os.getenv("AGENT_MODEL", "gpt-4o-mini").strip()
    prompt = (
        "Extract procurement request details and return strict JSON with keys: "
        "material, quantity_kg, max_budget_per_kg, delivery_days, location. "
        f"Input: {query}"
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You extract structured procurement fields."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        response = httpx.post("https://api.openai.com/v1/chat/completions", json=payload, headers=headers, timeout=12)
        response.raise_for_status()
        body = response.json()
        content = body.get("choices", [{}])[0].get("message", {}).get("content", "{}")
        parsed = json.loads(content)

        material = str(parsed.get("material") or "cotton yarn").strip().lower()
        quantity_kg = float(parsed.get("quantity_kg") or 1000)
        max_budget = float(parsed.get("max_budget_per_kg") or 300)
        delivery_days = int(parsed.get("delivery_days") or 7)
        location = parsed.get("location")
        location = str(location).strip() if location else None

        return ParsedRequest(
            material=material,
            quantity_kg=quantity_kg,
            max_budget_per_kg=max_budget,
            delivery_days=delivery_days,
            location=location,
            raw_query=query,
        )
    except Exception:
        return None


def parse_request(query: str) -> ParsedRequest:
    """Parse a natural language sourcing request into structured data."""
    llm_result = _llm_parse_request(query)
    if llm_result is not None:
        return llm_result

    q = query.lower()

    # Extract quantity (kg)
    qty_match = re.search(r'(\d[\d,]*)\s*kg', q)
    quantity_kg = float(qty_match.group(1).replace(',', '')) if qty_match else 1000.0

    # Extract budget per kg
    budget_match = re.search(r'[₹rs\.]*\s*(\d+)\s*(?:/kg|per\s*kg)', q)
    max_budget = float(budget_match.group(1)) if budget_match else 300.0

    # Extract delivery days
    delivery_match = re.search(r'(\d+)\s*days?', q)
    delivery_days = int(delivery_match.group(1)) if delivery_match else 7

    # Detect material
    materials = {
        'cotton yarn': ['cotton yarn', 'yarn', 'cotton'],
        'steel rod': ['steel rod', 'steel', 'iron rod'],
        'textile dye': ['dye', 'textile dye', 'fabric dye'],
        'machine parts': ['machine parts', 'spare parts', 'components'],
    }
    material = 'cotton yarn'
    for mat, keywords in materials.items():
        if any(k in q for k in keywords):
            material = mat
            break

    # Extract location
    locations = ['tamil nadu', 'coimbatore', 'mumbai', 'delhi', 'surat', 'ahmedabad', 'bangalore', 'hyderabad']
    location = None
    for loc in locations:
        if loc in q:
            location = loc.title()
            break

    return ParsedRequest(
        material=material,
        quantity_kg=quantity_kg,
        max_budget_per_kg=max_budget,
        delivery_days=delivery_days,
        location=location,
        raw_query=query
    )
