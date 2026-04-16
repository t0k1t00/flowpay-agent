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

    def test_readiness_endpoint_reports_ready(self) -> None:
        response = self.client.get("/health/ready")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload.get("ready"))
        self.assertTrue(payload.get("db_ready"))

    def test_gst_runs_endpoint_returns_recent_run(self) -> None:
        run = self.client.post(
            "/api/gstn/automate",
            json={
                "gstin": "33ABCDE1234F1Z5",
                "filing_period": "2026-04",
                "tax_amount": 125,
                "session_id": "test_gst_runs",
                "auto_pay": False,
            },
        )
        self.assertEqual(run.status_code, 200)
        run_id = run.json().get("run_id")

        listing = self.client.get("/api/gstn/runs?limit=20")
        self.assertEqual(listing.status_code, 200)
        ids = [row.get("run_id") for row in listing.json()]
        self.assertIn(run_id, ids)

    def test_virtual_card_transactions_endpoint_returns_debit(self) -> None:
        created = self.client.post(
            "/api/virtual-cards",
            json={
                "spend_limit": 1500,
                "session_id": "test_card_txn_list",
                "alias": "Txn Test Card",
                "merchant_lock": "GSTN",
            },
        )
        self.assertEqual(created.status_code, 200)
        card_id = created.json().get("id")

        debit = self.client.post(
            "/api/virtual-cards/debit",
            json={
                "card_id": card_id,
                "amount": 450,
                "session_id": "test_card_txn_list",
                "reason": "history_check",
            },
        )
        self.assertEqual(debit.status_code, 200)
        txn_id = debit.json().get("id")

        history = self.client.get(f"/api/virtual-cards/{card_id}/transactions?limit=20")
        self.assertEqual(history.status_code, 200)
        ids = [row.get("id") for row in history.json()]
        self.assertIn(txn_id, ids)

    def test_a2a_marketplace_register_and_execute(self) -> None:
        registered = self.client.post(
            "/api/a2a/services/register",
            json={
                "name": "Page Scraper",
                "capability": "web_scrape",
                "price_per_unit": 0.5,
                "seller_agent_id": "agent_seller_01",
                "session_id": "test_a2a",
            },
        )
        self.assertEqual(registered.status_code, 200)
        service_id = registered.json().get("id")

        listed = self.client.get("/api/a2a/services?capability=web_scrape")
        self.assertEqual(listed.status_code, 200)
        ids = [row.get("id") for row in listed.json()]
        self.assertIn(service_id, ids)

        executed = self.client.post(
            "/api/a2a/tasks/execute",
            json={
                "service_id": service_id,
                "requester_agent_id": "agent_buyer_01",
                "units": 3,
                "payload": {"target": "https://example.com"},
                "session_id": "test_a2a",
            },
        )
        self.assertEqual(executed.status_code, 200)
        task = executed.json()
        self.assertEqual(task.get("status"), "completed")
        self.assertAlmostEqual(float(task.get("total_amount", 0.0)), 1.5, places=6)

        tasks = self.client.get("/api/a2a/tasks?limit=20&session_id=test_a2a")
        self.assertEqual(tasks.status_code, 200)
        task_ids = [row.get("id") for row in tasks.json()]
        self.assertIn(task.get("id"), task_ids)


if __name__ == "__main__":
    unittest.main()
