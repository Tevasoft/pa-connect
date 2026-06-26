# 🔌 pdp-connect-mock

> **Local mock PDP** (Plateforme de Dématérialisation Partenaire) for testing French e-invoicing flows without a real PDP connection — covering the September 2026 reform.

[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-Apache%202.0-green)](LICENSE)
[![Reform](https://img.shields.io/badge/France-Sept.%202026%20Reform-red)]()
[![FastAPI](https://img.shields.io/badge/FastAPI-powered-teal)]()

Built with ❤️ by [TEVASOFT](https://tevasoft.eu) — creators of [EVA](https://tevasoft.eu), the AI-powered expense audit & e-invoicing compliance platform.

---

## 🎯 What is this?

The French e-invoicing reform (September 2026) requires all companies to route invoices through a **PDP** (certified dematerialization platform). Developing and testing your PDP integration before go-live is painful:

- Real PDPs require certification and onboarding
- Test environments are shared and slow
- Error scenarios are hard to reproduce reliably

**pdp-connect-mock** solves this by running a full PDP API locally in ~5 seconds:

```bash
pip install pdp-connect-mock
pdp-mock start
# 🚀 PDP mock running at http://localhost:8042
```

---

## 🚀 Quick start

```bash
pip install pdp-connect-mock
pdp-mock start --port 8042
```

```python
import httpx

# Submit a Factur-X invoice to the mock PDP
with open("invoice.pdf", "rb") as f:
    resp = httpx.post(
        "http://localhost:8042/api/v1/invoices",
        files={"file": ("invoice.pdf", f, "application/pdf")},
        headers={"Authorization": "Bearer test-token"},
        data={"routing_id": "FR12345678901", "format": "FACTURX"},
    )
print(resp.json())
# {
#   "invoice_id": "PDP-2026-00001",
#   "status": "DEPOSITED",
#   "lifecycle": [{"status": "DEPOSITED", "timestamp": "2026-06-01T10:00:00Z"}],
#   "routing_id": "FR12345678901"
# }
```

---

## 📡 Implemented API surface

The mock implements the core flows defined by the DGFIP PDP specifications:

### Invoice submission
| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/invoices` | Deposit an invoice (PDF/XML) |
| `GET` | `/api/v1/invoices/{id}` | Get invoice status |
| `GET` | `/api/v1/invoices` | List all invoices (with filters) |
| `POST` | `/api/v1/invoices/{id}/cancel` | Cancel a deposited invoice |

### Lifecycle & statuses
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/invoices/{id}/lifecycle` | Full lifecycle history |
| `POST` | `/api/v1/invoices/{id}/advance` | Force status transition (test only) |

### Webhooks
| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/webhooks` | Register a webhook endpoint |
| `GET` | `/api/v1/webhooks` | List registered webhooks |
| `DELETE` | `/api/v1/webhooks/{id}` | Unregister webhook |

### Test control (mock-only)
| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/_mock/scenario` | Set rejection/error scenario |
| `POST` | `/api/v1/_mock/reset` | Reset all state |
| `GET` | `/api/v1/_mock/stats` | Request stats |

---

## 🔄 Invoice lifecycle

```
DEPOSITED → VALIDATED → SENT → RECEIVED → ACCEPTED
                ↓
            REJECTED (business rule violation)
                ↓
           CANCELLED
```

The mock auto-advances invoices through the lifecycle at configurable intervals (default: immediate).

---

## ⚙️ Configuration

```yaml
# pdp-mock.yaml
server:
  host: "0.0.0.0"
  port: 8042
  log_level: "info"

lifecycle:
  auto_advance: true          # automatically progress invoices
  advance_delay_seconds: 2    # delay between status changes

validation:
  reject_invalid_xml: true    # reject non-conformant Factur-X
  require_routing_id: true    # require FR routing identifier

scenarios:
  rejection_rate: 0           # 0-100, % of invoices to auto-reject (chaos testing)
  network_delay_ms: 0         # simulate slow PDP

webhooks:
  retry_attempts: 3
  retry_delay_seconds: 5
```

---

## 🧪 Test scenarios

```python
import httpx

base = "http://localhost:8042/api/v1"

# Scenario: force next submission to be rejected
httpx.post(f"{base}/_mock/scenario", json={
    "type": "REJECT_NEXT",
    "reason": "INVALID_ROUTING_ID",
    "count": 1
})

# Scenario: simulate slow PDP (chaos testing)
httpx.post(f"{base}/_mock/scenario", json={
    "type": "DELAY",
    "delay_ms": 3000
})

# Scenario: simulate PDP outage
httpx.post(f"{base}/_mock/scenario", json={
    "type": "ERROR_RATE",
    "http_status": 503,
    "rate": 50   # 50% of requests return 503
})

# Reset everything
httpx.post(f"{base}/_mock/reset")
```

---

## 🐳 Docker

```bash
docker run -p 8042:8042 tevasoft/pdp-connect-mock
```

```yaml
# docker-compose.yml
services:
  pdp-mock:
    image: tevasoft/pdp-connect-mock:latest
    ports:
      - "8042:8042"
    environment:
      PDP_AUTO_ADVANCE: "true"
      PDP_REJECTION_RATE: "0"
```

---

## 🔗 Python client

```python
from pdp_mock import PDPClient

client = PDPClient("http://localhost:8042", token="test-token")

# Submit
invoice_id = client.submit("invoice.pdf", routing_id="FR12345678901")

# Poll until terminal state
result = client.wait_for_terminal(invoice_id, timeout=30)
print(result.status)  # ACCEPTED or REJECTED
```

---

## 🏢 About TEVASOFT

[TEVASOFT](https://tevasoft.eu) builds **EVA**, an AI-powered platform for expense report audit and e-invoicing compliance for enterprise.

---

## 📄 License

Apache 2.0
