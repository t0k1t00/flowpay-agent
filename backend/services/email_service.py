"""Email Automation Service — Resend API (simulated)"""

import uuid
from datetime import datetime
from typing import List

from models import Supplier, ParsedRequest
from services.spending_controls import charge_api_usage


def generate_email_body(supplier: Supplier, parsed: ParsedRequest) -> dict:
    return {
        "to": supplier.email,
        "subject": f"Quote Request for {parsed.material.title()} Procurement",
        "body": f"""Dear {supplier.company_name},

We are looking to source {parsed.quantity_kg}kg of {parsed.material} 
under ₹{parsed.max_budget_per_kg}/kg with delivery within {parsed.delivery_days} days.

Please share your best quote, availability, and delivery timeline.

Payment via secure escrow. GSTIN invoice required.

Best regards,
PayGentic Procurement Agent
"""
    }


def send_quote_emails(suppliers: List[Supplier], parsed: ParsedRequest, session_id: str = "manual") -> List[dict]:
    """
    Send quote request emails via Resend API.
    In production: 
      import resend
      resend.api_key = os.environ["RESEND_API_KEY"]
      resend.Emails.send({from: ..., to: ..., subject: ..., text: ...})
    """
    if suppliers:
        charge_api_usage(
            provider="resend",
            amount=0.01 * len(suppliers),
            session_id=session_id,
            metadata={"emails": len(suppliers), "material": parsed.material},
        )

    results = []
    for supplier in suppliers:
        email = generate_email_body(supplier, parsed)
        # Simulated send — replace with resend.Emails.send(email)
        results.append({
            "id": f"email_{uuid.uuid4().hex[:8]}",
            "to": email["to"],
            "subject": email["subject"],
            "status": "sent",
            "sent_at": datetime.now().isoformat()
        })
    return results
