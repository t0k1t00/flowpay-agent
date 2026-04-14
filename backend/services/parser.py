"""
Request Parser Service
Extracts structured procurement data from natural language queries.
In production: use LangChain + OpenAI function calling.
For demo: regex + heuristics with smart defaults.
"""

import re
from models import ParsedRequest


def parse_request(query: str) -> ParsedRequest:
    """Parse a natural language sourcing request into structured data."""
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
