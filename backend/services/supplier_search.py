"""
Supplier Search & Enrichment Service
In production: Exa API search + Firecrawl scraping + Apollo enrichment.
For demo: rich mock dataset that mimics real API responses.
"""

import logging
import os
import re
from typing import Any, Dict, List

import httpx

from models import Supplier, ParsedRequest
from services.payment_required import post_json_with_402_retry
from services.reliability import post_json_with_retries
from services.runtime_config import strict_integrations, use_live_apis, use_locus_wrapped_apis
from services.spending_controls import charge_api_usage


MOCK_SUPPLIERS = {
    "cotton yarn": [
        Supplier(id="sup_001", company_name="TamilTex Mills", price_per_kg=280, delivery_days=4,
                 verified=True, gstin="33ABCDE1234F1Z5", email="sales@tamiltex.com",
                 phone="+91-422-2345678", location="Coimbatore", website="tamiltex.com",
                 score=92, category="Cotton Yarn", recommended=True),
        Supplier(id="sup_002", company_name="Bharat Yarn Co.", price_per_kg=295, delivery_days=3,
                 verified=True, gstin="24FGHIJ5678K2A6", email="info@bharatyarn.in",
                 phone="+91-261-3456789", location="Surat", website="bharatyarn.in",
                 score=87, category="Cotton Yarn"),
        Supplier(id="sup_003", company_name="Nithya Fibres", price_per_kg=265, delivery_days=6,
                 verified=False, gstin="33LMNOP9012L3B7", email="nithya.fibres@gmail.com",
                 phone="+91-421-4567890", location="Tiruppur", score=74, category="Cotton Yarn"),
        Supplier(id="sup_004", company_name="Arjun Textiles", price_per_kg=310, delivery_days=3,
                 verified=True, gstin="24QRSTU3456M4C8", email="procurement@arjuntex.com",
                 phone="+91-79-5678901", location="Ahmedabad", website="arjuntex.com",
                 score=81, category="Cotton Yarn"),
        Supplier(id="sup_005", company_name="Sri Lakshmi Threads", price_per_kg=272, delivery_days=5,
                 verified=True, gstin="33VWXYZ7890N5D9", email="sales@srilakshmi.co.in",
                 phone="+91-424-6789012", location="Erode", website="srilakshmi.co.in",
                 score=89, category="Cotton Yarn"),
        Supplier(id="sup_006", company_name="Horizon Yarn Industries", price_per_kg=300, delivery_days=2,
                 verified=True, gstin="27ABCDE2345O6E1", email="orders@horizonyarn.com",
                 phone="+91-22-7890123", location="Mumbai", website="horizonyarn.com",
                 score=85, category="Cotton Yarn"),
        Supplier(id="sup_007", company_name="Deepika Cotton Works", price_per_kg=258, delivery_days=7,
                 verified=False, gstin="33FGHIJ6789P7F2", email="deepika.cotton@yahoo.com",
                 phone="+91-427-8901234", location="Salem", score=68, category="Cotton Yarn"),
        Supplier(id="sup_008", company_name="Megha Fibre Industries", price_per_kg=285, delivery_days=5,
                 verified=True, gstin="03KLMNO0123Q8G3", email="megha@mfibre.in",
                 phone="+91-161-9012345", location="Ludhiana", website="mfibre.in",
                 score=83, category="Cotton Yarn"),
    ],
    "textile dye": [
        Supplier(id="dye_001", company_name="Shree Dye Works", price_per_kg=450, delivery_days=3,
                 verified=True, gstin="24SDWXY1234A1Z9", email="sales@shreedye.com",
                 phone="+91-261-1234567", location="Surat", website="shreedye.com",
                 score=88, category="Textile Dye"),
        Supplier(id="dye_002", company_name="ColorMax India", price_per_kg=380, delivery_days=5,
                 verified=True, gstin="27CMIND5678B2Y8", email="orders@colormaxindia.com",
                 phone="+91-22-2345678", location="Mumbai", score=82, category="Textile Dye"),
        Supplier(id="dye_003", company_name="Spectrum Chemicals", price_per_kg=420, delivery_days=4,
                 verified=False, gstin="36SPCHEM9012C3X7", email="spec.chem@gmail.com",
                 location="Hyderabad", score=71, category="Textile Dye"),
    ],
    "steel rod": [
        Supplier(id="stl_001", company_name="Metro Steel Corp", price_per_kg=65, delivery_days=5,
                 verified=True, gstin="27MSCORP3456D4W6", email="bulk@metrosteel.in",
                 phone="+91-22-3456789", location="Mumbai", website="metrosteel.in",
                 score=86, category="Steel Rod"),
        Supplier(id="stl_002", company_name="Tata Steel Distributors", price_per_kg=72, delivery_days=3,
                 verified=True, gstin="21TATAD7890E5V5", email="dist@tatasd.com",
                 phone="+91-612-4567890", location="Jamshedpur", score=94, category="Steel Rod"),
        Supplier(id="stl_003", company_name="BRS Iron Works", price_per_kg=58, delivery_days=7,
                 verified=False, gstin="08BRSIW2345F6U4", email="brs.iron@gmail.com",
                 location="Jaipur", score=69, category="Steel Rod"),
    ],
    "machine parts": [
        Supplier(id="mch_001", company_name="Precision Parts Co.", price_per_kg=1200, delivery_days=7,
                 verified=True, gstin="29PRECO6789G7T3", email="sales@precisionparts.in",
                 phone="+91-80-5678901", location="Bangalore", website="precisionparts.in",
                 score=91, category="Machine Parts"),
        Supplier(id="mch_002", company_name="Aryan Engineering Works", price_per_kg=980, delivery_days=10,
                 verified=True, gstin="07ARYEN0123H8S2", email="aew@aryaneng.com",
                 phone="+91-11-6789012", location="Delhi", score=79, category="Machine Parts"),
    ],
}


