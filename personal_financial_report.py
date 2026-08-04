#!/usr/bin/env python3
"""Bank statement PDF report generator.

Parses First Interstate Bank checking account PDF statements and produces
a comprehensive PDF report with charts, tables, and categorized spending.

Usage:
    python3 personal_financial_report.py                          # combined yearly + monthly
    python3 personal_financial_report.py --year 2026              # yearly overview only
    python3 personal_financial_report.py --month 5                # single month (May of inferred year)
    python3 personal_financial_report.py --month 5 --year 2026    # explicit month/year
    python3 personal_financial_report.py -o custom_report.pdf     # custom output path
"""

import argparse
import hashlib
import io
import os
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from fpdf import FPDF

from ledgersight.categorizer import normalize_merchant
from ledgersight.parsers import _clean_description, fmt_dollar, parse_amount
from ledgersight.parsers import extract_text as _extract_text_path
from ledgersight.parsers import find_pdfs as _find_pdfs_path
from ledgersight.pdf_renderer import ReportPDF

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPT_HASH = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:8]

# ---------------------------------------------------------------------------
# Category rules: each category maps to a list of regex patterns.
# Patterns are tested in order; first match wins.
# ---------------------------------------------------------------------------
CATEGORY_RULES: dict[str, list[str]] = {
    "Payroll": [
        r"RICHERS TRUCKING PAYROLL",
    ],
    "Deposit": [
        r"\bDEPOSIT\b",
    ],
    "Rent": [
        r"125 S Roosevelt Ave",
    ],
    "Insurance": [
        r"PROGRESSIVE INS",
    ],
    "Transfers": [
        r"WEB XFER",
        r"ZELLE",
        r"PAYPAL INST XFER",
    ],
    "Fuel": [
        r"\bPILOT\b",
        r"FLYING J",
        r"LOVE'?S",
        r"KWIK STAR",
        r"\bMARATHON\b",
        r"\bYESWAY\b",
        r"CIRCLE K",
        r"\bTRAVEL CNT\b",
        r"\bTRAVEL CENTER",
        r"\bTA\b\s+(?:#\d+\s+)?[A-Z]{3,}",  # TA truck stops: TA #153 LAREDO, TA DENTON
        r"\bCASEYS\b",
        r"\bBP#\d",
        r"\bBB OF HOUSTON\b",
        r"\bBREAK TIME\b",
        r"\bSHELL\b.*SERVICE",
        r"\b59 FASTLANE\b",
        r"\bCTLP\b",
        r"\bWOODSHED\b",
        r"\bPORT AUTO TRUCK\b",
        r"\bTEX BEST\b",
        r"\bTBS\b.*DENISON",
    ],
    "Restaurants": [
        r"SONIC DRIVE",
        r"\bARBYS?\b",
        r"MCDONALD",
        r"TEXAS BEST (?:SMOKE|BBQ)",
        r"HOSHI JAPANE",
        r"\bARANDAS\b",
        r"DOORDASH",
        r"HUCK'?S FOOD",
        r"\bWENDYS?\b",
        r"BURGER KING",
        r"TACO JOHN",
        r"PANCHEROS[\s-]*MEXICA",
        r"CARL'?S\s*JR",
        r"\bDENNY'?S?\b",
        r"\bHARDEE'?S\b",
        r"DAIRY QUEEN",
        r"\bHAYMAKERS?\b",
        r"STILL SMOKIN",
        r"HONG KONG BUFFET",
    ],
    "Groceries": [
        r"WAL-MART",
        r"WALMART",
        r"WM SUPERCENTER",
        r"HY[ -]?VEE",
    ],
    "Subscriptions": [
        r"PAYPAL.*DISCORD",
        r"PAYPAL PURCHASE.*(?:HIDIVE|TWITCH|YOUTUBE|DASHDRAM|STORYMAT|SHRIKELI|CLOCKWOR|NETSHORT)",
        r"APPLE\W?COM.*BILL",
        r"GOOGLE\s+(?!.*(?:DramaBox|My Drama|DashDram|StoryMat))",
        r"NETFLIX",
        r"HULU",
        r"OPENAI",
        r"LEGALSHIELD",
        r"Oracle America",
        r"AMAZON PRIME",
        r"Prime Video",
        r"WL STEAM",
        r"CLOUD FACTORY",
        r"EXPERIAN\b",
        r"SHORTMAX",
        r"\biDrama\b",
        r"DramaBox",
        r"My Drama",
        r"DashDram",
        r"StoryMat",
        r"ANOMALY",
        r"\bAudible\b",
        r"TV.?C PRO DRIVER",
        r"HIDIVE",
        r"HFinance.*Simplex",
        r"Dramawave",
        r"\bNETSHORT\b",
        r"\bSTORYREEL\b",
    ],
    "Shopping": [
        r"AMAZON\b.*\b(?:MARK|MKTPL)\b",
        r"\bSQ\b(?!.*HOSHI)",
        r"SHIP'?S PLACE",
        r"THE KERR SPOT",
        r"LOWE'?S\b",
        r"\bTRACTOR SUPPLY\b",
        r"\bDOLLAR.GENERAL\b",
        r"WESTLAND THEATRE",
        r"MNRD-WESTBULITN",
    ],
    "Auto Care": [
        r"LAZER SPOT",
        r"HOMETOWN CARWASH",
        r"PETRO DODGE",
        r"TRUCKPARKINGCLUB",
    ],
    "Utilities": [
        r"ALLIANT ENERGY",
        r"ALLPAID",
        r"FARMERS ELEVATOR",
        r"SCHOOL PROCESSIN",
    ],
    "Loan/Credit Payment": [
        r"CAPITAL ONE",
        r"JD BYRIDER",
        r"\bChime\b",
    ],
    "Bank Fees": [
        r"JACOB PFEIFF.*XXXXXXXXXXX",
    ],
    "Checks": [
        r"CHECK #",
    ],
    "Government": [
        r"IOWA JUDICIAL",
        r"\bUSPS\b",
    ],
}

