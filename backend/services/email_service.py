"""Email Automation Service — Resend API (simulated)"""

import logging
import os
import uuid
from datetime import datetime
from typing import List

from models import Supplier, ParsedRequest
from services.payment_required import post_json_with_402_retry
from services.runtime_config import strict_integrations, use_live_apis
from services.spending_controls import charge_api_usage


logger = logging.getLogger("flowpay.email")


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

    resend_key = os.getenv("RESEND_API_KEY", "").strip()
    if use_live_apis() and (not resend_key or "your_" in resend_key):
        if strict_integrations():
            raise RuntimeError("RESEND_API_KEY is required in strict live mode")
        logger.warning("RESEND_API_KEY missing in live mode, degrading to simulated email dispatch")

    can_send_live = use_live_apis() and resend_key and "your_" not in resend_key
    from_email = os.getenv("RESEND_FROM_EMAIL", "Flowpay <noreply@flowpay.ai>")

    results = []
    for supplier in suppliers:
        email = generate_email_body(supplier, parsed)
        if can_send_live:
            payload = {
                "from": from_email,
                "to": [email["to"]],
                "subject": email["subject"],
                "text": email["body"],
            }
            headers = {
                "Authorization": f"Bearer {resend_key}",
                "Content-Type": "application/json",
            }

            response = post_json_with_402_retry(
                url="https://api.resend.com/emails",
                payload=payload,
                headers=headers,
                provider="resend",
                session_id=session_id,
                timeout=15,
                circuit_key="resend",
            ).response
            body = response.json() if response.content else {}
            results.append(
                {
                    "id": str(body.get("id") or f"email_{uuid.uuid4().hex[:8]}"),
                    "to": email["to"],
                    "subject": email["subject"],
                    "status": "sent",
                    "provider": "resend",
                    "mode": "live",
                    "sent_at": datetime.now().isoformat(),
                }
            )
        else:
            results.append(
                {
                    "id": f"email_{uuid.uuid4().hex[:8]}",
                    "to": email["to"],
                    "subject": email["subject"],
                    "status": "sent",
                    "provider": "simulated",
                    "mode": "degraded",
                    "sent_at": datetime.now().isoformat(),
                }
            )
    return results
