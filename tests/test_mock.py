"""Tests for pdp-connect-mock — uses FastAPI TestClient, no real server needed."""
from __future__ import annotations
import pytest
from fastapi.testclient import TestClient
from pdp_mock.app import app
from pdp_mock.storage import reset_all
from pdp_mock.models import InvoiceStatus, TERMINAL_STATUSES

AUTH = {"Authorization": "Bearer test-token"}

SAMPLE_XML = b"""<?xml version="1.0"?>
<rsm:CrossIndustryInvoice xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100">
  <rsm:ExchangedDocument><ram:ID xmlns:ram="urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100">TEST-001</ram:ID></rsm:ExchangedDocument>
</rsm:CrossIndustryInvoice>"""

SAMPLE_PDF = b"%PDF-1.4 fake pdf content for testing"


@pytest.fixture(autouse=True)
def clean_state():
    reset_all()
    yield
    reset_all()


@pytest.fixture
def client():
    return TestClient(app)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
class TestAuth:
    def test_no_token_returns_401(self, client):
        resp = client.post("/api/v1/invoices",
            files={"file": ("inv.xml", SAMPLE_XML)},
            data={"routing_id": "FR12345678901"})
        assert resp.status_code == 401

    def test_valid_token_passes(self, client):
        resp = client.post("/api/v1/invoices",
            files={"file": ("inv.xml", SAMPLE_XML)},
            data={"routing_id": "FR12345678901"},
            headers=AUTH)
        assert resp.status_code == 201


# ---------------------------------------------------------------------------
# Submission
# ---------------------------------------------------------------------------
class TestSubmission:
    def test_submit_xml_returns_invoice_id(self, client):
        resp = client.post("/api/v1/invoices",
            files={"file": ("invoice.xml", SAMPLE_XML)},
            data={"routing_id": "FR12345678901"},
            headers=AUTH)
        assert resp.status_code == 201
        data = resp.json()
        assert "invoice_id" in data
        assert data["invoice_id"].startswith("PDP-")
        assert data["status"] == "DEPOSITED"

    def test_submit_pdf_detected_as_facturx(self, client):
        resp = client.post("/api/v1/invoices",
            files={"file": ("invoice.pdf", SAMPLE_PDF)},
            data={"routing_id": "FR12345678901"},
            headers=AUTH)
        assert resp.status_code == 201
        assert resp.json()["format"] == "FACTURX"

    def test_submit_duplicate_returns_409(self, client):
        for _ in range(2):
            resp = client.post("/api/v1/invoices",
                files={"file": ("inv.xml", SAMPLE_XML)},
                data={"routing_id": "FR12345678901"},
                headers=AUTH)
        assert resp.status_code == 409
        assert resp.json()["detail"]["error"] == "DUPLICATE_INVOICE"

    def test_routing_id_stored(self, client):
        resp = client.post("/api/v1/invoices",
            files={"file": ("inv.xml", SAMPLE_XML)},
            data={"routing_id": "FR98765432101"},
            headers=AUTH)
        invoice_id = resp.json()["invoice_id"]
        detail = client.get(f"/api/v1/invoices/{invoice_id}", headers=AUTH).json()
        assert detail["routing_id"] == "FR98765432101"

    def test_lifecycle_starts_with_deposited(self, client):
        resp = client.post("/api/v1/invoices",
            files={"file": ("inv.xml", SAMPLE_XML)},
            data={"routing_id": "FR12345678901"},
            headers=AUTH)
        lifecycle = resp.json()["lifecycle"]
        assert lifecycle[0]["status"] == "DEPOSITED"


