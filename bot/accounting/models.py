"""Data models for accounting reconciliation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Transaction:
    date: str
    description: str
    value: float
    type: str  # "debit" or "credit"
    row_index: int
    original_notes: str = ""
    category: str | None = None
    note: str | None = None
    confidence: str = "unknown"  # "rule", "ai", "user", "unknown"


@dataclass
class ReconciliationResult:
    """Result of parsing + categorizing a reconciliation PDF."""
    filename: str
    bank_balance: float | None
    reconciled_balance: float | None
    company_balance: float | None
    difference: float | None
    debit_transactions: list[Transaction] = field(default_factory=list)
    credit_transactions: list[Transaction] = field(default_factory=list)
    company_debits: list[Transaction] = field(default_factory=list)
    company_credits: list[Transaction] = field(default_factory=list)
    parsed_at: datetime = field(default_factory=datetime.now)

    @property
    def all_transactions(self) -> list[Transaction]:
        return (
            self.debit_transactions
            + self.credit_transactions
            + self.company_debits
            + self.company_credits
        )

    @property
    def uncategorized(self) -> list[Transaction]:
        return [t for t in self.all_transactions if t.confidence == "unknown"]

    @property
    def categorized(self) -> list[Transaction]:
        return [t for t in self.all_transactions if t.confidence != "unknown"]


@dataclass
class CategoryRule:
    pattern: str
    category: str
    note_template: str = ""
    match_type: str = "contains"  # "exact", "contains", "regex"
    confidence: float = 1.0
    match_count: int = 0
    id: int | None = None
