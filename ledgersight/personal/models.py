"""Data models for the personal report profile."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal


@dataclass
class Transaction:
    """A single personal-banking transaction."""

    post_date: str
    description: str
    amount: Decimal
    is_credit: bool
    balance: Decimal
    category: str = ""
    # Where the row was read from: 1-based PDF page and the row's ordinal
    # within the statement's activity listing (1 = first row). Zero when the
    # transaction was synthesised (e.g. a card's interest line).
    source_page: int = 0
    source_row: int = 0


@dataclass
class Statement:
    """Parsed personal bank statement."""

    statement_date: str  # MM/DD/YYYY (period end)
    account_number: str
    beginning_balance: Decimal
    ending_balance: Decimal
    total_credits: Decimal
    total_debits: Decimal
    credit_count: int
    debit_count: int
    transactions: list[Transaction] = field(default_factory=list)
    checks_cleared: list[dict] = field(default_factory=list)
    daily_balances: list[dict] = field(default_factory=list)
    overdraft_fees: Decimal = Decimal("0")
    returned_item_fees: Decimal = Decimal("0")
    fees_charged: Decimal = Decimal("0")
    interest_charged: Decimal = Decimal("0")
    account_type: str = ""  # "Checking", "Savings", "Credit Card"
    institution: str = ""
    file_path: str = ""
    period_start: str = ""  # MM/DD/YYYY (period begin), when the statement states it
    # Card-statement terms, when printed (used for the bill & debt calendar).
    payment_due_date: str = ""  # MM/DD/YYYY
    minimum_payment: Decimal = Decimal("0")
    credit_limit: Decimal = Decimal("0")
    apr_purchases: Decimal = Decimal("0")  # percent, e.g. 30.49

    @property
    def month(self) -> int:
        return int(self.statement_date.split("/")[0])

    @property
    def year(self) -> int:
        return int(self.statement_date.split("/")[2])

    @property
    def month_label(self) -> str:
        return datetime(self.year, self.month, 1).strftime("%B %Y")

    @property
    def account_label(self) -> str:
        """Display name for this account."""
        if self.institution:
            return f"{self.institution} {self.account_number}"
        return self.account_number or "Unknown Account"