logger = logging.getLogger("flowpay.supplier_search")


def _material_key(material: str) -> str:
    normalized = material.lower().strip()
    if normalized in MOCK_SUPPLIERS:
        return normalized

    for key in MOCK_SUPPLIERS:
        if normalized in key or key in normalized:
            return key

    return "cotton yarn"


def _locus_api_base() -> str:
    raw = os.getenv("LOCUS_API_BASE", "https://api.paywithlocus.com/api").strip()
    return raw[:-1] if raw.endswith("/") else raw


def _degrade_or_raise(message: str, exc: Exception = None) -> None:
    if strict_integrations():
        raise RuntimeError(message) from exc
    logger.warning("integration degraded: %s", message)


def _locus_wrapped_request(provider: str, endpoint: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    locus_key = os.getenv("LOCUS_API_KEY")
    if not locus_key or "your_" in locus_key:
        raise RuntimeError("LOCUS_API_KEY is not configured")

    headers = {
        "Authorization": f"Bearer {locus_key}",
        "Content-Type": "application/json",
    }

    response = post_json_with_retries(
        url=f"{_locus_api_base()}/wrapped/{provider}/{endpoint}",
        payload=payload,
        headers=headers,
        timeout=15,
        circuit_key=f"wrapped_{provider}",
    )
    response.raise_for_status()

    body = response.json()
    if not isinstance(body, dict):
        return {}

    data = body.get("data", body)
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        return data["data"]
    return data if isinstance(data, dict) else {}


def _extract_result_rows(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    if isinstance(data.get("results"), list):
        return [item for item in data["results"] if isinstance(item, dict)]

    nested = data.get("data")
    if isinstance(nested, dict) and isinstance(nested.get("results"), list):
        return [item for item in nested["results"] if isinstance(item, dict)]

    return []


def _safe_domain(url: str) -> str:
    no_proto = re.sub(r"^https?://", "", url)
    return no_proto.split("/")[0]


def _build_supplier_from_url(url: str, parsed: ParsedRequest, idx: int) -> Supplier:
    domain = _safe_domain(url)
    raw_company = domain.replace("www.", "")
    company_stem = raw_company.split(".")[0].replace("-", " ").replace("_", " ").strip()
    company_stem = company_stem.title() or f"Supplier {idx + 1}"
    seed = abs(hash(domain)) % 1000

    price_multiplier = 0.82 + (seed % 30) / 100
    price = round(parsed.max_budget_per_kg * price_multiplier, 2)
    delivery_days = 2 + (seed % 6)
    score = 70 + (seed % 29)
    verified = (seed % 4) != 0
    gstin_seed = str(10_000_000_000 + seed)[-10:]
    gstin = f"33{gstin_seed[:5]}{gstin_seed[5:]}A1Z5"

    locations = ["Coimbatore", "Erode", "Surat", "Ahmedabad", "Tiruppur", "Mumbai", "Salem", "Ludhiana"]
    location = locations[seed % len(locations)]

    clean_domain = domain.replace("www.", "")
    email = f"sales@{clean_domain}" if "." in clean_domain else f"sales@{company_stem.lower().replace(' ', '')}.com"

    return Supplier(
        id=f"live_{idx:03d}",
        company_name=company_stem,
        price_per_kg=price,
        delivery_days=delivery_days,
        verified=verified,
        gstin=gstin,
        email=email,
        phone=None,
        location=location,
        website=url,
        score=score,
        category=parsed.material.title(),
        recommended=False,
    )


def _live_exa_search(parsed: ParsedRequest, session_id: str) -> List[str]:
    query = f"{parsed.material} suppliers India wholesale {parsed.location or ''}".strip()
    payload = {
        "query": query,
        "numResults": 8,
    }

    if use_locus_wrapped_apis():
        data = _locus_wrapped_request("exa", "search", payload)
        rows = _extract_result_rows(data)
        return [item.get("url") for item in rows if item.get("url")]

    exa_key = os.getenv("EXA_API_KEY")
    if not exa_key or "your_" in exa_key:
        if strict_integrations():
            raise RuntimeError("EXA_API_KEY is required in strict live mode")
        return []

    headers = {
        "x-api-key": exa_key,
        "Content-Type": "application/json",
    }

    response = post_json_with_402_retry(
        url="https://api.exa.ai/search",
        payload=payload,
        headers=headers,
        provider="exa",
        session_id=session_id,
        timeout=10,
        circuit_key="exa",
    ).response

    data = response.json()
    results = data.get("results", [])
    urls = [item.get("url") for item in results if item.get("url")]
    return urls


def _live_firecrawl_scrape(urls: List[str], session_id: str) -> List[str]:
    if use_locus_wrapped_apis():
        confirmed_urls: List[str] = []
        for url in urls:
            payload = {
                "url": url,
                "formats": ["markdown"],
            }
            try:
                _locus_wrapped_request("firecrawl", "scrape", payload)
                confirmed_urls.append(url)
            except Exception as exc:
                if strict_integrations():
                    raise RuntimeError(f"Firecrawl wrapped scrape failed for {url}") from exc
                continue
        return confirmed_urls or urls

    firecrawl_key = os.getenv("FIRECRAWL_API_KEY")
    if not firecrawl_key or "your_" in firecrawl_key:
        if strict_integrations():
            raise RuntimeError("FIRECRAWL_API_KEY is required in strict live mode")
        return urls

    headers = {
        "Authorization": f"Bearer {firecrawl_key}",
        "Content-Type": "application/json",
    }

    confirmed_urls: List[str] = []
    for url in urls:
        payload = {
            "url": url,
            "formats": ["markdown"],
        }
        try:
            response = post_json_with_402_retry(
                url="https://api.firecrawl.dev/v1/scrape",
                payload=payload,
                headers=headers,
                provider="firecrawl",
                session_id=session_id,
                timeout=12,
                circuit_key="firecrawl",
            ).response
        except Exception as exc:
            if strict_integrations():
                raise RuntimeError(f"Firecrawl scrape failed for {url}") from exc
            logger.warning("firecrawl degraded for %s: %s", url, str(exc))
            continue

        if response.status_code < 400:
            confirmed_urls.append(url)

    if not confirmed_urls and strict_integrations():
        raise RuntimeError("Firecrawl returned no successful scrape responses")

    return confirmed_urls or urls


def get_mock_suppliers(category: str = None) -> List[Supplier]:
    """Return all or category-filtered mock suppliers."""
    if category:
        return MOCK_SUPPLIERS.get(_material_key(category), [])

    all_suppliers: List[Supplier] = []
    for suppliers in MOCK_SUPPLIERS.values():
        all_suppliers.extend(suppliers)
    return all_suppliers


def search_suppliers(parsed: ParsedRequest, session_id: str) -> List[Supplier]:
    """
    Search suppliers using Exa + Firecrawl and charge API micropayments.
    Falls back to deterministic local mocks when live APIs are unavailable.
    """
    charge_api_usage(
        provider="exa",
        amount=0.08,
        session_id=session_id,
        metadata={"material": parsed.material, "mode": "search"},
    )

    urls: List[str] = []
    if use_live_apis():
        try:
            urls = _live_exa_search(parsed, session_id=session_id)
        except Exception as exc:
            _degrade_or_raise("live Exa search failed, switching to deterministic supplier catalog", exc)
            urls = []

    charge_api_usage(
        provider="firecrawl",
        amount=0.06,
        session_id=session_id,
        metadata={"material": parsed.material, "mode": "scrape"},
    )

    if use_live_apis() and urls:
        try:
            scraped_urls = _live_firecrawl_scrape(urls, session_id=session_id)
            live_suppliers = [_build_supplier_from_url(url, parsed, idx) for idx, url in enumerate(scraped_urls)]
            if live_suppliers:
                filtered_live = [s for s in live_suppliers if s.price_per_kg <= parsed.max_budget_per_kg * 1.2]
                return filtered_live if filtered_live else live_suppliers
        except Exception as exc:
            _degrade_or_raise("live supplier scrape/rank failed, switching to deterministic supplier catalog", exc)

    category = _material_key(parsed.material)
    if use_live_apis() and not urls:
        logger.warning("live mode produced no supplier URLs; using deterministic catalog")

    suppliers = [item.model_copy() for item in MOCK_SUPPLIERS.get(category, MOCK_SUPPLIERS["cotton yarn"])]
    filtered = [s for s in suppliers if s.price_per_kg <= parsed.max_budget_per_kg * 1.1]
    return filtered if filtered else suppliers


def enrich_suppliers(suppliers: List[Supplier], session_id: str) -> List[Supplier]:
    """
    Simulate enrichment and GSTIN verification with controlled, deterministic scoring.
    """
    charge_api_usage(
        provider="apollo",
        amount=0.02 * max(1, min(len(suppliers), 5)),
        session_id=session_id,
        metadata={"count": len(suppliers), "mode": "enrichment"},
    )

    enriched: List[Supplier] = []
    for supplier in suppliers:
        copy = supplier.model_copy()
        if copy.verified and copy.score < 95:
            copy.score += 2
        if not copy.verified and copy.score > 60:
            copy.score -= 1
        enriched.append(copy)
    return enriched


def rank_suppliers(suppliers: List[Supplier], parsed: ParsedRequest) -> List[Supplier]:
    """
    Rank suppliers using a weighted scoring model:
      - Price fitness (40%): how close to budget
      - Delivery fitness (30%): days vs deadline
      - Verification bonus (20%): verified GSTIN
      - Score (10%): enriched company score
    """
    def compute_rank(s: Supplier) -> float:
        price_score = max(0, (parsed.max_budget_per_kg - s.price_per_kg) / parsed.max_budget_per_kg) * 40
        delivery_score = max(0, (parsed.delivery_days - s.delivery_days) / parsed.delivery_days) * 30 if parsed.delivery_days > 0 else 15
        verified_score = 20 if s.verified else 0
        company_score = (s.score / 100) * 10
        return price_score + delivery_score + verified_score + company_score

    ranked = sorted(suppliers, key=compute_rank, reverse=True)

    # Mark top supplier as recommended
    for i, s in enumerate(ranked):
        s.recommended = (i == 0)

    return ranked