CATEGORY_COLORS: dict[str, str] = {
    "Payroll": "#27ae60",
    "Deposit": "#2ecc71",
    "Rent": "#c0392b",
    "Insurance": "#d35400",
    "Transfers": "#2980b9",
    "Fuel": "#e74c3c",
    "Restaurants": "#e67e22",
    "Groceries": "#f1c40f",
    "Subscriptions": "#9b59b6",
    "Shopping": "#1abc9c",
    "Utilities": "#16a085",
    "Auto Care": "#1abc9c",
    "Loan/Credit Payment": "#8e44ad",
    "Bank Fees": "#95a5a6",
    "Checks": "#d35400",
    "Government": "#7f8c8d",
    "Other": "#bdc3c7",
}

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class Transaction:
    post_date: str
    description: str
    amount: Decimal
    is_credit: bool
    balance: Decimal
    category: str = ""


@dataclass
class Statement:
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
    overdraft_fees: Decimal = Decimal("0")
    returned_item_fees: Decimal = Decimal("0")

    @property
    def month(self) -> int:
        return int(self.statement_date.split("/")[0])

    @property
    def year(self) -> int:
        return int(self.statement_date.split("/")[2])

    @property
    def month_label(self) -> str:
        dt = datetime(self.year, self.month, 1)
        return dt.strftime("%B %Y")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def find_pdfs(directory: str) -> list[str]:
    return [str(p) for p in _find_pdfs_path(Path(directory))]


# ---------------------------------------------------------------------------
# PDF text extraction
# ---------------------------------------------------------------------------


def extract_text(pdf_path: str) -> str:
    return _extract_text_path(Path(pdf_path))


# ---------------------------------------------------------------------------
# Statement parsing
# ---------------------------------------------------------------------------


def _is_page_artifact(line: str, stripped: str) -> bool:
    if not stripped:
        return True
    if "\x0c" in line:
        return True
    if re.match(r"^[0-9A-F]{32}", stripped):
        return True
    if "Statement Ending" in stripped:
        return True
    if "continued" in stripped:
        return True
    if "Post Date" in stripped:
        return True
    if re.match(r"^Page \d+ of \d+", stripped):
        return True
    if re.match(r"^JACOB PFEIFF\s+X+", stripped):
        return True
    if re.match(r"BASIC CHECKING\s*-\s*X+", stripped):
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
    if re.match(r"^RETURN SERVICE REQUESTED", stripped):
        return True
    if re.match(r"^#\d+\s+", stripped):
        return True
    if re.match(r"^\d{1,2}\s*$", stripped):
        return True
    if re.match(r"[A-Z]{2}\s+\d{5}", stripped) and len(stripped) < 20:
        return True
    return False


def parse_statement(text: str) -> Statement:
    lines = text.split("\n")

    statement_date = ""
    account_number = ""
    for line in lines[:15]:
        if not statement_date:
            m = re.search(r"Statement Ending\s+(\d{2}/\d{2}/\d{4})", line)
            if m:
                statement_date = m.group(1)
        if not account_number:
            m = re.search(r"(XXXXXXXXXXX\d{4})", line)
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
        if "Beginning Balance" in line:
            m = re.search(r"\$[\d,]+\.\d{2}", line)
            if m:
                beginning_balance = parse_amount(m.group())
        elif "Credit" in line and "This Period" in line:
            m = re.search(r"(\d+)\s+Credit", line)
            if m:
                credit_count = int(m.group(1))
            m2 = re.search(r"\$[\d,]+\.\d{2}", line)
            if m2:
                total_credits = parse_amount(m2.group())
        elif "Debit" in line and "This Period" in line:
            m = re.search(r"(\d+)\s+Debit", line)
            if m:
                debit_count = int(m.group(1))
            m2 = re.search(r"\$[\d,]+\.\d{2}", line)
            if m2:
                total_debits = parse_amount(m2.group())
        elif "Ending Balance" in line:
            m = re.search(r"\$[\d,]+\.\d{2}", line)
            if m:
                ending_balance = parse_amount(m.group())
            break

    # ---- Account Activity ----
    transactions: list[Transaction] = []
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
        if "Checks Cleared" in line:
            if desc_buffer and transactions:
                extra = _clean_description(" ".join(desc_buffer))
                if extra:
                    transactions[-1].description = _clean_description(
                        f"{transactions[-1].description} {extra}"
                    )
                desc_buffer.clear()
            break
        if not line.strip():
            continue

        # Detect column header to find Debits/Credits/Balance columns
        if not header_positions and "Post Date" in line:
            for col_name in ["Debits", "Credits", "Balance"]:
                pos = line.find(col_name)
                if pos >= 0:
                    header_positions[col_name] = pos
            continue

        date_match = re.match(r"^(\d{2}/\d{2}/\d{4})", line)
        if date_match:
            post_date = date_match.group(1)

            # Skip balance-marker lines inside activity
            if "Beginning Balance" in line or "Ending Balance" in line:
                desc_buffer.clear()
                continue

            amounts = list(re.finditer(r"\$[\d,]+\.\d{2}", line))
            if not amounts:
                desc_part = line[date_match.end() :].strip()
                if desc_part:
                    desc_buffer.append(desc_part)
                continue

            if len(amounts) == 1:
                # e.g. "05/27/2026   Beginning Balance                       $645.07"
                continue

            balance = parse_amount(amounts[-1].group())
            tx_amount = parse_amount(amounts[-2].group())

            # Determine credit vs debit
            is_credit = False
            if header_positions:
                amount_col = amounts[-2].start()
                credit_col = header_positions.get("Credits", 9999)
                debit_col = header_positions.get("Debits", 0)
                # If amount lands in or beyond the Credits column
                if amount_col >= credit_col - 2:
                    is_credit = True
                elif amount_col < debit_col + 10 and amount_col + 6 < credit_col:
                    is_credit = False
                else:
                    # Fallback: use balance direction
                    if transactions:
                        is_credit = balance > transactions[-1].balance
                    elif beginning_balance:
                        is_credit = balance > beginning_balance
            else:
                if transactions:
                    is_credit = balance > transactions[-1].balance
                elif beginning_balance:
                    is_credit = balance > beginning_balance

            # Build full description
            desc_parts = list(desc_buffer)
            current_desc = line[date_match.end() : amounts[-2].start()].strip()
            has_own_desc = bool(current_desc)

            if has_own_desc:
                # desc_buffer lines belong to the PREVIOUS transaction
                if transactions:
                    stray_text = " ".join(desc_parts).strip()
                    if stray_text:
                        tx = transactions[-1]
                        tx.description = _clean_description(
                            f"{tx.description} {stray_text}"
                        )
                desc_buffer.clear()
                description = _clean_description(current_desc)
            else:
                # desc_buffer lines are THIS transaction's description
                if current_desc:
                    desc_parts.append(current_desc)
                description = " ".join(desc_parts).strip()
                description = _clean_description(description)
                desc_buffer.clear()

            transactions.append(
                Transaction(
                    post_date=post_date,
                    description=description,
                    amount=tx_amount,
                    is_credit=is_credit,
                    balance=balance,
                )
            )
        else:
            stripped = line.strip()
            if stripped and not _is_page_artifact(line, stripped):
                desc_buffer.append(stripped)

    # ---- Checks Cleared ----
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
            checks.append(
                {
                    "number": int(m.group(1)),
                    "date": m.group(2),
                    "amount": parse_amount("$" + m.group(3)),
                }
            )

    # ---- Daily Balances ----
    daily_balances: list[dict] = []
    in_daily = False
    for line in lines:
        if "Daily Balances" in line:
            in_daily = True
            continue
        if not in_daily:
            continue
        if "Overdraft" in line or "Total Overdraft Fees" in line:
            break
        pairs = re.findall(r"(\d{2}/\d{2}/\d{4})\s+(-?\$[\d,]+\.\d{2})", line)
        for date_str, amt_str in pairs:
            daily_balances.append(
                {"date": date_str, "balance": parse_amount(amt_str)}
            )

    # ---- Fees ----
    overdraft_fees = Decimal("0")
    returned_fees = Decimal("0")
    for line in lines:
        if "Total Overdraft Fees" in line:
            m = re.search(r"\$[\d,]+\.\d{2}", line)
            if m:
                overdraft_fees = parse_amount(m.group())
        if "Total Returned Item Fees" in line:
            m = re.search(r"\$[\d,]+\.\d{2}", line)
            if m:
                returned_fees = parse_amount(m.group())

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
        overdraft_fees=overdraft_fees,
        returned_item_fees=returned_fees,
    )


