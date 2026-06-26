"""
pdp_mock.models — Domain models for the PDP mock
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id(prefix: str = "PDP") -> str:
    return f"{prefix}-{datetime.now().strftime('%Y%m%d')}-{str(uuid.uuid4())[:8].upper()}"


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class InvoiceStatus(str, Enum):
    DEPOSITED = "DEPOSITED"          # Received by PDP, not yet validated
    VALIDATED = "VALIDATED"          # Format/schema checks passed
    REJECTED = "REJECTED"            # Failed validation or business rules
    SENT = "SENT"                    # Forwarded to buyer's PDP/PPF
    RECEIVED = "RECEIVED"            # Buyer's PDP acknowledged receipt
    ACCEPTED = "ACCEPTED"            # Buyer accepted the invoice
    REFUSED = "REFUSED"              # Buyer refused the invoice
    CANCELLED = "CANCELLED"          # Cancelled by issuer
    IN_DISPUTE = "IN_DISPUTE"        # Under dispute process


TERMINAL_STATUSES = {
    InvoiceStatus.REJECTED,
    InvoiceStatus.ACCEPTED,
    InvoiceStatus.REFUSED,
    InvoiceStatus.CANCELLED,
}

LIFECYCLE_TRANSITIONS = {
    InvoiceStatus.DEPOSITED: [InvoiceStatus.VALIDATED, InvoiceStatus.REJECTED],
    InvoiceStatus.VALIDATED: [InvoiceStatus.SENT, InvoiceStatus.REJECTED],
    InvoiceStatus.SENT: [InvoiceStatus.RECEIVED, InvoiceStatus.REJECTED],
    InvoiceStatus.RECEIVED: [InvoiceStatus.ACCEPTED, InvoiceStatus.REFUSED, InvoiceStatus.IN_DISPUTE],
    InvoiceStatus.ACCEPTED: [],
    InvoiceStatus.REFUSED: [],
    InvoiceStatus.REJECTED: [],
    InvoiceStatus.CANCELLED: [],
    InvoiceStatus.IN_DISPUTE: [InvoiceStatus.ACCEPTED, InvoiceStatus.REFUSED],
}


class InvoiceFormat(str, Enum):
    FACTURX = "FACTURX"
    UBL = "UBL"
    CII = "CII"
    FATTURAPA = "FATTURAPA"
    UNKNOWN = "UNKNOWN"


class RejectionReason(str, Enum):
    INVALID_FORMAT = "INVALID_FORMAT"
    INVALID_ROUTING_ID = "INVALID_ROUTING_ID"
    MISSING_MANDATORY_FIELD = "MISSING_MANDATORY_FIELD"
    DUPLICATE_INVOICE = "DUPLICATE_INVOICE"
    EXPIRED_CERTIFICATE = "EXPIRED_CERTIFICATE"
    BUYER_NOT_FOUND = "BUYER_NOT_FOUND"
    SCHEMA_VALIDATION_FAILED = "SCHEMA_VALIDATION_FAILED"
    MOCK_FORCED_REJECTION = "MOCK_FORCED_REJECTION"


class WebhookEvent(str, Enum):
    INVOICE_DEPOSITED = "invoice.deposited"
    INVOICE_VALIDATED = "invoice.validated"
    INVOICE_REJECTED = "invoice.rejected"
    INVOICE_SENT = "invoice.sent"
    INVOICE_RECEIVED = "invoice.received"
    INVOICE_ACCEPTED = "invoice.accepted"
    INVOICE_REFUSED = "invoice.refused"
    INVOICE_CANCELLED = "invoice.cancelled"


# ---------------------------------------------------------------------------
# Core models
# ---------------------------------------------------------------------------

class LifecycleEvent(BaseModel):
    status: InvoiceStatus
    timestamp: datetime = Field(default_factory=_now)
    actor: str = "PDP_MOCK"
    reason: Optional[str] = None
    details: Optional[str] = None


class Invoice(BaseModel):
    invoice_id: str = Field(default_factory=lambda: _new_id("PDP"))
    external_id: Optional[str] = None       # caller's reference
    routing_id: str                          # seller FR VAT / SIREN
    buyer_routing_id: Optional[str] = None
    format: InvoiceFormat = InvoiceFormat.UNKNOWN
    filename: Optional[str] = None
    file_size_bytes: Optional[int] = None
    content_hash: Optional[str] = None      # SHA-256 of file
    status: InvoiceStatus = InvoiceStatus.DEPOSITED
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    lifecycle: list[LifecycleEvent] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def advance_to(self, new_status: InvoiceStatus,
                   reason: Optional[str] = None,
                   actor: str = "PDP_MOCK") -> bool:
        """Advance to a new status if the transition is allowed."""
        allowed = LIFECYCLE_TRANSITIONS.get(self.status, [])
        if new_status not in allowed:
            return False
        self.status = new_status
        self.updated_at = _now()
        self.lifecycle.append(LifecycleEvent(
            status=new_status,
            actor=actor,
            reason=reason,
        ))
        return True

    def cancel(self, reason: str = "Cancelled by issuer") -> bool:
        if self.status in TERMINAL_STATUSES:
            return False
        self.status = InvoiceStatus.CANCELLED
        self.updated_at = _now()
        self.lifecycle.append(LifecycleEvent(
            status=InvoiceStatus.CANCELLED,
            actor="ISSUER",
            reason=reason,
        ))
        return True

    def to_summary(self) -> dict:
        return {
            "invoice_id": self.invoice_id,
            "external_id": self.external_id,
            "routing_id": self.routing_id,
            "format": self.format,
            "status": self.status,
            "filename": self.filename,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


# ---------------------------------------------------------------------------
# Webhook models
# ---------------------------------------------------------------------------

class Webhook(BaseModel):
    webhook_id: str = Field(default_factory=lambda: _new_id("WH"))
    url: str
    events: list[WebhookEvent] = Field(default_factory=list)
    secret: Optional[str] = None
    active: bool = True
    created_at: datetime = Field(default_factory=_now)
    delivery_count: int = 0
    failure_count: int = 0


class WebhookPayload(BaseModel):
    event: WebhookEvent
    timestamp: datetime = Field(default_factory=_now)
    invoice_id: str
    status: InvoiceStatus
    routing_id: str
    details: Optional[dict] = None


# ---------------------------------------------------------------------------
# Scenario models (test control)
# ---------------------------------------------------------------------------

class ScenarioType(str, Enum):
    REJECT_NEXT = "REJECT_NEXT"
    DELAY = "DELAY"
    ERROR_RATE = "ERROR_RATE"
    DUPLICATE_CHECK_FAIL = "DUPLICATE_CHECK_FAIL"


class Scenario(BaseModel):
    type: ScenarioType
    reason: Optional[str] = None
    count: int = 1          # how many requests to affect (-1 = infinite)
    delay_ms: int = 0
    http_status: int = 503
    rate: int = 100         # percentage for ERROR_RATE
    applied: int = 0        # how many times already applied


# ---------------------------------------------------------------------------
# Request/Response models
# ---------------------------------------------------------------------------

class SubmitResponse(BaseModel):
    invoice_id: str
    status: InvoiceStatus
    lifecycle: list[LifecycleEvent]
    routing_id: str
    format: InvoiceFormat
    created_at: datetime


class StatusResponse(BaseModel):
    invoice_id: str
    status: InvoiceStatus
    lifecycle: list[LifecycleEvent]
    routing_id: str
    buyer_routing_id: Optional[str]
    format: InvoiceFormat
    filename: Optional[str]
    created_at: datetime
    updated_at: datetime


class AdvanceRequest(BaseModel):
    status: InvoiceStatus
    reason: Optional[str] = None
    actor: str = "TEST_HARNESS"


class ScenarioRequest(BaseModel):
    type: ScenarioType
    reason: Optional[RejectionReason] = None
    count: int = 1
    delay_ms: int = 0
    http_status: int = 503
    rate: int = 100


class WebhookCreate(BaseModel):
    url: str
    events: list[WebhookEvent] = Field(default_factory=lambda: list(WebhookEvent))
    secret: Optional[str] = None


class MockStats(BaseModel):
    total_submissions: int
    by_status: dict[str, int]
    by_format: dict[str, int]
    total_webhooks_delivered: int
    total_webhook_failures: int
    active_scenarios: int
    uptime_seconds: float
