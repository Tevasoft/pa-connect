"""
pdp_mock.client — Python client for the PDP mock (and real PDPs with compatible API).

Usage:
    from pdp_mock import PDPClient

    client = PDPClient("http://localhost:8042", token="test-token")
    invoice_id = client.submit("invoice.pdf", routing_id="FR12345678901")
    result = client.wait_for_terminal(invoice_id, timeout=30)
    print(result.status)  # ACCEPTED
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional, Union

import httpx

from .models import InvoiceStatus, StatusResponse, SubmitResponse, TERMINAL_STATUSES


class PDPError(Exception):
    """Raised when the PDP returns an error response."""
    def __init__(self, status_code: int, detail: dict | str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"PDP error {status_code}: {detail}")


class PDPClient:
    """
    Lightweight synchronous Python client for the PDP mock API.

    Args:
        base_url:  URL of the mock PDP (e.g. "http://localhost:8042")
        token:     Bearer token (any non-empty string for the mock)
        timeout:   HTTP timeout in seconds
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8042",
        token: str = "test-token",
        timeout: float = 30.0,
    ):
        self.base_url = base_url.rstrip("/")
        self._http = httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )

    # ------------------------------------------------------------------
    # Invoice operations
    # ------------------------------------------------------------------

    def submit(
        self,
        source: Union[str, Path, bytes],
        routing_id: str,
        buyer_routing_id: Optional[str] = None,
        external_id: Optional[str] = None,
        format: Optional[str] = None,
    ) -> str:
        """
        Submit an invoice. Returns the invoice_id.

        Args:
            source:            File path or raw bytes.
            routing_id:        Seller FR routing identifier.
            buyer_routing_id:  Optional buyer routing identifier.
            external_id:       Optional caller reference.
            format:            Force format (FACTURX/UBL/CII). Auto-detected if None.
        """
        if isinstance(source, (str, Path)):
            path = Path(source)
            content = path.read_bytes()
            filename = path.name
        else:
            content = source
            filename = "invoice.xml"

        data = {"routing_id": routing_id}
        if buyer_routing_id:
            data["buyer_routing_id"] = buyer_routing_id
        if external_id:
            data["external_id"] = external_id
        if format:
            data["format"] = format

        resp = self._http.post(
            "/api/v1/invoices",
            files={"file": (filename, content, "application/octet-stream")},
            data=data,
        )
        self._raise_for_status(resp)
        return resp.json()["invoice_id"]

    def get_status(self, invoice_id: str) -> StatusResponse:
        """Fetch current status of an invoice."""
        resp = self._http.get(f"/api/v1/invoices/{invoice_id}")
        self._raise_for_status(resp)
        return StatusResponse(**resp.json())

    def list_invoices(
        self,
        status: Optional[str] = None,
        routing_id: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict]:
        params = {"limit": limit}
        if status:
            params["status"] = status
        if routing_id:
            params["routing_id"] = routing_id
        resp = self._http.get("/api/v1/invoices", params=params)
        self._raise_for_status(resp)
        return resp.json()["items"]

    def cancel(self, invoice_id: str, reason: Optional[str] = None) -> dict:
        params = {}
        if reason:
            params["reason"] = reason
        resp = self._http.post(f"/api/v1/invoices/{invoice_id}/cancel", params=params)
        self._raise_for_status(resp)
        return resp.json()

    def get_lifecycle(self, invoice_id: str) -> list[dict]:
        resp = self._http.get(f"/api/v1/invoices/{invoice_id}/lifecycle")
        self._raise_for_status(resp)
        return resp.json()["lifecycle"]

    def advance(
        self,
        invoice_id: str,
        status: str,
        reason: Optional[str] = None,
    ) -> dict:
        """Force a status transition (test helper)."""
        resp = self._http.post(
            f"/api/v1/invoices/{invoice_id}/advance",
            json={"status": status, "reason": reason, "actor": "TEST_HARNESS"},
        )
        self._raise_for_status(resp)
        return resp.json()

    def wait_for_terminal(
        self,
        invoice_id: str,
        timeout: float = 30.0,
        poll_interval: float = 0.5,
    ) -> StatusResponse:
        """
        Poll until the invoice reaches a terminal status (ACCEPTED/REJECTED/REFUSED/CANCELLED).
        Raises TimeoutError if timeout is exceeded.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status_resp = self.get_status(invoice_id)
            if InvoiceStatus(status_resp.status) in TERMINAL_STATUSES:
                return status_resp
            time.sleep(poll_interval)
        raise TimeoutError(
            f"Invoice {invoice_id} did not reach terminal status within {timeout}s. "
            f"Last status: {status_resp.status}"
        )

    # ------------------------------------------------------------------
    # Mock control
    # ------------------------------------------------------------------

    def set_scenario(self, scenario_type: str, **kwargs) -> dict:
        resp = self._http.post(
            "/api/v1/_mock/scenario",
            json={"type": scenario_type, **kwargs},
        )
        self._raise_for_status(resp)
        return resp.json()

    def reset(self) -> None:
        resp = self._http.post("/api/v1/_mock/reset")
        self._raise_for_status(resp)

    def stats(self) -> dict:
        resp = self._http.get("/api/v1/_mock/stats")
        self._raise_for_status(resp)
        return resp.json()

    def health(self) -> dict:
        resp = self._http.get("/health")
        self._raise_for_status(resp)
        return resp.json()

    # ------------------------------------------------------------------
    # Webhooks
    # ------------------------------------------------------------------

    def register_webhook(self, url: str, events: Optional[list] = None,
                          secret: Optional[str] = None) -> dict:
        body = {"url": url, "events": events or [], "secret": secret}
        resp = self._http.post("/api/v1/webhooks", json=body)
        self._raise_for_status(resp)
        return resp.json()

    def list_webhooks(self) -> list[dict]:
        resp = self._http.get("/api/v1/webhooks")
        self._raise_for_status(resp)
        return resp.json()["items"]

    def delete_webhook(self, webhook_id: str) -> None:
        resp = self._http.delete(f"/api/v1/webhooks/{webhook_id}")
        self._raise_for_status(resp)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _raise_for_status(self, resp: httpx.Response) -> None:
        if resp.status_code >= 400:
            try:
                detail = resp.json()
            except Exception:
                detail = resp.text
            raise PDPError(resp.status_code, detail)

    def close(self) -> None:
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