# ---------------------------------------------------------------------------
# Categorization
# ---------------------------------------------------------------------------


BANK_FEE_PRECEDENCE_PATTERNS = [
    r"MasterCard Cross Border",
    r"SALES TAX",
    r"Overdraft",
    r"FEE FOR DDA",
    r"DDA WITHDRAWAL",
]

SUBSCRIPTION_PRECEDENCE_PATTERNS = [
    r"PAYPAL (?:INST XFER|PURCHASE).*HIDIVE",
    r"PAYPAL (?:INST XFER|PURCHASE).*DISCORD",
    r"PAYPAL (?:INST XFER|PURCHASE).*TWITCH",
    r"PAYPAL.*CLOCKWOR",
    r"PAYPAL.*SHRIKELI",
]


def categorize(description: str) -> str:
    for pat in BANK_FEE_PRECEDENCE_PATTERNS:
        if re.search(pat, description, re.IGNORECASE):
            return "Bank Fees"
    for pat in SUBSCRIPTION_PRECEDENCE_PATTERNS:
        if re.search(pat, description, re.IGNORECASE):
            return "Subscriptions"
    for cat, patterns in CATEGORY_RULES.items():
        if cat in ("Bank Fees", "Subscriptions"):
            continue
        for pat in patterns:
            if re.search(pat, description, re.IGNORECASE):
                return cat
    for pat in CATEGORY_RULES.get("Subscriptions", []):
        if re.search(pat, description, re.IGNORECASE):
            return "Subscriptions"
    return "Other"


def categorize_transactions(statements: list[Statement]) -> None:
    for stmt in statements:
        for tx in stmt.transactions:
            tx.category = categorize(tx.description)


# ---------------------------------------------------------------------------
# Chart generation (matplotlib → PNG byte buffer)
# ---------------------------------------------------------------------------