# ---------------------------------------------------------------------------
# Status & lifecycle
# ---------------------------------------------------------------------------
class TestStatus:
    def test_get_status(self, client):
        r = client.post("/api/v1/invoices",
            files={"file": ("i.xml", SAMPLE_XML)},
            data={"routing_id": "FR123"},
            headers=AUTH)
        inv_id = r.json()["invoice_id"]
        resp = client.get(f"/api/v1/invoices/{inv_id}", headers=AUTH)
        assert resp.status_code == 200
        assert resp.json()["invoice_id"] == inv_id

    def test_unknown_invoice_returns_404(self, client):
        resp = client.get("/api/v1/invoices/PDP-DOESNOTEXIST", headers=AUTH)
        assert resp.status_code == 404

    def test_list_invoices(self, client):
        client.post("/api/v1/invoices",
            files={"file": ("i.xml", SAMPLE_XML)},
            data={"routing_id": "FR123"},
            headers=AUTH)
        resp = client.get("/api/v1/invoices", headers=AUTH)
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

    def test_advance_status(self, client):
        r = client.post("/api/v1/invoices",
            files={"file": ("i.xml", SAMPLE_XML)},
            data={"routing_id": "FR123"},
            headers=AUTH)
        inv_id = r.json()["invoice_id"]
        resp = client.post(f"/api/v1/invoices/{inv_id}/advance",
            json={"status": "VALIDATED", "actor": "TEST"},
            headers=AUTH)
        assert resp.status_code == 200
        assert resp.json()["status"] == "VALIDATED"

    def test_cancel_invoice(self, client):
        r = client.post("/api/v1/invoices",
            files={"file": ("i.xml", SAMPLE_XML)},
            data={"routing_id": "FR123"},
            headers=AUTH)
        inv_id = r.json()["invoice_id"]
        resp = client.post(f"/api/v1/invoices/{inv_id}/cancel", headers=AUTH)
        assert resp.status_code == 200
        assert resp.json()["status"] == "CANCELLED"

    def test_cancel_terminal_returns_409(self, client):
        r = client.post("/api/v1/invoices",
            files={"file": ("i.xml", SAMPLE_XML)},
            data={"routing_id": "FR123"},
            headers=AUTH)
        inv_id = r.json()["invoice_id"]
        # Cancel first
        client.post(f"/api/v1/invoices/{inv_id}/cancel", headers=AUTH)
        # Cancel again → 409
        resp = client.post(f"/api/v1/invoices/{inv_id}/cancel", headers=AUTH)
        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Scenarios (mock control)
# ---------------------------------------------------------------------------
class TestScenarios:
    def test_reject_next_scenario(self, client):
        client.post("/api/v1/_mock/scenario", json={"type": "REJECT_NEXT", "count": 1})
        resp = client.post("/api/v1/invoices",
            files={"file": ("i.xml", SAMPLE_XML)},
            data={"routing_id": "FR123"},
            headers=AUTH)
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVOICE_REJECTED"

    def test_reject_next_consumed_after_use(self, client):
        client.post("/api/v1/_mock/scenario", json={"type": "REJECT_NEXT", "count": 1})
        # First → rejected
        client.post("/api/v1/invoices",
            files={"file": ("i.xml", SAMPLE_XML)},
            data={"routing_id": "FR123"},
            headers=AUTH)
        # Second (different content) → accepted
        resp = client.post("/api/v1/invoices",
            files={"file": ("i2.xml", SAMPLE_XML + b"<!-- 2 -->")},
            data={"routing_id": "FR123"},
            headers=AUTH)
        assert resp.status_code == 201

    def test_reset_clears_state(self, client):
        client.post("/api/v1/invoices",
            files={"file": ("i.xml", SAMPLE_XML)},
            data={"routing_id": "FR123"},
            headers=AUTH)
        client.post("/api/v1/_mock/reset")
        resp = client.get("/api/v1/invoices", headers=AUTH)
        assert resp.json()["total"] == 0

    def test_stats(self, client):
        resp = client.get("/api/v1/_mock/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_submissions" in data
        assert "uptime_seconds" in data


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------
class TestWebhooks:
    def test_register_webhook(self, client):
        resp = client.post("/api/v1/webhooks",
            json={"url": "http://example.com/hook", "events": ["invoice.accepted"]},
            headers=AUTH)
        assert resp.status_code == 201
        assert resp.json()["webhook_id"].startswith("WH-")

    def test_list_webhooks(self, client):
        client.post("/api/v1/webhooks",
            json={"url": "http://example.com/hook"},
            headers=AUTH)
        resp = client.get("/api/v1/webhooks", headers=AUTH)
        assert len(resp.json()["items"]) == 1

    def test_delete_webhook(self, client):
        r = client.post("/api/v1/webhooks",
            json={"url": "http://example.com/hook"},
            headers=AUTH)
        wh_id = r.json()["webhook_id"]
        resp = client.delete(f"/api/v1/webhooks/{wh_id}", headers=AUTH)
        assert resp.status_code == 200

    def test_delete_nonexistent_returns_404(self, client):
        resp = client.delete("/api/v1/webhooks/WH-FAKEID", headers=AUTH)
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
class TestHealth:
    def test_health_endpoint(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["version"] == "0.1.0"
