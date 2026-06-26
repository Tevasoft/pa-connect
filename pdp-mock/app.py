"""
pdp_mock.app — FastAPI application implementing the mock PDP REST API.
"""

from __future__ import annotations

import asyncio
import random
import time
from typing import Optional

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from .models import (
    AdvanceRequest,
    Invoice,
    InvoiceFormat,
    InvoiceStatus,
    LifecycleEvent,
    MockStats,
    RejectionReason,
    Scenario,
    ScenarioRequest,
    StatusResponse,
    SubmitResponse,
    WebhookCreate,
)
from .storage import (
    invoice_store,
    reset_all,
    scenario_store,
    uptime,
    webhook_store,
)
from .storage import _detect_format
from .webhooks import deliver_event

app = FastAPI(
    title="PDP Connect Mock",
    description=(
        "Local mock PDP (Plateforme de Dématérialisation Partenaire) "
        "for testing French e-invoicing flows. "
        "Implements the core DGFIP PDP API surface — no real PDP needed.\n\n"
        "Built by [TEVASOFT](https://tevasoft.eu)"
    ),
    version="0.1.0",
    contact={
        "name": "TEVASOFT",
        "url": "https://tevasoft.eu",
        "email": "contact@tevasoft.eu",
    },
    license_info={"name": "Apache 2.0"},
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _check_auth(authorization: Optional[str]) -> None:
    """Very basic token check — any non-empty bearer token is accepted."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Bearer token required")


def _apply_scenarios_or_raise(content: bytes) -> None:
    """Check active scenarios; raise if needed."""
    # Chaos: random error rate
    err = scenario_store.get_error_rate()
    if err:
        status_code, rate = err
        if random.randint(1, 100) <= rate:
            raise HTTPException(status_code=status_code, detail="Simulated PDP error (chaos)")

    # Forced rejection scenario
    rejection = scenario_store.pop_rejection()
    if rejection:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "INVOICE_REJECTED",
                "reason": rejection.reason or RejectionReason.MOCK_FORCED_REJECTION,
                "message": "Forced rejection by test scenario",
            },
        )


async def _auto_advance(invoice: Invoice) -> None:
    """
    Automatically advance invoice through the lifecycle.
    DEPOSITED → VALIDATED → SENT → RECEIVED → ACCEPTED
    Each step has a small simulated delay.
    """
    transitions = [
        (InvoiceStatus.VALIDATED, 0.5),
        (InvoiceStatus.SENT, 0.5),
        (InvoiceStatus.RECEIVED, 0.5),
        (InvoiceStatus.ACCEPTED, 0.5),
    ]
    for new_status, delay in transitions:
        await asyncio.sleep(delay)
        if invoice.is_terminal:
            break
        # Apply delay scenario
        extra_delay = scenario_store.get_delay()
        if extra_delay:
            await asyncio.sleep(extra_delay / 1000)
        ok = invoice.advance_to(new_status)
        if ok:
            asyncio.create_task(deliver_event(invoice, new_status))


# ---------------------------------------------------------------------------
# Invoice routes
# ---------------------------------------------------------------------------

@app.post(
    "/api/v1/invoices",
    response_model=SubmitResponse,
    status_code=201,
    tags=["Invoices"],
    summary="Deposit an invoice",
    description=(
        "Submit a Factur-X PDF, CII XML, or UBL XML invoice to the mock PDP. "
        "The invoice is auto-advanced through its lifecycle asynchronously."
    ),
)
async def submit_invoice(
    file: UploadFile = File(..., description="Invoice file (PDF or XML)"),
    routing_id: str = Form(..., description="Seller routing identifier (FR VAT / SIREN-based)"),
    buyer_routing_id: Optional[str] = Form(None, description="Buyer routing identifier"),
    external_id: Optional[str] = Form(None, description="Your internal reference"),
    format: Optional[str] = Form(None, description="Force format (FACTURX/UBL/CII)"),
    authorization: Optional[str] = Header(None),
):
    _check_auth(authorization)
    content = await file.read()

    # Simulate network delay
    delay = scenario_store.get_delay()
    if delay:
        await asyncio.sleep(delay / 1000)

    _apply_scenarios_or_raise(content)

    # Duplicate check
    dup_id = invoice_store.is_duplicate(content)
    if dup_id:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "DUPLICATE_INVOICE",
                "duplicate_of": dup_id,
                "message": "An invoice with identical content was already submitted",
            },
        )

    # Detect format
    detected_format = (
        InvoiceFormat(format.upper()) if format and format.upper() in InvoiceFormat.__members__
        else _detect_format(content, file.filename or "")
    )

    # Create invoice
    invoice = Invoice(
        routing_id=routing_id,
        buyer_routing_id=buyer_routing_id,
        external_id=external_id,
        format=detected_format,
        filename=file.filename,
    )
    invoice.lifecycle.append(LifecycleEvent(
        status=InvoiceStatus.DEPOSITED,
        actor="ISSUER",
        details=f"Received {len(content)} bytes, format={detected_format}",
    ))

    invoice_store.add(invoice, content)

    # Fire webhook for deposit
    asyncio.create_task(deliver_event(invoice, InvoiceStatus.DEPOSITED))

    # Auto-advance lifecycle in background
    asyncio.create_task(_auto_advance(invoice))

    return SubmitResponse(
        invoice_id=invoice.invoice_id,
        status=invoice.status,
        lifecycle=invoice.lifecycle,
        routing_id=invoice.routing_id,
        format=invoice.format,
        created_at=invoice.created_at,
    )


@app.get(
    "/api/v1/invoices/{invoice_id}",
    response_model=StatusResponse,
    tags=["Invoices"],
    summary="Get invoice status",
)
async def get_invoice(
    invoice_id: str,
    authorization: Optional[str] = Header(None),
):
    _check_auth(authorization)
    invoice = invoice_store.get(invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail=f"Invoice {invoice_id!r} not found")
    return StatusResponse(
        invoice_id=invoice.invoice_id,
        status=invoice.status,
        lifecycle=invoice.lifecycle,
        routing_id=invoice.routing_id,
        buyer_routing_id=invoice.buyer_routing_id,
        format=invoice.format,
        filename=invoice.filename,
        created_at=invoice.created_at,
        updated_at=invoice.updated_at,
    )


@app.get(
    "/api/v1/invoices",
    tags=["Invoices"],
    summary="List invoices",
)
async def list_invoices(
    status: Optional[str] = None,
    routing_id: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    authorization: Optional[str] = Header(None),
):
    _check_auth(authorization)
    status_filter = InvoiceStatus(status) if status else None
    invoices = invoice_store.list(
        status=status_filter,
        routing_id=routing_id,
        limit=limit,
        offset=offset,
    )
    return {
        "items": [i.to_summary() for i in invoices],
        "total": invoice_store.count(),
        "limit": limit,
        "offset": offset,
    }


@app.post(
    "/api/v1/invoices/{invoice_id}/cancel",
    tags=["Invoices"],
    summary="Cancel an invoice",
)
async def cancel_invoice(
    invoice_id: str,
    reason: Optional[str] = None,
    authorization: Optional[str] = Header(None),
):
    _check_auth(authorization)
    invoice = invoice_store.get(invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail=f"Invoice {invoice_id!r} not found")
    if not invoice.cancel(reason or "Cancelled by issuer"):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot cancel invoice in terminal status {invoice.status!r}",
        )
    asyncio.create_task(deliver_event(invoice, InvoiceStatus.CANCELLED))
    return {"invoice_id": invoice_id, "status": invoice.status}


@app.get(
    "/api/v1/invoices/{invoice_id}/lifecycle",
    tags=["Invoices"],
    summary="Get full lifecycle history",
)
async def get_lifecycle(
    invoice_id: str,
    authorization: Optional[str] = Header(None),
):
    _check_auth(authorization)
    invoice = invoice_store.get(invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail=f"Invoice {invoice_id!r} not found")
    return {
        "invoice_id": invoice_id,
        "current_status": invoice.status,
        "lifecycle": invoice.lifecycle,
    }


@app.post(
    "/api/v1/invoices/{invoice_id}/advance",
    tags=["Invoices"],
    summary="Force status transition (test only)",
)
async def advance_invoice(
    invoice_id: str,
    body: AdvanceRequest,
    authorization: Optional[str] = Header(None),
):
    _check_auth(authorization)
    invoice = invoice_store.get(invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail=f"Invoice {invoice_id!r} not found")
    ok = invoice.advance_to(body.status, reason=body.reason, actor=body.actor)
    if not ok:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Transition {invoice.status!r} → {body.status!r} not allowed. "
                f"Valid transitions: {[s.value for s in invoice.status.__class__]}"
            ),
        )
    asyncio.create_task(deliver_event(invoice, body.status))
    return {"invoice_id": invoice_id, "status": invoice.status}


# ---------------------------------------------------------------------------
# Webhook routes
# ---------------------------------------------------------------------------

@app.post("/api/v1/webhooks", tags=["Webhooks"], status_code=201, summary="Register a webhook")
async def create_webhook(
    body: WebhookCreate,
    authorization: Optional[str] = Header(None),
):
    from .models import Webhook
    _check_auth(authorization)
    wh = Webhook(url=body.url, events=body.events, secret=body.secret)
    webhook_store.add(wh)
    return wh


@app.get("/api/v1/webhooks", tags=["Webhooks"], summary="List webhooks")
async def list_webhooks(authorization: Optional[str] = Header(None)):
    _check_auth(authorization)
    return {"items": webhook_store.list()}


@app.delete("/api/v1/webhooks/{webhook_id}", tags=["Webhooks"], summary="Delete a webhook")
async def delete_webhook(webhook_id: str, authorization: Optional[str] = Header(None)):
    _check_auth(authorization)
    ok = webhook_store.remove(webhook_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Webhook not found")
    return {"deleted": webhook_id}


# ---------------------------------------------------------------------------
# Mock control routes
# ---------------------------------------------------------------------------

@app.post("/api/v1/_mock/scenario", tags=["Mock Control"], summary="Set a test scenario")
async def set_scenario(body: ScenarioRequest):
    scenario = Scenario(
        type=body.type,
        reason=body.reason.value if body.reason else None,
        count=body.count,
        delay_ms=body.delay_ms,
        http_status=body.http_status,
        rate=body.rate,
    )
    scenario_store.add(scenario)
    return {"active_scenarios": scenario_store.count(), "added": scenario}


@app.post("/api/v1/_mock/reset", tags=["Mock Control"], summary="Reset all state")
async def reset():
    reset_all()
    return {"reset": True}


@app.get("/api/v1/_mock/stats", tags=["Mock Control"], summary="Request statistics")
async def stats():
    whs = webhook_store.list()
    return MockStats(
        total_submissions=invoice_store.count(),
        by_status=invoice_store.by_status(),
        by_format=invoice_store.by_format(),
        total_webhooks_delivered=sum(w.delivery_count for w in whs),
        total_webhook_failures=sum(w.failure_count for w in whs),
        active_scenarios=scenario_store.count(),
        uptime_seconds=uptime(),
    )


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health", tags=["Health"])
async def health():
    return {
        "status": "ok",
        "service": "pdp-connect-mock",
        "version": "0.1.0",
        "invoices": invoice_store.count(),
        "uptime_seconds": uptime(),
    }
