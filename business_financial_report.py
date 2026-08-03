#!/usr/bin/env python3
"""Business Financial Report Generator.

Parses business checking-account PDF statements, categorizes income and expenses,
and produces comprehensive business financial reports including:

- Cash-basis Profit & Loss statements
- Configurable financial projections
- CPA / tax-preparer package
- Charts, tables, and audit exports

Usage:
    python3 business_financial_report.py
    python3 business_financial_report.py --year 2026
    python3 business_financial_report.py --year 2026 --projections
    python3 business_financial_report.py --quarter 2 --year 2026
    python3 business_financial_report.py --mode cpa --year 2026
    python3 business_financial_report.py --audit --export-pl --export-cpa
    python3 business_financial_report.py --config lagoon_transport.toml
    python3 business_financial_report.py --init-config
    python3 business_financial_report.py --self-test

Dependencies: fpdf2, matplotlib, pdftotext (system)
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import logging
import os
import re
import subprocess
import sys
import tomllib
import zipfile
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from fpdf import FPDF

# =============================================================================
# Constants
# =============================================================================

SCRIPT_DIR = Path(__file__).resolve().parent
_SCRIPT_HASH = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:8]
_DEFAULT_CONFIG = "business_report.toml"
_PNL_DISCLAIMER = "Cash-Basis Management Report \u2014 Subject to CPA Review"
_CPA_DISCLAIMER = (
    "This package is an organizational aid prepared from available bank-statement data. "
    "It is not a tax return, audit, review, compilation, or substitute for professional "
    "accounting advice. All classifications and deductions are subject to verification "
    "by the business owner and tax professional."
)

logger = logging.getLogger("business_report")

_QUARTER_MONTHS: dict[int, list[int]] = {
    1: [1, 2, 3],
    2: [4, 5, 6],
    3: [7, 8, 9],
    4: [10, 11, 12],
}


def _fiscal_quarter_months(quarter: int, fiscal_year_start: int = 1) -> list[int]:
    """Return the calendar months for a given fiscal quarter.

    fiscal_year_start is the calendar month (1-12) that begins the fiscal year.
    """
    fy_start_0 = (fiscal_year_start - 1) % 12  # zero-based
    q_start_0 = fy_start_0 + (quarter - 1) * 3
    return [(q_start_0 + i) % 12 + 1 for i in range(3)]

# =============================================================================
# Example TOML Configuration
# =============================================================================

EXAMPLE_TOML = '''# Business Financial Report Configuration
# Placeholder values — replace with your own business data.

[general]
business_name = "Acme Transport LLC"
dba = "Acme Transport"
address = "123 Main St, Anytown ST 00000"
phone = "555-555-1212"
email = "contact@example.com"
tax_year = 2025
fiscal_year_start = 1  # 1 = January
entity_type = "single-member-llc"  # sole-prop, single-member-llc, partnership, s-corp, c-corp, other
accounting_method = "cash"  # cash or accrual
ein_display = "XX-XXXXXXX"
mask_ein = true
bank_account_display = "CLASSIC BUSINESS CHECKING - XXXX0000"
mask_account = true
industry = "trucking"
currency = "USD"

[cpa]
name = "Jane Smith, CPA"
firm = "Smith & Associates, CPAs"
email = "jane@smithcpa.example.com"
phone = "555-555-3000"

[owner]
owners = ["John Doe"]

[projections]
monthly_revenue_growth = 0.03  # 3%
monthly_expense_inflation = 0.02  # 2%
cogs_percentage = 0.35  # 35% of revenue
payroll_growth = 0.02  # 2% (reserved for future use)
lookback_months = 6
projection_months = 12
min_cash_balance = 5000.0
tax_reserve_pct = 0.25  # 25% of net income
one_time_revenue = []  # reserved for future use
one_time_expenses = []  # reserved for future use
planned_equipment = []  # reserved for future use

# Seasonal multipliers (1.0 = no adjustment)
[projections.seasonal]
1 = 0.85
2 = 0.90
3 = 0.95
4 = 1.05
5 = 1.10
6 = 1.15
7 = 1.10
8 = 1.05
9 = 1.00
10 = 0.95
11 = 0.90
12 = 0.85

# Three scenarios (growth rates override monthly_revenue_growth)
[projections.scenarios.conservative]
monthly_revenue_growth = 0.01

[projections.scenarios.base]
monthly_revenue_growth = 0.03

[projections.scenarios.growth]
monthly_revenue_growth = 0.06

# Beginning balance sheet (reserved for future use — not yet integrated into reports)
[balances]
beginning_cash = 0
beginning_accounts_receivable = 0
beginning_inventory = 0
beginning_fixed_assets = 0
beginning_accumulated_depreciation = 0
beginning_accounts_payable = 0
beginning_loan_balances = 0
beginning_owner_equity = 0

# Known fixed assets (reserved for future integration)
[[fixed_assets]]
name = "2020 Freightliner Cascadia"
purchase_date = "2020-06-15"
cost = 85000
asset_class = "5-year"

# Known loans (reserved for future integration)
[[loans]]
lender = "Equipment Finance Co"
original_amount = 70000
current_balance = 45000
monthly_payment = 1850
interest_rate = 0.065
description_keywords = ["EQUIPMENT FINANCE"]

# Owner activity (reserved for future integration)
[[owner_activity]]
type = "contribution"
date = "2025-01-15"
amount = 10000
description = "Initial capital contribution"

# Custom transaction category rules
# Each rule has: pattern (regex), category, tax_category, deductibility, direction
# Rules are evaluated in order; first match wins.
# direction: "credit", "debit", or "either" — rules skip mismatched-direction transactions
[[rules]]
pattern = "APEX CAPITAL CORP"
category = "Service Revenue"
tax_category = "Gross Receipts"
deductibility = "not-applicable"
direction = "credit"
is_income = true

[[rules]]
pattern = "PILOT|FLYING J|LOVE'?S|KWIK STAR|CASEYS|MARATHON|TA\\\\s+#?\\\\d*\\\\s+[A-Z]{3}"
category = "Fuel"
tax_category = "Vehicle Fuel"
deductibility = "likely-deductible"
direction = "debit"

[[rules]]
pattern = "ALLIANT ENERGY|ALLPAID"
category = "Utilities"
tax_category = "Utilities"
deductibility = "likely-deductible"
direction = "debit"

[[rules]]
pattern = "WEB XFER|ZELLE|PAYPAL INST XFER"
category = "Account Transfer"
tax_category = "non-pl"
deductibility = "not-applicable"
direction = "either"

[[rules]]
pattern = "CHECK #"
category = "Uncategorized"
tax_category = "CPA Review"
deductibility = "unknown"
direction = "debit"

# Document checklist for CPA package
[document_checklist]
bank_statements = "provided"
credit_card_statements = "not-provided"
loan_statements = "not-provided"
payroll_reports = "not-provided"
quarterly_payroll_filings = "not-provided"
forms_w2 = "not-provided"
forms_1099 = "not-provided"
contractor_w9 = "not-provided"
sales_tax_filings = "not-provided"
prior_year_tax_return = "not-provided"
fixed_asset_docs = "not-provided"
vehicle_docs = "not-provided"
mileage_logs = "not-provided"
insurance_statements = "not-provided"
merchant_processor_reports = "not-provided"
ar_records = "not-provided"
ap_records = "not-provided"
inventory_records = "not-provided"
owner_contribution_records = "not-provided"
owner_distribution_records = "not-provided"
estimated_tax_confirmations = "not-provided"
business_license = "not-provided"
home_office_records = "not-provided"
health_insurance_docs = "not-provided"
'''

# =============================================================================
# Data Models
# =============================================================================

VALID_DEDUCTIBILITY = {
    "likely-deductible", "possibly-deductible",
    "not-normally-deductible", "unknown", "not-applicable",
}

VALID_ENTITY_TYPES = {
    "sole-prop", "single-member-llc", "partnership",
    "s-corp", "c-corp", "other",
}


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


# =============================================================================
# Financial Period Helpers
# =============================================================================


@dataclass
class FinancialPeriod:
    """A period for P&L or projection."""

    label: str
    start_date: date
    end_date: date
    months: int = 1
    year: int = 0


def get_period_months(year: int, month: int) -> list[FinancialPeriod]:
    """Build list of monthly periods for a year."""
    periods = []
    for m in range(1, 13):
        sd = date(year, m, 1)
        if m == 12:
            ed = date(year, 12, 31)
        else:
            ed = date(year, m + 1, 1) - timedelta(days=1)
        periods.append(FinancialPeriod(
            label=datetime(year, m, 1).strftime("%B %Y"),
            start_date=sd,
            end_date=ed,
            months=1,
            year=year,
        ))
    return periods


def get_quarter_periods(year: int) -> list[FinancialPeriod]:
    """Build list of quarterly periods."""
    periods = []
    labels = ["Q1", "Q2", "Q3", "Q4"]
    for qi, (label, months) in enumerate(zip(labels, _QUARTER_MONTHS.values())):
        m_start = months[0]
        m_end = months[-1]
        sd = date(year, m_start, 1)
        if m_end == 12:
            ed = date(year, 12, 31)
        else:
            ed = date(year, m_end + 1, 1) - timedelta(days=1)
        periods.append(FinancialPeriod(
            label=f"{label} {year}",
            start_date=sd,
            end_date=ed,
            months=3,
            year=year,
        ))
    return periods


# =============================================================================
# Helpers
# =============================================================================


MONEY_RE = re.compile(
    r"(?P<paren>\()?"
    r"(?P<minus>-)?"
    r"\$(?P<amount>[\d,]+\.\d{2})"
    r"(?(paren)\))"
)


def parse_amount(text: str) -> Decimal:
    """Parse a dollar amount string into Decimal.

    Handles: $1,234.56, -$500.00, ($99.99), and plain numbers.
    """
    t = text.strip()
    m = MONEY_RE.search(t)
    if m:
        amt = m.group("amount").replace(",", "")
        sign = -1 if (m.group("minus") or m.group("paren")) else 1
        return Decimal(amt) * sign
    t = t.replace("$", "").replace(",", "").replace("\xa0", "")
    if not t or t == "-":
        return Decimal("0")
    return Decimal(t)


def fmt_dollar(val: Decimal) -> str:
    """Format Decimal as a dollar string."""
    sign = "-" if val < 0 else ""
    v = abs(val)
    return f"{sign}${v:,.2f}"


def fmt_dollar_plain(val: Decimal) -> str:
    """Format without currency sign."""
    sign = "-" if val < 0 else ""
    return f"{sign}{abs(val):,.2f}"


def safe_pct(numerator: Decimal, denominator: Decimal) -> str:
    """Safe percentage string, returning N/A on zero denominator."""
    if denominator == 0:
        return "N/A"
    pct = float(numerator / denominator) * 100
    return f"{pct:.1f}%"


def safe_float(val: Decimal) -> float:
    """Convert Decimal to float, useful for charting."""
    return float(val)


def safe_div(numerator: Decimal, denominator: Decimal) -> Decimal:
    """Safe division returning Decimal 0 on zero denominator."""
    if denominator == 0:
        return Decimal("0")
    return numerator / denominator


def find_pdfs(directory: Path) -> list[Path]:
    """Find all PDF files in a directory, excluding the generated report."""
    pdfs = []
    exclude_patterns = [
        "personal_financial_report", "business_financial_report",
    ]
    for p in sorted(directory.iterdir()):
        if not p.suffix.lower() == ".pdf":
            continue
        name_lower = p.name.lower()
        if any(pat in name_lower for pat in exclude_patterns):
            continue
        pdfs.append(p)
    return pdfs


def file_hash(path: Path) -> str:
    """SHA-256 hash of a file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parse_daily_date(date_str: str) -> date | None:
    """Parse a daily-balance date string like '01/15/2023' to a date object."""
    if not date_str:
        return None
    try:
        parts = date_str.split("/")
        return date(int(parts[2]), int(parts[0]), int(parts[1]))
    except (ValueError, IndexError):
        return None


# =============================================================================
# PDF Text Extraction
# =============================================================================


def check_pdftotext() -> None:
    """Verify pdftotext is installed."""
    try:
        subprocess.run(
            ["pdftotext", "-v"],
            capture_output=True,
            timeout=5,
        )
    except FileNotFoundError:
        logger.error(
            "pdftotext not found. Install poppler-utils:\n"
            "  Ubuntu/Debian: sudo apt install poppler-utils\n"
            "  macOS: brew install poppler\n"
            "  Windows: https://github.com/oschwartz10612/poppler-windows/releases"
        )
        sys.exit(1)
    except Exception as exc:
        logger.error(f"Cannot run pdftotext: {exc}")
        sys.exit(1)


