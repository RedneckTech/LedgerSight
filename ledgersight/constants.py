"""Shared constants for LedgerSight."""
from __future__ import annotations
import re
from decimal import Decimal
from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent

_DEFAULT_CONFIG = "business_report.toml"

_PNL_DISCLAIMER = "Cash-Basis Management Report \u2014 Subject to CPA Review"

_CPA_DISCLAIMER = (
    "This package is an organizational aid prepared from available bank-statement data. "
    "It is not a tax return, audit, review, compilation, or substitute for professional "
    "accounting advice. All classifications and deductions are subject to verification "
    "by the business owner and tax professional."
)

_QUARTER_MONTHS: dict[int, list[int]] = {
    1: [1, 2, 3],
    2: [4, 5, 6],
    3: [7, 8, 9],
    4: [10, 11, 12],
}

VALID_DEDUCTIBILITY = {
    "likely-deductible", "possibly-deductible",
    "not-normally-deductible", "unknown", "not-applicable",
}

VALID_ENTITY_TYPES = {
    "sole-prop", "single-member-llc", "partnership",
    "s-corp", "c-corp", "other",
}

MONEY_RE = re.compile(
    r"(?P<paren>\()?"
    r"(?P<minus>-)?"
    r"\$(?P<amount>[\d,]+\.\d{2})"
    r"(?(paren)\))"
)

RC_TOLERANCE = Decimal("0.01")

_ACTIVITY_SECTION_ENDINGS = (
    "Checks Cleared",
    "Daily Balances",
    "Service Charges",
    "Overdraft",
    "Total Overdraft Fees",
    "Managing Your Accounts",
    "Client Contact",
)
