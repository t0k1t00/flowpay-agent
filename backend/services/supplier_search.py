"""
Supplier Search & Enrichment Service
In production: Exa API search + Firecrawl scraping + Apollo enrichment.
For demo: rich mock dataset that mimics real API responses.
"""

import uuid
import random
from typing import List
from models import Supplier, ParsedRequest


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


def get_mock_suppliers(category: str = None) -> List[Supplier]:
    """Return all or category-filtered mock suppliers."""
    if category:
        return MOCK_SUPPLIERS.get(category.lower(), [])
    all_suppliers = []
    for v in MOCK_SUPPLIERS.values():
        all_suppliers.extend(v)
    return all_suppliers


def search_suppliers(parsed: ParsedRequest) -> List[Supplier]:
    """
    Simulate Exa API search + Firecrawl scraping.
    In production:
      - exa.search(f"{parsed.material} supplier India wholesale")
      - For each result URL: firecrawl.scrape(url)
      - Parse pricing, contact, delivery info from scraped content
    """
    category = parsed.material
    suppliers = MOCK_SUPPLIERS.get(category, MOCK_SUPPLIERS["cotton yarn"])

    # Filter by budget constraint
    in_budget = [s for s in suppliers if s.price_per_kg <= parsed.max_budget_per_kg * 1.1]
    return in_budget if in_budget else suppliers


def enrich_suppliers(suppliers: List[Supplier]) -> List[Supplier]:
    """
    Simulate Apollo/Clado enrichment:
    - GSTIN validation (GST portal API)
    - Company score computation
    - Email verification
    - Phone normalization
    In demo: returns as-is with minor randomness.
    """
    enriched = []
    for s in suppliers:
        # Simulate GSTIN validation pass
        s_copy = s.copy()
        enriched.append(s_copy)
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