def extract_text(pdf_path: Path) -> str:
    """Extract text from a PDF using pdftotext."""
    result = subprocess.run(
        ["pdftotext", "-layout", str(pdf_path), "-"],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return result.stdout


# =============================================================================
# Statement Parsing
# =============================================================================


def _clean_description(desc: str) -> str:
    """Clean a transaction description."""
    desc = re.sub(r"\d\s+\d{2}/\d{2}", "", desc)
    desc = re.sub(r"\s+\d{1,2}\s*$", "", desc)
    desc = re.sub(r"\b\w{50,}\b", "", desc)
    desc = re.sub(r"\b([A-Za-z]+)\d{1,2}\b", r"\1", desc)
    desc = re.sub(r"\s+", " ", desc).strip()
    return desc


def _is_page_artifact(line: str, stripped: str) -> bool:
    """Check if a line is a page header/footer artifact to skip."""
    if not stripped:
        return True
    if "\x0c" in line:
        return True
    if re.match(r"^[0-9A-F]{32}", stripped):
        return True
    if "Statement Ending" in stripped and "Page" not in stripped:
        return True
    if "continued" in stripped.lower():
        return True
    if "Post Date" in stripped and "Description" in line:
        return True
    if re.match(r"^Page \d+ of \d+", stripped):
        return True
    if re.match(r"^[A-Z\s]+LLC\s+X+", stripped):
        return True
    if "BASIC CHECKING" in stripped and "XXXX" in stripped:
        return True
    if "Checking Account Statements" in stripped:
        return True
    if "Account Activity" in stripped:
        return True
    if "Managing Your Accounts" in stripped:
        return True
    if "Client Contact" in stripped:
        return True
    if re.match(r"^\d{8}\s+Checking Account Statements", stripped):
        return True
    if "RETURN SERVICE REQUESTED" in stripped:
        return True
    if re.match(r"^#\d+\s+", stripped):
        return True
    if re.match(r"^\d{1,2}\s*$", stripped):
        return True
    if re.match(r"[A-Z]{2}\s+\d{5}", stripped) and len(stripped) < 20:
        return True
    if "Summary of Accounts" in stripped:
        return True
    if re.match(r"^(?:CLASSIC|BASIC|BUSINESS)\s+CHECKING", stripped):
        return True
    if "Average Ledger" in stripped:
        return True
    if "Service Charges" in stripped:
        return True
    return False


# Section boundaries that terminate account-activity parsing.
_ACTIVITY_SECTION_ENDINGS = (
    "Checks Cleared",
    "Daily Balances",
    "Service Charges",
    "Overdraft",
    "Total Overdraft Fees",
    "Managing Your Accounts",
    "Client Contact",
)


def _is_section_ending(line: str) -> bool:
    """Return True if *line* is a recognised next-section heading."""
    stripped = line.strip()
    for heading in _ACTIVITY_SECTION_ENDINGS:
        if heading in stripped:
            return True
    return False


def parse_statement(text: str, file_path: str = "") -> Statement:
    """Parse a First Interstate Bank statement PDF text into a Statement.

    Uses a section-aware state machine that terminates transaction
    extraction on recognised next-section headings instead of relying
    on a single "Checks Cleared" sentinel.
    """
    lines = text.split("\n")

    statement_date = ""
    account_number = ""
    for line in lines[:15]:
        if not statement_date:
            m = re.search(r"Statement Ending\s+(\d{2}/\d{2}/\d{4})", line)
            if m:
                statement_date = m.group(1)
        if not account_number:
            m = re.search(r"(XXXXXXXXXXX?\d{4})", line)
            if m:
                account_number = m.group(1)
        if statement_date and account_number:
            break

    beginning_balance = Decimal("0")
    ending_balance = Decimal("0")
    total_credits = Decimal("0")
    total_debits = Decimal("0")
    credit_count = 0
    debit_count = 0

    in_summary = False
    for line in lines:
        if "Account Summary" in line:
            in_summary = True
            continue
        if not in_summary:
            continue
        if _is_section_ending(line):
            break
        if "Beginning Balance" in line:
            m = MONEY_RE.search(line)
            if m:
                beginning_balance = parse_amount(m.group())
        elif "Credit" in line and "This Period" in line:
            m = re.search(r"(\d+)\s+Credit", line)
            if m:
                credit_count = int(m.group(1))
            m2 = MONEY_RE.search(line)
            if m2:
                total_credits = parse_amount(m2.group())
        elif "Debit" in line and "This Period" in line:
            m = re.search(r"(\d+)\s+Debit", line)
            if m:
                debit_count = int(m.group(1))
            m2 = MONEY_RE.search(line)
            if m2:
                total_debits = parse_amount(m2.group())
        elif "Ending Balance" in line:
            m = MONEY_RE.search(line)
            if m:
                ending_balance = parse_amount(m.group())
            break

    # ---- Account Activity (section-aware extraction) ----
    transactions: list[Transaction] = []
    tx_seq = 0
    in_activity = False
    activity_started = False
    header_positions: dict[str, int] = {}
    desc_buffer: list[str] = []

    for line in lines:
        if "Account Activity" in line and not activity_started:
            in_activity = True
            activity_started = True
            continue
        if not in_activity:
            continue
        if not line.strip():
            continue

        # Column-header detection
        if not header_positions and "Post Date" in line:
            for col_name in ["Debits", "Credits", "Balance"]:
                pos = line.find(col_name)
                if pos >= 0:
                    header_positions[col_name] = pos
            continue

        # ---- Check for a dated transaction line FIRST ----
        # A line may contain both a date+transaction AND a later
        # section heading (e.g. "02/28 SALES TAX Daily Balances ...").
        # We must extract the transaction before deciding to stop.
        date_match = re.match(r"^(\d{2}/\d{2}/\d{4})", line)
        if date_match:
            post_date = date_match.group(1)

            if "Beginning Balance" in line or "Ending Balance" in line:
                desc_buffer.clear()
                continue

            amounts = list(MONEY_RE.finditer(line))
            if not amounts:
                desc_part = line[date_match.end():].strip()
                if desc_part:
                    desc_buffer.append(desc_part)
                continue

            # ---- Determine balance and amount columns ----
            balance = parse_amount(amounts[-1].group())

            if len(amounts) == 1:
                # Single-amount line: could be a balance-only row, a
                # wrapped continuation, or a stand-alone amount with
                # balance on another line.  Try column-position
                # heuristics first.
                if header_positions:
                    amt_col = amounts[0].start()
                    bal_col = header_positions.get("Balance", 9999)
                    if amt_col >= bal_col - 3:
                        # Amount is in the Balance column position —
                        # treat as a balance-only line, skip.
                        continue
                # Fallback: treat as a description-line continuation.
                desc_part = line[date_match.end():amounts[0].start()].strip()
                if desc_part:
                    desc_buffer.append(desc_part)
                continue

            tx_amount = parse_amount(amounts[-2].group())

            # ---- Credit/debit detection (arithmetic first) ----
            # Running-balance arithmetic is the most reliable indicator.
            # Column-position heuristics are the fallback.
            is_credit = False
            prev_bal = (transactions[-1].balance if transactions
                        else beginning_balance)

            credited = abs((prev_bal + tx_amount) - balance) <= RC_TOLERANCE
            debited = abs((prev_bal - tx_amount) - balance) <= RC_TOLERANCE

            if credited and not debited:
                is_credit = True
            elif debited and not credited:
                is_credit = False
            elif credited and debited:
                # Ambiguous (amount = 0 or balance didn't change).
                # Fall through to column-position heuristics.
                pass

            # Column-position fallback when arithmetic is inconclusive
            if not (credited or debited) or (credited and debited):
                if header_positions:
                    amount_col = amounts[-2].start()
                    credit_col = header_positions.get("Credits", 9999)
                    debit_col = header_positions.get("Debits", 0)
                    if amount_col >= credit_col - 2:
                        is_credit = True
                    elif amount_col < debit_col + 10 and amount_col + 6 < credit_col:
                        is_credit = False
                    else:
                        if transactions:
                            is_credit = balance > transactions[-1].balance
                        elif beginning_balance:
                            is_credit = balance > beginning_balance
                else:
                    if transactions:
                        is_credit = balance > transactions[-1].balance
                    elif beginning_balance:
                        is_credit = balance > beginning_balance

            desc_parts = list(desc_buffer)
            current_desc = line[date_match.end():amounts[-2].start()].strip()
            has_own_desc = bool(current_desc)

            if has_own_desc:
                if transactions:
                    stray_text = " ".join(desc_parts).strip()
                    if stray_text:
                        tx = transactions[-1]
                        tx.description = _clean_description(
                            f"{tx.description} {stray_text}"
                        )
                        tx.original_description = tx.description
                desc_buffer.clear()
                description = _clean_description(current_desc)
            else:
                if current_desc:
                    desc_parts.append(current_desc)
                description = " ".join(desc_parts).strip()
                description = _clean_description(description)
                desc_buffer.clear()

            tx_seq += 1
            transactions.append(Transaction(
                post_date=post_date,
                description=description,
                original_description=description,
                amount=tx_amount,
                is_credit=is_credit,
                balance=balance,
                source_statement=file_path,
                sequence=tx_seq,
            ))

            # After parsing the transaction line, check whether this
            # line ALSO contains a section-ending heading that tells us
            # to stop reading further lines.
            if _is_section_ending(line):
                break
            continue

        # ---- No date: check for section ending ----
        if _is_section_ending(line):
            if desc_buffer and transactions:
                extra = _clean_description(" ".join(desc_buffer))
                if extra:
                    tx = transactions[-1]
                    tx.description = _clean_description(
                        f"{tx.description} {extra}"
                    )
                    tx.original_description = tx.description
                desc_buffer.clear()
            break

        # ---- Plain description continuation line ----
        stripped = line.strip()
        if stripped and not _is_page_artifact(line, stripped):
            desc_buffer.append(stripped)

    # Checks Cleared
    checks: list[dict] = []
    in_checks = False
    for line in lines:
        if "Checks Cleared" in line:
            in_checks = True
            continue
        if not in_checks:
            continue
        if "Daily Balances" in line:
            break
        m = re.match(r"\s*(\d+)\s+(\d{2}/\d{2}/\d{4})\s+\$([\d,]+\.\d{2})", line)
        if m:
            checks.append({
                "number": int(m.group(1)),
                "date": m.group(2),
                "amount": parse_amount("$" + m.group(3)),
            })

    # Daily Balances
    daily_balances: list[dict] = []
    in_daily = False
    for line in lines:
        if "Daily Balances" in line:
            in_daily = True
            continue
        if not in_daily:
            continue
        # Stop on next section headings
        if "Overdraft" in line or "Total Overdraft Fees" in line or _is_section_ending(line):
            break
        pairs = re.findall(r"(\d{2}/\d{2}/\d{4})\s+(-?\$[\d,]+\.\d{2})", line)
        for date_str, amt_str in pairs:
            daily_balances.append({
                "date": date_str,
                "balance": parse_amount(amt_str),
            })

    return Statement(
        statement_date=statement_date,
        account_number=account_number,
        beginning_balance=beginning_balance,
        ending_balance=ending_balance,
        total_credits=total_credits,
        total_debits=total_debits,
        credit_count=credit_count,
        debit_count=debit_count,
        transactions=transactions,
        checks_cleared=checks,
        daily_balances=daily_balances,
        file_path=file_path,
    )


# =============================================================================
# Default Business Categories
# =============================================================================

_INCOME_CATEGORIES: dict[str, list[str]] = {
    "Sales Revenue": [
        r"\bINVOICE\b", r"\bSALE\b.*\bREVENUE\b",
    ],
    "Service Revenue": [
        r"\bSERVICE\s+REVENUE\b",
    ],
    "Freight Revenue": [
        r"\bFREIGHT\b", r"\bTRUCKING\s+REVENUE\b",
        r"Incoming\s+Wire\s+.*APEX\s+CAPITAL\s+CORP",
    ],
    "Contract Revenue": [
        r"\bCONTRACT\s+PAY(?:MENT)?\b",
    ],
    "Other Operating Income": [
        r"\bOPERATING\s+INCOME\b",
    ],
    "Interest Income": [
        r"\bINTEREST\s+(?:PAID|EARNED|INCOME)\b",
    ],
    "Refunds and Reimbursements": [
        r"\bREFUND\b", r"\bREIMBURSEMENT\b", r"\bREBATE\b",
    ],
    "Other Income": [
        r"\bOTHER\s+INCOME\b",
    ],
}

_EXPENSE_CATEGORIES: dict[str, list[str]] = {
    "Fuel": [
        r"\bPILOT\b", r"FLYING\s*J", r"LOVE'?S",
        r"KWIK\s*STAR", r"\bCASEYS?\b",
        r"\bMARATHON\b", r"\bYESWAY\b", r"CIRCLE\s*K",
        r"\bTRAVEL\s+CNT\b", r"\bTRAVEL\s+CENTER\b",
        r"\bTA\b\s+(?:#\d+\s+)?[A-Z]{3,}",
        r"\bBP#\d", r"\bBB\s+OF\s+HOUSTON\b",
        r"\bBREAK\s+TIME\b", r"\bSHELL\b.*SERVICE",
        r"\b59\s+FASTLANE\b", r"\bCTLP\b",
        r"\bWOODSHED\b", r"\bPORT\s+AUTO\s+TRUCK\b",
        r"\bTEX\s+BEST\b", r"\bTBS\b.*DENISON",
        r"\bDIESEL\b", r"\bGAS\b.*\bSTATION\b",
    ],
    "Freight and Shipping": [
        r"\bFREIGHT\s+(?:CHARGE|FEE|COST)\b",
        r"\bSHIPPING\b",
    ],
    "Subcontractors": [
        r"\bSUBCONTRACTOR\b", r"\bSUBCONTRACT\b",
    ],
    "Direct Labor": [
        r"\bDIRECT\s+LABOR\b",
    ],
    "Materials and Supplies": [
        r"\bMATERIALS\b", r"\bSUPPLIES\b",
    ],
    "Equipment Rental": [
        r"\bRENTAL\b", r"\bLEASE\b", r"\bEQUIPMENT\s+RENTAL\b",
    ],
    "Tolls and Scale Fees": [
        r"\bTOLL\b", r"\bSCALE\s+FEE\b", r"\bWEIGH\s+STATION\b",
    ],
    "Other Direct Costs": [],
    "Advertising and Marketing": [
        r"\bADVERTISING\b", r"\bMARKETING\b", r"\bDAT\s+SOLUTIONS\b",
        r"\bLOAD\s*BOARD\b", r"\b123LOADBOARD\b",
    ],
    "Bank and Merchant Fees": [
        r"MasterCard Cross Border", r"SALES\s+TAX\b", r"Overdraft",
        r"FEE\s+FOR\s+DDA", r"DDA\s+WITHDRAWAL",
        r"Incoming Wire Transfer Fee",
        r"\bWIRE\s+TRANSFER\s+FEE\b",
        r"^SERVICE\s+CHARGE$",
    ],
    "Business Insurance": [
        r"\bPROGRESSIVE\s+INS\b", r"\bINSURANCE\b",
    ],
    "Commissions": [
        r"\bCOMMISSION\b",
    ],
    "Contract Labor": [
        r"\bCONTRACT\s+LABOR\b",
    ],
    "Depreciation Placeholder": [],
    "Dues and Subscriptions": [
        r"\bDUES\b", r"\bMEMBERSHIP\b",
    ],
    "Employee Benefits": [
        r"\bBENEFITS\b",
    ],
    "Equipment Maintenance": [
        r"\bPARTS\b", r"\bREPAIR\b", r"\bMAINTENANCE\b",
        r"O'REILLY\s+AUTO", r"\bAUTO\s+ZONE\b",
    ],
    "Legal and Professional Fees": [
        r"\bLEGAL\b", r"\bATTORNEY\b", r"\bLAW\s+OFFICE\b",
        r"\bLEGALSHIELD\b",
    ],
    "Licenses and Permits": [
        r"\bLICENSE\b", r"\bPERMIT\b", r"\bREGISTRATION\b",
    ],
    "Meals": [
        r"SONIC\s+DRIVE", r"\bARBYS?\b", r"MCDONALD",
        r"\bARANDAS\b", r"DOORDASH", r"\bWENDYS?\b",
        r"BURGER\s+KING", r"TACO\s+JOHN",
        r"PANCHEROS[\s-]*MEXICA", r"CARL'?S\s*JR",
        r"\bDENNY'?S?\b", r"\bHARDEE'?S\b",
        r"DAIRY\s+QUEEN", r"\bHAYMAKERS?\b",
        r"STILL\s+SMOKIN", r"HONG\s+KONG\s+BUFFET",
        r"\bRESTAURANT\b",
    ],
    "Office Expense": [
        r"\bOFFICE\b", r"\bSTAPLES\b", r"\bOFFICE\s+DEPOT\b",
    ],
    "Payroll": [
        r"\bPAYROLL\b", r"\bSALARY\b", r"\bWAGES\b",
    ],
    "Payroll Taxes": [
        r"\bPAYROLL\s+TAX\b",
    ],
    "Rent or Lease": [
        r"\bRENT\b", r"\bLEASE\s+PAYMENT\b",
    ],
    "Repairs and Maintenance": [
        r"\bREPAIR\b", r"\bMAINTENANCE\b",
    ],
    "Software and Cloud Services": [
        r"GOOGLE\s+LLC\s+GSUIT", r"\bGSUITE\b",
        r"\bMICROSOFT\s*365\b", r"\bDROPBOX\b",
        r"\bSOFTWARE\b", r"\bCLOUD\b",
    ],
    "Taxes and Fees": [
        r"\bTAX\b", r"\bFEE\b",
    ],
    "Telephone and Internet": [
        r"\bSTRAIGHT\s*TALK\b", r"\bVERIZON\b",
        r"\bAT&T\b", r"\bT-MOBILE\b",
        r"\bINTERNET\b", r"\bPHONE\b",
    ],
    "Travel": [
        r"\bTRAVEL\b", r"\bHOTEL\b", r"\bMOTEL\b",
        r"\bAIRLINE\b", r"\bFLIGHT\b",
    ],
    "Utilities": [
        r"\bALLIANT\s+ENERGY\b", r"\bUTILITY\b",
        r"\bELECTRIC\b", r"\bWATER\b", r"\bGAS\s+COMPANY\b",
    ],
    "Vehicle Expense": [
        r"TRUCK\s*PARKING", r"\bCAR\s*WASH\b",
        r"\bTIRE\b", r"\bOIL\s+CHANGE\b",
    ],
}

_NON_PNL_CATEGORIES: dict[str, list[str]] = {
    "Account Transfer": [
        r"WEB\s+XFER", r"ZELLE", r"PAYPAL\s+INST\s+XFER",
    ],
    "Credit Card Payment": [
        r"\bCAPITAL\s+ONE\b", r"\bCHIME\b",
        r"\bCREDIT\s+CARD\s+PAYMENT\b",
    ],
    "Loan Proceeds": [
        r"\bLOAN\s+PROCEEDS?\b", r"\bLOAN\s+DISBURSEMENT\b",
    ],
    "Payment Reversal": [
        r"RETURNED\s+ITEM.*INSUFFICIENT\s+FUNDS",
    ],
    "Loan Principal Payment": [
        r"APEX\s+CAPITAL\s+FUNDING",
    ],
    "Loan Interest": [],
    "Owner Contribution": [
        r"\bOWNER\s+CONTRIBUTION\b",
    ],
    "Owner Draw or Distribution": [
        r"\bOWNER\s+DRAW\b", r"\bDISTRIBUTION\b",
    ],
    "Fixed Asset Purchase": [
        r"\bTRUCK\s+PURCHASE\b", r"\bEQUIPMENT\s+PURCHASE\b",
    ],
    "Tax Payment": [
        r"\bTAX\s+PAYMENT\b", r"IOWA\s+JUDICIAL\b",
    ],
    "Refund": [
        r"\bREFUND\b",
    ],
    "Reimbursement": [
        r"\bREIMBURSEMENT\b",
    ],
    "Opening Balance": [],
    "Uncategorized": [],
    "CPA Review Required": [],
}


# =============================================================================
# Transaction Categorizer
# =============================================================================


def build_default_rules() -> list[CategoryRule]:
    """Build the complete ordered list of default category rules."""
    rules: list[CategoryRule] = []
    priority_counter = 0

    # Non-P&L categories (higher priority to catch transfers before other matches)
    for cat, patterns in _NON_PNL_CATEGORIES.items():
        for pat in patterns:
            priority_counter += 1
            rule = CategoryRule(
                pattern=pat,
                category=cat,
                tax_category="non-pl",
                deductibility="not-applicable",
                priority=priority_counter,
            )
            if cat == "Account Transfer":
                rule.is_transfer = True
                rule.include_in_pnl = False
            elif cat == "Credit Card Payment":
                rule.is_transfer = True
                rule.include_in_pnl = False
            elif cat == "Loan Proceeds" or cat == "Loan Principal Payment":
                rule.is_loan = True
                rule.include_in_pnl = False
            elif cat == "Payment Reversal":
                rule.include_in_pnl = False
                rule.direction = "credit"
            elif cat == "Loan Interest":
                rule.is_loan = True
                rule.include_in_pnl = True
            elif cat == "Owner Contribution" or cat == "Owner Draw or Distribution":
                rule.is_owner_related = True
                rule.include_in_pnl = False
            elif cat == "Fixed Asset Purchase":
                rule.is_fixed_asset = True
                rule.include_in_pnl = False
            elif cat == "Tax Payment":
                rule.include_in_pnl = False
            elif cat in ("Refund", "Reimbursement", "Opening Balance", "Uncategorized", "CPA Review Required"):
                rule.include_in_pnl = False
            rules.append(rule)

    # Expense categories — evaluate BEFORE income so bank fees don't match
    # against income patterns embedded in the same description line.
    for cat, patterns in _EXPENSE_CATEGORIES.items():
        for pat in patterns:
            priority_counter += 1
            rules.append(CategoryRule(
                pattern=pat,
                category=cat,
                tax_category=_get_default_tax_category(cat),
                deductibility="likely-deductible" if cat != "Meals" else "possibly-deductible",
                include_in_pnl=True,
                direction="debit",
                priority=priority_counter,
            ))

    # Income categories
    for cat, patterns in _INCOME_CATEGORIES.items():
        for pat in patterns:
            priority_counter += 1
            rules.append(CategoryRule(
                pattern=pat,
                category=cat,
                tax_category="Gross Receipts",
                deductibility="not-applicable",
                is_income=True,
                include_in_pnl=True,
                direction="credit",
                priority=priority_counter,
            ))

    return rules


def _get_default_tax_category(business_cat: str) -> str:
    """Map business category to a default tax category."""
    mapping = {
        "Fuel": "Vehicle Fuel",
        "Freight and Shipping": "Freight & Shipping",
        "Subcontractors": "Contract Labor",
        "Direct Labor": "Direct Labor",
        "Materials and Supplies": "Materials & Supplies",
        "Equipment Rental": "Equipment Rental",
        "Tolls and Scale Fees": "Tolls & Fees",
        "Other Direct Costs": "Other Direct Costs",
        "Advertising and Marketing": "Advertising",
        "Bank and Merchant Fees": "Bank Charges",
        "Business Insurance": "Insurance",
        "Commissions": "Commissions",
        "Contract Labor": "Contract Labor",
        "Depreciation Placeholder": "Depreciation",
        "Dues and Subscriptions": "Dues & Subscriptions",
        "Employee Benefits": "Employee Benefits",
        "Equipment Maintenance": "Repairs & Maintenance",
        "Legal and Professional Fees": "Legal & Professional",
        "Licenses and Permits": "Licenses & Permits",
        "Meals": "Meals (50%)",
        "Office Expense": "Office Expenses",
        "Payroll": "Wages & Salaries",
        "Payroll Taxes": "Payroll Taxes",
        "Rent or Lease": "Rent Expense",
        "Repairs and Maintenance": "Repairs & Maintenance",
        "Software and Cloud Services": "Software & Cloud",
        "Taxes and Fees": "Taxes & Licenses",
        "Telephone and Internet": "Telephone & Internet",
        "Travel": "Travel",
        "Utilities": "Utilities",
        "Vehicle Expense": "Vehicle Expenses",
    }
    return mapping.get(business_cat, "Other Expense")


class TransactionCategorizer:
    """Categorizes transactions using ordered category rules."""

    def __init__(self, custom_rules: list[CategoryRule] | None = None):
        self.default_rules = build_default_rules()
        self.custom_rules = custom_rules or []
        self._build_combined_rules()

    def _build_combined_rules(self):
        """Merge custom and default rules, custom taking priority."""
        all_rules = list(self.custom_rules) + self.default_rules
        all_rules.sort(key=lambda r: r.priority)
        self._rules = all_rules

    def categorize(self, transaction: Transaction) -> Transaction:
        """Apply rules to categorize a single transaction. First match wins.

        A rule with direction="credit" is skipped for debit transactions;
        a rule with direction="debit" is skipped for credit transactions.
        Mismatched-direction rules never clear the CPA-review flag.
        """
        desc_upper = transaction.description.upper()
        for rule in self._rules:
            try:
                # ---- Direction check ----
                rule_dir = rule.direction
                tx_is_credit = transaction.is_credit
                if rule_dir == "credit" and not tx_is_credit:
                    continue
                if rule_dir == "debit" and tx_is_credit:
                    continue

                if re.search(rule.pattern, desc_upper, re.IGNORECASE):
                    transaction.business_category = rule.category
                    transaction.tax_category = rule.tax_category
                    transaction.deductibility = rule.deductibility
                    transaction.include_in_pnl = rule.include_in_pnl
                    transaction.is_transfer = rule.is_transfer
                    transaction.is_owner_related = rule.is_owner_related
                    transaction.is_fixed_asset = rule.is_fixed_asset
                    transaction.is_loan = rule.is_loan
                    # Keep CPA-review flag for categories that should
                    # always be reviewed by a professional.
                    if rule.category not in (
                        "Uncategorized", "CPA Review Required",
                    ) and rule.tax_category != "non-pl":
                        transaction.cpa_review = False
                    else:
                        transaction.cpa_review = True
                        transaction.review_reason = (
                            f"Matched '{rule.category}' rule — requires CPA verification"
                        )
                    return transaction
            except re.error as exc:
                logger.warning(
                    "Invalid regex pattern skipped: %s — %s",
                    rule.pattern, exc,
                )
                continue

        # No rule matched
        transaction.business_category = "Uncategorized"
        transaction.tax_category = "CPA Review"
        transaction.deductibility = "unknown"
        transaction.include_in_pnl = False
        transaction.cpa_review = True
        transaction.review_reason = "No category rule matched"
        return transaction

    def categorize_all(self, statements: list[Statement]) -> None:
        """Categorize all transactions across all statements."""
        for stmt in statements:
            for tx in stmt.transactions:
                self.categorize(tx)

    def mark_credit_deposits(self, statements: list[Statement]) -> None:
        """Mark uncategorized credits (deposits) for CPA review."""
        for stmt in statements:
            for tx in stmt.transactions:
                if tx.is_credit and tx.business_category == "Uncategorized":
                    tx.cpa_review = True
                    tx.review_reason = "Uncategorized deposit - verify revenue vs transfer vs loan"
                    tx.business_category = "CPA Review Required"


# =============================================================================
# Reconciliation
# =============================================================================

RC_TOLERANCE = Decimal("0.01")


def reconcile_statement(stmt: Statement, tolerance: Decimal = RC_TOLERANCE) -> ReconciliationResult:
    """Reconcile a single statement's parsed data against reported figures."""
    parsed_credits = sum(
        (tx.amount for tx in stmt.transactions if tx.is_credit),
        Decimal("0"),
    )
    parsed_debits = sum(
        (tx.amount for tx in stmt.transactions if not tx.is_credit),
        Decimal("0"),
    )
    parsed_credit_count = sum(1 for tx in stmt.transactions if tx.is_credit)
    parsed_debit_count = sum(1 for tx in stmt.transactions if not tx.is_credit)

    calculated_ending = stmt.beginning_balance + parsed_credits - parsed_debits
    balance_ok = abs(calculated_ending - stmt.ending_balance) <= tolerance

    credits_ok = (
        abs(parsed_credits - stmt.total_credits) <= tolerance
        and parsed_credit_count == stmt.credit_count
    )
    debits_ok = (
        abs(parsed_debits - stmt.total_debits) <= tolerance
        and parsed_debit_count == stmt.debit_count
    )

    warnings: list[str] = []
    if not credits_ok:
        warnings.append(
            f"Credit mismatch: parsed {parsed_credit_count}/{parsed_credits} vs "
            f"expected {stmt.credit_count}/{stmt.total_credits}"
        )
    if not debits_ok:
        warnings.append(
            f"Debit mismatch: parsed {parsed_debit_count}/{parsed_debits} vs "
            f"expected {stmt.debit_count}/{stmt.total_debits}"
        )
    if not balance_ok:
        warnings.append(
            f"Balance mismatch: calculated={calculated_ending} vs ending={stmt.ending_balance}"
        )

    return ReconciliationResult(
        statement_label=stmt.month_label,
        passed=credits_ok and debits_ok and balance_ok,
        parsed_credit_count=parsed_credit_count,
        expected_credit_count=stmt.credit_count,
        parsed_debit_count=parsed_debit_count,
        expected_debit_count=stmt.debit_count,
        parsed_credit_total=parsed_credits,
        expected_credit_total=stmt.total_credits,
        parsed_debit_total=parsed_debits,
        expected_debit_total=stmt.total_debits,
        beginning_balance=stmt.beginning_balance,
        ending_balance=stmt.ending_balance,
        calculated_ending=calculated_ending,
        balance_ok=balance_ok,
        warnings=warnings,
    )


def reconcile_all(
    statements: list[Statement],
    tolerance: Decimal = RC_TOLERANCE,
    allow_mismatch: bool = False,
) -> tuple[list[ReconciliationResult], bool, bool]:
    """Reconcile all statements and check continuity.

    Returns:
        (results, all_reconciled, forced_generation)
        *all_reconciled* is true only when every statement genuinely passes.
        *forced_generation* is true when output is permitted despite failures.
    """
    results = []
    all_reconciled = True
    for stmt in statements:
        result = reconcile_statement(stmt, tolerance)
        results.append(result)
        if not result.passed:
            all_reconciled = False
            logger.warning(
                "Reconciliation failed for %s: %s",
                stmt.month_label, "; ".join(result.warnings),
            )

    # Check continuity between statements
    for i in range(1, len(statements)):
        prev_ending = statements[i - 1].ending_balance
        curr_beginning = statements[i].beginning_balance
        if abs(prev_ending - curr_beginning) > tolerance:
            results.append(ReconciliationResult(
                statement_label=f"Continuity: {statements[i-1].month_label} -> {statements[i].month_label}",
                passed=False,
                parsed_credit_count=0, expected_credit_count=0,
                parsed_debit_count=0, expected_debit_count=0,
                parsed_credit_total=Decimal("0"), expected_credit_total=Decimal("0"),
                parsed_debit_total=Decimal("0"), expected_debit_total=Decimal("0"),
                beginning_balance=prev_ending,
                ending_balance=curr_beginning,
                calculated_ending=prev_ending,
                balance_ok=False,
                warnings=[f"Balance discontinuity: prev ending={prev_ending}, curr beginning={curr_beginning}, gap={curr_beginning - prev_ending}"],
            ))
            all_reconciled = False

    forced = allow_mismatch and not all_reconciled
    return results, all_reconciled, forced


# =============================================================================
# Financial Calculator / P&L
# =============================================================================


class ProfitAndLoss:
    """Cash-basis Profit & Loss statement built from categorized transactions."""

    def __init__(self, label: str = ""):
        self.label = label
        self.revenue: dict[str, Decimal] = defaultdict(Decimal)
        self.direct_costs: dict[str, Decimal] = defaultdict(Decimal)
        self.operating_expenses: dict[str, Decimal] = defaultdict(Decimal)
        self.other_income: dict[str, Decimal] = defaultdict(Decimal)
        self.other_expense: dict[str, Decimal] = defaultdict(Decimal)
        # ---- Separate non-P&L buckets (each tracked individually) ----
        self.owner_contributions: Decimal = Decimal("0")
        self.owner_distributions: Decimal = Decimal("0")
        self.loan_proceeds: Decimal = Decimal("0")
        self.loan_principal_payments: Decimal = Decimal("0")
        self.payment_reversals: Decimal = Decimal("0")
        self.fixed_asset_purchases: Decimal = Decimal("0")
        self.account_transfers_credits: Decimal = Decimal("0")
        self.account_transfers_debits: Decimal = Decimal("0")
        self.credit_card_transfers: Decimal = Decimal("0")
        self.uncategorized_non_pnl_credits: Decimal = Decimal("0")
        self.uncategorized_non_pnl_debits: Decimal = Decimal("0")

    @property
    def non_pnl_credits(self) -> Decimal:
        return (self.owner_contributions + self.loan_proceeds
                + self.payment_reversals
                + self.account_transfers_credits + self.uncategorized_non_pnl_credits)

    @property
    def non_pnl_debits(self) -> Decimal:
        return (self.owner_distributions + self.loan_principal_payments
                + self.fixed_asset_purchases + self.account_transfers_debits
                + self.credit_card_transfers + self.uncategorized_non_pnl_debits)

    @property
    def total_revenue(self) -> Decimal:
        return sum(self.revenue.values(), Decimal("0"))

    @property
    def total_direct_costs(self) -> Decimal:
        return sum(self.direct_costs.values(), Decimal("0"))

    @property
    def gross_profit(self) -> Decimal:
        return self.total_revenue - self.total_direct_costs

    @property
    def gross_margin(self) -> str:
        return safe_pct(self.gross_profit, self.total_revenue)

    @property
    def total_operating_expenses(self) -> Decimal:
        return sum(self.operating_expenses.values(), Decimal("0"))

    @property
    def operating_profit(self) -> Decimal:
        return self.gross_profit - self.total_operating_expenses

    @property
    def operating_margin(self) -> str:
        return safe_pct(self.operating_profit, self.total_revenue)

    @property
    def total_other_income(self) -> Decimal:
        return sum(self.other_income.values(), Decimal("0"))

    @property
    def total_other_expense(self) -> Decimal:
        return sum(self.other_expense.values(), Decimal("0"))

    @property
    def net_profit(self) -> Decimal:
        return self.operating_profit + self.total_other_income - self.total_other_expense

    @property
    def net_margin(self) -> str:
        return safe_pct(self.net_profit, self.total_revenue)


_INCOME_CAT_SET = set(_INCOME_CATEGORIES.keys())
_DIRECT_COST_CATS = {
    "Fuel", "Freight and Shipping", "Subcontractors", "Direct Labor",
    "Materials and Supplies", "Equipment Rental", "Tolls and Scale Fees",
    "Other Direct Costs",
}
_OP_EXPENSE_CATS = set(_EXPENSE_CATEGORIES.keys()) - _DIRECT_COST_CATS
_OTHER_INCOME_CATS = {"Interest Income", "Other Income"}
_OTHER_EXPENSE_CATS = {"Loan Interest"}


def build_pl(
    transactions: list[Transaction],
    label: str = "",
) -> ProfitAndLoss:
    """Build a P&L from a list of categorized transactions.

    Non-P&L transactions are routed to separate accounting buckets
    (owner, loan, transfers, fixed assets) rather than combined into
    generic credit/debit totals.
    """
    pl = ProfitAndLoss(label=label)
    for tx in transactions:
        cat = tx.business_category

        # ---- Transfers ----
        if tx.is_transfer:
            if tx.is_credit:
                pl.account_transfers_credits += tx.amount
            else:
                if cat == "Credit Card Payment":
                    pl.credit_card_transfers += tx.amount
                else:
                    pl.account_transfers_debits += tx.amount
            continue

        # ---- Owner-related ----
        if tx.is_owner_related:
            if tx.is_credit:
                pl.owner_contributions += tx.amount
            else:
                pl.owner_distributions += tx.amount
            continue

        # ---- Loan proceeds / principal / reversals (excluded from P&L) ----
        if tx.is_loan and not tx.include_in_pnl:
            if tx.is_credit:
                pl.loan_proceeds += tx.amount
            else:
                pl.loan_principal_payments += tx.amount
            continue

        # ---- Payment reversals (NSF returns — not necessarily loan-related) ----
        if cat == "Payment Reversal":
            pl.payment_reversals += tx.amount
            continue

        # ---- Fixed assets ----
        if tx.is_fixed_asset:
            pl.fixed_asset_purchases += tx.amount
            continue

        # ---- Other explicitly excluded ----
        if not tx.include_in_pnl:
            if tx.is_credit:
                pl.uncategorized_non_pnl_credits += tx.amount
            else:
                pl.uncategorized_non_pnl_debits += tx.amount
            continue

        # ---- P&L classification ----
        if cat in _INCOME_CAT_SET:
            pl.revenue[cat] += tx.amount if tx.is_credit else -tx.amount
        elif cat in _DIRECT_COST_CATS:
            # Credits (refunds, rebates, returns) reduce the expense
            signed = tx.amount if not tx.is_credit else -tx.amount
            pl.direct_costs[cat] += signed
        elif cat in _OP_EXPENSE_CATS:
            signed = tx.amount if not tx.is_credit else -tx.amount
            pl.operating_expenses[cat] += signed
        elif cat in _OTHER_INCOME_CATS:
            pl.other_income[cat] += tx.amount if tx.is_credit else -tx.amount
        elif cat in _OTHER_EXPENSE_CATS:
            signed = tx.amount if not tx.is_credit else -tx.amount
            pl.other_expense[cat] += signed
        else:
            # Put uncategorized expense-like debits in operating expenses
            if not tx.is_credit:
                pl.operating_expenses[cat] += tx.amount
            else:
                pl.revenue[cat] += tx.amount

    return pl


def build_pl_by_period(
    statements: list[Statement],
    period_filter=None,
) -> ProfitAndLoss:
    """Build a P&L from statements, optionally filtered by a date filter function."""
    tx_list: list[Transaction] = []
    for stmt in statements:
        for tx in stmt.transactions:
            if period_filter is None or period_filter(tx):
                tx_list.append(tx)
    return build_pl(tx_list)


def build_monthly_pls(statements: list[Statement]) -> dict[str, ProfitAndLoss]:
    """Build monthly P&L statements keyed by 'YYYY-MM'."""
    monthly: defaultdict[str, list[Transaction]] = defaultdict(list)
    for stmt in statements:
        for tx in stmt.transactions:
            key = datetime.strptime(tx.post_date, "%m/%d/%Y").strftime("%Y-%m")
            monthly[key].append(tx)
    result = {}
    for key, txs in sorted(monthly.items()):
        yr, mo = key.split("-")
        label = datetime(int(yr), int(mo), 1).strftime("%B %Y")
        result[key] = build_pl(txs, label=label)
    return result


def build_quarterly_pls(
    statements: list[Statement],
    fiscal_year_start: int = 1,
) -> dict[tuple[int, int], ProfitAndLoss]:
    """Build quarterly P&L statements keyed by (fiscal_year, quarter).

    *fiscal_year_start* is the calendar month (1-12) that begins the fiscal year.
    """
    quarterly: dict[tuple[int, int], list[Transaction]] = defaultdict(list)
    for stmt in statements:
        for tx in stmt.transactions:
            parts = tx.post_date.split("/")
            cal_month = int(parts[0])
            cal_year = int(parts[2])
            # Determine fiscal year and fiscal quarter
            if cal_month >= fiscal_year_start:
                fy = cal_year
                fy_month = cal_month - fiscal_year_start + 1
            else:
                fy = cal_year - 1
                fy_month = cal_month + (12 - fiscal_year_start + 1)
            fq = (fy_month - 1) // 3 + 1
            quarterly[(fy, fq)].append(tx)
    result: dict[tuple[int, int], ProfitAndLoss] = {}
    for (fy, fq), txs in sorted(quarterly.items()):
        result[(fy, fq)] = build_pl(txs, label=f"FY{fy} Q{fq}")
    return result


# =============================================================================
# Financial KPIs
# =============================================================================


@dataclass
class FinancialKPIs:
    """Key performance indicators from bank-statement data."""

    gross_margin: str = "N/A"
    operating_margin: str = "N/A"
    net_margin: str = "N/A"
    expense_to_revenue: str = "N/A"
    avg_monthly_revenue: Decimal = Decimal("0")
    avg_monthly_expenses: Decimal = Decimal("0")
    avg_monthly_net: Decimal = Decimal("0")
    avg_transaction_value: Decimal = Decimal("0")
    largest_income: Decimal = Decimal("0")
    largest_expense: Decimal = Decimal("0")
    min_monthly_balance: Decimal = Decimal("0")
    max_monthly_balance: Decimal = Decimal("0")
    total_revenue: Decimal = Decimal("0")
    total_expenses: Decimal = Decimal("0")
    net_income: Decimal = Decimal("0")
    cash_runway_months: str = "N/A"


def calculate_kpis(pl: ProfitAndLoss, month_count: int, statements: list[Statement]) -> FinancialKPIs:
    """Calculate KPIs from P&L and statement data."""
    kpi = FinancialKPIs()
    kpi.gross_margin = pl.gross_margin
    kpi.operating_margin = pl.operating_margin
    kpi.net_margin = pl.net_margin
    kpi.total_revenue = pl.total_revenue
    kpi.total_expenses = pl.total_direct_costs + pl.total_operating_expenses
    kpi.net_income = pl.net_profit
    kpi.expense_to_revenue = safe_pct(kpi.total_expenses, pl.total_revenue)

    if month_count > 0:
        kpi.avg_monthly_revenue = pl.total_revenue / month_count
        kpi.avg_monthly_expenses = kpi.total_expenses / month_count
        kpi.avg_monthly_net = pl.net_profit / month_count

    all_tx = [tx for stmt in statements for tx in stmt.transactions]
    if all_tx:
        amounts = [tx.amount for tx in all_tx]
        kpi.avg_transaction_value = sum(amounts, Decimal("0")) / len(amounts)
        kpi.largest_income = max(
            (tx.amount for tx in all_tx if tx.is_credit),
            default=Decimal("0"),
        )
        kpi.largest_expense = max(
            (tx.amount for tx in all_tx if not tx.is_credit),
            default=Decimal("0"),
        )

    if statements:
        all_bals: list[Decimal] = []
        for s in statements:
            all_bals.append(s.ending_balance)
            for db in s.daily_balances:
                all_bals.append(db["balance"])
        if all_bals:
            kpi.min_monthly_balance = min(all_bals)
            kpi.max_monthly_balance = max(all_bals)

    if kpi.avg_monthly_expenses > 0 and statements:
        last_bal = statements[-1].ending_balance
        if last_bal > 0:
            runway = float(last_bal / kpi.avg_monthly_expenses)
            kpi.cash_runway_months = f"{runway:.1f}"

    return kpi


# =============================================================================
# Projection Engine
# =============================================================================


@dataclass
class ProjectionResult:
    """Result of a financial projection."""

    scenario: str  # conservative, base, growth
    months: int
    monthly_revenue: list[Decimal]
    monthly_expenses: list[Decimal]
    monthly_gross_profit: list[Decimal]
    monthly_net_income: list[Decimal]
    monthly_cash_flow: list[Decimal]
    ending_cash: list[Decimal]
    tax_reserve: list[Decimal]
    assumptions: dict[str, Any]


class ProjectionEngine:
    """Generate financial projections based on historical data."""

    def __init__(self, config: dict[str, Any]):
        self.config = config

    def _avg_monthly(self, values: list[Decimal], lookback: int = 6) -> Decimal:
        if not values:
            return Decimal("0")
        recent = values[-lookback:]
        return sum(recent, Decimal("0")) / len(recent)

    def project(
        self,
        historical_revenue: list[Decimal],
        historical_expenses: list[Decimal],
        starting_cash: Decimal,
        scenario_name: str = "base",
        projection_start: date | None = None,
    ) -> ProjectionResult:
        """Run a projection scenario.

        *projection_start* is the first day of the first projected month
        (defaults to the month after the current real month).
        """
        c = self.config
        months = c.get("projection_months", 12)
        lookback = c.get("lookback_months", 6)

        # Growth rates: scenario-specific overrides base config
        scenarios = c.get("scenarios", {})
        sc = scenarios.get(scenario_name, {})
        rev_growth = Decimal(str(sc.get("monthly_revenue_growth", c.get("monthly_revenue_growth", 0.02))))
        exp_inflation = Decimal(str(c.get("monthly_expense_inflation", 0.02)))
        payroll_growth = Decimal(str(c.get("payroll_growth", 0.02)))
        cogs_pct = Decimal(str(c.get("cogs_percentage", 0.0)))
        tax_reserve_pct = Decimal(str(c.get("tax_reserve_pct", 0.25)))
        min_cash = Decimal(str(c.get("min_cash_balance", 0)))
        seasonal = c.get("seasonal", {})

        # Baseline from averages
        base_rev = self._avg_monthly(historical_revenue, lookback)
        base_exp = self._avg_monthly(historical_expenses, lookback)

        # Determine the start month for seasonal lookups
        if projection_start is None:
            projection_start = date(date.today().year, date.today().month, 1) + timedelta(days=32)
            projection_start = date(projection_start.year, projection_start.month, 1)
        start_month_num = projection_start.month
        start_year = projection_start.year

        monthly_rev: list[Decimal] = []
        monthly_exp: list[Decimal] = []
        monthly_gp: list[Decimal] = []
        monthly_ni: list[Decimal] = []
        monthly_cf: list[Decimal] = []
        ending_cash: list[Decimal] = []
        tax_reserve: list[Decimal] = []
        min_cash_warnings: list[int] = []

        cum_tax_reserve = Decimal("0")
        current_cash = starting_cash

        for i in range(1, months + 1):
            seasonal_month = ((start_month_num + i - 2) % 12) + 1
            season_factor = Decimal(str(seasonal.get(str(seasonal_month), 1.0)))

            rev = base_rev * (1 + rev_growth) ** i * season_factor
            monthly_rev.append(rev.quantize(Decimal("0.01"), ROUND_HALF_UP))

            gross_profit = rev * (1 - cogs_pct)
            monthly_gp.append(gross_profit.quantize(Decimal("0.01"), ROUND_HALF_UP))

            exp = base_exp * (1 + exp_inflation) ** i * season_factor
            monthly_exp.append(exp.quantize(Decimal("0.01"), ROUND_HALF_UP))

            net = gross_profit - exp
            monthly_ni.append(net.quantize(Decimal("0.01"), ROUND_HALF_UP))

            cf = net
            monthly_cf.append(cf.quantize(Decimal("0.01"), ROUND_HALF_UP))
            current_cash += cf
            ending_cash.append(current_cash.quantize(Decimal("0.01"), ROUND_HALF_UP))
            if min_cash > 0 and current_cash < min_cash:
                min_cash_warnings.append(i)

            if net > 0:
                cum_tax_reserve += net * tax_reserve_pct
            tax_reserve.append(cum_tax_reserve.quantize(Decimal("0.01"), ROUND_HALF_UP))

        return ProjectionResult(
            scenario=scenario_name,
            months=months,
            monthly_revenue=monthly_rev,
            monthly_expenses=monthly_exp,
            monthly_gross_profit=monthly_gp,
            monthly_net_income=monthly_ni,
            monthly_cash_flow=monthly_cf,
            ending_cash=ending_cash,
            tax_reserve=tax_reserve,
            assumptions={
                "revenue_growth_rate": f"{float(rev_growth) * 100:.1f}%",
                "expense_inflation": f"{float(exp_inflation) * 100:.1f}%",
                "cogs_percentage": f"{float(cogs_pct) * 100:.1f}%",
                "tax_reserve_pct": f"{float(tax_reserve_pct) * 100:.1f}%",
                "lookback_months": lookback,
                "scenario": scenario_name,
                "min_cash_balance": f"${min_cash:,.2f}",
                "min_cash_warnings": min_cash_warnings,
            },
        )

    def project_all_scenarios(
        self,
        historical_revenue: list[Decimal],
        historical_expenses: list[Decimal],
        starting_cash: Decimal,
        projection_start: date | None = None,
    ) -> list[ProjectionResult]:
        """Run all three scenarios."""
        return [
            self.project(historical_revenue, historical_expenses, starting_cash, "conservative", projection_start),
            self.project(historical_revenue, historical_expenses, starting_cash, "base", projection_start),
            self.project(historical_revenue, historical_expenses, starting_cash, "growth", projection_start),
        ]

    def project_selected(
        self,
        historical_revenue: list[Decimal],
        historical_expenses: list[Decimal],
        starting_cash: Decimal,
        scenario_name: str = "base",
        projection_start: date | None = None,
    ) -> ProjectionResult:
        """Run a single named scenario (conservative, base, or growth)."""
        return self.project(historical_revenue, historical_expenses, starting_cash, scenario_name, projection_start)


# =============================================================================
# Chart Generation (matplotlib -> PNG bytes)
# =============================================================================


def _empty_chart_buf(msg: str = "No data available") -> io.BytesIO:
    """Create a chart with a text notice."""
    fig, ax = plt.subplots(figsize=(8, 3))
    ax.text(0.5, 0.5, msg, ha="center", va="center", fontsize=12, color="#888")
    ax.axis("off")
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def chart_revenue_vs_expenses_monthly(statements: list[Statement], pls: dict[str, ProfitAndLoss]) -> io.BytesIO:
    """Chart revenue vs expenses by month."""
    if not pls:
        return _empty_chart_buf("No P&L data for chart")
    keys = sorted(pls.keys())
    labels = [pls[k].label for k in keys]
    revs = [float(pls[k].total_revenue) for k in keys]
    exps = [float(pls[k].total_direct_costs + pls[k].total_operating_expenses) for k in keys]

    fig, ax = plt.subplots(figsize=(12, 7))
    x = range(len(labels))
    w = 0.35
    ax.bar([i - w / 2 for i in x], revs, w, label="Revenue", color="#27ae60")
    ax.bar([i + w / 2 for i in x], exps, w, label="Expenses", color="#e74c3c")
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, fontsize=8, rotation=45, ha="right")
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"${v:,.0f}"))
    ax.set_ylabel("Amount ($)")
    ax.set_title("Revenue vs Expenses by Month", fontsize=11, fontweight="bold")
    ax.legend()
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def chart_profit_monthly(pls: dict[str, ProfitAndLoss]) -> io.BytesIO:
    """Chart gross and net profit by month."""
    if not pls:
        return _empty_chart_buf("No P&L data for chart")
    keys = sorted(pls.keys())
    labels = [pls[k].label for k in keys]
    gp = [float(pls[k].gross_profit) for k in keys]
    np_vals = [float(pls[k].net_profit) for k in keys]

    fig, ax = plt.subplots(figsize=(12, 7))
    x = range(len(labels))
    ax.plot(x, gp, "o-", label="Gross Profit", color="#27ae60", linewidth=2)
    ax.plot(x, np_vals, "s-", label="Net Profit", color="#2980b9", linewidth=2)
    ax.axhline(y=0, color="#e74c3c", linewidth=0.5, linestyle="--", alpha=0.5)
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, fontsize=8, rotation=45, ha="right")
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"${v:,.0f}"))
    ax.set_title("Gross & Net Profit by Month", fontsize=11, fontweight="bold")
    ax.legend()
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def chart_net_cash_flow(statements: list[Statement]) -> io.BytesIO:
    """Chart monthly net cash flow."""
    if not statements:
        return _empty_chart_buf("No statement data for chart")
    months = [s.month_label for s in statements]
    flows = [
        float(s.total_credits - s.total_debits) for s in statements
    ]

    fig, ax = plt.subplots(figsize=(12, 7))
    x = range(len(months))
    colors = ["#27ae60" if f >= 0 else "#e74c3c" for f in flows]
    ax.bar(x, flows, color=colors)
    ax.axhline(y=0, color="black", linewidth=0.5)
    ax.set_xticks(list(x))
    ax.set_xticklabels(months, fontsize=8, rotation=45, ha="right")
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"${v:,.0f}"))
    ax.set_title("Monthly Net Cash Flow", fontsize=11, fontweight="bold")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def chart_balance_trend(statements: list[Statement]) -> io.BytesIO:
    """Chart bank balance trend."""
    if not statements:
        return _empty_chart_buf("No statement data for chart")
    all_bals: list[tuple[date, float]] = []
    for s in statements:
        for db in s.daily_balances:
            dt = datetime.strptime(db["date"], "%m/%d/%Y").date()
            all_bals.append((dt, float(db["balance"])))
    if not all_bals:
        return _empty_chart_buf("No daily balance data")

    all_bals.sort(key=lambda x: x[0])
    dates = [b[0] for b in all_bals]
    vals = [b[1] for b in all_bals]

    fig, ax = plt.subplots(figsize=(12, 7))
    ax.plot(dates, vals, color="#2c3e50", linewidth=1.4, alpha=0.85)
    ax.fill_between(dates, 0, vals, alpha=0.08, color="#2c3e50")
    ax.axhline(y=0, color="#e74c3c", linewidth=0.5, linestyle="--", alpha=0.5)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"${v:,.0f}"))
    ax.set_title("Bank Balance Trend", fontsize=11, fontweight="bold")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.autofmt_xdate(rotation=30, ha="right")
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def chart_expenses_by_category(pl: ProfitAndLoss) -> io.BytesIO:
    """Pie chart of expenses by category."""
    exp_by_cat: dict[str, float] = {}
    for cat, val in pl.direct_costs.items():
        if val > 0:
            exp_by_cat[f"[COGS] {cat}"] = float(val)
    for cat, val in pl.operating_expenses.items():
        if val > 0:
            exp_by_cat[cat] = float(val)
    for cat, val in pl.other_expense.items():
        if val > 0:
            exp_by_cat[cat] = float(val)

    if not exp_by_cat:
        return _empty_chart_buf("No expense data")

    sorted_items = sorted(exp_by_cat.items(), key=lambda x: x[1], reverse=True)
    labels = [k for k, _ in sorted_items[:10]]
    sizes = [v for _, v in sorted_items[:10]]
    if len(sorted_items) > 10:
        other = sum(v for _, v in sorted_items[10:])
        if other > 0:
            labels.append("Other")
            sizes.append(other)

    colors = plt.cm.tab20([i / len(labels) for i in range(len(labels))])
    fig, ax = plt.subplots(figsize=(7, 5))
    wedges, texts, autotexts = ax.pie(
        sizes, labels=None, autopct="%1.1f%%",
        startangle=140, colors=colors,
    )
    for t in autotexts:
        t.set_fontsize(7)
    legend_labels = [f"{l} (${s:,.0f})" for l, s in zip(labels, sizes)]
    ax.legend(wedges, legend_labels, title="Expenses", loc="center left",
              bbox_to_anchor=(1, 0.5), fontsize=7)
    ax.set_title("Expenses by Category", fontsize=11, fontweight="bold")
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def chart_revenue_by_category(pl: ProfitAndLoss) -> io.BytesIO:
    """Pie chart of revenue by category."""
    rev_data = {k: float(v) for k, v in pl.revenue.items() if v > 0}
    for k, v in pl.other_income.items():
        rev_data[k] = float(v)

    if not rev_data:
        return _empty_chart_buf("No revenue data")

    sorted_items = sorted(rev_data.items(), key=lambda x: x[1], reverse=True)
    labels = [k for k, _ in sorted_items[:8]]
    sizes = [v for _, v in sorted_items[:8]]

    colors = plt.cm.Set2([i / max(len(labels), 1) for i in range(len(labels))])
    fig, ax = plt.subplots(figsize=(6, 5))
    wedges, _, autotexts = ax.pie(
        sizes, labels=None, autopct="%1.1f%%",
        startangle=140, colors=colors,
    )
    for t in autotexts:
        t.set_fontsize(7)
    legend_labels = [f"{l} (${s:,.0f})" for l, s in zip(labels, sizes)]
    ax.legend(wedges, legend_labels, title="Revenue", loc="center left",
              bbox_to_anchor=(1, 0.5), fontsize=7)
    ax.set_title("Revenue by Category", fontsize=11, fontweight="bold")
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def chart_cost_by_month(pls: dict[str, ProfitAndLoss]) -> io.BytesIO:
    """Chart direct costs and operating expenses by month."""
    if not pls:
        return _empty_chart_buf("No P&L data")
    keys = sorted(pls.keys())
    labels = [pls[k].label for k in keys]
    dc = [float(pls[k].total_direct_costs) for k in keys]
    oe = [float(pls[k].total_operating_expenses) for k in keys]

    fig, ax = plt.subplots(figsize=(12, 7))
    x = range(len(labels))
    w = 0.35
    ax.bar([i - w / 2 for i in x], dc, w, label="Direct Costs (COGS)", color="#e74c3c")
    ax.bar([i + w / 2 for i in x], oe, w, label="Operating Expenses", color="#d35400")
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, fontsize=8, rotation=45, ha="right")
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"${v:,.0f}"))
    ax.set_title("Direct Costs & Operating Expenses by Month", fontsize=11, fontweight="bold")
    ax.legend(fontsize=8)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def chart_projection(
    historical_rev: list[Decimal],
    projections: list[ProjectionResult],
    historical_labels: list[str],
) -> io.BytesIO:
    """Chart actual vs projected revenue."""
    if not projections:
        return _empty_chart_buf("No projection data")

    fig, ax = plt.subplots(figsize=(12, 7))
    x_hist = range(len(historical_rev))
    if historical_rev:
        ax.bar(x_hist, [float(r) for r in historical_rev],
               label="Actual Revenue", color="#27ae60", alpha=0.7)

    offset = len(historical_rev)
    colors = {"conservative": "#e74c3c", "base": "#2980b9", "growth": "#8e44ad"}
    linestyles = {"conservative": "--", "base": "-", "growth": ":"}

    for pr in projections:
        x_proj = range(offset, offset + pr.months)
        clr = colors.get(pr.scenario, "#888888")
        ls = linestyles.get(pr.scenario, "-")
        ax.plot(x_proj, [float(r) for r in pr.monthly_revenue],
                color=clr, linestyle=ls, linewidth=2,
                label=f"{pr.scenario.title()} Projection")

    # Divide line
    if historical_rev and projections:
        ax.axvline(x=offset - 0.5, color="#888", linestyle=":", linewidth=1)
        ax.text(offset - 0.5, ax.get_ylim()[1] * 0.95, "Projection ->",
                ha="right", fontsize=8, color="#888")

    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"${v:,.0f}"))
    ax.set_title("Revenue: Actual vs Projected", fontsize=11, fontweight="bold")
    ax.legend(fontsize=8)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def chart_top_vendors(
    statements: list[Statement], top_n: int = 10,
) -> io.BytesIO:
    """Bar chart of top vendors by expense."""
    vendor_totals: dict[str, float] = defaultdict(float)
    for s in statements:
        for tx in s.transactions:
            if not tx.is_credit and tx.include_in_pnl:
                merchant = tx.merchant or normalize_merchant(tx.description)
                vendor_totals[merchant] += float(tx.amount)

    sorted_v = sorted(vendor_totals.items(), key=lambda x: x[1], reverse=True)[:top_n]
    if not sorted_v:
        return _empty_chart_buf("No vendor data")

    names = [v[0][:25] for v in sorted_v]
    vals = [v[1] for v in sorted_v]

    fig, ax = plt.subplots(figsize=(10, 6))
    ax.barh(range(len(names)), vals, color="#d35400")
    ax.set_yticks(range(len(names)))
    ax.set_yticklabels(names, fontsize=8)
    ax.invert_yaxis()
    ax.set_xlabel("Total ($)")
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"${v:,.0f}"))
    ax.set_title("Top Vendors by Expense", fontsize=11, fontweight="bold")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def chart_top_revenue_sources(
    statements: list[Statement], top_n: int = 10,
) -> io.BytesIO:
    """Bar chart of top revenue sources."""
    source_totals: dict[str, float] = defaultdict(float)
    for s in statements:
        for tx in s.transactions:
            if tx.is_credit and tx.include_in_pnl:
                merchant = tx.merchant or normalize_merchant(tx.description)
                source_totals[merchant] += float(tx.amount)

    sorted_v = sorted(source_totals.items(), key=lambda x: x[1], reverse=True)[:top_n]
    if not sorted_v:
        return _empty_chart_buf("No revenue source data")

    names = [v[0][:25] for v in sorted_v]
    vals = [v[1] for v in sorted_v]

    fig, ax = plt.subplots(figsize=(10, 6))
    ax.barh(range(len(names)), vals, color="#27ae60")
    ax.set_yticks(range(len(names)))
    ax.set_yticklabels(names, fontsize=8)
    ax.invert_yaxis()
    ax.set_xlabel("Total ($)")
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"${v:,.0f}"))
    ax.set_title("Top Revenue Sources", fontsize=11, fontweight="bold")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