def chart_credits_vs_debits(
    statements: list[Statement],
) -> io.BytesIO:
    months = [s.month_label for s in statements]
    credits = [float(s.total_credits) for s in statements]
    debits = [float(s.total_debits) for s in statements]

    fig, ax = plt.subplots(figsize=(12, 7))
    x = range(len(months))
    w = 0.35
    bars1 = ax.bar([i - w / 2 for i in x], credits, w, label="Credits", color="#27ae60")
    bars2 = ax.bar([i + w / 2 for i in x], debits, w, label="Debits", color="#e74c3c")

    for bar in bars1:
        h = bar.get_height()
        ax.text(bar.get_x() + bar.get_width() / 2.0, h + 10, f"${h:,.0f}",
                ha="center", va="bottom", fontsize=7)
    for bar in bars2:
        h = bar.get_height()
        ax.text(bar.get_x() + bar.get_width() / 2.0, h + 10, f"${h:,.0f}",
                ha="center", va="bottom", fontsize=7)

    ax.set_xticks(list(x))
    ax.set_xticklabels(months, fontsize=8)
    ax.set_ylabel("Amount ($)", fontsize=9)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"${v:,.0f}"))
    ax.legend(fontsize=8)
    ax.set_title("Credits vs Debits by Month", fontsize=11, fontweight="bold")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def chart_category_pie(statements: list[Statement]) -> io.BytesIO:
    cat_totals: dict[str, float] = defaultdict(float)
    for s in statements:
        for tx in s.transactions:
            if not tx.is_credit:
                cat_totals[tx.category] += float(tx.amount)

    cat_totals = {k: v for k, v in cat_totals.items() if v > 0}
    if not cat_totals:
        buf = io.BytesIO()
        buf.write(b"\x89PNG")
        buf.seek(0)
        return buf

    total = sum(cat_totals.values())
    threshold = total * 0.03

    main_items = [(k, v) for k, v in cat_totals.items() if v >= threshold]
    small_total = sum(v for k, v in cat_totals.items() if v < threshold)

    sorted_main = sorted(main_items, key=lambda x: x[1], reverse=True)
    labels = [k for k, _ in sorted_main]
    sizes = [v for _, v in sorted_main]

    if small_total > 0:
        labels.append("Other (<3% each)")
        sizes.append(small_total)

    colors = [CATEGORY_COLORS.get(l, "#bdc3c7") for l in labels]
    if small_total > 0:
        colors[-1] = "#bdc3c7"

    fig, ax = plt.subplots(figsize=(6, 4.5))
    wedges, texts, autotexts = ax.pie(
        sizes, labels=None, autopct="%1.1f%%", startangle=140,
        colors=colors, pctdistance=0.75,
    )
    for t in autotexts:
        t.set_fontsize(7)

    legend_labels = [f"{l}  (${s:,.0f})" for l, s in zip(labels, sizes)]
    ax.legend(wedges, legend_labels, title="Categories", loc="center left",
              bbox_to_anchor=(1, 0.5), fontsize=7, title_fontsize=8)
    ax.set_title("Debits by Category", fontsize=11, fontweight="bold")
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def chart_weekly_balance(statements: list[Statement]) -> io.BytesIO:
    # Aggregate all daily balances into weekly averages, using (year, week) keys
    week_bals: dict[tuple[int, int], list[float]] = {}
    for s in statements:
        for db in s.daily_balances:
            dt = datetime.strptime(db["date"], "%m/%d/%Y")
            iso = dt.isocalendar()
            key = (iso[0], iso[1])
            week_bals.setdefault(key, []).append(float(db["balance"]))

    if not week_bals:
        buf = io.BytesIO()
        buf.write(b"\x89PNG")
        buf.seek(0)
        return buf

    points = sorted(
        (k, sum(bals) / len(bals))
        for k, bals in week_bals.items()
    )
    week_keys = [p[0] for p in points]
    balances = [p[1] for p in points]
    x_labels = [f"{y}-W{wk:02d}" for y, wk in week_keys]
    positions = list(range(len(points)))

    fig, ax = plt.subplots(figsize=(12, 7))
    ax.plot(positions, balances, color="#2c3e50", linewidth=1.6, marker="o",
            markersize=3, alpha=0.85)
    ax.fill_between(positions, 0, balances, alpha=0.08, color="#2c3e50")
    ax.axhline(y=0, color="#e74c3c", linewidth=0.5, linestyle="--", alpha=0.5)
    ax.set_xticks(positions)
    step = max(1, len(positions) // 15)
    shown_positions = positions[::step]
    ax.set_xticks(shown_positions)
    ax.set_xticklabels(
        [x_labels[i] for i in shown_positions],
        rotation=45, ha="right", fontsize=8,
    )
    ax.set_ylabel("Average Weekly Balance ($)", fontsize=9)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"${v:,.0f}"))
    ax.set_title("Weekly Average Balance \u2013 Year to Date", fontsize=11, fontweight="bold")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.set_xlim(-0.5, len(points) - 0.5)
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def chart_daily_balance_single(stmt: Statement) -> io.BytesIO:
    if not stmt.daily_balances:
        buf = io.BytesIO()
        buf.write(b"\x89PNG")
        buf.seek(0)
        return buf

    points = sorted(
        (datetime.strptime(db["date"], "%m/%d/%Y"), float(db["balance"]))
        for db in stmt.daily_balances
    )
    date_objs = [p[0] for p in points]
    balances = [p[1] for p in points]
    x_labels = [d.strftime("%m/%d") for d in date_objs]

    fig, ax = plt.subplots(figsize=(9, 3.2))
    ax.plot(date_objs, balances, color="#2c3e50", linewidth=1.4, marker="o",
            markersize=3, alpha=0.85)
    ax.fill_between(date_objs, 0, balances, alpha=0.08, color="#2c3e50")
    ax.axhline(y=0, color="#e74c3c", linewidth=0.5, linestyle="--", alpha=0.5)
    ax.set_ylabel("Balance ($)", fontsize=9)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"${v:,.0f}"))
    ax.set_title(f"Daily Balance \u2013 {stmt.month_label}", fontsize=11, fontweight="bold")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.autofmt_xdate(rotation=30, ha="right")
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def chart_category_by_month(statements: list[Statement]) -> io.BytesIO:
    all_cats: set[str] = set()
    for s in statements:
        for tx in s.transactions:
            if not tx.is_credit:
                all_cats.add(tx.category)

    # Only show top categories
    cat_totals: dict[str, float] = defaultdict(float)
    for s in statements:
        for tx in s.transactions:
            if not tx.is_credit:
                cat_totals[tx.category] += float(tx.amount)
    top_cats = sorted(cat_totals, key=lambda c: cat_totals[c], reverse=True)[:8]

    months = [s.month_label for s in statements]
    data: dict[str, list[float]] = {cat: [] for cat in top_cats}
    for s in statements:
        month_cats: dict[str, float] = defaultdict(float)
        for tx in s.transactions:
            if not tx.is_credit:
                month_cats[tx.category] += float(tx.amount)
        for cat in top_cats:
            data[cat].append(month_cats.get(cat, 0))

    fig, ax = plt.subplots(figsize=(12, 7))
    x = range(len(months))
    w = 0.8 / len(top_cats)
    colors = [CATEGORY_COLORS.get(c, "#bdc3c7") for c in top_cats]

    for idx, cat in enumerate(top_cats):
        offset = (idx - len(top_cats) / 2 + 0.5) * w
        vals = data[cat]
        ax.bar([i + offset for i in x], vals, w, label=cat, color=colors[idx])

    ax.set_xticks(list(x))
    ax.set_xticklabels(months, fontsize=8)
    ax.set_ylabel("Amount ($)", fontsize=9)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"${v:,.0f}"))
    ax.legend(fontsize=6, ncol=2)
    ax.set_title("Debits by Category per Month", fontsize=11, fontweight="bold")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


