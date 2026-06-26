"""pdp_mock — Local PDP mock for French e-invoicing 2026 reform testing."""
from .client import PDPClient, PDPError
from .models import InvoiceStatus, InvoiceFormat, TERMINAL_STATUSES

__version__ = "0.1.0"
__all__ = ["PDPClient", "PDPError", "InvoiceStatus", "InvoiceFormat", "TERMINAL_STATUSES"]
