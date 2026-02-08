"""Data models for invoice scanning."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class InvoiceLineItem:
    description: str
    quantity: float
    unit_price: float
    iva_rate: float          # e.g., 6.0, 13.0, 23.0
    iva_amount: float
    total: float
    line_index: int = 0


@dataclass
class IVABreakdown:
    rate: float              # e.g., 6.0, 13.0, 23.0
    base_amount: float       # taxable base for this rate
    iva_amount: float        # tax amount


@dataclass
class Invoice:
    vendor_name: str
    vendor_nif: str
    invoice_number: str
    invoice_date: str        # YYYY-MM-DD

    due_date: str = ""
    line_items: list[InvoiceLineItem] = field(default_factory=list)
    iva_breakdown: list[IVABreakdown] = field(default_factory=list)

    subtotal: float = 0.0   # before IVA
    total_iva: float = 0.0
    total: float = 0.0      # subtotal + total_iva

    category: str | None = None
    note: str | None = None
    confidence: str = "unknown"  # "ai", "rule", "user", "unknown"

    source_type: str = "pdf"     # "pdf" or "photo"
    source_filename: str = ""
    session_id: str = ""
    id: int | None = None
    created_at: datetime = field(default_factory=datetime.now)

    @property
    def is_categorized(self) -> bool:
        return self.confidence != "unknown"