# ---------------------------------------------------------------------------
# PDF Report generation
# ---------------------------------------------------------------------------


def build_monthly_table_rows(stmt: Statement) -> list[list[str]]:
    rows = [
        ["Beginning Balance", fmt_dollar(stmt.beginning_balance)],
        ["Total Credits", fmt_dollar(stmt.total_credits)],
        ["Total Debits", fmt_dollar(stmt.total_debits)],
        ["Ending Balance", fmt_dollar(stmt.ending_balance)],
        ["Net Change", fmt_dollar(stmt.ending_balance - stmt.beginning_balance)],
        ["Credit Transactions", str(stmt.credit_count)],
        ["Debit Transactions", str(stmt.debit_count)],
        ["Overdraft Fees", fmt_dollar(stmt.overdraft_fees)],
        ["Returned Item Fees", fmt_dollar(stmt.returned_item_fees)],
    ]
    return rows


def build_category_table_rows(statements: list[Statement]) -> list[list[str]]:
    cat_totals: dict[str, Decimal] = defaultdict(Decimal)
    for s in statements:
        for tx in s.transactions:
            if not tx.is_credit:
                cat_totals[tx.category] += tx.amount

    total = sum(cat_totals.values(), Decimal("0"))
    rows = []
    for cat, amt in sorted(cat_totals.items(), key=lambda x: x[1], reverse=True):
        pct_raw = float(amt) / float(total) * 100 if total > 0 else 0
        if pct_raw < 0.1 and pct_raw > 0:
            pct = "<0.1%"
        else:
            pct = f"{pct_raw:.1f}%"
        rows.append([cat, fmt_dollar(amt), pct])
    return rows


# ---------------------------------------------------------------------------
# Merchant normalization
# ---------------------------------------------------------------------------

MERCHANT_ALIASES: list[tuple[str, str]] = [
    (r".*125 S ROOSEVELT AVE.*", "125 S Roosevelt Ave"),
    (r".*MNRD-WESTBULITN.*", "Menards - West Burlington"),
    (r".*PROGRESSIVE INS.*", "Progressive Insurance"),
    (r".*WAL-?MART.*|.*WM SUPERCENTER.*", "Walmart"),
    (r".*ALLIANT ENERGY.*", "Alliant Energy"),
    (r".*JD BYRIDER.*", "JD Byrider"),
    (r".*CAPITAL ONE.*", "Capital One"),
    (r".*CHIME\b.*", "Chime"),
    (r".*FARMERS ELEVATOR.*", "Farmers Elevator"),
    (r".*HY[ -]?VEE.*", "Hy-Vee"),
    (r".*CASEYS?\b.*", "Casey's"),
    (r".*PILOT\b.*", "Pilot Travel Center"),
    (r".*FLYING J\b.*", "Flying J"),
    (r".*LOVE'?S\b.*", "Love's Travel Stop"),
    (r".*KWIK STAR\b.*", "Kwik Star"),
    (r".*RICHERS TRUCKING PAYROLL.*", "Richers Trucking"),
    (r".*DOLLAR.GENERAL.*", "Dollar General"),
    (r".*TRACTOR SUPPLY.*", "Tractor Supply"),
    (r".*LOWE'?S\b.*", "Lowe's"),
    (r".*IOWA JUDICIAL.*", "Iowa Judicial Branch"),
    (r".*USPS\b.*", "USPS"),
    (r".*AMAZON\s+(?:MARK|MKTPL).*", "Amazon"),
    (r".*HFINANCE.*SIMPLEX.*", "Simplex"),
    (r".*ORACLE AMERICA.*", "Oracle"),
    (r".*TVC PRO DRIVER.*", "TVC Pro Driver"),
    (r".*DOORDASH.*", "DoorDash"),
    (r".*\bAUDIBLE\b.*", "Audible"),
    (r".*CLOUD FACTORY.*", "Cloud Factory"),
    (r".*ALLPAID.*MORNING SUN.*", "City of Morning Sun"),
        (r".*SCHOOL PROCESSIN.*", "School Payment"),
        (r".*GOOGLE.*DRAMABOX.*", "Google DramaBox"),
        (r".*GOOGLE.*MY DRAMA.*", "Google My Drama"),
        (r".*GOOGLE.*DASHDRAM.*", "Google DashDram"),
        (r".*GOOGLE.*STORYMAT.*", "Google StoryMat"),
        (r".*\bDRAMAWAVE\b.*", "Dramawave"),
        (r".*PAYPAL CLOCKWOR.*", "Clockwork"),
        (r".*PAYPAL SHRIKELI.*", "Shrikeli"),
        (r".*ARANDAS.*", "Arandas Mexican"),
        (r".*SHIP'?S PLACE.*", "Ship's Place"),
        (r".*DENNY'?S?\b.*", "Denny's"),
        (r".*SONIC DRIVE.*", "Sonic Drive-In"),
        (r".*HONG KONG BUFFET.*", "Hong Kong Buffet"),
    ]

EXCLUDED_MERCHANT_CATS = {"Transfers", "Checks", "Bank Fees", "Loan/Credit Payment", "Other"}


def build_top_merchants(statements: list[Statement], top_n: int = 15,
                        mask_personal: bool = False) -> list[list[str]]:
    merchant_totals: dict[str, Decimal] = defaultdict(Decimal)
    for s in statements:
        for tx in s.transactions:
            if tx.is_credit or tx.category in EXCLUDED_MERCHANT_CATS:
                continue
            name = normalize_merchant(tx.description)
            merchant_totals[name] += tx.amount

    sorted_merchants = sorted(merchant_totals.items(), key=lambda x: x[1], reverse=True)[:top_n]
    rows = []
    rank = 1
    for desc, amt in sorted_merchants:
        if mask_personal:
            desc = _mask_desc(desc)
        desc_short = desc[:70] + ("..." if len(desc) > 70 else "")
        rows.append([str(rank), desc_short, fmt_dollar(amt)])
        rank += 1
    return rows