# =============================================================================
# Merchant Normalization
# =============================================================================


def normalize_merchant(description: str) -> str:
    """Normalize a transaction description into a merchant/customer name."""
    desc = description.upper()
    desc = re.sub(r"XX\d{4}\s+(POS\s+)?PINNED\s+\d{2}/\d{2}\s+\d{2}:\d{2}\s*", "", desc)
    desc = re.sub(r"XX\d{4}\s+DEBIT\s+CARD\s+\d{2}/\d{2}\s+\d{2}:\d{2}\s*", "", desc)
    desc = re.sub(r"DEBIT\s+CARD\s+\d{2}/\d{2}\s+\d{2}:\d{2}\s*", "", desc)
    desc = re.sub(r"CARD\s+\d{2}/\d{2}\s+\d{2}:\d{2}\s*", "", desc)
    desc = re.sub(r"\b\d{2}/\d{2}\s+\d{2}:\d{2}\b", "", desc)
    desc = re.sub(r"\b[0-9A-F]{12,}\b", "", desc)
    desc = re.sub(r"\b\d{8,}\b", "", desc)
    desc = re.sub(r"\s+[A-Z]{2}\s+\d{5}(?:-\d{4})?", "", desc)
    desc = re.sub(r"\b([A-Za-z]{4,})\d{1,2}\b", r"\1", desc)
    desc = re.sub(r"\s+", " ", desc).strip()
    if not desc:
        desc = description[:80]
    return desc


# =============================================================================
# PDF Report Generation
# =============================================================================


