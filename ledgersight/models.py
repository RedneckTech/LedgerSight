"""Shared data models for LedgerSight."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any


@dataclass
class Transaction:
    """A single bank transaction with business metadata."""

    post_date: str
    description: str
    original_description: str
    amount: Decimal
    is_credit: bool
    balance: Decimal
    business_category: str = "Uncategorized"
    tax_category: str = "CPA Review"
    pnl_classification: str = ""
    merchant: str = ""
    include_in_pnl: bool = False
    cpa_review: bool = True
    review_reason: str = ""
    deductibility: str = "unknown"
    is_transfer: bool = False
    is_owner_related: bool = False
    is_fixed_asset: bool = False
    is_loan: bool = False
    source_statement: str = ""
    user_note: str = ""
    sequence: int = 0

    @property
    def signed_amount(self) -> Decimal:
        """Return positive for credits, negative for debits."""
        return self.amount if self.is_credit else -self.amount

    @property
    def date_obj(self) -> date:
        parts = self.post_date.split("/")
        return date(int(parts[2]), int(parts[0]), int(parts[1]))


@dataclass
class Statement:
    """Parsed bank statement."""

    statement_date: str  # MM/DD/YYYY
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
    file_path: str = ""

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
    def date_obj(self) -> date:
        parts = self.statement_date.split("/")
        return date(int(parts[2]), int(parts[0]), int(parts[1]))

    @property
    def quarter(self) -> int:
        return (self.month - 1) // 3 + 1


@dataclass
class ReconciliationResult:
    """Per-statement reconciliation outcome."""

    statement_label: str
    passed: bool
    parsed_credit_count: int
    expected_credit_count: int
    parsed_debit_count: int
    expected_debit_count: int
    parsed_credit_total: Decimal
    expected_credit_total: Decimal
    parsed_debit_total: Decimal
    expected_debit_total: Decimal
    beginning_balance: Decimal
    ending_balance: Decimal
    calculated_ending: Decimal
    balance_ok: bool
    warnings: list[str] = field(default_factory=list)
    forced: bool = False


@dataclass
class CategoryRule:
    """A rule for categorizing transactions."""

    pattern: str
    category: str
    tax_category: str = ""
    deductibility: str = "unknown"
    is_income: bool = False
    include_in_pnl: bool = True
    is_transfer: bool = False
    is_owner_related: bool = False
    is_fixed_asset: bool = False
    is_loan: bool = False
    direction: str = "either"  # "credit", "debit", or "either"
    priority: int = 50

    def __post_init__(self):
        if not self.tax_category:
            self.tax_category = "CPA Review"


@dataclass
class BusinessConfig:
    """Business configuration loaded from TOML or defaults."""

    business_name: str = "Business"
    dba: str = ""
    address: str = ""
    phone: str = ""
    email: str = ""
    tax_year: int = date.today().year
    fiscal_year_start: int = 1
    entity_type: str = "sole-prop"
    accounting_method: str = "cash"
    ein_display: str = ""
    mask_ein: bool = True
    bank_account_display: str = ""
    mask_account: bool = True
    industry: str = ""
    currency: str = "USD"
    cpa_name: str = "CPA"
    cpa_firm: str = ""
    cpa_email: str = ""
    cpa_phone: str = ""
    owners: list[str] = field(default_factory=list)
    projection_config: dict[str, Any] = field(default_factory=dict)
    custom_rules: list[CategoryRule] = field(default_factory=list)
    beginning_balances: dict[str, Decimal] = field(default_factory=dict)
    fixed_assets: list[dict] = field(default_factory=list)
    loans: list[dict] = field(default_factory=list)
    owner_activities: list[dict] = field(default_factory=list)
    document_checklist: dict[str, str] = field(default_factory=dict)
    merchant_aliases: dict[str, str] = field(default_factory=dict)

    def masked_ein(self) -> str:
        if self.mask_ein and self.ein_display:
            return re.sub(r"\d", "X", self.ein_display)
        return self.ein_display

    def masked_account(self) -> str:
        if self.mask_account and self.bank_account_display:
            parts = re.split(r"(\d+)", self.bank_account_display)
            for i in range(len(parts)):
                if parts[i].isdigit() and len(parts[i]) >= 4:
                    parts[i] = "X" * (len(parts[i]) - 4) + parts[i][-4:]
            return "".join(parts)
        return self.bank_account_display

    def display_name(self) -> str:
        if self.dba:
            return f"{self.business_name} ({self.dba})"
        return self.business_name


@dataclass
class FinancialPeriod:
    """A period for P&L or projection."""

    label: str
    start_date: date
    end_date: date
    months: int = 1
    year: int = 0