def _reconcile_statements(statements: list[Statement]) -> bool:
    passed = True
    for stmt in statements:
        parsed_credits = sum(
            tx.amount for tx in stmt.transactions if tx.is_credit
        )
        parsed_debits = sum(
            tx.amount for tx in stmt.transactions if not tx.is_credit
        )
        parsed_count = len(stmt.transactions)
        expected_count = stmt.credit_count + stmt.debit_count

        if (parsed_count != expected_count
                or parsed_credits != stmt.total_credits
                or parsed_debits != stmt.total_debits):
            passed = False
            print(
                f"WARNING: {stmt.month_label} reconciliation failed "
                f"parsed={parsed_count} expected={expected_count} "
                f"credits={parsed_credits}/{stmt.total_credits} "
                f"debits={parsed_debits}/{stmt.total_debits}",
                file=sys.stderr,
            )
    return passed


def generate_report(
    statements: list[Statement],
    output_path: str,
    mode: str = "combined",
    target_month: int | None = None,
    target_year: int | None = None,
    audit_path: str | None = None,
    mask_personal: bool = False,
    allow_mismatch: bool = False,
):
    pdf = ReportPDF("Bank Statement Report")

    # Filter by year/month if requested
    if target_year:
        statements = [s for s in statements if s.year == target_year]
    if target_month:
        statements = [s for s in statements if s.month == target_month]

    if not statements:
        print("No statements found matching filters.", file=sys.stderr)
        sys.exit(1)

    statements.sort(key=lambda s: (s.year, s.month))
    categorize_transactions(statements)

    # Per-statement reconciliation
    reconciled = _reconcile_statements(statements)
    if not reconciled:
        print("WARNING: reconciliation failed (see above).", file=sys.stderr)
        if not allow_mismatch:
            print("Aborting. Use --allow-mismatch to force report generation.",
                  file=sys.stderr)
            sys.exit(1)

    # ---- COVER PAGE ----
    pdf.add_page()
    pdf.ln(15)
    pdf.set_font("DJV", "B", 24)
    pdf.set_text_color(44, 62, 80)
    pdf.cell(0, 12, "Bank Statement Report", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)

    pdf.set_font("DJV", "", 11)
    pdf.set_text_color(100, 100, 100)
    date_range = f"{statements[0].month_label}  to  {statements[-1].month_label}"
    pdf.cell(0, 7, date_range, align="C", new_x="LMARGIN", new_y="NEXT")
    account_display = "XXXXXXXXXXXX" if mask_personal else statements[0].account_number
    pdf.cell(0, 7, f"Account: {account_display}", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 7, f"{len(statements)} statement(s)", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(10)

    # Summary box
    total_credits_val = sum((s.total_credits for s in statements), Decimal("0"))
    total_debits_val = sum((s.total_debits for s in statements), Decimal("0"))
    net_flow = total_credits_val - total_debits_val
    first_bal = statements[0].beginning_balance
    last_bal = statements[-1].ending_balance

    summary_rows = [
        ["Total Credits", fmt_dollar(total_credits_val)],
        ["Total Debits", fmt_dollar(total_debits_val)],
        ["Net Account Change", fmt_dollar(net_flow)],
        ["Starting Balance", fmt_dollar(first_bal)],
        ["Ending Balance", fmt_dollar(last_bal)],
    ]
    cw = [pdf.w - pdf.l_margin - pdf.r_margin - 50, 50]
    for label, val in summary_rows:
        pdf.set_fill_color(245, 245, 245)
        pdf.set_font("DJV", "B", 10)
        pdf.set_text_color(50, 50, 50)
        pdf.cell(cw[0], 7, f"  {label}", fill=True)
        pdf.set_font("DJV", "", 10)
        pdf.cell(cw[1], 7, val, fill=True, align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)

    # Transaction count with reconciliation
    expected_tx = sum(s.credit_count + s.debit_count for s in statements)
    parsed_tx = sum(len(s.transactions) for s in statements)
    if parsed_tx == expected_tx:
        pdf.body_text(f"Transactions parsed: {parsed_tx}")
    else:
        print(
            f"WARNING: expected {expected_tx} transactions, parsed {parsed_tx}",
            file=sys.stderr,
        )
        pdf.body_text(
            f"Transactions parsed: {parsed_tx} of {expected_tx} expected"
        )

    if allow_mismatch:
        pdf.body_text("Reconciliation: SKIPPED (--allow-mismatch)")
    elif reconciled:
        pdf.body_text("Reconciliation: PASSED")
    else:
        pdf.body_text("Reconciliation: FAILED")

    # Compare debits/credits totals
    parsed_credits = sum(
        sum(tx.amount for tx in s.transactions if tx.is_credit)
        for s in statements
    )
    parsed_debits = sum(
        sum(tx.amount for tx in s.transactions if not tx.is_credit)
        for s in statements
    )
    if (parsed_credits != total_credits_val) or (parsed_debits != total_debits_val):
        print(
            f"WARNING: credit totals differ: parsed={parsed_credits} vs "
            f"reported={total_credits_val}",
            file=sys.stderr,
        )
        print(
            f"WARNING: debit totals differ: parsed={parsed_debits} vs "
            f"reported={total_debits_val}",
            file=sys.stderr,
        )

    # Monthly balances mini-table on cover
    pdf.ln(2)
    pdf.sub_title("Monthly Balances")
    bal_rows = []
    for s in statements:
        bal_rows.append([
            s.month_label,
            fmt_dollar(s.beginning_balance),
            fmt_dollar(s.ending_balance),
            fmt_dollar(s.ending_balance - s.beginning_balance),
        ])
    cw_bal = [40, 35, 35, 35]
    pdf.draw_table(
        ["Month", "Start Balance", "End Balance", "Change"],
        bal_rows,
        col_widths=cw_bal,
        col_aligns=["L", "R", "R", "R"],
    )

    # ---- PERIOD OVERVIEW CHARTS ----
    if mode in ("combined", "yearly"):
        period_label = statements[0].month_label
        if len(statements) > 1:
            period_label = f"{statements[0].month_label} \u2013 {statements[-1].month_label}"
            pdf.add_page(orientation="L")
            pdf.section_title(f"{period_label} \u2013 Credits vs Debits")
            chart_buf = chart_credits_vs_debits(statements)
            pdf.embed_chart(chart_buf, w=pdf.w - pdf.l_margin - pdf.r_margin)
            chart_buf.close()

            pdf.add_page(orientation="L")
            pdf.section_title(f"{period_label} \u2013 Weekly Average Balance, Year to Date")
            weekly_chart = chart_weekly_balance(statements)
            pdf.embed_chart(weekly_chart, w=pdf.w - pdf.l_margin - pdf.r_margin)
            weekly_chart.close()

            pdf.add_page(orientation="L")
            pdf.section_title(f"{period_label} \u2013 Debits by Category per Month")
            cat_month_chart = chart_category_by_month(statements)
            pdf.embed_chart(cat_month_chart, w=pdf.w - pdf.l_margin - pdf.r_margin)
            cat_month_chart.close()

        pdf.add_page()
        pdf.section_title(f"{period_label} Overview")
        cat_pie = chart_category_pie(statements)
        pdf.embed_chart(cat_pie, w=140)
        cat_pie.close()

        pdf.sub_title("Debits by Category")
        cat_rows = build_category_table_rows(statements)
        cw_cat = [55, 35, 25]
        pdf.draw_table(["Category", "Amount", "Share"], cat_rows,
                       col_widths=cw_cat, col_aligns=["L", "R", "R"],
                       section_label="Category Breakdown")

        pdf.add_page()
        pdf.sub_title("Top Payees by Total Debits (All Periods)")
        merchant_rows = build_top_merchants(statements, top_n=15, mask_personal=mask_personal)
        cw_merch = [10, 120, 35]
        pdf.draw_table(["#", "Payee", "Total"], merchant_rows,
                       col_widths=cw_merch, col_aligns=["R", "L", "R"],
                       section_label="Top Payees by Total Debits")

    # ---- MONTHLY DETAIL ----
    if mode in ("combined", "monthly"):
        for stmt in statements:
            pdf.add_page()
            pdf.section_title(stmt.month_label)
            pdf.body_text(f"Statement Date: {stmt.statement_date}", size=8)

            # Summary table
            pdf.sub_title("Account Summary")
            month_rows = build_monthly_table_rows(stmt)
            cw_month = [55, 40]
            pdf.draw_table(["Metric", "Amount"], month_rows,
                           col_widths=cw_month, col_aligns=["L", "R"])

            # Daily balance chart for this month
            daily_single = chart_daily_balance_single(stmt)
            pdf.embed_chart(daily_single, w=185)
            daily_single.close()

            # Monthly category breakdown
            cat_totals_m: dict[str, Decimal] = defaultdict(Decimal)
            for tx in stmt.transactions:
                if not tx.is_credit:
                    cat_totals_m[tx.category] += tx.amount
            if cat_totals_m:
                total_m = sum(cat_totals_m.values(), Decimal("0"))
                cat_rows_m = []
                for cat, amt in sorted(cat_totals_m.items(), key=lambda x: x[1], reverse=True):
                    pct_raw = float(amt) / float(total_m) * 100 if total_m > 0 else 0
                    if pct_raw < 0.1 and pct_raw > 0:
                        pct = "<0.1%"
                    else:
                        pct = f"{pct_raw:.1f}%"
                    cat_rows_m.append([cat, fmt_dollar(amt), pct])
                pdf.sub_title("Debits by Category")
                cw_cat_m = [55, 35, 25]
                pdf.draw_table(["Category", "Amount", "Share"], cat_rows_m,
                               col_widths=cw_cat_m, col_aligns=["L", "R", "R"])

            # Top merchants for this month
            stmt_wrapper = [stmt]
            merchant_m = build_top_merchants(stmt_wrapper, top_n=10, mask_personal=mask_personal)
            if merchant_m:
                pdf.sub_title("Top Payees by Total Debits")
                cw_mm = [10, 120, 35]
                pdf.draw_table(["#", "Payee", "Total"], merchant_m,
                               col_widths=cw_mm, col_aligns=["R", "L", "R"],
                               section_label="Top Payees by Total Debits")

            # Recent transactions — newest first
            pdf.sub_title("Recent Transactions")
            sorted_tx = sorted(
                stmt.transactions,
                key=lambda tx: datetime.strptime(tx.post_date, "%m/%d/%Y"),
                reverse=True,
            )[:40]
            tx_rows = []
            for tx in sorted_tx:
                raw_desc = tx.description
                if mask_personal:
                    raw_desc = _mask_desc(raw_desc)
                desc = raw_desc[:55] + ("..." if len(raw_desc) > 55 else "")
                sign = "+" if tx.is_credit else "-"
                tx_rows.append([
                    tx.post_date,
                    desc,
                    tx.category[:18],
                    f"{sign}{fmt_dollar(tx.amount)}",
                    fmt_dollar(tx.balance),
                ])
            cw_tx = [24, 62, 30, 28, 28]
            pdf.draw_table(
                ["Date", "Description", "Category", "Amount", "Balance"],
                tx_rows,
                col_widths=cw_tx,
                col_aligns=["L", "L", "L", "R", "R"],
                section_label="Recent Transactions",
            )

            # Checks cleared
            if stmt.checks_cleared:
                pdf.sub_title("Checks Cleared")
                chk_rows = [[c["date"], f"#{c['number']}", fmt_dollar(c["amount"])]
                            for c in stmt.checks_cleared]
                cw_chk = [30, 30, 30]
                pdf.draw_table(["Date", "Check #", "Amount"], chk_rows,
                               col_widths=cw_chk, col_aligns=["L", "C", "R"])

    # ---- SAVE ----
    pdf.output(output_path)
    print(f"Report saved to: {output_path}")

    # ---- AUDIT ----
    if audit_path:
        _write_audit_csv(statements, audit_path, mask_personal)


# ---------------------------------------------------------------------------
# Audit / Masking
# ---------------------------------------------------------------------------


def _mask_desc(description: str) -> str:
    desc = description
    desc = re.sub(r'JACOB PFEIFF', '[NAME REDACTED]', desc, flags=re.IGNORECASE)
    desc = re.sub(r'PFEIFF', '[NAME REDACTED]', desc, flags=re.IGNORECASE)
    desc = re.sub(
        r"\b\d+\s+(?:[NSEW]\s+)?"
        r"[A-Z0-9.'-]+(?:\s+[A-Z0-9.'-]+){0,4}\s+"
        r"(?:RD|ROAD|ST|STREET|AVE|AVENUE|DR|DRIVE|LN|LANE|"
        r"WAY|BLVD|BOULEVARD)\b",
        "[ADDRESS REDACTED]",
        desc,
        flags=re.IGNORECASE,
    )
    desc = re.sub(r"\bX{2,}\d{4}\b", "[ID REDACTED]", desc)
    return desc


def _write_audit_csv(statements: list[Statement], audit_path: str,
                     mask_personal: bool) -> None:
    import csv
    with open(audit_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Statement", "PostDate", "Description", "Amount",
                         "Type", "Balance", "Category"])
        for stmt in statements:
            for tx in stmt.transactions:
                desc = _mask_desc(tx.description) if mask_personal else tx.description
                writer.writerow([
                    stmt.month_label,
                    tx.post_date,
                    desc,
                    str(tx.amount) if tx.is_credit else str(-tx.amount),
                    "Credit" if tx.is_credit else "Debit",
                    str(tx.balance),
                    tx.category,
                ])
    print(f"Audit file saved to: {audit_path}")

    # Show Other transactions
    other_tx = [(s.month_label, tx) for s in statements
                for tx in s.transactions if tx.category == "Other"]
    if other_tx:
        print(f"\n{len(other_tx)} transaction(s) categorized as Other:", file=sys.stderr)
        for month, tx in other_tx:
            print(f"  [{month}] {tx.post_date}  {tx.description[:90]}  "
                  f"{'credit' if tx.is_credit else 'debit'} {tx.amount}",
                  file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Generate PDF bank statement reports from First Interstate Bank PDFs."
    )
    parser.add_argument(
        "--year", type=int,
        help="Filter to a specific year (e.g. 2026)",
    )
    parser.add_argument(
        "--month", type=int, choices=range(1, 13), metavar="1-12",
        help="Filter to a specific month (1-12). Requires --year or infers from statements.",
    )
    parser.add_argument(
        "-o", "--output", type=str, default=None,
        help="Output PDF path (default: personal_financial_report_<year>.pdf or personal_financial_report_<month>_<year>.pdf)",
    )
    parser.add_argument(
        "-d", "--directory", type=str, default=SCRIPT_DIR,
        help="Directory containing statement PDFs (default: script directory)",
    )
    parser.add_argument(
        "--mode", type=str, choices=["combined", "monthly", "yearly"],
        default=None,
        help="Report mode: combined (yearly+monthly), yearly only, or monthly only",
    )
    parser.add_argument(
        "--audit", action="store_true",
        help="Write an audit CSV with every transaction and its category",
    )
    parser.add_argument(
        "--mask", action="store_true",
        help="Redact personal names and addresses in output",
    )
    parser.add_argument(
        "--allow-mismatch", action="store_true",
        help="Generate report even if reconciliation fails",
    )
    args = parser.parse_args()

    pdf_files = find_pdfs(args.directory)
    if not pdf_files:
        print(f"No PDF files found in {args.directory}", file=sys.stderr)
        sys.exit(1)

    statements = []
    for pdf_path in pdf_files:
        text = extract_text(pdf_path)
        stmt = parse_statement(text)
        if stmt.transactions:
            statements.append(stmt)

    if not statements:
        print("No valid statements found.", file=sys.stderr)
        sys.exit(1)

    # Determine mode
    if args.mode and args.month and args.mode == "yearly":
        print("Error: --mode yearly and --month are incompatible.",
              file=sys.stderr)
        sys.exit(1)
    if args.mode:
        mode = args.mode
    elif args.month:
        mode = "monthly"
    elif args.year and not args.month:
        mode = "yearly"
    else:
        mode = "combined"

    # Auto-detect year for --month when --year is missing
    if args.month and not args.year:
        matching_years = sorted({
            s.year for s in statements if s.month == args.month
        })
        if not matching_years:
            print(
                f"No statements found for month {args.month}.",
                file=sys.stderr,
            )
            sys.exit(1)
        args.year = matching_years[-1]
        if len(matching_years) > 1:
            print(
                f"Note: --month without --year, using latest year {args.year} "
                f"(found: {matching_years}). Use --year to override.",
                file=sys.stderr,
            )

    # Determine output filename
    if args.output:
        output = args.output
    elif args.month:
        yr = args.year or statements[0].year
        output = os.path.join(args.directory, f"personal_financial_report_{args.month:02d}_{yr}.pdf")
    elif args.year:
        output = os.path.join(args.directory, f"personal_financial_report_{args.year}.pdf")
    else:
        output = os.path.join(args.directory, "personal_financial_report.pdf")

    # Determine audit path
    audit_path = None
    if args.audit:
        base = os.path.splitext(output)[0]
        audit_path = f"{base}_audit.csv"

    generate_report(statements, output, mode=mode,
                    target_year=args.year, target_month=args.month,
                    audit_path=audit_path, mask_personal=args.mask,
                    allow_mismatch=args.allow_mismatch)


if __name__ == "__main__":
    main()