class ReportPDF(FPDF):
    """Extended FPDF for business financial reports."""

    DEJAVU_SANS = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    DEJAVU_SANS_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    DEJAVU_MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
    DEJAVU_MONO_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"

    def __init__(self, title: str, orientation: str = "P"):
        super().__init__(orientation=orientation, unit="mm", format="A4")
        self._report_title = title
        self.generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
        self.set_auto_page_break(auto=True, margin=18)
        self.set_margins(12, 12, 12)
        self._setup_fonts()

    def _setup_fonts(self):
        if os.path.exists(self.DEJAVU_SANS):
            self.add_font("DJV", "", self.DEJAVU_SANS)
            self.add_font("DJV", "B", self.DEJAVU_SANS_BOLD)
            self.add_font("DJV", "I", self.DEJAVU_SANS)
            self.add_font("DJVM", "", self.DEJAVU_MONO)
            self.add_font("DJVM", "B", self.DEJAVU_MONO_BOLD)
        else:
            # Fallback: use built-in Helvetica
            pass

    def header(self):
        if self.page_no() <= 1:
            return
        self.set_font("DJV", "I", 7)
        self.set_text_color(120, 120, 120)
        title_short = self._report_title[:60]
        self.cell(0, 4, title_short, align="L")
        self.cell(0, 4, f"Page {self.page_no()}", align="R", new_x="LMARGIN", new_y="NEXT")
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(3)

    def footer(self):
        self.set_y(-15)
        self.set_font("DJV", "I", 6)
        self.set_text_color(150, 150, 150)
        self.cell(
            0, 8,
            f"Generated {self.generated_at}  |  business_financial_report.py  |  {_SCRIPT_HASH}",
            align="C",
        )

    def section_title(self, text: str):
        self.set_font("DJV", "B", 14)
        self.set_text_color(44, 62, 80)
        self.cell(0, 8, text, new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(44, 62, 80)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(4)

    def sub_title(self, text: str):
        self.set_font("DJV", "B", 11)
        self.set_text_color(52, 73, 94)
        self.cell(0, 6, text, new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def body_text(self, text: str, size: int = 9):
        self.set_font("DJV", "", size)
        self.set_text_color(50, 50, 50)
        self.multi_cell(0, 4.5, text)
        self.ln(1)

    def body_text_small(self, text: str, size: int = 7):
        self.set_font("DJV", "", size)
        self.set_text_color(80, 80, 80)
        self.multi_cell(0, 3.5, text)
        self.ln(1)

    def truncate_text(self, text: str, width_mm: float, font_size: int = 7) -> str:
        self.set_font("DJV", "", font_size)
        if self.get_string_width(text) <= width_mm:
            return text
        ellipsis = "..."
        while len(text) > 3 and self.get_string_width(text + ellipsis) > width_mm:
            text = text[:-1]
        return text + ellipsis

    def draw_table(
        self,
        headers: list[str],
        rows: list[list[str]],
        col_widths: list[float] | None = None,
        col_aligns: list[str] | None = None,
        header_color: tuple = (44, 62, 80),
        section_label: str = "",
        header_font_size: int = 7,
        row_font_size: int = 7,
        row_height: float = 4.5,
    ):
        if col_widths is None:
            usable = self.w - self.l_margin - self.r_margin
            col_widths = [usable / len(headers)] * len(headers)
        if col_aligns is None:
            col_aligns = ["L"] * len(headers)

        header_h = 6
        min_body_rows = 2
        header_space = header_h + min_body_rows * row_height + 4

        total_space = header_h + len(rows) * row_height + 4
        if self.get_y() + total_space <= self.h - self.b_margin:
            self._draw_table_section(
                headers, rows, col_widths, col_aligns,
                header_color, header_font_size, row_font_size, row_height,
            )
        else:
            if self.get_y() + header_space > self.h - self.b_margin:
                self.add_page()

            remaining = list(rows)
            first_page = True
            while remaining:
                available = int(
                    (self.h - self.b_margin - self.get_y() - header_h) / row_height
                )
                if available < min_body_rows:
                    self.add_page()
                    available = int(
                        (self.h - self.b_margin - self.get_y() - header_h) / row_height
                    )

                chunk = remaining[:available]
                remaining = remaining[available:]

                if not first_page and section_label:
                    self.set_font("DJV", "I", 7)
                    self.set_text_color(100, 100, 100)
                    self.cell(0, 4, f"{section_label} (continued)", new_x="LMARGIN", new_y="NEXT")
                    self.ln(1)

                self._draw_table_section(
                    headers, chunk, col_widths, col_aligns,
                    header_color, header_font_size, row_font_size, row_height,
                )
                first_page = False

    def _draw_table_section(
        self, headers, rows, col_widths, col_aligns,
        header_color, header_font_size, row_font_size, row_height,
    ):
        self.set_fill_color(*header_color)
        self.set_text_color(255, 255, 255)
        self.set_font("DJV", "B", header_font_size)
        for i, h in enumerate(headers):
            self.cell(col_widths[i], 6, h, border=0, fill=True, align="C")
        self.ln()

        for idx, row in enumerate(rows):
            if idx % 2 == 0:
                self.set_fill_color(245, 245, 245)
            else:
                self.set_fill_color(255, 255, 255)
            self.set_text_color(50, 50, 50)
            self.set_font("DJV", "", row_font_size)
            for i, cell_text in enumerate(row):
                truncated = self.truncate_text(str(cell_text), col_widths[i], row_font_size)
                self.cell(
                    col_widths[i], row_height, truncated,
                    border=0, fill=True, align=col_aligns[i],
                )
            self.ln()
        self.ln(3)

    def embed_chart(self, buf: io.BytesIO, w: float | None = None):
        if w is None:
            w = self.w - self.l_margin - self.r_margin
        if self.get_y() + w * 0.5 > self.h - 25:
            self.add_page()
        self.image(buf, x=self.l_margin, w=w)
        self.ln(3)

    def draw_kv_table(
        self,
        pairs: list[tuple[str, str]],
        col_widths: list[float] | None = None,
        font_size: int = 9,
    ):
        """Draw a simple key-value table."""
        if col_widths is None:
            usable = self.w - self.l_margin - self.r_margin
            col_widths = [usable * 0.55, usable * 0.45]
        for label, val in pairs:
            if self.get_y() > self.h - 20:
                self.add_page()
            self.set_fill_color(245, 245, 245)
            self.set_font("DJV", "B", font_size)
            self.set_text_color(50, 50, 50)
            self.cell(col_widths[0], 7, f"  {label}", fill=True)
            self.set_font("DJV", "", font_size)
            self.cell(col_widths[1], 7, val, fill=True, align="R", new_x="LMARGIN", new_y="NEXT")
        self.ln(3)


# =============================================================================
# Main Report Builder
# =============================================================================


class ReportBuilder:
    """Builds the complete Business Financial Report PDF."""

    def __init__(
        self,
        statements: list[Statement],
        config: BusinessConfig,
        categorizer: TransactionCategorizer,
        pl: ProfitAndLoss,
        monthly_pls: dict[str, ProfitAndLoss],
        quarterly_pls: dict[tuple[int, int], ProfitAndLoss],
        kpis: FinancialKPIs,
        recon_results: list[ReconciliationResult],
        all_reconciled: bool,
        forced_generation: bool,
        projections: list[ProjectionResult] | None = None,
        mask_personal: bool = False,
        full_detail: bool = False,
        projection_status: str = "not_requested",
        period_start_date: date | None = None,
        period_end_date: date | None = None,
        mode: str = "combined",
    ):
        self.statements = statements
        self.config = config
        self.categorizer = categorizer
        self.pl = pl
        self.monthly_pls = monthly_pls
        self.quarterly_pls = quarterly_pls
        self.kpis = kpis
        self.recon_results = recon_results
        self.all_reconciled = all_reconciled
        self.forced_generation = forced_generation
        self.projections = projections or []
        self.mask_personal = mask_personal
        self.full_detail = full_detail
        self.projection_status = projection_status
        self.period_start_date = period_start_date
        self.period_end_date = period_end_date
        self.mode = mode

    def build(self) -> ReportPDF:
        report_title = f"Business Financial Report - {self.config.display_name()}"
        pdf = ReportPDF(report_title)

        self._cover_page(pdf)
        self._executive_summary(pdf)
        self._data_quality(pdf)
        self._monthly_balance_summary(pdf)

        if self.mode in ("combined", "yearly"):
            self._revenue_expense_overview(pdf)

        self._pnl_statement(pdf)

        if self.mode in ("combined",):
            self._monthly_pnl(pdf)
        else:
            self._individual_monthly_pnl(pdf)

        if self.mode in ("combined", "quarterly"):
            self._quarterly_pnl(pdf)

        if self.mode in ("combined", "yearly"):
            self._revenue_analysis(pdf)
            self._expense_analysis(pdf)
            self._top_customers(pdf)
            self._top_vendors(pdf)
            self._cash_flow_analysis(pdf)
            self._balance_trend(pdf)
            self._expense_trends(pdf)
            self._financial_ratios(pdf)

        if self.projections:
            self._projections(pdf)
            self._projection_assumptions(pdf)
        elif self.projection_status == "withheld":
            pdf.add_page()
            pdf.section_title("Financial Projections")
            pdf.set_font("DJV", "B", 10)
            pdf.set_text_color(180, 60, 60)
            pdf.multi_cell(
                0, 5,
                "Financial projections were withheld because classified revenue is "
                "insufficient or too many transactions remain under CPA review. "
                "Re-run after categorizing more transactions to enable projections."
            )
        elif self.projection_status == "not_requested":
            pass

        if self.mode in ("combined", "yearly"):
            self._transaction_detail(pdf)
            self._cpa_package(pdf)
        elif self.mode in ("cpa",):
            self._cpa_package(pdf)
        return pdf

    def _mask_text(self, text: str) -> str:
        if not self.mask_personal:
            return text
        text = re.sub(
            r"\b\d+\s+(?:[NSEW]\s+)?"
            r"[A-Z0-9.'-]+(?:\s+[A-Z0-9.'-]+){0,4}\s+"
            r"(?:RD|ROAD|ST|STREET|AVE|AVENUE|DR|DRIVE|LN|LANE|"
            r"WAY|BLVD|BOULEVARD)\b",
            "[ADDRESS REDACTED]",
            text,
            flags=re.IGNORECASE,
        )
        for owner in self.config.owners:
            parts = owner.split()
            for part in parts:
                if len(part) > 2:
                    text = re.sub(re.escape(part), "[NAME REDACTED]", text, flags=re.IGNORECASE)
        return text

    def _cover_page(self, pdf: ReportPDF):
        pdf.add_page()
        pdf.ln(20)
        pdf.set_font("DJV", "B", 26)
        pdf.set_text_color(44, 62, 80)
        pdf.cell(0, 14, "Business Financial Report", align="C", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(4)

        pdf.set_font("DJV", "B", 14)
        pdf.set_text_color(80, 80, 80)
        pdf.cell(0, 9, self.config.display_name(), align="C", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(6)

        pdf.set_font("DJV", "", 10)
        pdf.set_text_color(100, 100, 100)
        if self.period_start_date or self.period_end_date:
            if self.period_start_date and self.period_end_date:
                dr = f"{self.period_start_date.strftime('%B %d, %Y')}  to  {self.period_end_date.strftime('%B %d, %Y')}"
            elif self.period_start_date:
                dr = f"{self.period_start_date.strftime('%B %d, %Y')}  to  {self.statements[-1].month_label}"
            else:
                dr = f"{self.statements[0].month_label}  to  {self.period_end_date.strftime('%B %d, %Y')}"
            pdf.cell(0, 7, dr, align="C", new_x="LMARGIN", new_y="NEXT")
        elif len(self.statements) > 0:
            dr = f"{self.statements[0].month_label}  to  {self.statements[-1].month_label}"
            pdf.cell(0, 7, dr, align="C", new_x="LMARGIN", new_y="NEXT")
        if self.config.address:
            addr = self._mask_text(self.config.address) if self.mask_personal else self.config.address
            pdf.cell(0, 7, addr, align="C", new_x="LMARGIN", new_y="NEXT")
        pdf.cell(0, 7, f"{len(self.statements)} statement(s)", align="C", new_x="LMARGIN", new_y="NEXT")
        acct = self.config.masked_account() or self.config.bank_account_display or "Bank Account"
        pdf.cell(0, 7, acct, align="C", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(10)

        pdf.set_font("DJV", "I", 8)
        pdf.set_text_color(150, 150, 150)
        pdf.cell(0, 5, _PNL_DISCLAIMER, align="C", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(8)

        # Summary box
        summary_rows = [
            ("Total Revenue", fmt_dollar(self.pl.total_revenue)),
            ("Total Direct Costs", fmt_dollar(self.pl.total_direct_costs)),
            ("Gross Profit", fmt_dollar(self.pl.gross_profit)),
            ("Total Operating Expenses", fmt_dollar(self.pl.total_operating_expenses)),
            ("Net Profit (Loss)", fmt_dollar(self.pl.net_profit)),
            ("Starting Balance", fmt_dollar(self.statements[0].beginning_balance)),
            ("Ending Balance", fmt_dollar(self.statements[-1].ending_balance)),
        ]
        cw = [pdf.w - pdf.l_margin - pdf.r_margin - 50, 50]
        for label, val in summary_rows:
            pdf.set_fill_color(245, 245, 245)
            pdf.set_font("DJV", "B", 9)
            pdf.set_text_color(50, 50, 50)
            pdf.cell(cw[0], 7, f"  {label}", fill=True)
            pdf.set_font("DJV", "", 9)
            pdf.cell(cw[1], 7, val, fill=True, align="R", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(6)

        # Reconciliation status
        if self.forced_generation:
            pdf.body_text(
                "Reconciliation: FAILED \u2014 report generation was forced "
                "(--allow-mismatch). Financial totals are NOT validated.", size=8,
            )
        elif self.all_reconciled:
            pdf.body_text("Reconciliation: PASSED", size=8)
        else:
            pdf.body_text("Reconciliation: FAILED - see Data Quality section", size=8)

        # Transaction count
        total_tx = sum(len(s.transactions) for s in self.statements)
        cpa_review_count = sum(
            1 for s in self.statements for tx in s.transactions if tx.cpa_review
        )
        pdf.body_text(f"Total Transactions: {total_tx}", size=8)
        if cpa_review_count > 0:
            pdf.set_font("DJV", "B", 8)
            pdf.set_text_color(180, 60, 60)
            pdf.multi_cell(
                0, 4.5,
                f"PRELIMINARY P&L — {cpa_review_count} of {total_tx} transactions "
                f"require classification. Revenue and expense totals may change materially.",
            )

    def _executive_summary(self, pdf: ReportPDF):
        pdf.add_page()
        pdf.section_title("Executive Financial Summary")
        if self.period_start_date or self.period_end_date:
            if self.period_start_date and self.period_end_date:
                period_label = f"{self.period_start_date.strftime('%B %d, %Y')} - {self.period_end_date.strftime('%B %d, %Y')}"
            elif self.period_start_date:
                period_label = f"{self.period_start_date.strftime('%B %d, %Y')} - {self.statements[-1].month_label}"
            else:
                period_label = f"{self.statements[0].month_label} - {self.period_end_date.strftime('%B %d, %Y')}"
        else:
            period_label = f"{self.statements[0].month_label} - {self.statements[-1].month_label}"
        pdf.body_text(
            f"Reporting Period: {period_label}",
            size=8,
        )

        revenue = self.pl.total_revenue
        direct_costs = self.pl.total_direct_costs
        op_exp = self.pl.total_operating_expenses
        gross = self.pl.gross_profit
        net = self.pl.net_profit

        total_in = sum(
            (s.total_credits for s in self.statements), Decimal("0")
        )
        total_out = sum(
            (s.total_debits for s in self.statements), Decimal("0")
        )
        start_bal = self.statements[0].beginning_balance
        end_bal = self.statements[-1].ending_balance
        net_cash = end_bal - start_bal

        uncategorized_count = sum(
            1 for s in self.statements
            for tx in s.transactions
            if tx.business_category == "Uncategorized" or tx.cpa_review
        )

        rows = [
            ("Total Revenue", fmt_dollar(revenue)),
            ("Total Direct Costs (COGS)", fmt_dollar(direct_costs)),
            ("Gross Profit", fmt_dollar(gross)),
            ("Gross Margin", self.pl.gross_margin),
            ("Total Operating Expenses", fmt_dollar(op_exp)),
            ("Operating Profit", fmt_dollar(self.pl.operating_profit)),
            ("Operating Margin", self.pl.operating_margin),
            ("Net Profit (Loss)", fmt_dollar(net)),
            ("Net Margin", self.pl.net_margin),
            ("", ""),
            ("Total Cash Inflows", fmt_dollar(total_in)),
            ("Total Cash Outflows", fmt_dollar(total_out)),
            ("Starting Bank Balance", fmt_dollar(start_bal)),
            ("Ending Bank Balance", fmt_dollar(end_bal)),
            ("Net Cash Change", fmt_dollar(net_cash)),
            ("", ""),
            ("Difference: Cash Change vs Net Profit", fmt_dollar(net_cash - net)),
            ("Total Transactions", str(sum(len(s.transactions) for s in self.statements))),
            ("CPA Review Transactions", str(uncategorized_count)),
        ]
        pdf.draw_kv_table(rows)
        pdf.body_text_small(
            "Note: Net cash change differs from net profit because this is a cash-basis "
            "report that includes non-P&L transactions (transfers, owner contributions, "
            "loan activity, fixed-asset purchases)."
        )

    def _data_quality(self, pdf: ReportPDF):
        pdf.add_page()
        pdf.section_title("Data Quality and Reconciliation Status")

        # Per-statement results
        headers = ["Statement", "Status", "Credits OK", "Debits OK", "Balance OK", "Notes"]
        rows = []
        for rr in self.recon_results:
            status = "PASS" if rr.passed else ("FORCED" if rr.forced else "FAIL")
            rows.append([
                rr.statement_label,
                status,
                "Yes" if rr.parsed_credit_count == rr.expected_credit_count else "No",
                "Yes" if rr.parsed_debit_count == rr.expected_debit_count else "No",
                "Yes" if rr.balance_ok else "No",
                "; ".join(rr.warnings)[:80],
            ])
        cw = [35, 14, 18, 18, 18, 70]
        pdf.draw_table(headers, rows, col_widths=cw,
                       section_label="Reconciliation Status")

        # Missing months check
        if len(self.statements) >= 2:
            all_dates = sorted(s.date_obj for s in self.statements)
            prev = all_dates[0]
            gaps = []
            for d in all_dates[1:]:
                expected_next = date(prev.year, prev.month, 1) + timedelta(days=32)
                expected_next = date(expected_next.year, expected_next.month, 1)
                actual = date(d.year, d.month, 1)
                if actual != expected_next:
                    gaps.append(f"{prev.strftime('%B %Y')} -> {actual.strftime('%B %Y')}")
                prev = d
            if gaps:
                pdf.sub_title("Missing Statements or Date Gaps")
                for g in gaps:
                    pdf.body_text_small(f"Gap: {g}")

    def _monthly_balance_summary(self, pdf: ReportPDF):
        pdf.add_page()
        pdf.section_title("Monthly Bank Balance Summary")
        headers = ["Month", "Start Balance", "End Balance", "Change", "Credits", "Debits"]
        rows = []
        for s in self.statements:
            rows.append([
                s.month_label,
                fmt_dollar(s.beginning_balance),
                fmt_dollar(s.ending_balance),
                fmt_dollar(s.ending_balance - s.beginning_balance),
                fmt_dollar(s.total_credits),
                fmt_dollar(s.total_debits),
            ])
        cw = [30, 30, 30, 30, 30, 30]
        pdf.draw_table(headers, rows, col_widths=cw,
                       col_aligns=["L", "R", "R", "R", "R", "R"],
                       section_label="Monthly Balances")

    def _revenue_expense_overview(self, pdf: ReportPDF):
        pdf.add_page()
        pdf.section_title("Revenue and Expense Overview")

        buf1 = chart_revenue_vs_expenses_monthly(self.statements, self.monthly_pls)
        pdf.embed_chart(buf1, w=pdf.w - pdf.l_margin - pdf.r_margin)
        buf1.close()

        buf2 = chart_profit_monthly(self.monthly_pls)
        pdf.embed_chart(buf2, w=pdf.w - pdf.l_margin - pdf.r_margin)
        buf2.close()

    def _pnl_statement(self, pdf: ReportPDF):
        pdf.add_page(orientation="L")
        pdf.section_title("Cash-Basis Profit and Loss Statement")
        pdf.body_text_small(_PNL_DISCLAIMER)
        pdf.ln(2)

        pl = self.pl
        income_rows: list[list[str]] = []
        for cat, val in sorted(pl.revenue.items()):
            if val != 0:
                income_rows.append([cat, fmt_dollar(val)])

        cogs_rows: list[list[str]] = []
        for cat, val in sorted(pl.direct_costs.items()):
            if val != 0:
                cogs_rows.append([cat, fmt_dollar(val)])

        op_exp_rows: list[list[str]] = []
        for cat, val in sorted(pl.operating_expenses.items()):
            if val != 0:
                op_exp_rows.append([cat, fmt_dollar(val)])

        other_inc_rows: list[list[str]] = []
        for cat, val in sorted(pl.other_income.items()):
            if val != 0:
                other_inc_rows.append([cat, fmt_dollar(val)])

        other_exp_rows: list[list[str]] = []
        for cat, val in sorted(pl.other_expense.items()):
            if val != 0:
                other_exp_rows.append([cat, fmt_dollar(val)])

        cw = [60, 30, 30]
        total_rev = pl.total_revenue

        def _section(heading: str, data_rows, subtotal_label: str, subtotal_val: Decimal):
            if not data_rows and subtotal_val == 0:
                return
            pdf.sub_title(heading)
            if data_rows:
                pdf.draw_table(
                    ["Category", "Amount", "% of Revenue"],
                    [r + [safe_pct(parse_amount(r[1]), total_rev)]
                     for r in data_rows],
                    col_widths=cw,
                    col_aligns=["L", "R", "R"],
                    header_font_size=7,
                    row_font_size=7,
                )
            pdf.set_font("DJV", "B", 8)
            pdf.set_text_color(44, 62, 80)
            pdf.cell(60, 5, subtotal_label)
            pdf.cell(30, 5, fmt_dollar(subtotal_val), align="R")
            pdf.cell(30, 5, safe_pct(subtotal_val, total_rev), align="R", new_x="LMARGIN", new_y="NEXT")
            pdf.ln(3)

        _section("Revenue", income_rows, "Total Revenue", total_rev)
        _section("Cost of Goods Sold / Direct Costs", cogs_rows,
                 "Total COGS / Direct Costs", pl.total_direct_costs)
        pdf.set_font("DJV", "B", 9)
        pdf.cell(60, 6, "Gross Profit")
        pdf.cell(30, 6, fmt_dollar(pl.gross_profit), align="R")
        pdf.cell(30, 6, pl.gross_margin, align="R", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(4)
        _section("Operating Expenses", op_exp_rows,
                 "Total Operating Expenses", pl.total_operating_expenses)
        pdf.set_font("DJV", "B", 9)
        pdf.cell(60, 6, "Operating Profit (Loss)")
        pdf.cell(30, 6, fmt_dollar(pl.operating_profit), align="R")
        pdf.cell(30, 6, pl.operating_margin, align="R", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(4)
        _section("Other Income", other_inc_rows,
                 "Total Other Income", pl.total_other_income)
        _section("Other Expense", other_exp_rows,
                 "Total Other Expense", pl.total_other_expense)
        pdf.set_font("DJV", "B", 10)
        pdf.set_fill_color(44, 62, 80)
        pdf.set_text_color(255, 255, 255)
        pdf.cell(60, 7, "Net Profit (Loss)", fill=True)
        pdf.cell(30, 7, fmt_dollar(pl.net_profit), align="R", fill=True)
        pdf.cell(30, 7, pl.net_margin, align="R", fill=True)
        pdf.ln(12)

        # Non-P&L summary
        pdf.body_text_small("Non-P&L Transactions (excluded from above):")
        non_pl_rows = [
            ("Owner Contributions", fmt_dollar(pl.owner_contributions)),
            ("Owner Distributions / Draws", fmt_dollar(pl.owner_distributions)),
            ("Loan Proceeds", fmt_dollar(pl.loan_proceeds)),
            ("Loan Principal Payments", fmt_dollar(pl.loan_principal_payments)),
            ("Payment Reversals (NSF Returns)", fmt_dollar(pl.payment_reversals)),
            ("Fixed Asset Purchases", fmt_dollar(pl.fixed_asset_purchases)),
            ("Account Transfers (credits)", fmt_dollar(pl.account_transfers_credits)),
            ("Account Transfers (debits)", fmt_dollar(pl.account_transfers_debits)),
            ("Credit Card Transfers", fmt_dollar(pl.credit_card_transfers)),
            ("",
             "--- Items Requiring CPA Review ---"),
            ("Unclassified Credits (excluded from P&L)",
             fmt_dollar(pl.uncategorized_non_pnl_credits)),
            ("Unclassified Debits (excluded from P&L)",
             fmt_dollar(pl.uncategorized_non_pnl_debits)),
        ]
        pdf.draw_kv_table(non_pl_rows)
        pdf.ln(2)
        cpa_review_count = sum(
            1 for s in self.statements for tx in s.transactions if tx.cpa_review
        )
        total_tx = sum(len(s.transactions) for s in self.statements)
        if cpa_review_count > 0:
            pdf.set_font("DJV", "B", 9)
            pdf.set_text_color(180, 60, 60)
            pdf.multi_cell(
                0, 5,
                f"PRELIMINARY P&L \u2014 {cpa_review_count} of {total_tx} "
                f"transactions remain unclassified or require CPA confirmation. "
                f"Revenue and expense totals may change materially after classification."
            )

    def _monthly_pnl(self, pdf: ReportPDF):
        if not self.monthly_pls:
            return
        pdf.add_page(orientation="L")
        pdf.section_title("Monthly Profit and Loss Statements")
        pdf.body_text_small(_PNL_DISCLAIMER)

        keys = sorted(self.monthly_pls.keys())
        all_cats = set()
        for k in keys:
            for cat in self.monthly_pls[k].revenue:
                all_cats.add(("R", cat))
            for cat in self.monthly_pls[k].direct_costs:
                all_cats.add(("D", cat))
            for cat in self.monthly_pls[k].operating_expenses:
                all_cats.add(("O", cat))
        sorted_cats = sorted(all_cats, key=lambda x: f"{x[0]}{x[1]}")

        month_labels_short = [datetime.strptime(k, "%Y-%m").strftime("%b %y") for k in keys]
        headers = ["Category"] + month_labels_short + ["YTD Total"]
        cw_first = 40
        cw_month = (pdf.w - pdf.l_margin - pdf.r_margin - cw_first) / (len(keys) + 1)
        cw = [cw_first] + [cw_month] * (len(keys) + 1)

        def _rows_for_section(prefix: str, source_dict_provider) -> list[list[str]]:
            rows = []
            for pref, cat in sorted_cats:
                if pref != prefix:
                    continue
                row = [cat]
                ytd = Decimal("0")
                for ki, k in enumerate(keys):
                    val = source_dict_provider(self.monthly_pls[k]).get(cat, Decimal("0"))
                    ytd += val
                    row.append(fmt_dollar(val))
                row.append(fmt_dollar(ytd))
                rows.append(row)
            return rows

        def _add_section(title: str, prefix: str, provider, total_fn):
            pdf.sub_title(title)
            sec_rows = _rows_for_section(prefix, provider)
            if sec_rows:
                # Add totals row
                totals_row = ["TOTAL"]
                ytd_total = Decimal("0")
                for ki, k in enumerate(keys):
                    val = total_fn(self.monthly_pls[k])
                    ytd_total += val
                    totals_row.append(fmt_dollar(val))
                totals_row.append(fmt_dollar(ytd_total))
                sec_rows.append(totals_row)
            pdf.draw_table(headers, sec_rows, col_widths=cw,
                           col_aligns=["L"] + ["R"] * (len(keys) + 1),
                           section_label=title)

        pl_totals: dict[str, list[Decimal]] = {}
        for k in keys:
            pl_totals[k] = [
                Decimal("0"),  # will build as we go
            ]

        _add_section("Revenue", "R", lambda pl: pl.revenue, lambda pl: pl.total_revenue)
        _add_section("Direct Costs / COGS", "D", lambda pl: pl.direct_costs, lambda pl: pl.total_direct_costs)
        _add_section("Operating Expenses", "O", lambda pl: pl.operating_expenses, lambda pl: pl.total_operating_expenses)

        # Summary line
        pdf.sub_title("Profit Summary")
        sum_headers = ["Metric"] + month_labels_short + ["YTD Total"]
        sum_rows = [
            ["Gross Profit"] + [fmt_dollar(self.monthly_pls[k].gross_profit) for k in keys]
            + [fmt_dollar(sum((self.monthly_pls[k].gross_profit for k in keys), Decimal("0")))],
            ["Operating Profit"] + [fmt_dollar(self.monthly_pls[k].operating_profit) for k in keys]
            + [fmt_dollar(sum((self.monthly_pls[k].operating_profit for k in keys), Decimal("0")))],
            ["Net Profit"] + [fmt_dollar(self.monthly_pls[k].net_profit) for k in keys]
            + [fmt_dollar(sum((self.monthly_pls[k].net_profit for k in keys), Decimal("0")))],
        ]
        pdf.draw_table(sum_headers, sum_rows, col_widths=cw,
                       col_aligns=["L"] + ["R"] * (len(keys) + 1))

    def _individual_monthly_pnl(self, pdf: ReportPDF):
        """Generate a full P&L page for each individual month."""
        if not self.monthly_pls:
            return
        for key in sorted(self.monthly_pls.keys()):
            pl = self.monthly_pls[key]
            pdf.add_page()
            pdf.section_title(f"P&L Statement {pl.label}")
            pdf.body_text_small(_PNL_DISCLAIMER)

            total_rev = pl.total_revenue

            # Revenue
            pdf.sub_title("Revenue")
            income_rows = [[cat, fmt_dollar(val),
                            safe_pct(val, total_rev)]
                           for cat, val in sorted(pl.revenue.items())
                           if val != 0]
            if income_rows:
                pdf.draw_table(["Category", "Amount", "% of Revenue"], income_rows,
                               col_widths=[60, 35, 25], col_aligns=["L", "R", "R"],
                               header_font_size=7, row_font_size=7)
            pdf.set_font("DJV", "B", 8)
            pdf.set_text_color(44, 62, 80)
            pdf.cell(60, 5, "Total Revenue")
            pdf.cell(35, 5, fmt_dollar(total_rev), align="R", new_x="LMARGIN", new_y="NEXT")
            pdf.ln(4)

            # Direct Costs / COGS
            pdf.sub_title("Direct Costs / COGS")
            cogs_rows = [[cat, fmt_dollar(val),
                          safe_pct(val, total_rev)]
                         for cat, val in sorted(pl.direct_costs.items())
                         if val != 0]
            if cogs_rows:
                pdf.draw_table(["Category", "Amount", "% of Revenue"], cogs_rows,
                               col_widths=[60, 35, 25], col_aligns=["L", "R", "R"],
                               header_font_size=7, row_font_size=7)
            pdf.set_font("DJV", "B", 8)
            pdf.set_text_color(44, 62, 80)
            pdf.cell(60, 5, "Total Direct Costs")
            pdf.cell(35, 5, fmt_dollar(pl.total_direct_costs), align="R", new_x="LMARGIN", new_y="NEXT")
            pdf.ln(2)
            pdf.set_font("DJV", "B", 9)
            pdf.cell(60, 6, f"Gross Profit: {fmt_dollar(pl.gross_profit)} ({pl.gross_margin})",
                     new_x="LMARGIN", new_y="NEXT")
            pdf.ln(4)

            # Operating Expenses
            pdf.sub_title("Operating Expenses")
            op_rows = [[cat, fmt_dollar(val),
                        safe_pct(val, total_rev)]
                       for cat, val in sorted(pl.operating_expenses.items())
                       if val != 0]
            if op_rows:
                pdf.draw_table(["Category", "Amount", "% of Revenue"], op_rows,
                               col_widths=[60, 35, 25], col_aligns=["L", "R", "R"],
                               header_font_size=7, row_font_size=7)
            pdf.set_font("DJV", "B", 8)
            pdf.set_text_color(44, 62, 80)
            pdf.cell(60, 5, "Total Operating Expenses")
            pdf.cell(35, 5, fmt_dollar(pl.total_operating_expenses), align="R",
                     new_x="LMARGIN", new_y="NEXT")
            pdf.ln(2)
            pdf.set_font("DJV", "B", 9)
            pdf.cell(60, 6, f"Operating Profit: {fmt_dollar(pl.operating_profit)} ({pl.operating_margin})",
                     new_x="LMARGIN", new_y="NEXT")
            pdf.ln(4)

            # Other Income / Expense
            if pl.total_other_income != 0 or pl.total_other_expense != 0:
                pdf.sub_title("Other Income / Expense")
                if pl.total_other_income != 0:
                    for cat, val in sorted(pl.other_income.items()):
                        if val != 0:
                            pdf.set_font("DJV", "", 8)
                            pdf.cell(60, 5, f"  {cat}")
                            pdf.cell(35, 5, fmt_dollar(val), align="R",
                                     new_x="LMARGIN", new_y="NEXT")
                if pl.total_other_expense != 0:
                    for cat, val in sorted(pl.other_expense.items()):
                        if val != 0:
                            pdf.set_font("DJV", "", 8)
                            pdf.cell(60, 5, f"  {cat}")
                            pdf.cell(35, 5, fmt_dollar(val), align="R",
                                     new_x="LMARGIN", new_y="NEXT")
                pdf.ln(4)

            # Net Result
            pdf.set_font("DJV", "B", 10)
            pdf.set_fill_color(44, 62, 80)
            pdf.set_text_color(255, 255, 255)
            pdf.cell(60, 7, "Net Profit (Loss)", fill=True)
            pdf.cell(35, 7, fmt_dollar(pl.net_profit), align="R", fill=True)
            pdf.cell(30, 7, pl.net_margin, align="R", fill=True)
            pdf.ln(12)

            # Non-P&L items for this month
            non_pl_items = []
            if pl.owner_contributions:
                non_pl_items.append(("Owner Contributions", fmt_dollar(pl.owner_contributions)))
            if pl.owner_distributions:
                non_pl_items.append(("Owner Distributions", fmt_dollar(pl.owner_distributions)))
            if pl.loan_proceeds:
                non_pl_items.append(("Loan Proceeds", fmt_dollar(pl.loan_proceeds)))
            if pl.loan_principal_payments:
                non_pl_items.append(("Loan Principal Payments", fmt_dollar(pl.loan_principal_payments)))
            if pl.payment_reversals:
                non_pl_items.append(("Payment Reversals", fmt_dollar(pl.payment_reversals)))
            if pl.fixed_asset_purchases:
                non_pl_items.append(("Fixed Asset Purchases", fmt_dollar(pl.fixed_asset_purchases)))
            if pl.account_transfers_credits:
                non_pl_items.append(("Account Transfers (credits)", fmt_dollar(pl.account_transfers_credits)))
            if pl.account_transfers_debits:
                non_pl_items.append(("Account Transfers (debits)", fmt_dollar(pl.account_transfers_debits)))

            if non_pl_items:
                pdf.body_text_small("Non-P&L Transactions (excluded from P&L above):")
                pdf.draw_kv_table(non_pl_items)

    def _quarterly_pnl(self, pdf: ReportPDF):
        if not self.quarterly_pls:
            return
        pdf.add_page()
        pdf.section_title("Quarterly Profit and Loss Statements")
        pdf.body_text_small(_PNL_DISCLAIMER)

        q_keys = sorted(self.quarterly_pls.keys())
        q_labels = [f"FY{fy} Q{q}" for fy, q in q_keys]
        headers = ["Metric"] + q_labels + ["Total"]

        def q_val(fn) -> list[str]:
            vals = []
            total = Decimal("0")
            for key in q_keys:
                v = fn(self.quarterly_pls.get(key, ProfitAndLoss()))
                vals.append(fmt_dollar(v))
                total += v
            return vals + [fmt_dollar(total)]

        q_width = max(26, int(120 / max(len(q_keys), 1)))
        total_width = 30
        cw_q = [45] + [q_width] * len(q_keys) + [total_width]
        rows = [
            ["Revenue"] + q_val(lambda p: p.total_revenue),
            ["Direct Costs"] + q_val(lambda p: p.total_direct_costs),
            ["Gross Profit"] + q_val(lambda p: p.gross_profit),
            ["Operating Expenses"] + q_val(lambda p: p.total_operating_expenses),
            ["Operating Profit"] + q_val(lambda p: p.operating_profit),
            ["Net Profit"] + q_val(lambda p: p.net_profit),
        ]
        pdf.draw_table(headers, rows, col_widths=cw_q,
                       col_aligns=["L"] + ["R"] * (len(q_keys) + 1),
                       section_label="Quarterly P&L")

    def _revenue_analysis(self, pdf: ReportPDF):
        pdf.add_page()
        pdf.section_title("Revenue Analysis")

        buf = chart_revenue_by_category(self.pl)
        pdf.embed_chart(buf, w=120)
        buf.close()

        pdf.sub_title("Revenue by Category")
        rev_rows = []
        for cat, val in sorted(self.pl.revenue.items(), key=lambda x: x[1], reverse=True):
            if val != 0:
                rev_rows.append([cat, fmt_dollar(val), safe_pct(val, self.pl.total_revenue)])
        cw = [60, 35, 25]
        pdf.draw_table(["Category", "Amount", "% of Revenue"], rev_rows,
                       col_widths=cw, col_aligns=["L", "R", "R"])

    def _expense_analysis(self, pdf: ReportPDF):
        pdf.add_page()
        pdf.section_title("Expense Analysis")

        buf = chart_expenses_by_category(self.pl)
        pdf.embed_chart(buf, w=120)
        buf.close()

        total_exp = self.pl.total_direct_costs + self.pl.total_operating_expenses
        pdf.sub_title("Expenses by Category")
        all_exp = {}
        for cat, val in self.pl.direct_costs.items():
            if val > 0:
                all_exp[f"[COGS] {cat}"] = val
        for cat, val in self.pl.operating_expenses.items():
            if val > 0:
                all_exp[cat] = val

        exp_rows = []
        for cat, val in sorted(all_exp.items(), key=lambda x: x[1], reverse=True):
            exp_rows.append([cat, fmt_dollar(val), safe_pct(val, total_exp)])
        cw = [60, 35, 25]
        pdf.draw_table(["Category", "Amount", "% of Total"], exp_rows,
                       col_widths=cw, col_aligns=["L", "R", "R"],
                       section_label="Expense Breakdown")

    def _top_customers(self, pdf: ReportPDF):
        pdf.add_page()
        pdf.section_title("Top Customers / Income Sources")

        buf = chart_top_revenue_sources(self.statements)
        pdf.embed_chart(buf, w=pdf.w - pdf.l_margin - pdf.r_margin)
        buf.close()

        source_totals: dict[str, Decimal] = defaultdict(Decimal)
        for s in self.statements:
            for tx in s.transactions:
                if tx.is_credit and tx.include_in_pnl:
                    m = normalize_merchant(tx.description)
                    source_totals[m] += tx.amount

        rows = []
        for i, (name, amt) in enumerate(
            sorted(source_totals.items(), key=lambda x: x[1], reverse=True)[:20], 1
        ):
            rows.append([str(i), self._mask_text(name)[:60], fmt_dollar(amt)])
        cw = [8, 100, 35]
        pdf.draw_table(["#", "Source", "Total"], rows,
                       col_widths=cw, col_aligns=["R", "L", "R"],
                       section_label="Top Income Sources")

    def _top_vendors(self, pdf: ReportPDF):
        pdf.add_page()
        pdf.section_title("Top Vendors and Payees")

        buf = chart_top_vendors(self.statements)
        pdf.embed_chart(buf, w=pdf.w - pdf.l_margin - pdf.r_margin)
        buf.close()

        vendor_totals: dict[str, Decimal] = defaultdict(Decimal)
        for s in self.statements:
            for tx in s.transactions:
                if not tx.is_credit and tx.include_in_pnl:
                    m = normalize_merchant(tx.description)
                    vendor_totals[m] += tx.amount

        rows = []
        for i, (name, amt) in enumerate(
            sorted(vendor_totals.items(), key=lambda x: x[1], reverse=True)[:20], 1
        ):
            rows.append([str(i), self._mask_text(name)[:60], fmt_dollar(amt)])
        cw = [8, 100, 35]
        pdf.draw_table(["#", "Vendor", "Total"], rows,
                       col_widths=cw, col_aligns=["R", "L", "R"],
                       section_label="Top Vendors")

    def _cash_flow_analysis(self, pdf: ReportPDF):
        pdf.add_page()
        pdf.section_title("Monthly Cash-Flow Analysis")

        buf = chart_net_cash_flow(self.statements)
        pdf.embed_chart(buf, w=pdf.w - pdf.l_margin - pdf.r_margin)
        buf.close()

        headers = ["Month", "Credits In", "Debits Out", "Net Cash Flow", "Ending Balance"]
        rows = []
        for s in self.statements:
            rows.append([
                s.month_label,
                fmt_dollar(s.total_credits),
                fmt_dollar(s.total_debits),
                fmt_dollar(s.total_credits - s.total_debits),
                fmt_dollar(s.ending_balance),
            ])
        cw = [30, 35, 35, 35, 35]
        pdf.draw_table(headers, rows, col_widths=cw,
                       col_aligns=["L", "R", "R", "R", "R"],
                       section_label="Monthly Cash Flow")

    def _balance_trend(self, pdf: ReportPDF):
        pdf.add_page(orientation="L")
        pdf.section_title("Bank-Balance Trend")
        buf = chart_balance_trend(self.statements)
        pdf.embed_chart(buf, w=pdf.w - pdf.l_margin - pdf.r_margin)
        buf.close()

    def _expense_trends(self, pdf: ReportPDF):
        pdf.add_page(orientation="L")
        pdf.section_title("Expense Category Trends")
        buf = chart_cost_by_month(self.monthly_pls)
        pdf.embed_chart(buf, w=pdf.w - pdf.l_margin - pdf.r_margin)
        buf.close()

    def _financial_ratios(self, pdf: ReportPDF):
        pdf.add_page()
        pdf.section_title("Financial Ratios and Key Performance Indicators")
        kpi = self.kpis
        rows = [
            ("Gross Margin", kpi.gross_margin),
            ("Operating Margin", kpi.operating_margin),
            ("Net Margin", kpi.net_margin),
            ("Expense-to-Revenue Ratio", kpi.expense_to_revenue),
            ("Avg Monthly Revenue", fmt_dollar(kpi.avg_monthly_revenue)),
            ("Avg Monthly Expenses", fmt_dollar(kpi.avg_monthly_expenses)),
            ("Avg Monthly Net Profit", fmt_dollar(kpi.avg_monthly_net)),
            ("Average Transaction Value", fmt_dollar(kpi.avg_transaction_value)),
            ("Largest Bank Credit", fmt_dollar(kpi.largest_income)),
            ("Largest Bank Debit", fmt_dollar(kpi.largest_expense)),
            ("Min Monthly Balance", fmt_dollar(kpi.min_monthly_balance)),
            ("Max Monthly Balance", fmt_dollar(kpi.max_monthly_balance)),
            ("Cash Runway Estimate (months)", kpi.cash_runway_months),
            ("Total Revenue", fmt_dollar(kpi.total_revenue)),
            ("Total Expenses", fmt_dollar(kpi.total_expenses)),
            ("Net Income (Cash Basis)", fmt_dollar(kpi.net_income)),
        ]
        pdf.draw_kv_table(rows)

    def _projections(self, pdf: ReportPDF):
        if not self.projections:
            return

        pr = next(
            (item for item in self.projections if item.scenario == "base"),
            self.projections[0],
        )

        # ---- Page 1: Chart ----
        pdf.add_page(orientation="L")
        pdf.section_title("Financial Projections")
        pdf.body_text_small(
            "These projections are estimates based on historical data and configured assumptions. "
            "They are not guaranteed results. Actual results may differ materially."
        )

        hist_rev = [self.monthly_pls[k].total_revenue for k in sorted(self.monthly_pls.keys())]
        hist_labels = [k for k in sorted(self.monthly_pls.keys())]

        buf = chart_projection(hist_rev, self.projections, hist_labels)
        pdf.embed_chart(buf, w=pdf.w - pdf.l_margin - pdf.r_margin)
        buf.close()

        # ---- Page 2: Detail table (fresh landscape page) ----
        if self.statements:
            last_stmt_date = self.statements[-1].date_obj
            proj_start = date(last_stmt_date.year, last_stmt_date.month, 1) + timedelta(days=32)
            proj_start = date(proj_start.year, proj_start.month, 1)
        else:
            proj_start = date.today()

        month_labels: list[str] = []
        for i in range(pr.months):
            m = ((proj_start.month + i - 1) % 12) + 1
            y = proj_start.year + (proj_start.month + i - 1) // 12
            month_labels.append(date(y, m, 1).strftime("%b %y"))

        pdf.add_page(orientation="L")
        pdf.section_title(f"Projection Detail - {pr.scenario.title()} Scenario")
        headers = ["Month", "Rev (Base)", "Exp (Base)", "Gross Profit", "Net Income",
                   "Cash Flow", "End Cash", "Tax Reserve"]
        rows = []
        for i in range(pr.months):
            label = month_labels[i] if i < len(month_labels) else f"Month {i + 1}"
            rows.append([
                label,
                fmt_dollar(pr.monthly_revenue[i]),
                fmt_dollar(pr.monthly_expenses[i]),
                fmt_dollar(pr.monthly_gross_profit[i]),
                fmt_dollar(pr.monthly_net_income[i]),
                fmt_dollar(pr.monthly_cash_flow[i]),
                fmt_dollar(pr.ending_cash[i]),
                fmt_dollar(pr.tax_reserve[i]),
            ])
        cw_p = [22, 28, 28, 28, 28, 28, 28, 28]
        pdf.draw_table(headers, rows, col_widths=cw_p,
                       col_aligns=["L"] + ["R"] * 7,
                       section_label="Projection Table",
                       header_font_size=6, row_font_size=6)

        # ---- Page 3: Scenario comparison (fresh landscape page) ----
        if len(self.projections) > 1:
            pdf.add_page(orientation="L")
            pdf.section_title("Scenario Comparison")
            comp_headers = ["Month"] + [f"{p.scenario.title()} Rev" for p in self.projections] \
                + [f"{p.scenario.title()} Net" for p in self.projections]
            comp_rows = []
            for i in range(pr.months):
                label = month_labels[i] if i < len(month_labels) else f"Month {i + 1}"
                row = [label]
                for p in self.projections:
                    row.append(fmt_dollar(p.monthly_revenue[i]))
                for p in self.projections:
                    row.append(fmt_dollar(p.monthly_net_income[i]))
                comp_rows.append(row)
            cw_c = [20] + [32] * (len(self.projections) * 2)
            pdf.draw_table(comp_headers, comp_rows, col_widths=cw_c,
                           col_aligns=["L"] + ["R"] * (len(self.projections) * 2),
                           section_label="Scenario Comparison",
                           header_font_size=6, row_font_size=6)

    def _projection_assumptions(self, pdf: ReportPDF):
        pdf.add_page()
        pdf.section_title("Projection Assumptions")
        pdf.body_text_small(
            "All projections are based on the following assumptions. "
            "Changes in market conditions, business volume, or costs will affect actual results."
        )
        if self.projections:
            pr = next(
                (item for item in self.projections if item.scenario == "base"),
                self.projections[0],
            )
            for k, v in pr.assumptions.items():
                if k == "min_cash_warnings":
                    if v:
                        pdf.body_text_small(
                            f"WARNING: Minimum cash balance breached in projected months: {v}"
                        )
                    continue
                pdf.body_text_small(f"{k}: {v}")
        pdf.body_text_small(
            "Methodology: Baseline uses average of recent months. "
            "Revenue and expenses grow at configured rates. "
            "Seasonal adjustments applied when configured. "
            "Tax reserve is a percentage of net income. "
            "Projections do not include one-time items unless configured."
        )

    def _transaction_detail(self, pdf: ReportPDF):
        """Monthly transaction detail section — all months."""
        pdf.add_page()
        pdf.section_title("Transaction Detail")
        if self.full_detail:
            pdf.body_text_small(
                f"Complete transaction listing for all {len(self.statements)} months."
            )
        else:
            pdf.body_text_small(
                f"Transaction detail excerpt — up to 50 transactions per statement. "
                f"Use --full-detail for the complete listing."
            )

        for stmt in self.statements:
            pdf.add_page()
            pdf.section_title(f"Transaction Detail - {stmt.month_label}")

            sorted_tx = sorted(
                stmt.transactions,
                key=lambda tx: tx.post_date,
                reverse=True,
            )[:50] if not self.full_detail else sorted(
                stmt.transactions,
                key=lambda tx: tx.post_date,
                reverse=True,
            )
            tx_rows = []
            for tx in sorted_tx:
                desc = self._mask_text(tx.description)[:50]
                sign = "+" if tx.is_credit else "-"
                tx_rows.append([
                    tx.post_date,
                    desc,
                    tx.business_category[:18],
                    f"{sign}{fmt_dollar(tx.amount)}",
                    fmt_dollar(tx.balance),
                    "Yes" if tx.cpa_review else "",
                ])
            cw_tx = [22, 55, 28, 26, 26, 12]
            pdf.draw_table(
                ["Date", "Description", "Category", "Amount", "Balance", "Review"],
                tx_rows,
                col_widths=cw_tx,
                col_aligns=["L", "L", "L", "R", "R", "C"],
                section_label="Transaction Detail",
                header_font_size=6,
                row_font_size=6,
            )

    # =========================================================================
    # CPA / Tax Preparer Package
    # =========================================================================

    def _cpa_package(self, pdf: ReportPDF):
        """Build the complete CPA package."""
        pdf.add_page()
        pdf.ln(30)
        pdf.set_font("DJV", "B", 22)
        pdf.set_text_color(44, 62, 80)
        pdf.cell(0, 12, "CPA / Tax Preparer Package", align="C", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(6)
        pdf.set_font("DJV", "", 10)
        pdf.set_text_color(100, 100, 100)
        pdf.cell(0, 7, self.config.display_name(), align="C", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(8)
        pdf.set_font("DJV", "I", 8)
        pdf.set_text_color(150, 150, 150)
        pdf.multi_cell(0, 4.5, _CPA_DISCLAIMER, align="C")
        pdf.ln(4)

        self._cpa_business_info(pdf)
        self._cpa_tax_summary(pdf)
        self._cpa_expense_by_tax_cat(pdf)
        self._cpa_revenue_detail(pdf)
        self._cpa_deduction_detail(pdf)
        self._cpa_fixed_assets(pdf)
        self._cpa_vehicle(pdf)
        self._cpa_payroll(pdf)
        self._cpa_loans(pdf)
        self._cpa_owner_activity(pdf)
        self._cpa_taxes_govt(pdf)
        self._cpa_uncategorized(pdf)
        self._cpa_reconciliation(pdf)
        self._cpa_questions(pdf)
        self._cpa_document_checklist(pdf)
        self._cpa_certification(pdf)

    def _cpa_business_info(self, pdf: ReportPDF):
        pdf.add_page()
        pdf.section_title("Page 1: Business and Report Information")
        rows = [
            ("Legal Business Name", self.config.business_name),
            ("DBA", self.config.dba or "N/A"),
            ("Entity Type", self.config.entity_type),
            ("Tax Year", str(self.config.tax_year)),
            ("Fiscal Year Start", f"Month {self.config.fiscal_year_start}"),
            ("Accounting Method", self.config.accounting_method),
            ("Masked EIN", self.config.masked_ein() or "N/A"),
            ("Bank Account", self.config.masked_account() or "N/A"),
            ("Report Generated", datetime.now().strftime("%Y-%m-%d %H:%M")),
            ("Source Statements", str(len(self.statements))),
            ("First Statement", self.statements[0].month_label if self.statements else "N/A"),
            ("Last Statement", self.statements[-1].month_label if self.statements else "N/A"),
            ("Reconciliation Status", "PASSED" if self.all_reconciled else ("FORCED" if self.forced_generation else "FAILED")),
            ("Script Version", _SCRIPT_HASH),
        ]
        pdf.draw_kv_table(rows)

    def _cpa_tax_summary(self, pdf: ReportPDF):
        pdf.add_page()
        pdf.section_title("Page 2: Tax Summary")
        pdf.body_text_small("Cash-basis summary from bank statements. Not a filed tax return.")
        pl = self.pl
        rows = [
            ("Total Gross Receipts / Revenue", fmt_dollar(pl.total_revenue)),
            ("Cost of Goods Sold / Direct Costs", fmt_dollar(pl.total_direct_costs)),
            ("Gross Profit", fmt_dollar(pl.gross_profit)),
            ("Operating Expenses", fmt_dollar(pl.total_operating_expenses)),
            ("Interest Income", fmt_dollar(pl.total_other_income)),
            ("Interest Expense", fmt_dollar(pl.total_other_expense)),
            ("Net Cash-Basis Profit (Loss)", fmt_dollar(pl.net_profit)),
            ("", ""),
            ("Estimated Tax Reserve ({:.0f}%)".format(
                float(self.config.projection_config.get("tax_reserve_pct", 0.25)) * 100
                if self.config.projection_config else 25),
             fmt_dollar(pl.net_profit * Decimal(str(
                 self.config.projection_config.get("tax_reserve_pct", 0.25)
                 if self.config.projection_config else 0.25
             )) if pl.net_profit > 0 else Decimal("0"))),
            ("", ""),
            ("Non-Income/Expense Items:", ""),
            ("Owner Contributions", fmt_dollar(pl.owner_contributions)),
            ("Owner Distributions / Draws", fmt_dollar(pl.owner_distributions)),
            ("Loan Proceeds", fmt_dollar(pl.loan_proceeds)),
            ("Loan Principal Payments", fmt_dollar(pl.loan_principal_payments)),
            ("Fixed Asset Purchases", fmt_dollar(pl.fixed_asset_purchases)),
            ("Account Transfers (net credits)", fmt_dollar(pl.account_transfers_credits)),
            ("Account Transfers (net debits)", fmt_dollar(pl.account_transfers_debits)),
            ("Credit Card Transfers", fmt_dollar(pl.credit_card_transfers)),
            ("Payment Reversals (NSF Returns)", fmt_dollar(pl.payment_reversals)),
            ("", ""),
            ("Reconciliation Items:", ""),
            ("Unclassified Credits (excluded from P&L)", fmt_dollar(pl.uncategorized_non_pnl_credits)),
            ("Unclassified Debits (excluded from P&L)", fmt_dollar(pl.uncategorized_non_pnl_debits)),
        ]
        pdf.draw_kv_table(rows)

        # ---- Cash-to-P&L Reconciliation (separate page) ----
        pdf.add_page()
        pdf.section_title("Cash-to-P&L Reconciliation")
        pdf.body_text_small(
            "How the preliminary P&L result bridges to the net bank-account change."
        )
        cpa_review_count = sum(
            1 for s in self.statements for tx in s.transactions if tx.cpa_review
        )
        pdf.body_text_small(
            f"Note: {cpa_review_count} transactions remain unclassified; "
            f"the bridge below may change after classification."
        )
        bridge_rows: list[tuple[str, str]] = [
            ("Preliminary Net Profit/Loss", fmt_dollar(pl.net_profit)),
            ("+ Payment Reversals (NSF Returns)", fmt_dollar(pl.payment_reversals)),
            ("+ Loan Proceeds", fmt_dollar(pl.loan_proceeds)),
            ("- Loan Principal Payments", fmt_dollar(-pl.loan_principal_payments)),
            ("+ Owner Contributions", fmt_dollar(pl.owner_contributions)),
            ("- Owner Distributions / Draws", fmt_dollar(-pl.owner_distributions)),
            ("- Fixed Asset Purchases", fmt_dollar(-pl.fixed_asset_purchases)),
            ("+ Net Account Transfers",
             fmt_dollar(pl.account_transfers_credits - pl.account_transfers_debits - pl.credit_card_transfers)),
            ("+ Net Unclassified Activity",
             fmt_dollar(pl.uncategorized_non_pnl_credits - pl.uncategorized_non_pnl_debits)),
            ("", ""),
            ("= Estimated Net Cash Change",
             fmt_dollar(pl.net_profit + pl.payment_reversals + pl.loan_proceeds
                        - pl.loan_principal_payments + pl.owner_contributions
                        - pl.owner_distributions - pl.fixed_asset_purchases
                        + pl.account_transfers_credits - pl.account_transfers_debits
                        - pl.credit_card_transfers
                        + pl.uncategorized_non_pnl_credits - pl.uncategorized_non_pnl_debits)),
        ]
        pdf.draw_kv_table(bridge_rows)

    def _cpa_expense_by_tax_cat(self, pdf: ReportPDF):
        pdf.add_page()
        pdf.section_title("Page 3: Expense Summary by Tax Category")
        if self.config.entity_type in ("sole-prop", "single-member-llc"):
            pdf.body_text_small("Suggested Schedule C organizational aid. Verify all classifications with a tax professional.")
        else:
            pdf.body_text_small("Tax preparation categories. Verify all classifications with a tax professional.")

        # Deductible expenses (P&L items only)
        tax_groups: dict[str, dict[str, Any]] = {}
        for s in self.statements:
            for tx in s.transactions:
                if tx.is_credit or tx.is_transfer:
                    continue
                # Exclude non-P&L items from deduction summary
                if tx.is_fixed_asset or tx.is_owner_related:
                    continue
                if tx.is_loan and not tx.include_in_pnl:
                    continue
                key = tx.tax_category
                if key not in tax_groups:
                    tax_groups[key] = {"count": 0, "total": Decimal("0"), "cats": set(), "review": False}
                tax_groups[key]["count"] += 1
                tax_groups[key]["total"] += tx.amount
                tax_groups[key]["cats"].add(tx.business_category)
                if tx.cpa_review:
                    tax_groups[key]["review"] = True

        pdf.sub_title("Potential Deductible Expenses")
        headers = ["Tax Category", "Business Categories", "Count", "Amount", "Deductibility", "Review"]
        rows = []
        for tcat, info in sorted(tax_groups.items()):
            rows.append([
                tcat,
                ", ".join(sorted(info["cats"]))[:60],
                str(info["count"]),
                fmt_dollar(info["total"]),
                "Review needed" if info["review"] else "Suggested",
                "Yes" if info["review"] else "No",
            ])
        cw = [35, 55, 12, 30, 30, 14]
        if rows:
            pdf.draw_table(headers, rows, col_widths=cw,
                           col_aligns=["L", "L", "R", "R", "L", "C"],
                           section_label="Tax Category Summary")
        else:
            pdf.body_text_small("No deductible expenses categorized.")

        # Financing / non-P&L summary
        pdf.sub_title("Non-P&L / Financing Summary (Excluded from Deduction Summary)")
        fin_rows = [
            ("Loan Principal Payments", fmt_dollar(self.pl.loan_principal_payments)),
            ("Fixed Asset Purchases", fmt_dollar(self.pl.fixed_asset_purchases)),
            ("Owner Distributions / Draws", fmt_dollar(self.pl.owner_distributions)),
            ("Credit Card Transfers", fmt_dollar(self.pl.credit_card_transfers)),
        ]
        pdf.draw_kv_table(fin_rows)

    def _cpa_revenue_detail(self, pdf: ReportPDF):
        pdf.add_page()
        pdf.section_title("Page 4: Revenue Detail")
        pdf.sub_title("Revenue by Category")
        rev_rows = [[cat, fmt_dollar(val)]
                    for cat, val in sorted(self.pl.revenue.items(),
                                           key=lambda x: x[1], reverse=True)
                    if val != 0]
        if rev_rows:
            pdf.draw_table(["Category", "Amount"], rev_rows,
                           col_widths=[70, 40], col_aligns=["L", "R"])
        else:
            pdf.body_text_small("No revenue categorized.")

        pdf.sub_title("Unusual or Large Deposits")
        large_dep = sorted(
            [tx for s in self.statements for tx in s.transactions
             if tx.is_credit and tx.amount >= Decimal("1000")],
            key=lambda tx: tx.amount, reverse=True,
        )[:20]
        if large_dep:
            l_rows = [[tx.post_date, self._mask_text(tx.description)[:50],
                       fmt_dollar(tx.amount), tx.business_category]
                      for tx in large_dep]
            pdf.draw_table(["Date", "Description", "Amount", "Category"], l_rows,
                           col_widths=[22, 70, 30, 35],
                           col_aligns=["L", "L", "R", "L"],
                           section_label="Large Deposits")
        else:
            pdf.body_text_small("No large deposits detected.")

    def _cpa_deduction_detail(self, pdf: ReportPDF):
        pdf.add_page()
        pdf.section_title("Page 5: Potential Deduction Detail")
        candidates = [
            tx for s in self.statements for tx in s.transactions
            if not tx.is_credit and tx.include_in_pnl and tx.amount > 0
        ]
        candidates.sort(key=lambda tx: tx.amount, reverse=True)

        headers = ["Date", "Vendor", "Description", "Category", "Tax Cat", "Amount", "Deduct", "Review"]
        rows = []
        for tx in candidates[:100]:
            rows.append([
                tx.post_date,
                normalize_merchant(tx.description)[:25],
                self._mask_text(tx.description)[:35],
                tx.business_category[:18],
                tx.tax_category[:18],
                fmt_dollar(tx.amount),
                tx.deductibility[:12],
                tx.review_reason[:30] if tx.cpa_review else "",
            ])
        cw = [20, 28, 38, 22, 22, 24, 18, 30]
        pdf.draw_table(headers, rows, col_widths=cw,
                       col_aligns=["L", "L", "L", "L", "L", "R", "L", "L"],
                       section_label="Potential Deductions",
                       header_font_size=5.5, row_font_size=5.5)

    def _cpa_fixed_assets(self, pdf: ReportPDF):
        pdf.add_page()
        pdf.section_title("Page 6: Fixed Assets and Large Purchases")
        large_purchases = get_fixed_asset_candidates(self.statements)
        large_purchases.sort(key=lambda tx: tx.amount, reverse=True)

        pdf.body_text_small(
            "Items over $500 that may require capitalization (excluding "
            "payroll, insurance, loan payments, transfers, and returned items). "
            "Section 179 or bonus depreciation may apply. Professional review required."
        )

        # Section 1: likely fixed-asset candidates
        likely = [tx for tx in large_purchases if tx.is_fixed_asset]
        other = [tx for tx in large_purchases if not tx.is_fixed_asset]

        headers = ["Date", "Vendor", "Description", "Amount", "Suggested Asset Class", "CPA Note"]
        cw = [20, 28, 38, 24, 30, 40]

        if likely:
            pdf.sub_title("Likely Fixed-Asset Candidates")
            lik_rows = []
            for tx in likely[:30]:
                lik_rows.append([
                    tx.post_date,
                    normalize_merchant(tx.description)[:25],
                    self._mask_text(tx.description)[:35],
                    fmt_dollar(tx.amount),
                    "Review needed",
                    "Flagged by category rule - verify asset treatment",
                ])
            pdf.draw_table(headers, lik_rows, col_widths=cw,
                           col_aligns=["L", "L", "L", "R", "L", "L"],
                           section_label="Fixed Asset Candidates")

        if other:
            pdf.sub_title("Other Large Transactions Requiring Review")
            oth_rows = []
            for tx in other[:30]:
                note = "CPA review" if tx.amount >= Decimal("2500") else "Review"
                oth_rows.append([
                    tx.post_date,
                    normalize_merchant(tx.description)[:25],
                    self._mask_text(tx.description)[:35],
                    fmt_dollar(tx.amount),
                    "Review needed",
                    note,
                ])
            pdf.draw_table(headers, oth_rows, col_widths=cw,
                           col_aligns=["L", "L", "L", "R", "L", "L"],
                           section_label="Other Large Transactions")

        if not likely and not other:
            pdf.body_text_small("No fixed-asset or large purchase candidates detected.")

    def _cpa_vehicle(self, pdf: ReportPDF):
        pdf.add_page()
        pdf.section_title("Page 7: Vehicle and Transportation Costs")
        vehicle_cats = {
            "Fuel": Decimal("0"),
            "Vehicle Expense": Decimal("0"),
            "Equipment Maintenance": Decimal("0"),
            "Repairs and Maintenance": Decimal("0"),
            "Tolls and Scale Fees": Decimal("0"),
        }
        for s in self.statements:
            for tx in s.transactions:
                if tx.business_category in vehicle_cats and not tx.is_credit:
                    vehicle_cats[tx.business_category] += tx.amount

        total_vehicle = sum(vehicle_cats.values(), Decimal("0"))
        if total_vehicle > 0:
            rows = [[cat, fmt_dollar(val)] for cat, val in sorted(vehicle_cats.items())
                    if val > 0]
            rows.append(["Total Vehicle Costs", fmt_dollar(total_vehicle)])
            pdf.draw_table(["Category", "Amount"], rows, col_widths=[70, 40],
                           col_aligns=["L", "R"])
        else:
            pdf.body_text_small("No vehicle-related costs detected.")

        pdf.body_text_small(
            "IMPORTANT: Bank statements alone cannot determine business mileage, "
            "personal-use percentage, vehicle basis, depreciation basis, or "
            "standard-mileage eligibility. Mileage logs and vehicle purchase "
            "documents are required for accurate tax reporting."
        )

    def _cpa_payroll(self, pdf: ReportPDF):
        pdf.add_page()
        pdf.section_title("Page 8: Payroll and Contractor Review")
        pdf.body_text_small(
            "This section identifies transactions that may represent payroll or "
            "contractor payments. Worker classification and tax-reporting requirements "
            "must be verified. Do not assume any payee requires a 1099."
        )

        payroll_tx = [
            tx for s in self.statements for tx in s.transactions
            if not tx.is_credit and tx.business_category in
            ("Payroll", "Payroll Taxes", "Employee Benefits", "Contract Labor", "Subcontractors")
        ]
        headers = ["Date", "Description", "Category", "Amount", "Review Note"]
        rows = []
        for tx in sorted(payroll_tx, key=lambda tx: tx.amount, reverse=True)[:50]:
            note = ""
            if tx.business_category in ("Contract Labor", "Subcontractors"):
                note = "Verify worker classification / 1099 requirement"
            rows.append([
                tx.post_date,
                self._mask_text(tx.description)[:45],
                tx.business_category,
                fmt_dollar(tx.amount),
                note,
            ])
        cw = [20, 50, 25, 25, 50]
        if rows:
            pdf.draw_table(headers, rows, col_widths=cw,
                           col_aligns=["L", "L", "L", "R", "L"],
                           section_label="Payroll & Contractor")
        else:
            pdf.body_text_small("No payroll or contractor payments categorized.")

    def _cpa_loans(self, pdf: ReportPDF):
        pdf.add_page()
        pdf.section_title("Page 9: Loans, Interest, and Financing")
        loan_tx = [
            tx for s in self.statements for tx in s.transactions
            if tx.is_loan or "LOAN" in tx.description.upper() or
            tx.business_category in ("Loan Proceeds", "Loan Principal Payment", "Loan Interest",
                                     "Credit Card Payment")
        ]
        headers = ["Date", "Description", "Category", "Amount", "Type", "Note"]
        rows = []
        for tx in sorted(loan_tx, key=lambda tx: tx.post_date, reverse=True)[:50]:
            cat = tx.business_category
            if cat == "Loan Proceeds":
                typ = "Proceeds"
            elif cat == "Loan Principal Payment":
                typ = "Principal"
            elif cat == "Loan Interest":
                typ = "Interest"
            elif tx.is_credit:
                typ = "Proceeds"
            else:
                typ = "Payment"
            note = "May include interest - verify with loan statement"
            rows.append([
                tx.post_date,
                self._mask_text(tx.description)[:45],
                tx.business_category,
                fmt_dollar(tx.amount),
                typ,
                note,
            ])
        cw = [20, 50, 25, 25, 20, 40]
        if rows:
            pdf.draw_table(headers, rows, col_widths=cw,
                           col_aligns=["L", "L", "L", "R", "L", "L"],
                           section_label="Loan Activity")
            pdf.body_text_small(
                "WARNING: Never classify an entire loan payment as an expense "
                "when principal and interest cannot be separated. Obtain loan "
                "statements to verify principal/interest breakdowns."
            )
        else:
            pdf.body_text_small("No loan-related transactions detected.")

    def _cpa_owner_activity(self, pdf: ReportPDF):
        pdf.add_page()
        pdf.section_title("Page 10: Owner and Related-Party Activity")
        owner_tx = [
            tx for s in self.statements for tx in s.transactions
            if tx.is_owner_related or
            tx.business_category in ("Owner Contribution", "Owner Draw or Distribution")
        ]
        headers = ["Date", "Description", "Category", "Amount", "Type"]
        rows = []
        for tx in sorted(owner_tx, key=lambda tx: tx.post_date, reverse=True)[:50]:
            typ = "Contribution" if tx.is_credit else "Draw/Distribution"
            rows.append([
                tx.post_date,
                self._mask_text(tx.description)[:50],
                tx.business_category,
                fmt_dollar(tx.amount),
                typ,
            ])
        cw = [20, 55, 30, 30, 30]
        if rows:
            pdf.draw_table(headers, rows, col_widths=cw,
                           col_aligns=["L", "L", "L", "R", "L"],
                           section_label="Owner Activity")
        else:
            pdf.body_text_small("No owner-related transactions detected.")
        pdf.body_text_small(
            "Note: Business expenses paid personally and business revenues "
            "deposited into personal accounts are not captured from bank "
            "statements alone. Review needed."
        )

    def _cpa_taxes_govt(self, pdf: ReportPDF):
        pdf.add_page()
        pdf.section_title("Page 11: Taxes and Government Payments")
        tax_tx = [
            tx for s in self.statements for tx in s.transactions
            if tx.business_category in ("Tax Payment", "Taxes and Fees",
                                        "Payroll Taxes", "Licenses and Permits")
            or "TAX" in tx.description.upper()
        ]
        headers = ["Date", "Description", "Category", "Amount"]
        rows = []
        for tx in sorted(tax_tx, key=lambda tx: tx.post_date, reverse=True)[:50]:
            rows.append([
                tx.post_date,
                self._mask_text(tx.description)[:60],
                tx.business_category,
                fmt_dollar(tx.amount),
            ])
        cw = [22, 70, 30, 30]
        if rows:
            pdf.draw_table(headers, rows, col_widths=cw,
                           col_aligns=["L", "L", "L", "R"],
                           section_label="Tax & Government Payments")
            pdf.body_text_small(
                "IMPORTANT: Owner income-tax payments should NOT be automatically "
                "treated as business operating expenses. Verify each payment."
            )
        else:
            pdf.body_text_small("No tax or government payments detected.")

    def _cpa_uncategorized(self, pdf: ReportPDF):
        pdf.add_page()
        pdf.section_title("Page 12: Uncategorized and Review Transactions")
        review_tx = [
            tx for s in self.statements for tx in s.transactions
            if tx.cpa_review or tx.business_category in ("Uncategorized", "CPA Review Required")
        ]
        if not review_tx:
            pdf.body_text("All transactions categorized. No CPA review items.")
            return

        pdf.body_text_small(
            f"{len(review_tx)} transaction(s) require CPA review. "
            "Do not truncate - all are listed below."
        )
        headers = ["Date", "Description", "Amount", "Type", "Review Reason"]
        rows = []
        for tx in sorted(review_tx, key=lambda tx: tx.post_date, reverse=True):
            rows.append([
                tx.post_date,
                self._mask_text(tx.description)[:55],
                fmt_dollar(tx.amount),
                "Credit" if tx.is_credit else "Debit",
                tx.review_reason or "Uncategorized",
            ])
        cw = [20, 65, 25, 16, 50]
        pdf.draw_table(headers, rows, col_widths=cw,
                       col_aligns=["L", "L", "R", "L", "L"],
                       section_label="CPA Review Transactions",
                       header_font_size=6, row_font_size=5.5)

    def _cpa_reconciliation(self, pdf: ReportPDF):
        pdf.add_page()
        pdf.section_title("Page 13: Reconciliation and Data Exceptions")
        headers = ["Item", "Status", "Detail"]
        rows = []
        all_passed = True
        for rr in self.recon_results:
            if not rr.passed:
                all_passed = False
                for w in rr.warnings:
                    rows.append([rr.statement_label, "FAIL", w])
            else:
                rows.append([rr.statement_label, "PASS", "All counts and balances match"])

        # Check for missing months
        if len(self.statements) >= 2:
            s_dates = sorted(s.date_obj for s in self.statements)
            for i in range(1, len(s_dates)):
                prev = s_dates[i - 1]
                curr = s_dates[i]
                expected = date(prev.year, prev.month, 1) + timedelta(days=32)
                expected = date(expected.year, expected.month, 1)
                if date(curr.year, curr.month, 1) != expected:
                    rows.append([
                        f"Gap: {prev.strftime('%b %Y')} -> {curr.strftime('%b %Y')}",
                        "GAP",
                        "Missing statement(s) detected",
                    ])

        cw = [45, 14, 110]
        pdf.draw_table(headers, rows, col_widths=cw,
                       col_aligns=["L", "C", "L"],
                       section_label="Reconciliation Detail")

        # Summary of assumptions
        pdf.sub_title("Key Assumptions")
        pdf.body_text_small(
            "- All deposits from unknown sources marked for CPA review.\n"
            "- Cash-basis reporting; no accounts receivable or payable recognized.\n"
            "- Transfers between accounts excluded from P&L.\n"
            "- Owner contributions and draws not included in P&L.\n"
            "- Loan principal payments not included as expenses.\n"
            "- Fixed-asset purchases not expensed.\n"
            "- Categories assigned by pattern matching; manual review recommended."
        )

    def _cpa_questions(self, pdf: ReportPDF):
        pdf.add_page()
        pdf.section_title("Page 14: Questions for the Business Owner and CPA")
        questions = []
        all_tx = [tx for s in self.statements for tx in s.transactions]

        cpa_review_tx = [tx for tx in all_tx if tx.cpa_review]
        uncat_deposits = sum(1 for tx in cpa_review_tx if tx.is_credit)
        uncat_debits = sum(1 for tx in cpa_review_tx if not tx.is_credit)
        uncat_transfers = sum(1 for tx in cpa_review_tx if tx.is_transfer)
        uncat_loans = sum(1 for tx in cpa_review_tx if tx.is_loan)

        if uncat_deposits > 0:
            questions.append(f"Which of the {uncat_deposits} unidentified deposits are loans, transfers, contributions, or revenue?")
        if uncat_debits > 0:
            questions.append(f"Which of the {uncat_debits} uncategorized expense transactions need reclassification?")
        if uncat_transfers > 0:
            questions.append(f"Confirm the {uncat_transfers} transactions flagged as transfers requiring confirmation.")
        if uncat_loans > 0:
            questions.append(f"Verify the {uncat_loans} transactions flagged as loan-related requiring confirmation.")

        large_purchases = len(get_fixed_asset_candidates(self.statements))
        if large_purchases > 0:
            questions.append(f"Which of the {large_purchases} purchases over $500 were fixed assets?")

        questions.extend([
            "Were any business expenses paid from personal accounts?",
            "Were any business revenues deposited into other accounts?",
            "Are mileage logs available for vehicle deductions?",
            "Are payroll reports and quarterly filings available?",
            "Are contractor W-9 forms available for 1099 preparation?",
            "Are business credit-card statements available?",
            "Are loan statements available for interest/principal breakdown?",
            "Are sales-tax records and filings available?",
            "Are there omitted accounts receivable or payable?",
            "Were any estimated tax payments made outside this account?",
            "Are there home-office or other deductible personal expenses?",
        ])

        for i, q in enumerate(questions, 1):
            pdf.set_font("DJV", "", 9)
            pdf.set_text_color(50, 50, 50)
            pdf.cell(10, 6, f"{i}.", align="R")
            pdf.multi_cell(0, 6, q)
            pdf.ln(2)

    def _cpa_document_checklist(self, pdf: ReportPDF):
        pdf.add_page()
        pdf.section_title("Page 15: Document Checklist")
        checklist = self.config.document_checklist or {
            "bank_statements": "not-provided",
            "credit_card_statements": "not-provided",
            "loan_statements": "not-provided",
            "payroll_reports": "not-provided",
            "quarterly_payroll_filings": "not-provided",
            "forms_w2": "not-provided",
            "forms_1099": "not-provided",
            "contractor_w9": "not-provided",
            "sales_tax_filings": "not-provided",
            "prior_year_tax_return": "not-provided",
            "fixed_asset_docs": "not-provided",
            "vehicle_docs": "not-provided",
            "mileage_logs": "not-provided",
            "insurance_statements": "not-provided",
            "merchant_processor_reports": "not-provided",
            "ar_records": "not-provided",
            "ap_records": "not-provided",
            "inventory_records": "not-provided",
            "owner_contribution_records": "not-provided",
            "owner_distribution_records": "not-provided",
            "estimated_tax_confirmations": "not-provided",
            "business_license": "not-provided",
            "home_office_records": "not-provided",
            "health_insurance_docs": "not-provided",
        }

        labels = {
            "bank_statements": "Bank Statements",
            "credit_card_statements": "Business Credit-Card Statements",
            "loan_statements": "Loan Statements",
            "payroll_reports": "Payroll Reports",
            "quarterly_payroll_filings": "Quarterly Payroll Filings",
            "forms_w2": "Forms W-2",
            "forms_1099": "Forms 1099",
            "contractor_w9": "Contractor W-9 Forms",
            "sales_tax_filings": "Sales-Tax Filings",
            "prior_year_tax_return": "Prior-Year Tax Return",
            "fixed_asset_docs": "Fixed-Asset Purchase Documents",
            "vehicle_docs": "Vehicle Purchase/Lease Documents",
            "mileage_logs": "Mileage Logs",
            "insurance_statements": "Insurance Statements",
            "merchant_processor_reports": "Merchant-Processor Reports",
            "ar_records": "Accounts-Receivable Records",
            "ap_records": "Accounts-Payable Records",
            "inventory_records": "Inventory Records",
            "owner_contribution_records": "Owner Contribution Records",
            "owner_distribution_records": "Owner Distribution Records",
            "estimated_tax_confirmations": "Estimated-Tax Payment Confirmations",
            "business_license": "Business License Records",
            "home_office_records": "Home-Office Records",
            "health_insurance_docs": "Health Insurance Documentation",
        }

        status_labels = {
            "provided": "[x] Provided",
            "not-provided": "[ ] Not Provided",
            "partial": "[~] Partial",
        }

        pdf.sub_title("Required Documents Status")
        headers = ["Document", "Status"]
        rows = []
        for key, label in labels.items():
            status_raw = checklist.get(key, "not-provided")
            status_display = status_labels.get(status_raw, f"[ ] {status_raw}")
            rows.append([label, status_display])
        cw = [120, 45]
        pdf.draw_table(headers, rows, col_widths=cw,
                       col_aligns=["L", "L"],
                       section_label="Document Checklist")

    def _cpa_certification(self, pdf: ReportPDF):
        pdf.add_page()
        pdf.section_title("Owner Certification and Notes")
        pdf.body_text_small(
            "This page acknowledges review of the report, not certification of tax treatment. "
            "All classifications, deductions, and conclusions must be verified independently."
        )

        pdf.ln(8)
        fields = [
            ("Prepared For:", self.config.display_name()),
            ("Prepared By:", self.config.cpa_name or "__________"),
            ("CPA Firm:", self.config.cpa_firm or "__________"),
            ("Date Reviewed:", "__________"),
        ]
        for label, val in fields:
            pdf.set_font("DJV", "B", 9)
            pdf.set_text_color(50, 50, 50)
            pdf.cell(35, 8, label)
            pdf.set_font("DJV", "", 9)
            pdf.cell(80, 8, val, new_x="LMARGIN", new_y="NEXT")

        pdf.ln(6)
        pdf.sub_title("Business Owner Notes")
        pdf.set_draw_color(150, 150, 150)
        for _ in range(6):
            pdf.cell(0, 8, "", border="B", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(4)

        pdf.sub_title("CPA Notes")
        for _ in range(6):
            pdf.cell(0, 8, "", border="B", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(4)

        pdf.sub_title("Requested Corrections")
        for _ in range(4):
            pdf.cell(0, 8, "", border="B", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(6)

        pdf.set_font("DJV", "", 9)
        pdf.set_text_color(50, 50, 50)
        pdf.cell(0, 8, "Business Owner Signature: ________________________  Date: ________",
                 new_x="LMARGIN", new_y="NEXT")
        pdf.cell(0, 8, "Preparer Signature:    ________________________  Date: ________",
                 new_x="LMARGIN", new_y="NEXT")
        pdf.ln(4)
        pdf.body_text_small(
            "By signing, the parties acknowledge review of the report contents. "
            "Signatures do not certify tax treatment or constitute a filed return."
        )


# =============================================================================
# CSV Exports
# =============================================================================


_FIXED_ASSET_EXCLUDED_CATS: set[str] = {
    "Payroll", "Payroll Taxes", "Bank and Merchant Fees",
    "Business Insurance", "Rent or Lease",
    "Software and Cloud Services", "Telephone and Internet",
    "Account Transfer", "Credit Card Payment", "Loan Proceeds",
    "Loan Principal Payment", "Owner Contribution",
    "Owner Draw or Distribution",
}


def get_fixed_asset_candidates(
    statements: list[Statement],
    min_amount: Decimal = Decimal("500"),
) -> list[Transaction]:
    """Return transactions that are fixed-asset candidates.

    Excludes payroll, insurance, loan payments, transfers, and returned items.
    This is the single source of truth used by both the PDF and CSV exporters.
    """
    return [
        tx for s in statements for tx in s.transactions
        if not tx.is_credit
        and tx.amount >= min_amount
        and not tx.is_transfer
        and not tx.is_loan
        and not tx.is_owner_related
        and tx.business_category not in _FIXED_ASSET_EXCLUDED_CATS
        and "RETURNED ITEM" not in tx.description.upper()
    ]


class CSVExporter:
    """Exports financial data to CSV files."""

    def __init__(self, statements: list[Statement], config: BusinessConfig,
                 pl: ProfitAndLoss, projections: list[ProjectionResult] | None = None,
                 recon_results: list[ReconciliationResult] | None = None):
        self.statements = statements
        self.config = config
        self.pl = pl
        self.projections = projections or []
        self.recon_results = recon_results or []

    def export_audit(self, path: Path, mask_personal: bool = False):
        """Export full transaction audit CSV."""
        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "Statement", "PostDate", "Description", "Amount", "Type", "Balance",
                "BusinessCategory", "TaxCategory", "P&LIncluded", "Deductibility",
                "CPAReview", "ReviewReason", "Merchant", "IsTransfer",
                "IsOwnerRelated", "IsFixedAsset", "IsLoan", "SourceStatement",
            ])
            for stmt in self.statements:
                for tx in stmt.transactions:
                    desc = tx.description
                    if mask_personal:
                        desc = re.sub(
                            r"\b\d+\s+(?:[NSEW]\s+)?[A-Z0-9.'-]+(?:\s+[A-Z0-9.'-]+){0,4}\s+(?:RD|ROAD|ST|STREET|AVE|AVENUE|DR|DRIVE|LN|LANE|WAY|BLVD|BOULEVARD)\b",
                            "[ADDRESS REDACTED]", desc, flags=re.IGNORECASE,
                        )
                    writer.writerow([
                        stmt.month_label,
                        tx.post_date,
                        desc,
                        str(tx.amount if tx.is_credit else -tx.amount),
                        "Credit" if tx.is_credit else "Debit",
                        str(tx.balance),
                        tx.business_category,
                        tx.tax_category,
                        "Yes" if tx.include_in_pnl else "No",
                        tx.deductibility,
                        "Yes" if tx.cpa_review else "No",
                        tx.review_reason,
                        tx.merchant or normalize_merchant(tx.description),
                        "Yes" if tx.is_transfer else "No",
                        "Yes" if tx.is_owner_related else "No",
                        "Yes" if tx.is_fixed_asset else "No",
                        "Yes" if tx.is_loan else "No",
                        tx.source_statement,
                    ])
        logger.info("Audit CSV saved to: %s", path)

    def export_pl(self, path: Path):
        """Export P&L CSV."""
        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["Category", "Type", "Amount"])
            for cat, val in sorted(self.pl.revenue.items()):
                writer.writerow([cat, "Revenue", str(val)])
            for cat, val in sorted(self.pl.direct_costs.items()):
                writer.writerow([cat, "COGS/Direct Cost", str(val)])
            for cat, val in sorted(self.pl.operating_expenses.items()):
                writer.writerow([cat, "Operating Expense", str(val)])
            writer.writerow(["Total Revenue", "Revenue", str(self.pl.total_revenue)])
            writer.writerow(["Total COGS", "COGS", str(self.pl.total_direct_costs)])
            writer.writerow(["Gross Profit", "Profit", str(self.pl.gross_profit)])
            writer.writerow(["Total OpEx", "Expense", str(self.pl.total_operating_expenses)])
            writer.writerow(["Operating Profit", "Profit", str(self.pl.operating_profit)])
            writer.writerow(["Net Profit", "Profit", str(self.pl.net_profit)])
        logger.info("P&L CSV saved to: %s", path)

    def export_cpa(self, output_dir: Path):
        """Export CPA package CSV files."""
        output_dir.mkdir(parents=True, exist_ok=True)

        # Revenue detail
        self._write_csv(output_dir / "cpa_revenue_detail.csv",
                        ["Date", "Description", "Category", "Amount"],
                        [[tx.post_date, tx.description, tx.business_category, str(tx.amount)]
                         for s in self.statements for tx in s.transactions
                         if tx.is_credit and tx.include_in_pnl])

        # Expense detail
        self._write_csv(output_dir / "cpa_expense_detail.csv",
                        ["Date", "Description", "Category", "TaxCategory", "Amount", "Deductibility", "CPAReview"],
                        [[tx.post_date, tx.description, tx.business_category, tx.tax_category,
                          str(tx.amount), tx.deductibility, "Yes" if tx.cpa_review else "No"]
                         for s in self.statements for tx in s.transactions
                         if not tx.is_credit and tx.include_in_pnl])

        # Fixed asset candidates (uses shared logic with PDF)
        self._write_csv(output_dir / "cpa_fixed_assets.csv",
                        ["Date", "Vendor", "Description", "Amount"],
                        [[tx.post_date, normalize_merchant(tx.description), tx.description, str(tx.amount)]
                         for tx in get_fixed_asset_candidates(self.statements)])

        # Loan activity
        self._write_csv(output_dir / "cpa_loan_activity.csv",
                        ["Date", "Description", "Category", "Amount", "Type"],
                        [[tx.post_date, tx.description, tx.business_category,
                          str(tx.amount), "Credit" if tx.is_credit else "Debit"]
                         for s in self.statements for tx in s.transactions
                         if tx.is_loan or "LOAN" in tx.description.upper()])

        # Owner activity
        self._write_csv(output_dir / "cpa_owner_activity.csv",
                        ["Date", "Description", "Category", "Amount", "Type"],
                        [[tx.post_date, tx.description, tx.business_category,
                          str(tx.amount), "Contribution" if tx.is_credit else "Draw"]
                         for s in self.statements for tx in s.transactions
                         if tx.is_owner_related or tx.business_category in ("Owner Contribution", "Owner Draw or Distribution")])

        # Uncategorized / CPA review
        self._write_csv(output_dir / "cpa_uncategorized.csv",
                        ["Date", "Description", "Amount", "Type", "ReviewReason"],
                        [[tx.post_date, tx.description, str(tx.amount),
                          "Credit" if tx.is_credit else "Debit", tx.review_reason]
                         for s in self.statements for tx in s.transactions
                         if tx.cpa_review])

        # Reconciliation
        recon_headers = [
            "Statement", "Status", "CreditsParsed", "CreditsExpected",
            "DebitsParsed", "DebitsExpected", "CreditTotalParsed",
            "CreditTotalExpected", "DebitTotalParsed", "DebitTotalExpected",
            "BeginningBalance", "EndingBalance", "CalculatedEnding",
            "BalanceOK", "Warnings",
        ]
        recon_rows_csv: list[list[str]] = []
        if self.recon_results:
            for rr in self.recon_results:
                status = "PASS" if rr.passed else ("FORCED" if rr.forced else "FAIL")
                recon_rows_csv.append([
                    rr.statement_label,
                    status,
                    str(rr.parsed_credit_count),
                    str(rr.expected_credit_count),
                    str(rr.parsed_debit_count),
                    str(rr.expected_debit_count),
                    str(rr.parsed_credit_total),
                    str(rr.expected_credit_total),
                    str(rr.parsed_debit_total),
                    str(rr.expected_debit_total),
                    str(rr.beginning_balance),
                    str(rr.ending_balance),
                    str(rr.calculated_ending),
                    "Yes" if rr.balance_ok else "No",
                    "; ".join(rr.warnings),
                ])
        else:
            recon_rows_csv = [[f"{s.month_label}", "N/A"] + [""] * (len(recon_headers) - 2)
                              for s in self.statements]
        self._write_csv(output_dir / "cpa_reconciliation.csv",
                        recon_headers, recon_rows_csv)

        logger.info("CPA CSV files saved to: %s", output_dir)

    def _write_csv(self, path: Path, headers: list[str], rows: list[list[str]]):
        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            for row in rows:
                writer.writerow(row)

    def export_category_template(self, path: Path):
        """Export a CSV with all detected merchants and their current categories."""
        merchants: dict[str, dict] = {}
        for s in self.statements:
            for tx in s.transactions:
                m = normalize_merchant(tx.description)
                if m not in merchants:
                    merchants[m] = {
                        "category": tx.business_category,
                        "tax_category": tx.tax_category,
                        "count": 0,
                        "total": Decimal("0"),
                    }
                merchants[m]["count"] += 1
                merchants[m]["total"] += tx.amount

        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["Merchant", "CurrentBusinessCategory", "CurrentTaxCategory",
                             "TransactionCount", "TotalAmount",
                             "SuggestedBusinessCategory", "SuggestedTaxCategory"])
            for m, info in sorted(merchants.items(), key=lambda x: x[1]["total"], reverse=True):
                writer.writerow([m, info["category"], info["tax_category"],
                                 str(info["count"]), str(info["total"]), "", ""])
        logger.info("Category template saved to: %s", path)


# =============================================================================
# Configuration Loading
# =============================================================================


def load_config(config_path: Path, was_explicit: bool = False) -> BusinessConfig:
    """Load business configuration from a TOML file.

    If *was_explicit* is True (user supplied --config), a missing file is fatal.
    Otherwise silently returns defaults.
    """
    config = BusinessConfig()

    if config_path.exists():
        logger.info("Configuration loaded: %s", config_path.resolve())
        with open(config_path, "rb") as f:
            data = tomllib.load(f)

        general = data.get("general", {})
        config.business_name = general.get("business_name", config.business_name)
        config.dba = general.get("dba", config.dba)
        config.address = general.get("address", config.address)
        config.phone = general.get("phone", config.phone)
        config.email = general.get("email", config.email)
        config.tax_year = general.get("tax_year", config.tax_year)
        config.fiscal_year_start = general.get("fiscal_year_start", config.fiscal_year_start)
        config.entity_type = general.get("entity_type", config.entity_type)
        if config.entity_type not in VALID_ENTITY_TYPES:
            logger.warning("Unknown entity_type: %s", config.entity_type)
        config.accounting_method = general.get("accounting_method", config.accounting_method)
        config.ein_display = general.get("ein_display", config.ein_display)
        config.mask_ein = general.get("mask_ein", config.mask_ein)
        config.bank_account_display = general.get("bank_account_display", config.bank_account_display)
        config.mask_account = general.get("mask_account", config.mask_account)
        config.industry = general.get("industry", config.industry)
        config.currency = general.get("currency", config.currency)

        cpa = data.get("cpa", {})
        config.cpa_name = cpa.get("name", config.cpa_name)
        config.cpa_firm = cpa.get("firm", config.cpa_firm)
        config.cpa_email = cpa.get("email", config.cpa_email)
        config.cpa_phone = cpa.get("phone", config.cpa_phone)

        owner = data.get("owner", {})
        config.owners = owner.get("owners", config.owners)

        config.projection_config = data.get("projections", {})

        # Load custom rules
        for rule_data in data.get("rules", []):
            cat = rule_data.get("category", "Uncategorized")
            tax_cat = rule_data.get("tax_category", "")
            # Derive safe defaults from category/tax names
            is_non_pl = tax_cat == "non-pl" or cat in (
                "Account Transfer", "Credit Card Payment",
                "Loan Proceeds", "Loan Principal Payment",
                "Owner Contribution", "Owner Draw or Distribution",
                "Fixed Asset Purchase", "Tax Payment",
                "Refund", "Reimbursement", "Opening Balance",
                "Uncategorized", "CPA Review Required",
            )
            include_pl = rule_data.get("include_in_pnl", not is_non_pl)
            try:
                re.compile(rule_data.get("pattern", ""))
            except re.error as exc:
                logger.error(
                    "Invalid regex pattern in custom rule '%s': %s",
                    rule_data.get("category", "?"), exc,
                )
                sys.exit(1)

            config.custom_rules.append(CategoryRule(
                pattern=rule_data.get("pattern", ""),
                category=cat,
                tax_category=tax_cat or "CPA Review",
                deductibility=rule_data.get("deductibility", "unknown"),
                is_income=rule_data.get("is_income", False),
                include_in_pnl=include_pl,
                is_transfer=rule_data.get("is_transfer", cat in ("Account Transfer", "Credit Card Payment")),
                is_owner_related=rule_data.get("is_owner_related", cat in ("Owner Contribution", "Owner Draw or Distribution")),
                is_fixed_asset=rule_data.get("is_fixed_asset", cat == "Fixed Asset Purchase"),
                is_loan=rule_data.get("is_loan", cat in ("Loan Proceeds", "Loan Principal Payment", "Loan Interest")),
                direction=rule_data.get("direction", "either"),
                priority=rule_data.get("priority", 0),  # custom rules default higher priority than defaults
            ))

        # Balances
        balances = data.get("balances", {})
        for key in balances:
            config.beginning_balances[key] = Decimal(str(balances[key]))

        config.fixed_assets = data.get("fixed_assets", [])
        config.loans = data.get("loans", [])
        config.owner_activities = data.get("owner_activity", [])
        config.document_checklist = data.get("document_checklist", {})

        logger.info("Business: %s", config.display_name())
        logger.info("Entity type: %s", config.entity_type)
    elif was_explicit:
        logger.error(
            "Configuration file not found: %s (use --init-config to create one)",
            config_path.resolve(),
        )
        sys.exit(1)

    return config


def generate_example_config(path: Path, force: bool = False) -> bool:
    """Generate example TOML configuration file."""
    if path.exists() and not force:
        logger.error("Config file already exists: %s (use --force to overwrite)", path)
        return False
    path.write_text(EXAMPLE_TOML)
    logger.info("Example config written to: %s", path)
    return True


# =============================================================================
# Main Control Flow
# =============================================================================


def build_report(
    statements: list[Statement],
    config: BusinessConfig,
    output_path: Path,
    mode: str = "combined",
    target_month: int | None = None,
    target_year: int | None = None,
    target_quarter: int | None = None,
    start_date_str: str | None = None,
    end_date_str: str | None = None,
    audit_path: Path | None = None,
    pl_csv_path: Path | None = None,
    cpa_export_dir: Path | None = None,
    category_template_path: Path | None = None,
    mask_personal: bool = False,
    allow_mismatch: bool = False,
    scenario: str = "base",
    strict: bool = False,
    full_detail: bool = False,
    do_projections: bool = False,
    allow_review_items: bool = False,
) -> None:
    """Orchestrate the full report build process."""

    # Filter statements by year/month/quarter
    if target_year:
        statements = [s for s in statements if s.year == target_year]
    if target_month:
        statements = [s for s in statements if s.month == target_month]
    if target_quarter:
        # Fiscal-year-aware quarter filtering
        fys = config.fiscal_year_start
        fy_quarter_months = _fiscal_quarter_months(target_quarter, fys)
        statements = [s for s in statements if s.month in fy_quarter_months]

    if not statements:
        logger.error("No statements found matching filters.")
        sys.exit(1)

    statements.sort(key=lambda s: (s.year, s.month))

    # Date-range filtering applies to individual transactions, not
    # statement-ending dates.  Full statements are reconciled; the
    # P&L is built from only the transactions that fall within the
    # requested range.
    report_statements = statements
    if start_date_str or end_date_str:
        sd = (datetime.strptime(start_date_str, "%Y-%m-%d").date()
              if start_date_str else None)
        ed = (datetime.strptime(end_date_str, "%Y-%m-%d").date()
              if end_date_str else None)
        filtered_stmts: list[Statement] = []
        for stmt in statements:
            filtered_tx = [
                tx for tx in stmt.transactions
                if (sd is None or tx.date_obj >= sd)
                and (ed is None or tx.date_obj <= ed)
            ]
            if filtered_tx:
                period_credits = sum(
                    (tx.amount for tx in filtered_tx if tx.is_credit),
                    Decimal("0"),
                )
                period_debits = sum(
                    (tx.amount for tx in filtered_tx if not tx.is_credit),
                    Decimal("0"),
                )
                period_credit_count = sum(1 for tx in filtered_tx if tx.is_credit)
                period_debit_count = sum(1 for tx in filtered_tx if not tx.is_credit)
                # Normalize chronologically in case source has newest-first ordering
                sorted_tx = sorted(
                    filtered_tx,
                    key=lambda tx: (tx.date_obj, tx.sequence),
                )
                first_tx = sorted_tx[0]
                last_tx = sorted_tx[-1]
                period_start_balance = first_tx.balance - first_tx.signed_amount
                period_end_balance = last_tx.balance
                # Filter daily balances to the period range
                period_daily: list[dict] = [
                    db for db in stmt.daily_balances
                    if (sd is None or _parse_daily_date(db.get("date", "")) >= sd)
                    and (ed is None or _parse_daily_date(db.get("date", "")) <= ed)
                ]
                filtered_stmts.append(Statement(
                    statement_date=stmt.statement_date,
                    account_number=stmt.account_number,
                    beginning_balance=period_start_balance,
                    ending_balance=period_end_balance,
                    total_credits=period_credits,
                    total_debits=period_debits,
                    credit_count=period_credit_count,
                    debit_count=period_debit_count,
                    transactions=sorted_tx,
                    checks_cleared=stmt.checks_cleared,
                    daily_balances=period_daily,
                    file_path=stmt.file_path,
                ))
        if not filtered_stmts:
            logger.error(
                "No transactions found between %s and %s.",
                start_date_str or "the beginning",
                end_date_str or "the end",
            )
            sys.exit(1)
        report_statements = filtered_stmts

    # Reconcile against the FULL statements (not the date-filtered subset)
    # so that the reconciliation report is accurate.

    # Deduplicate by file hash (handled during loading)

    # Setup categorizer
    categorizer = TransactionCategorizer(config.custom_rules)
    categorizer.categorize_all(statements)
    categorizer.mark_credit_deposits(statements)

    # Reconcile
    recon_results, all_reconciled, forced_generation = reconcile_all(
        statements, allow_mismatch=allow_mismatch,
    )
    if forced_generation:
        logger.warning(
            "Reconciliation FAILED for %d statement(s) — report generation forced by --allow-mismatch. "
            "Financial totals are NOT validated.",
            sum(1 for r in recon_results if not r.passed),
        )
    elif not all_reconciled and not allow_mismatch:
        logger.error(
            "Reconciliation failed. Use --allow-mismatch to force report generation."
        )
        for rr in recon_results:
            if not rr.passed:
                for w in rr.warnings:
                    logger.error("  %s: %s", rr.statement_label, w)
        sys.exit(1)

    # Build P&L from the period-filtered transactions (report_statements),
    # while reconciliation used the full statements.
    pl = build_pl(
        [tx for s in report_statements for tx in s.transactions],
        label=f"Cash-Basis P&L - {config.display_name()}",
    )
    monthly_pls = build_monthly_pls(report_statements)
    quarterly_pls = build_quarterly_pls(report_statements, config.fiscal_year_start)
    kpis = calculate_kpis(pl, len(report_statements), report_statements)

    # Projections
    projections = None
    projection_status = "not_requested"  # "requested", "withheld", "produced"
    if do_projections:
        projection_status = "requested"
        cpa_review_count = sum(
            1 for s in report_statements for tx in s.transactions if tx.cpa_review
        )
        total_rev = sum((monthly_pls[k].total_revenue for k in sorted(monthly_pls.keys())), Decimal("0"))
        if total_rev == 0 or cpa_review_count > len(report_statements) * 5:
            projection_status = "withheld"
            logger.info(
                "Projections withheld: $%.2f classified revenue, %d transactions unclassified.",
                total_rev, cpa_review_count,
            )
        else:
            pconfig = config.projection_config or {}
            engine = ProjectionEngine(pconfig)
            hist_rev = [monthly_pls[k].total_revenue for k in sorted(monthly_pls.keys())]
            hist_exp = [
                monthly_pls[k].total_operating_expenses
                for k in sorted(monthly_pls.keys())
            ]
            starting_cash = report_statements[-1].ending_balance if report_statements else Decimal("0")

            # Derive projection start from the last reported statement
            last_stmt = report_statements[-1] if report_statements else None
            if last_stmt:
                last_dt = last_stmt.date_obj
                proj_start = date(last_dt.year, last_dt.month, 1) + timedelta(days=32)
                proj_start = date(proj_start.year, proj_start.month, 1)
            else:
                proj_start = None

            if scenario and scenario != "all":
                projections = [engine.project_selected(
                    hist_rev, hist_exp, starting_cash, scenario, proj_start,
                )]
            else:
                projections = engine.project_all_scenarios(
                    hist_rev, hist_exp, starting_cash, proj_start,
                )
            projection_status = "produced"

    if strict:
        review_count = sum(
            1 for s in report_statements for tx in s.transactions
            if tx.cpa_review
        )
        if review_count and not allow_review_items:
            logger.error(
                "Strict mode: %d transactions require CPA review. "
                "Use --allow-review-items to generate a preliminary report.",
                review_count,
            )
            sys.exit(1)

    # Build report using the filtered transaction set
    period_start_date = (datetime.strptime(start_date_str, "%Y-%m-%d").date()
                         if start_date_str else None)
    period_end_date = (datetime.strptime(end_date_str, "%Y-%m-%d").date()
                       if end_date_str else None)
    builder = ReportBuilder(
        statements=report_statements,
        config=config,
        categorizer=categorizer,
        pl=pl,
        monthly_pls=monthly_pls,
        quarterly_pls=quarterly_pls,
        kpis=kpis,
        recon_results=recon_results,
        all_reconciled=all_reconciled,
        forced_generation=forced_generation,
        projections=projections,
        mask_personal=mask_personal,
        full_detail=full_detail,
        projection_status=projection_status,
        period_start_date=period_start_date,
        period_end_date=period_end_date,
        mode=mode,
    )

    if mode == "cpa":
        pdf = ReportPDF(f"CPA Package - {config.display_name()}")
        builder._cpa_package(pdf)
        pdf.output(str(output_path))
    else:
        pdf = builder.build()
        pdf.output(str(output_path))

    logger.info("Report saved to: %s", output_path)

    # Exports
    exporter = CSVExporter(report_statements, config, pl, projections,
                           recon_results=recon_results)
    if audit_path:
        exporter.export_audit(audit_path, mask_personal)
    if pl_csv_path:
        exporter.export_pl(pl_csv_path)
    if cpa_export_dir:
        exporter.export_cpa(cpa_export_dir)
    if category_template_path:
        exporter.export_category_template(category_template_path)


# =============================================================================
# CLI
# =============================================================================


def main():
    parser = argparse.ArgumentParser(
        description="Business Financial Report Generator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 business_financial_report.py
  python3 business_financial_report.py --year 2026
  python3 business_financial_report.py --year 2026 --projections
  python3 business_financial_report.py --quarter 2 --year 2026
  python3 business_financial_report.py --mode cpa --year 2026
  python3 business_financial_report.py --audit --export-pl --export-cpa
  python3 business_financial_report.py --config lagoon_transport.toml
  python3 business_financial_report.py --init-config
  python3 business_financial_report.py --self-test
        """,
    )
    parser.add_argument("--config", type=str, default=None,
                        help=f"Path to TOML config (default: {_DEFAULT_CONFIG})")
    parser.add_argument("--init-config", action="store_true",
                        help="Generate example TOML configuration file")
    parser.add_argument("--force", action="store_true",
                        help="Overwrite existing config with --init-config")
    parser.add_argument("--business-name", type=str, default=None,
                        help="Override business name")
    parser.add_argument("--year", type=int,
                        help="Filter to a specific year")
    parser.add_argument("--month", type=int, choices=range(1, 13),
                        help="Filter to a specific month (1-12)")
    parser.add_argument("--quarter", type=int, choices=range(1, 5),
                        help="Filter to a specific quarter (1-4)")
    parser.add_argument("--start-date", type=str, default=None,
                        help="Filter from date YYYY-MM-DD")
    parser.add_argument("--end-date", type=str, default=None,
                        help="Filter to date YYYY-MM-DD")
    parser.add_argument("--mode", type=str,
                        choices=["combined", "monthly", "quarterly", "yearly", "cpa"],
                        default=None,
                        help="Report mode")
    parser.add_argument("-o", "--output", type=str, default=None,
                        help="Output PDF path")
    parser.add_argument("-d", "--directory", type=str, default=None,
                        help="Directory containing statement PDFs")
    parser.add_argument("--audit", action="store_true",
                        help="Export transaction audit CSV")
    parser.add_argument("--export-pl", action="store_true",
                        help="Export P&L CSV")
    parser.add_argument("--export-cpa", action="store_true",
                        help="Export CPA package CSV files")
    parser.add_argument("--export-category-template", action="store_true",
                        help="Export category template CSV for editing")
    parser.add_argument("--projections", action="store_true",
                        help="Include financial projections")
    parser.add_argument("--projection-months", type=int, default=None,
                        help="Number of months to project (overrides config)")
    parser.add_argument("--scenario", type=str,
                        choices=["conservative", "base", "growth", "all"],
                        default="all",
                        help="Projection scenario")
    parser.add_argument("--mask", action="store_true",
                        help="Mask personal information in output")
    parser.add_argument("--mask-ein", action="store_true",
                        help="Mask EIN in output")
    parser.add_argument("--allow-mismatch", action="store_true",
                        help="Generate report even if reconciliation fails")
    parser.add_argument("--allow-review-items", action="store_true",
                        help="Generate report even with uncategorized transactions")
    parser.add_argument("--strict", action="store_true",
                        help="Exit with error on any reconciliation warning")
    parser.add_argument("--verbose", action="store_true",
                        help="Verbose output")
    parser.add_argument("--overwrite", action="store_true",
                        help="Overwrite existing output files")
    parser.add_argument("--full-detail", action="store_true",
                        help="Include every transaction rather than 50 per month")
    parser.add_argument("--debug", action="store_true",
                        help="Debug output")
    parser.add_argument("--self-test", action="store_true",
                        help="Run built-in self-tests")
    args = parser.parse_args()

    log_level = logging.WARNING
    if args.verbose:
        log_level = logging.INFO
    if args.debug:
        log_level = logging.DEBUG
    logging.basicConfig(level=log_level, format="%(levelname)s: %(message)s")

    # Handle --self-test before anything else
    if args.self_test:
        run_self_tests()
        return

    # Handle --init-config
    if args.init_config:
        cfg_path = Path(args.config or _DEFAULT_CONFIG)
        if not cfg_path.is_absolute():
            cfg_path = SCRIPT_DIR / cfg_path
        generate_example_config(cfg_path, force=args.force)
        return

    # Check pdftotext
    check_pdftotext()

    # Determine config path — auto-detect business_report*.toml in script dir
    if args.config:
        config_path = Path(args.config)
    else:
        default_path = SCRIPT_DIR / _DEFAULT_CONFIG
        if default_path.exists():
            config_path = default_path
        else:
            # Look for any file matching business_report*.toml
            candidates = sorted(
                SCRIPT_DIR.glob("business_report*.toml"),
                key=lambda p: p.stat().st_mtime, reverse=True,
            )
            config_path = candidates[0] if candidates else default_path
    if not config_path.is_absolute():
        config_path = SCRIPT_DIR / config_path
    config = load_config(config_path, was_explicit=bool(args.config))

    # CLI overrides
    if args.business_name:
        config.business_name = args.business_name
    if args.year:
        config.tax_year = args.year
    if args.mask or args.mask_ein:
        config.mask_ein = True
    if args.projection_months:
        if not config.projection_config:
            config.projection_config = {}
        config.projection_config["projection_months"] = args.projection_months

    # Find PDFs
    directory = Path(args.directory) if args.directory else SCRIPT_DIR
    pdf_files = find_pdfs(directory)
    if not pdf_files:
        logger.error("No PDF files found in %s", directory)
        sys.exit(1)

    # Parse statements, deduplicating by file hash
    seen_hashes: set[str] = set()
    statements: list[Statement] = []
    for pdf_path in pdf_files:
        fhash = file_hash(pdf_path)
        if fhash in seen_hashes:
            logger.warning("Skipping duplicate file: %s", pdf_path)
            continue
        seen_hashes.add(fhash)

        try:
            text = extract_text(pdf_path)
            stmt = parse_statement(text, str(pdf_path))
            if stmt.transactions:
                statements.append(stmt)
            else:
                logger.warning("No transactions parsed from: %s", pdf_path)
        except subprocess.CalledProcessError as exc:
            logger.error("Failed to parse %s: %s", pdf_path, exc)
            continue

    if not statements:
        logger.error("No valid statements found.")
        sys.exit(1)

    # Determine mode
    if args.mode:
        mode = args.mode
    elif args.quarter:
        mode = "quarterly"
    elif args.month:
        mode = "monthly"
    elif args.year and not args.month and not args.quarter:
        mode = "combined"
    else:
        mode = "combined"

    # Incompatible option checks
    if args.month and args.quarter:
        logger.error("--month and --quarter are incompatible.")
        sys.exit(1)
    if args.mode in ("monthly",) and not args.month:
        logger.error("--mode monthly requires --month.")
        sys.exit(1)

    # Auto-detect year for --month without --year
    target_year = args.year
    if args.month and not target_year:
        matching_years = sorted({
            s.year for s in statements if s.month == args.month
        })
        if not matching_years:
            logger.error("No statements found for month %s.", args.month)
            sys.exit(1)
        target_year = matching_years[-1]
        if len(matching_years) > 1:
            logger.info(
                "Note: --month without --year, using latest year %s "
                "(found: %s). Use --year to override.",
                target_year, matching_years,
            )

    # Auto-detect year for --quarter without --year (fiscal-year-aware)
    if args.quarter and not target_year:
        fy_start = config.fiscal_year_start
        q_months = _fiscal_quarter_months(args.quarter, fy_start)
        matching_years = sorted({
            s.year for s in statements if s.month in q_months
        })
        if not matching_years:
            logger.error("No statements found for quarter %s.", args.quarter)
            sys.exit(1)
        target_year = matching_years[-1]
        if len(matching_years) > 1:
            logger.info(
                "Note: --quarter without --year, using latest year %s "
                "(found: %s). Use --year to override.",
                target_year, matching_years,
            )

    # Determine output filename
    if args.output:
        output = args.output
    elif args.month:
        yr = target_year or statements[0].year
        output = f"business_financial_report_{args.month:02d}_{yr}.pdf"
    elif args.quarter:
        yr = target_year or statements[0].year
        output = f"business_financial_report_Q{args.quarter}_{yr}.pdf"
    elif args.year:
        output = f"business_financial_report_{args.year}.pdf"
    else:
        output = "business_financial_report.pdf"

    output_path = Path(output)
    if not output_path.is_absolute():
        output_path = SCRIPT_DIR / output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Check for overwriting existing output
    if output_path.exists() and not args.overwrite:
        logger.error(
            "Output file already exists: %s (use --overwrite to replace)",
            output_path,
        )
        sys.exit(1)

    # Export paths
    audit_path = None
    pl_csv_path = None
    cpa_export_dir = None
    category_template_path = None

    base = output_path.stem
    out_dir = output_path.parent

    if args.audit:
        audit_path = out_dir / f"{base}_audit.csv"
    if args.export_pl:
        pl_csv_path = out_dir / f"{base}_pl.csv"
    if args.export_cpa:
        cpa_export_dir = out_dir / f"{base}_cpa"
    if args.export_category_template:
        category_template_path = out_dir / f"{base}_category_template.csv"

    # Build report
    build_report(
        statements=statements,
        config=config,
        output_path=output_path,
        mode=mode,
        target_month=args.month,
        target_year=target_year,
        target_quarter=args.quarter,
        start_date_str=args.start_date,
        end_date_str=args.end_date,
        audit_path=audit_path,
        pl_csv_path=pl_csv_path,
        cpa_export_dir=cpa_export_dir,
        category_template_path=category_template_path,
        mask_personal=args.mask,
        allow_mismatch=args.allow_mismatch,
        scenario=args.scenario,
        strict=args.strict,
        full_detail=args.full_detail,
        do_projections=args.projections,
        allow_review_items=args.allow_review_items,
    )


# =============================================================================
# Self-Tests
# =============================================================================


def run_self_tests():
    """Run built-in unit tests using synthetic data."""
    print("Running self-tests...")
    passed = 0
    failed = 0

    def check(label: str, condition: bool):
        nonlocal passed, failed
        if condition:
            passed += 1
        else:
            failed += 1
            print(f"  FAIL: {label}")

    # 1. Dollar parsing
    check("parse_amount basic", parse_amount("$1,234.56") == Decimal("1234.56"))
    check("parse_amount negative", parse_amount("-$500.00") == Decimal("-500.00"))
    check("parse_amount zero", parse_amount("$0.00") == Decimal("0"))

    # 2. Statement date parsing
    check("statement month", Statement("01/31/2023", "", Decimal("0"), Decimal("0"),
                                       Decimal("0"), Decimal("0"), 0, 0).month == 1)
    check("statement year", Statement("12/29/2023", "", Decimal("0"), Decimal("0"),
                                      Decimal("0"), Decimal("0"), 0, 0).year == 2023)
    check("statement quarter Q1", Statement("03/31/2023", "", Decimal("0"), Decimal("0"),
                                            Decimal("0"), Decimal("0"), 0, 0).quarter == 1)
    check("statement quarter Q4", Statement("12/29/2023", "", Decimal("0"), Decimal("0"),
                                            Decimal("0"), Decimal("0"), 0, 0).quarter == 4)

    # 3. Credit/debit detection in transactions
    credit_tx = Transaction("01/15/2023", "DEPOSIT", "DEPOSIT", Decimal("100.00"),
                            True, Decimal("500.00"))
    debit_tx = Transaction("01/16/2023", "PURCHASE", "PURCHASE", Decimal("50.00"),
                           False, Decimal("450.00"))
    check("credit signed_amount positive", credit_tx.signed_amount == Decimal("100.00"))
    check("debit signed_amount negative", debit_tx.signed_amount == Decimal("-50.00"))

    # 4. Category precedence - first match wins
    rules = [
        CategoryRule(pattern="PILOT", category="Fuel", tax_category="Vehicle Fuel",
                     include_in_pnl=True, priority=1),
        CategoryRule(pattern="PILOT TRAVEL", category="Travel", tax_category="Travel",
                     include_in_pnl=True, priority=2),
        CategoryRule(pattern="APEX CAPITAL", category="Service Revenue",
                     tax_category="Gross Receipts", is_income=True,
                     include_in_pnl=True, priority=3),
    ]
    categorizer = TransactionCategorizer(custom_rules=rules)
    tx = Transaction("01/15/2023", "PILOT TRAVEL CENTER", "PILOT TRAVEL CENTER",
                     Decimal("200.00"), False, Decimal("0"))
    categorizer.categorize(tx)
    check("category precedence first match", tx.business_category == "Fuel")

    # 5. Transfer exclusion from P&L
    tx_xfer = Transaction("01/15/2023", "WEB XFER TO SAVINGS", "WEB XFER TO SAVINGS",
                          Decimal("500.00"), False, Decimal("0"))
    categorizer_all = TransactionCategorizer()
    categorizer_all.categorize(tx_xfer)
    check("transfer excluded from P&L", not tx_xfer.include_in_pnl)
    check("transfer flagged", tx_xfer.is_transfer)

    # 6. Owner contribution exclusion
    tx_owner = Transaction("01/15/2023", "OWNER CONTRIBUTION", "OWNER CONTRIBUTION",
                           Decimal("1000.00"), True, Decimal("0"))
    # This would need a rule; test that the default rules handle transfers
    # Let's test with custom rule
    custom_rules = [
        CategoryRule(pattern="OWNER CONTRIBUTION", category="Owner Contribution",
                     tax_category="non-pl", is_owner_related=True, include_in_pnl=False, priority=1),
    ]
    cat2 = TransactionCategorizer(custom_rules=custom_rules)
    cat2.categorize(tx_owner)
    check("owner contribution excluded", not tx_owner.include_in_pnl)
    check("owner contribution flagged", tx_owner.is_owner_related)

    # 7. Fixed asset exclusion
    tx_asset = Transaction("01/15/2023", "TRUCK PURCHASE FREIGHTLINER",
                           "TRUCK PURCHASE FREIGHTLINER",
                           Decimal("50000.00"), False, Decimal("0"))
    asset_rules = [
        CategoryRule(pattern="TRUCK PURCHASE", category="Fixed Asset Purchase",
                     tax_category="non-pl", is_fixed_asset=True,
                     include_in_pnl=False, priority=1),
    ]
    cat3 = TransactionCategorizer(custom_rules=asset_rules)
    cat3.categorize(tx_asset)
    check("fixed asset excluded from P&L", not tx_asset.include_in_pnl)
    check("fixed asset flagged", tx_asset.is_fixed_asset)

    # 8. Category matching for service revenue
    tx_rev = Transaction("01/15/2023", "Incoming Wire 12345 APEX CAPITAL CORP",
                         "Incoming Wire 12345 APEX CAPITAL CORP",
                         Decimal("5000.00"), True, Decimal("0"))
    cat4 = TransactionCategorizer()
    cat4.categorize(tx_rev)
    check("APEX wire matched as Freight Revenue", tx_rev.business_category == "Freight Revenue")

    # 9. P&L calculations
    txs = [
        Transaction("01/15/2023", "REVENUE INCOME", "REVENUE INCOME",
                    Decimal("1000.00"), True, Decimal("0"),
                    business_category="Service Revenue", include_in_pnl=True),
        Transaction("01/16/2023", "FUEL", "FUEL",
                    Decimal("200.00"), False, Decimal("0"),
                    business_category="Fuel", include_in_pnl=True),
        Transaction("01/17/2023", "INSURANCE", "INSURANCE",
                    Decimal("150.00"), False, Decimal("0"),
                    business_category="Business Insurance", include_in_pnl=True),
    ]
    pl = build_pl(txs)
    check("P&L total revenue", pl.total_revenue == Decimal("1000.00"))
    check("P&L total direct costs", pl.total_direct_costs == Decimal("200.00"))
    check("P&L total op expenses", pl.total_operating_expenses == Decimal("150.00"))
    check("P&L gross profit", pl.gross_profit == Decimal("800.00"))
    check("P&L net profit", pl.net_profit == Decimal("650.00"))

    # 10. Zero revenue percentage handling
    check("zero rev gross margin N/A", safe_pct(Decimal("0"), Decimal("0")) == "N/A")

    # 11. Statement reconciliation
    stmt_good = Statement("01/31/2023", "XXXX2136",
                          Decimal("100.00"), Decimal("150.00"),
                          Decimal("200.00"), Decimal("150.00"),
                          1, 1,
                          transactions=[
                              Transaction("01/15/2023", "DEP", "DEP", Decimal("200.00"),
                                          True, Decimal("300.00")),
                              Transaction("01/16/2023", "W/D", "W/D", Decimal("150.00"),
                                          False, Decimal("150.00")),
                          ])
    result = reconcile_statement(stmt_good)
    check("reconciliation passes", result.passed is True)

    # 12. Reconciliation failure
    stmt_bad = Statement("01/31/2023", "XXXX2136",
                         Decimal("100.00"), Decimal("150.00"),
                         Decimal("200.00"), Decimal("150.00"),
                         2, 2,
                         transactions=[
                             Transaction("01/15/2023", "DEP", "DEP", Decimal("100.00"),
                                         True, Decimal("200.00")),
                         ])
    result_bad = reconcile_statement(stmt_bad)
    check("reconciliation fails on mismatch", result_bad.passed is False)

    # 13. Decimal arithmetic
    check("Decimal addition", Decimal("0.10") + Decimal("0.20") == Decimal("0.30"))
    check("Decimal subtraction", Decimal("1.00") - Decimal("0.33") == Decimal("0.67"))

    # 14. Safe division by zero
    check("safe_div zero", safe_div(Decimal("100"), Decimal("0")) == Decimal("0"))

    # 15. CPA review flagging for uncategorized transactions
    tx_unknown = Transaction("01/15/2023", "SOMETHING WEIRD", "SOMETHING WEIRD",
                             Decimal("99.00"), False, Decimal("0"))
    cat5 = TransactionCategorizer()
    cat5.categorize(tx_unknown)
    check("unknown tx flagged for CPA review", tx_unknown.cpa_review is True)
    check("unknown tx category is Uncategorized", tx_unknown.business_category == "Uncategorized")

    # 16. fmt_dollar
    check("fmt_dollar positive", fmt_dollar(Decimal("1234.56")) == "$1,234.56")
    check("fmt_dollar negative", fmt_dollar(Decimal("-500.00")) == "-$500.00")

    # 17. Category template export
    exporter = CSVExporter([], BusinessConfig(), ProfitAndLoss())
    tmp_path = Path("/tmp/test_category_template.csv")
    if tmp_path.exists():
        tmp_path.unlink()
    exporter.export_category_template(tmp_path)
    check("category template created", tmp_path.exists())
    if tmp_path.exists():
        tmp_path.unlink()

    # 18. Audit CSV export
    tx1 = Transaction("01/15/2023", "TEST DESC", "TEST DESC",
                      Decimal("50.00"), False, Decimal("0"),
                      business_category="Fuel", tax_category="Vehicle Fuel",
                      include_in_pnl=True, deductibility="likely-deductible")
    stmt_test = Statement("01/31/2023", "XXXX2136",
                          Decimal("0"), Decimal("0"),
                          Decimal("0"), Decimal("50.00"),
                          0, 1,
                          transactions=[tx1])
    exporter2 = CSVExporter([stmt_test], BusinessConfig(), ProfitAndLoss())
    audit_tmp = Path("/tmp/test_audit.csv")
    exporter2.export_audit(audit_tmp)
    check("audit CSV created", audit_tmp.exists())
    if audit_tmp.exists():
        content = audit_tmp.read_text()
        check("audit CSV has correct headers", "BusinessCategory" in content)
        check("audit CSV has transaction", "Fuel" in content)
        audit_tmp.unlink()

    # 19. Config loading - empty config
    config = BusinessConfig()
    check("default business name", config.business_name == "Business")
    check("default entity type", config.entity_type == "sole-prop")
    check("masked EIN empty", config.masked_ein() == "")
    check("display name without DBA", config.display_name() == "Business")
    config.dba = "Test DBA"
    check("display name with DBA", "Test DBA" in config.display_name())

    # 20. FinancialPeriod
    periods = get_period_months(2023, 1)
    check("12 monthly periods", len(periods) == 12)
    check("first period January", periods[0].label == "January 2023")
    check("last period December", periods[-1].label == "December 2023")

    # 21. Quarterly P&L with fiscal year awareness
    stmts_q = [
        Statement("01/31/2023", "X", Decimal("0"), Decimal("0"),
                  Decimal("0"), Decimal("0"), 0, 0, transactions=[
                      Transaction("01/15/2023", "REV", "REV", Decimal("1000.00"), True, Decimal("0"),
                                  business_category="Service Revenue", include_in_pnl=True),
                  ]),
        Statement("04/30/2023", "X", Decimal("0"), Decimal("0"),
                  Decimal("0"), Decimal("0"), 0, 0, transactions=[
                      Transaction("04/15/2023", "EXP", "EXP", Decimal("500.00"), False, Decimal("0"),
                                  business_category="Fuel", include_in_pnl=True),
                  ]),
    ]
    qpl = build_quarterly_pls(stmts_q, fiscal_year_start=1)
    check("quarterly P&L has (2023, 1) key", (2023, 1) in qpl)
    check("quarterly P&L has (2023, 2) key", (2023, 2) in qpl)
    check("Q1 revenue correct", qpl[(2023, 1)].total_revenue == Decimal("1000.00"))
    check("Q2 expenses correct", qpl[(2023, 2)].total_direct_costs == Decimal("500.00"))

    # 22. Fiscal year with non-January start
    qpl_fiscal = build_quarterly_pls(stmts_q, fiscal_year_start=7)
    check("fiscal year Q3 for July start (Jan in FY-1 Q3)", (2022, 3) in qpl_fiscal)

    # 23. Fixed-asset candidate filtering
    tx_asset_candidate = Transaction("01/15/2023", "EQUIPMENT PURCHASE", "EQUIPMENT PURCHASE",
                                     Decimal("5000.00"), False, Decimal("0"),
                                     business_category="Fixed Asset Purchase")
    tx_payroll = Transaction("01/16/2023", "PAYROLL", "PAYROLL",
                             Decimal("2000.00"), False, Decimal("0"),
                             business_category="Payroll")
    stmt_fa = Statement("01/31/2023", "X", Decimal("0"), Decimal("0"),
                        Decimal("0"), Decimal("0"), 0, 0,
                        transactions=[tx_asset_candidate, tx_payroll])
    candidates = get_fixed_asset_candidates([stmt_fa])
    check("fixed asset candidate included", len(candidates) == 1)
    check("payroll excluded from FA candidates", candidates[0].business_category == "Fixed Asset Purchase")

    # 24. Reconciliation CSV export
    recon_res = ReconciliationResult(
        statement_label="January 2023",
        passed=True,
        parsed_credit_count=1, expected_credit_count=1,
        parsed_debit_count=1, expected_debit_count=1,
        parsed_credit_total=Decimal("100.00"), expected_credit_total=Decimal("100.00"),
        parsed_debit_total=Decimal("50.00"), expected_debit_total=Decimal("50.00"),
        beginning_balance=Decimal("0"), ending_balance=Decimal("50.00"),
        calculated_ending=Decimal("50.00"), balance_ok=True,
        warnings=[],
    )
    exporter3 = CSVExporter([], BusinessConfig(), ProfitAndLoss(),
                            recon_results=[recon_res])
    recon_tmp = Path("/tmp/test_recon.csv")
    exporter3.export_cpa(Path("/tmp/test_cpa_recon"))
    recon_file = Path("/tmp/test_cpa_recon/cpa_reconciliation.csv")
    check("recon CSV created", recon_file.exists())
    if recon_file.exists():
        content = recon_file.read_text()
        check("recon CSV has PASS status", "PASS" in content)
        check("recon CSV has parsed counts", "1" in content)
        import shutil
        shutil.rmtree(Path("/tmp/test_cpa_recon"), ignore_errors=True)

    # 25. Fiscal quarter months helper
    cal_q1 = _fiscal_quarter_months(1, 1)
    check("calendar Q1 months", cal_q1 == [1, 2, 3])
    cal_q4 = _fiscal_quarter_months(4, 1)
    check("calendar Q4 months", cal_q4 == [10, 11, 12])
    fy_jul_q1 = _fiscal_quarter_months(1, 7)
    check("fiscal Q1 with July start", fy_jul_q1 == [7, 8, 9])
    fy_jul_q4 = _fiscal_quarter_months(4, 7)
    check("fiscal Q4 with July start", fy_jul_q4 == [4, 5, 6])

    # 26. Chronological transaction ordering for date-range balances
    # Simulate newest-first statement ordering
    tx_jan_20 = Transaction("01/20/2023", "DEBIT", "DEBIT", Decimal("20.00"),
                            False, Decimal("80.00"), sequence=2)
    tx_jan_10 = Transaction("01/10/2023", "CREDIT", "CREDIT", Decimal("100.00"),
                            True, Decimal("100.00"), sequence=1)
    sorted_tx = sorted([tx_jan_20, tx_jan_10],
                       key=lambda tx: (tx.date_obj, tx.sequence))
    check("sorted oldest-first", sorted_tx[0].sequence == 1)
    check("sorted newest-last", sorted_tx[1].sequence == 2)
    first = sorted_tx[0]
    last = sorted_tx[-1]
    start_bal = first.balance - first.signed_amount
    end_bal = last.balance
    check("chrono start balance correct", start_bal == Decimal("0"))
    check("chrono end balance correct", end_bal == Decimal("80.00"))

    # Summary
    total = passed + failed
    print(f"\nSelf-test results: {passed}/{total} passed, {failed} failed")
    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
