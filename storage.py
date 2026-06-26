"""
pdp_mock.storage — Thread-safe in-memory store for the mock PDP.
"""

from __future__ import annotations

import hashlib
import threading
import time
from typing import Optional

from .models import Invoice, InvoiceFormat, InvoiceStatus, Scenario, Webhook


def _detect_format(content: bytes, filename: str = "") -> InvoiceFormat:
    """Auto-detect invoice format from content or filename."""
    if filename.lower().endswith(".pdf") or content[:4] == b"%PDF":
        return InvoiceFormat.FACTURX
    if b"CrossIndustryInvoice" in content:
        return InvoiceFormat.CII
    if b"urn:oasis:names:specification:ubl" in content:
        return InvoiceFormat.UBL
    if b"FatturaElettronica" in content:
        return InvoiceFormat.FATTURAPA
    return InvoiceFormat.UNKNOWN


class InvoiceStore:
    """In-memory invoice registry."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._invoices: dict[str, Invoice] = {}
        self._content_hashes: dict[str, str] = {}   # hash → invoice_id

    def add(self, invoice: Invoice, content: bytes) -> None:
        h = hashlib.sha256(content).hexdigest()
        with self._lock:
            invoice.content_hash = h
            invoice.file_size_bytes = len(content)
            self._invoices[invoice.invoice_id] = invoice
            self._content_hashes[h] = invoice.invoice_id

    def get(self, invoice_id: str) -> Optional[Invoice]:
        return self._invoices.get(invoice_id)

    def is_duplicate(self, content: bytes) -> Optional[str]:
        h = hashlib.sha256(content).hexdigest()
        return self._content_hashes.get(h)

    def list(
        self,
        status: Optional[InvoiceStatus] = None,
        routing_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Invoice]:
        with self._lock:
            items = list(self._invoices.values())
        if status:
            items = [i for i in items if i.status == status]
        if routing_id:
            items = [i for i in items if i.routing_id == routing_id]
        items.sort(key=lambda i: i.created_at, reverse=True)
        return items[offset : offset + limit]

    def count(self) -> int:
        return len(self._invoices)

    def by_status(self) -> dict[str, int]:
        with self._lock:
            counts: dict[str, int] = {}
            for inv in self._invoices.values():
                counts[inv.status] = counts.get(inv.status, 0) + 1
        return counts

    def by_format(self) -> dict[str, int]:
        with self._lock:
            counts: dict[str, int] = {}
            for inv in self._invoices.values():
                counts[inv.format] = counts.get(inv.format, 0) + 1
        return counts

    def reset(self) -> None:
        with self._lock:
            self._invoices.clear()
            self._content_hashes.clear()


class WebhookStore:
    """In-memory webhook registry."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._webhooks: dict[str, Webhook] = {}

    def add(self, wh: Webhook) -> None:
        with self._lock:
            self._webhooks[wh.webhook_id] = wh

    def get(self, webhook_id: str) -> Optional[Webhook]:
        return self._webhooks.get(webhook_id)

    def list(self) -> list[Webhook]:
        return list(self._webhooks.values())

    def remove(self, webhook_id: str) -> bool:
        with self._lock:
            return self._webhooks.pop(webhook_id, None) is not None

    def reset(self) -> None:
        with self._lock:
            self._webhooks.clear()


class ScenarioStore:
    """Active test scenarios."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._scenarios: list[Scenario] = []

    def add(self, scenario: Scenario) -> None:
        with self._lock:
            self._scenarios.append(scenario)

    def pop_rejection(self) -> Optional[Scenario]:
        """Return and consume a REJECT_NEXT scenario if available."""
        with self._lock:
            for s in self._scenarios:
                if s.type.value == "REJECT_NEXT":
                    s.applied += 1
                    if s.applied >= s.count:
                        self._scenarios.remove(s)
                    return s
        return None

    def get_delay(self) -> int:
        """Return delay_ms if a DELAY scenario is active."""
        with self._lock:
            for s in self._scenarios:
                if s.type.value == "DELAY":
                    s.applied += 1
                    if s.applied >= s.count and s.count != -1:
                        self._scenarios.remove(s)
                    return s.delay_ms
        return 0

    def get_error_rate(self) -> Optional[tuple[int, int]]:
        """Return (http_status, rate) if an ERROR_RATE scenario is active."""
        with self._lock:
            for s in self._scenarios:
                if s.type.value == "ERROR_RATE":
                    return s.http_status, s.rate
        return None

    def count(self) -> int:
        return len(self._scenarios)

    def reset(self) -> None:
        with self._lock:
            self._scenarios.clear()


# Singletons
invoice_store = InvoiceStore()
webhook_store = WebhookStore()
scenario_store = ScenarioStore()
_start_time = time.time()


def uptime() -> float:
    return time.time() - _start_time


def reset_all() -> None:
    invoice_store.reset()
    webhook_store.reset()
    scenario_store.reset()
