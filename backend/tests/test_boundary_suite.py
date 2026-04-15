"""Boundary tests for spending controls, card limits, and audit integrity."""

import unittest

from fastapi.testclient import TestClient

from main import app


class BoundarySuiteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)
        baseline = {
            "auto_approve_threshold": 2000,
            "monthly_spend_limit": 1_000_000_000,
            "daily_spend_limit": 1_000_000_000,
            "allowed_categories": ["cotton yarn", "textile dye", "steel rod", "machine parts"],
        }
        response = self.client.put("/api/spending-controls", json=baseline)
        self.assertEqual(response.status_code, 200)

    def test_budget_breach_is_blocked(self) -> None:
        tighten = {
            "daily_spend_limit": 0.5,
            "monthly_spend_limit": 1_000_000_000,
            "auto_approve_threshold": 2000,
            "allowed_categories": ["cotton yarn", "textile dye", "steel rod", "machine parts"],
        }
        update = self.client.put("/api/spending-controls", json=tighten)
        self.assertEqual(update.status_code, 200)

        breach = self.client.post(
            "/api/create-escrow",
            json={
                "supplier": "Boundary Supplier",
                "amount": 10,
                "session_id": "test_budget_breach",
                "category": "cotton yarn",
            },
        )
        self.assertEqual(breach.status_code, 400)
        self.assertIn("Daily spending limit exceeded", breach.text)

    def test_unauthorized_vendor_category_is_blocked(self) -> None:
        restrict = {
            "auto_approve_threshold": 2000,
            "monthly_spend_limit": 1_000_000_000,
            "daily_spend_limit": 1_000_000_000,
            "allowed_categories": ["cotton yarn"],
        }
        update = self.client.put("/api/spending-controls", json=restrict)
        self.assertEqual(update.status_code, 200)

        disallowed = self.client.post(
            "/api/create-escrow",
            json={
                "supplier": "Blocked Category Supplier",
                "amount": 100,
                "session_id": "test_category_block",
                "category": "electronics",
            },
        )
        self.assertEqual(disallowed.status_code, 400)
        self.assertIn("not allowed", disallowed.text.lower())

    def test_virtual_card_limit_boundary_is_enforced(self) -> None:
        created = self.client.post(
            "/api/virtual-cards",
            json={
                "spend_limit": 1000,
                "session_id": "test_card_limit",
                "alias": "Boundary Card",
                "merchant_lock": "GSTN",
            },
        )
        self.assertEqual(created.status_code, 200)
        card_id = created.json()["id"]

        over_debit = self.client.post(
            "/api/virtual-cards/debit",
            json={
                "card_id": card_id,
                "amount": 1200,
                "session_id": "test_card_limit",
                "reason": "over_limit_check",
            },
        )
        self.assertEqual(over_debit.status_code, 400)
        self.assertIn("available limit", over_debit.text.lower())

    def test_gst_automation_writes_audit_records(self) -> None:
        card = self.client.post(
            "/api/virtual-cards",
            json={
                "spend_limit": 2500,
                "session_id": "test_gst_audit",
                "alias": "GST Run Card",
                "merchant_lock": "GSTN",
            },
        )
        self.assertEqual(card.status_code, 200)
        card_id = card.json()["id"]

        run = self.client.post(
            "/api/gstn/automate",
            json={
                "gstin": "33ABCDE1234F1Z5",
                "filing_period": "2026-04",
                "tax_amount": 900,
                "session_id": "test_gst_audit",
                "card_id": card_id,
                "auto_pay": True,
            },
        )
        self.assertEqual(run.status_code, 200)
        payload = run.json()
        self.assertEqual(payload.get("status"), "completed")

        audit = self.client.get("/api/audit-trail")
        self.assertEqual(audit.status_code, 200)
        actions = [row.get("action") for row in audit.json()]
        self.assertIn("virtual_card_provisioned", actions)
        self.assertIn("gstn_automation_run", actions)


if __name__ == "__main__":
    unittest.main()
