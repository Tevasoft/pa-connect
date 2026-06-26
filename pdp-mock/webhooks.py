"""
pdp_mock.webhooks — Async webhook delivery with retry logic.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone

import httpx

from .models import Invoice, InvoiceStatus, Webhook, WebhookEvent, WebhookPayload
from .storage import webhook_store

logger = logging.getLogger("pdp_mock.webhooks")

STATUS_TO_EVENT: dict[InvoiceStatus, WebhookEvent] = {
    InvoiceStatus.DEPOSITED: WebhookEvent.INVOICE_DEPOSITED,
    InvoiceStatus.VALIDATED: WebhookEvent.INVOICE_VALIDATED,
    InvoiceStatus.REJECTED: WebhookEvent.INVOICE_REJECTED,
    InvoiceStatus.SENT: WebhookEvent.INVOICE_SENT,
    InvoiceStatus.RECEIVED: WebhookEvent.INVOICE_RECEIVED,
    InvoiceStatus.ACCEPTED: WebhookEvent.INVOICE_ACCEPTED,
    InvoiceStatus.REFUSED: WebhookEvent.INVOICE_REFUSED,
    InvoiceStatus.CANCELLED: WebhookEvent.INVOICE_CANCELLED,
}

MAX_RETRIES = 3
RETRY_DELAYS = [2, 5, 10]


def _sign(payload: str, secret: str) -> str:
    """HMAC-SHA256 signature for webhook payload."""
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


async def deliver_event(invoice: Invoice, status: InvoiceStatus) -> None:
    """Fire-and-forget: deliver webhook events to all subscribed endpoints."""
    event = STATUS_TO_EVENT.get(status)
    if not event:
        return

    webhooks = [
        wh for wh in webhook_store.list()
        if wh.active and (not wh.events or event in wh.events)
    ]
    if not webhooks:
        return

    payload = WebhookPayload(
        event=event,
        invoice_id=invoice.invoice_id,
        status=status,
        routing_id=invoice.routing_id,
    )
    payload_json = payload.model_dump_json()

    tasks = [_deliver_to(wh, payload_json) for wh in webhooks]
    await asyncio.gather(*tasks, return_exceptions=True)


async def _deliver_to(wh: Webhook, payload_json: str) -> None:
    headers = {
        "Content-Type": "application/json",
        "X-PDP-Delivery": "pdp-connect-mock/0.1.0",
        "X-PDP-Timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if wh.secret:
        headers["X-PDP-Signature"] = f"sha256={_sign(payload_json, wh.secret)}"

    async with httpx.AsyncClient(timeout=10) as client:
        for attempt, delay in enumerate(RETRY_DELAYS, start=1):
            try:
                resp = await client.post(wh.url, content=payload_json, headers=headers)
                if resp.status_code < 400:
                    wh.delivery_count += 1
                    logger.info(f"Webhook {wh.webhook_id} delivered (attempt {attempt})")
                    return
                logger.warning(
                    f"Webhook {wh.webhook_id} got HTTP {resp.status_code} "
                    f"(attempt {attempt}/{MAX_RETRIES})"
                )
            except Exception as e:
                logger.warning(
                    f"Webhook {wh.webhook_id} delivery failed: {e} "
                    f"(attempt {attempt}/{MAX_RETRIES})"
                )
            if attempt < MAX_RETRIES:
                await asyncio.sleep(delay)

    wh.failure_count += 1
    logger.error(f"Webhook {wh.webhook_id} exhausted retries")
