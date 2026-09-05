"""Personal financial report generation (PDF + optional audit CSV)."""

from __future__ import annotations

import csv
import re
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from ledgersight.categorizer import normalize_merchant
from ledgersight.parsers import fmt_dollar
from ledgersight.pdf_renderer import ReportPDF
from ledgersight.personal.categorizer import categorize_transactions
from ledgersight.personal.charts import (
    chart_category_by_month,
    chart_category_pie,
    chart_credits_vs_debits,
    chart_daily_balance_ledger_month,
    chart_weekly_balance_ledgers,
)
from ledgersight.personal.consolidation import (
    AccountLedger,
    ConsolidatedResult,
    MovementMatch,
    _to_date,
    balance_asof,
    consolidate,
    missing_coverage,
    running_balance_map,
)
from ledgersight.personal.insights import (
    FORECAST_HORIZON_DAYS,
    Budget,
    best_display_name,
    budget_category_rows,
    budget_month_rows,
    build_forecast,
    canon_merchant,
    category_spend_totals,
    dashboard_totals,
    detect_repeating_payments,
    display_merchant,
    estimate_income_groups,
    load_budget,
    month_series,
    typical_monthly_spending,
)
from ledgersight.personal.models import Statement, Transaction

EXCLUDED_MERCHANT_CATS = {
    "Transfers",
    "Checks",
    "Bank Fees",
    "Loan/Credit Payment",
    "Other",
}

DEBT_CATEGORY = "Loan/Credit Payment"
TRANSFER_CATEGORY = "Transfers"


# ---------------------------------------------------------------------------
# Rendering helpers (kept for tests / public use)
# ---------------------------------------------------------------------------


def build_monthly_table_rows(stmt: Statement) -> list[list[str]]:
    """Build the account summary rows for a single statement."""
    rows = [
        ["Account Type", stmt.account_type or "N/A"],
        ["Beginning Balance", fmt_dollar(stmt.beginning_balance)],
        ["Total Credits", fmt_dollar(stmt.total_credits)],
        ["Total Debits", fmt_dollar(stmt.total_debits)],
        ["Ending Balance", fmt_dollar(stmt.ending_balance)],
        ["Net Change", fmt_dollar(stmt.ending_balance - stmt.beginning_balance)],
        ["Credit Transactions", str(stmt.credit_count)],
        ["Debit Transactions", str(stmt.debit_count)],
    ]
    if stmt.account_type == "Credit Card":
        rows.extend(
            [
                ["Fees Charged", fmt_dollar(stmt.fees_charged)],
                ["Interest Charged", fmt_dollar(stmt.interest_charged)],
            ]
        )
    else:
        rows.extend(
            [
                ["Overdraft Fees", fmt_dollar(stmt.overdraft_fees)],
                ["Returned Item Fees", fmt_dollar(stmt.returned_item_fees)],
            ]
        )
    return rows


def _account_short(stmt: Statement) -> str:
    """Short display label for an account (e.g. 'First Interstate ****6781')."""
    inst = stmt.institution or "Account"
    return f"{inst} ****{stmt.account_number[-4:]}" if stmt.account_number else inst


def _ledger_short(ledger: AccountLedger) -> str:
    return (
        f"{ledger.institution} ****{ledger.account_number[-4:]}"
        if ledger.account_number
        else ledger.institution or "Account"
    )


STATUS_ORDER_ONLY = "Order differs \u2013 printed sequence reconciles"
STATUS_VERIFY = "Verify \u2013 possible parse error or missing row"


def _printed_sequence_consistent(stmt: Statement, is_card: bool) -> bool:
    """True when the printed per-row balances chain from the beginning balance
    to the ending balance in the statement's own row order.

    Some statements list rows grouped by section rather than by date, so a
    date-ordered recomputation disagrees with the printed column even though
    nothing is missing. When this returns True the disagreement is purely
    about ordering and is reported as informational rather than as a
    possible parse error.
    """
    if not any(tx.balance for tx in stmt.transactions):
        return False
    balance = stmt.beginning_balance
    for tx in stmt.transactions:
        delta = tx.amount if tx.is_credit else -tx.amount
        balance = balance - delta if is_card else balance + delta
        if tx.balance and abs(tx.balance - balance) > Decimal("0.005"):
            return False
    return abs(balance - stmt.ending_balance) <= Decimal("0.005")


def _running_balance_mismatches(ledger: AccountLedger) -> list[list[str]]:
    """Rows whose printed running balance disagrees with the recomputed one.

    The printed balance column (what the bank printed for that specific row)
    is compared against the balance immediately after that same transaction
    in the corrected, date-sorted ledger sequence - even when several
    transactions share a post date. The ledger's copy of each transaction is
    the earliest statement that listed it, so every printed balance is
    evaluated exactly once. Returns rows shaped for the review list:

    [statement_date, post_date, description, printed, recomputed,
     amount, source_page, status]

    ``status`` is ``STATUS_ORDER_ONLY`` when the statement's printed balances
    chain correctly in the statement's own row order (the bank simply listed
    rows out of date order), otherwise ``STATUS_VERIFY``.
    """
    run = running_balance_map(ledger)
    is_card = ledger.account_type == "Credit Card"
    seq_ok: dict[str, bool] = {}
    rows: list[list[str]] = []
    for tx in ledger.transactions:
        if not tx.balance:
            continue
        stmt = next(
            (s for s in ledger.statements if any(t is tx for t in s.transactions)),
            ledger.statements[0] if ledger.statements else None,
        )
        if stmt is None:
            continue
        source = Path(stmt.file_path).name if stmt.file_path else f"statement {stmt.statement_date}"
        recomputed = run[id(tx)]
        if abs(tx.balance - recomputed) <= Decimal("0.005"):
            continue
        if abs(recomputed + tx.balance) <= Decimal("0.005"):
            continue
        if stmt.statement_date not in seq_ok:
            seq_ok[stmt.statement_date] = _printed_sequence_consistent(stmt, is_card)
        rows.append(
            [
                stmt.statement_date,
                tx.post_date,
                tx.description[:44],
                fmt_dollar(tx.balance),
                fmt_dollar(recomputed),
                fmt_dollar(tx.amount),
                source,
                STATUS_ORDER_ONLY if seq_ok[stmt.statement_date] else STATUS_VERIFY,
            ]
        )
    return rows


def _to_terminal(year: int, month: int) -> date:
    """First day after month (year, month)."""
    return date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)


def _merge_ledgers_by_month(ledgers: list[AccountLedger]) -> list[Statement]:
    """Build one synthetic Statement per calendar month from all ledgers.

    Transactions are already deduplicated across overlapping statements, so
    monthly totals no longer double-count the duplicated savings rows. The
    beginning/ending balances are the sum of each covered account's
    reconstructed balance at the month boundaries.
    """
    buckets: dict[tuple[int, int], list[Transaction]] = defaultdict(list)
    balances: dict[tuple[int, int], list[tuple[Decimal, Decimal]]] = defaultdict(list)
    for ledger in ledgers:
        for month in ledger.months:
            buckets[(month.year, month.month)].extend(month.transactions)
            month_start = date(month.year, month.month, 1)
            month_end = _to_terminal(month.year, month.month) - timedelta(days=1)
            bal_start = balance_asof(ledger, month_start - timedelta(days=1))
            balances[(month.year, month.month)].append((bal_start, balance_asof(ledger, month_end)))

    result: list[Statement] = []
    for y, m in sorted(buckets):
        tx = buckets[(y, m)]
        credits = sum((t.amount for t in tx if t.is_credit), Decimal("0"))
        debits = sum((t.amount for t in tx if not t.is_credit), Decimal("0"))
        credit_count = sum(1 for t in tx if t.is_credit)
        debit_count = sum(1 for t in tx if not t.is_credit)
        starts = [b[0] for b in balances[(y, m)]]
        ends = [b[1] for b in balances[(y, m)]]
        result.append(
            Statement(
                statement_date=f"{m:02d}/01/{y}",
                account_number="",
                beginning_balance=sum(starts, Decimal("0")) if starts else Decimal("0"),
                ending_balance=sum(ends, Decimal("0")) if ends else Decimal("0"),
                total_credits=credits,
                total_debits=debits,
                credit_count=credit_count,
                debit_count=debit_count,
                transactions=tx,
            )
        )
    return result


def build_category_table_rows(statements: list[Statement]) -> list[list[str]]:
    """Build the overall debits-by-category table rows."""
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


def _build_top_merchants_from_tx(
    transactions: list[Transaction],
    top_n: int,
    mask_personal: bool,
) -> list[list[str]]:
    merchant_totals: dict[str, Decimal] = defaultdict(Decimal)
    display_names: dict[str, set[str]] = defaultdict(set)
    for tx in transactions:
        if tx.is_credit or tx.category in EXCLUDED_MERCHANT_CATS:
            continue
        name = display_merchant(normalize_merchant(tx.description))
        canon = canon_merchant(name)
        merchant_totals[canon] += tx.amount
        display_names[canon].add(name)

    rows = []
    for rank, (canon, amt) in enumerate(
        sorted(merchant_totals.items(), key=lambda x: x[1], reverse=True)[:top_n], start=1
    ):
        desc = best_display_name(display_names.get(canon, ())) or canon
        if mask_personal:
            desc = _mask_desc(desc)
        desc_short = desc[:70] + ("..." if len(desc) > 70 else "")
        rows.append([str(rank), desc_short, fmt_dollar(amt)])
    return rows


def build_top_merchants(
    statements: list[Statement],
    top_n: int = 15,
    mask_personal: bool = False,
) -> list[list[str]]:
    """Build ranked payee rows by total debits."""
    return _build_top_merchants_from_tx([tx for s in statements for tx in s.transactions], top_n, mask_personal)


def _statement_reconciles(stmt: Statement) -> tuple[bool, Decimal, Decimal, int, int]:
    """Per-statement arithmetic check: parsed totals vs reported summary."""
    parsed_credits = sum((tx.amount for tx in stmt.transactions if tx.is_credit), Decimal("0"))
    parsed_debits = sum((tx.amount for tx in stmt.transactions if not tx.is_credit), Decimal("0"))
    parsed_count = len(stmt.transactions)
    expected_count = stmt.credit_count + stmt.debit_count
    ok = parsed_count == expected_count and parsed_credits == stmt.total_credits and parsed_debits == stmt.total_debits
    return ok, parsed_credits, parsed_debits, parsed_count, expected_count


def _reconcile_statements(statements: list[Statement]) -> bool:
    """Verify parsed transaction totals against statement summaries."""
    passed = True
    for stmt in statements:
        ok, parsed_credits, parsed_debits, parsed_count, expected_count = _statement_reconciles(stmt)
        if not ok:
            passed = False
            print(
                f"WARNING: {stmt.month_label} reconciliation failed "
                f"parsed={parsed_count} expected={expected_count} "
                f"credits={parsed_credits}/{stmt.total_credits} "
                f"debits={parsed_debits}/{stmt.total_debits}",
                file=sys.stderr,
            )
    return passed


# ---------------------------------------------------------------------------
# Reconciliation & data-quality helpers
# ---------------------------------------------------------------------------


def _date_sanity(ledger: AccountLedger) -> list[dict]:
    """Flag transactions whose post date falls outside their statement period."""
    issues: list[dict] = []
    for stmt in ledger.statements:
        end = _to_date(stmt.statement_date)
        source = Path(stmt.file_path).name if stmt.file_path else f"statement {stmt.statement_date}"
        for tx in stmt.transactions:
            tx_date = _to_date(tx.post_date)
            problem = ""
            if tx_date > end:
                problem = f"dated after statement end {stmt.statement_date}"
            elif stmt.period_start and tx_date < _to_date(stmt.period_start):
                problem = f"dated before statement start {stmt.period_start}"
            if problem:
                issues.append(
                    {
                        "post_date": tx.post_date,
                        "description": tx.description[:44],
                        "amount": fmt_dollar(tx.amount),
                        "source": source,
                        "problem": problem,
                    }
                )
    return issues


def _checks_by_month(ledger: AccountLedger) -> dict[tuple[int, int], list[dict]]:
    """Group unique cleared checks by calendar month of the clear date."""
    seen: set[tuple] = set()
    grouped: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for stmt in ledger.statements:
        for check in stmt.checks_cleared:
            key = (check["number"], check["date"], str(check["amount"]))
            if key in seen:
                continue
            seen.add(key)
            d = _to_date(check["date"])
            grouped[(d.year, d.month)].append(check)
    for month_checks in grouped.values():
        month_checks.sort(key=lambda c: c["date"])
    return dict(sorted(grouped.items()))


def _ledger_month_statements(ledger: AccountLedger, year: int, month: int) -> list[Statement]:
    """Statements that list transactions posted during the given calendar month."""
    return [
        stmt
        for stmt in ledger.statements
        if any((d := _to_date(tx.post_date)).year == year and d.month == month for tx in stmt.transactions)
    ]


def _statement_window(ledger: AccountLedger | None, stmt: Statement) -> tuple[date | None, date, bool]:
    """(period start, period end, start_was_inferred) for one statement.

    Uses the printed period when the parser found one. Card statements often
    print only a closing date; because consecutive statements abut, the
    period then starts the day after the previous statement closed. Only
    when neither is available does the start fall back to the earliest
    printed daily balance, then to the first transaction date.
    """
    end = _to_date(stmt.statement_date)
    if stmt.period_start:
        try:
            return _to_date(stmt.period_start), end, False
        except ValueError:
            pass
    if ledger is not None:
        previous = [_to_date(s.statement_date) for s in ledger.statements if _to_date(s.statement_date) < end]
        # Only trust adjacency when the prior statement closed within a normal
        # monthly cycle; a longer gap means a statement is missing, and the
        # unknown start must not be stretched over months with no data.
        if previous and (end - max(previous)).days <= 35:
            return max(previous) + timedelta(days=1), end, True
    if stmt.daily_balances:
        try:
            return min(_to_date(db["date"]) for db in stmt.daily_balances), end, True
        except ValueError, KeyError:
            pass
    tx_dates = [_to_date(t.post_date) for t in stmt.transactions]
    return (min(tx_dates) if tx_dates else None), end, True


def _month_first_supported(ledger: AccountLedger, year: int, month: int) -> date:
    """Earliest day of the month backed by statement coverage for the ledger.

    Uses the statement window boundaries, not the first transaction date: a
    statement spanning May 30 - Jun 30 supports the whole of June even with
    no June 1 offset, and the savings statement Nov 29 - Feb 27 supports all
    of January from its opening balance. Only genuinely unsupported days
    (before the earliest coverage starts) are excluded.
    """
    month_start = date(year, month, 1)
    month_end = _to_terminal(year, month) - timedelta(days=1)
    candidates: list[date] = []
    for stmt in ledger.statements:
        start, end, _ = _statement_window(ledger, stmt)
        if start is None or end < month_start or start > month_end:
            continue
        if start <= month_start and end >= month_end:
            return month_start
        candidates.append(max(month_start, start))
    return min(candidates) if candidates else month_start


def _period_note(stmt: Statement, ledger: AccountLedger | None = None) -> str:
    start, end, inferred = _statement_window(ledger, stmt)
    if start is None:
        return f"{stmt.statement_date} (start date not stated)"
    if inferred:
        return f"{start:%m/%d/%Y} \u2013 {stmt.statement_date} (start inferred from prior statement)"
    return f"{start:%m/%d/%Y} \u2013 {stmt.statement_date}"


def _render_reconciliation(
    pdf: ReportPDF,
    result: ConsolidatedResult,
    period_end_year: int,
    period_end_month: int,
    mask_personal: bool = False,
) -> None:
    """Render the reconciliation and data-quality panel."""
    pdf.add_page()
    pdf.section_title("Reconciliation & Data Quality")

    status_rows: list[list[str]] = []
    for ledger in result.ledgers:
        arith_ok = all(_statement_reconciles(s)[0] for s in ledger.statements)
        cont_ok = all(c.ok for c in ledger.checks)
        date_issues = _date_sanity(ledger)
        missing = missing_coverage(ledger, period_end_year, period_end_month)
        status_rows.append(
            [
                _ledger_short(ledger),
                "PASSED" if arith_ok else "FAILED",
                "PASSED" if cont_ok else "FAILED",
                str(len(ledger.duplicates)),
                "none" if not date_issues else f"{len(date_issues)} issue(s)",
                ", ".join(missing) or "\u2013",
            ]
        )
    pdf.sub_title("Per-Account Status")
    pdf.draw_table(
        ["Account", "Totals", "Balances", "Duplicates Removed", "Date Issues", "Missing Coverage"],
        status_rows,
        col_widths=[45, 18, 19, 27, 28, 35],
        col_aligns=["L", "L", "L", "R", "L", "L"],
        row_font_size=7,
        row_height=4.5,
    )
    pdf.body_text(
        "\u201cTotals\u201d compares each statement\u2019s parsed transactions with the bank\u2019s printed "
        "credit/debit summary. \u201cBalances\u201d recomputes the balance after every transaction and compares it "
        "with each statement\u2019s printed ending balance.",
        size=8,
    )
    pdf.ln(2)

    pdf.sub_title("Statement Arithmetic")
    rows_all: list[list[str]] = []
    for ledger in result.ledgers:
        for stmt in ledger.statements:
            ok, parsed_credits, parsed_debits, parsed_count, expected_count = _statement_reconciles(stmt)
            rows_all.append(
                [
                    _ledger_short(ledger),
                    stmt.statement_date,
                    _period_note(stmt, ledger),
                    "PASSED" if ok else "FAILED",
                    f"{parsed_count}/{expected_count}",
                ]
            )
    pdf.draw_table(
        ["Account", "Period End", "Statement Period", "Result", "Tx (parsed/expected)"],
        rows_all,
        col_widths=[44, 25, 52, 18, 33],
        col_aligns=["L", "L", "L", "L", "R"],
        row_font_size=7,
        row_height=4.5,
    )

    any_dup = any(len(led.duplicates) for led in result.ledgers)
    if any_dup:
        pdf.ln(2)
        pdf.sub_title("Duplicate Transactions Removed (overlapping statement windows)")
        dup_rows: list[list[str]] = []
        for ledger in result.ledgers:
            if not ledger.duplicates:
                continue
            dup_credits = sum(
                (d.transaction.amount for d in ledger.duplicates if d.transaction.is_credit), Decimal("0")
            )
            dup_debits = sum(
                (d.transaction.amount for d in ledger.duplicates if not d.transaction.is_credit), Decimal("0")
            )
            dup_rows.append(
                [
                    _ledger_short(ledger),
                    str(len(ledger.duplicates)),
                    fmt_dollar(dup_credits),
                    fmt_dollar(dup_debits),
                ]
            )
        pdf.draw_table(
            ["Account", "Rows Removed", "Credit $", "Debit $"],
            dup_rows,
            col_widths=[50, 28, 30, 30],
            col_aligns=["L", "R", "R", "R"],
            row_font_size=7,
        )
        pdf.body_text(
            "Banks print transactions more than once when a statement covers a window that overlaps "
            "another. Identical transactions are kept from the earliest statement that lists them.",
            size=8,
        )
        dup_detail = _duplicate_detail_rows(result.ledgers, mask_personal)
        if dup_detail:
            pdf.ln(1)
            detail_height = 10 + len(dup_detail) * 4.5 + 12
            if pdf.get_y() + detail_height > pdf.h - pdf.b_margin:
                pdf.add_page()
            pdf.sub_title("Duplicates Removed \u2013 Detail")
            pdf.draw_table(
                ["Account", "Statement", "Date", "Description", "Type", "Amount"],
                dup_detail,
                col_widths=[40, 24, 24, 52, 16, 26],
                col_aligns=["L", "L", "L", "L", "L", "R"],
                row_font_size=7,
                row_height=4.5,
            )

    date_issues_flat = [(led, x) for led in result.ledgers for x in _date_sanity(led)]
    if date_issues_flat:
        pdf.ln(2)
        pdf.sub_title("Date Out of Statement Period")
        pdf.draw_table(
            ["Account", "Issue"],
            [
                [_ledger_short(led), f"{x['post_date']}: {x['description']} \u2013 {x['problem']}"]
                for led, x in date_issues_flat
            ],
            col_widths=[45, 127],
            col_aligns=["L", "L"],
            row_font_size=7,
            row_height=4.5,
        )

    bal_issues = [(led, x) for led in result.ledgers for x in _running_balance_mismatches(led)]
    if bal_issues:
        pdf.ln(2)
        pdf.sub_title("Running Balances \u2013 Printed vs Recomputed")
        pdf.body_text(
            "Per-row balances are recomputed from the corrected transaction sequence - even rows that "
            "share a post date are checked individually. Statement-printed balances are kept in the "
            "audit CSV for reference. \u201cOrder differs\u201d means the statement simply lists rows out of "
            "date order and its printed column still chains to the closing balance; \u201cVerify\u201d rows "
            "can indicate a parse error or a missing row.",
            size=8,
        )
        pdf.draw_table(
            ["Account", "Statement", "Date", "Description", "Printed", "Computed", "Status"],
            [[_ledger_short(led), x[0], x[1], x[2], x[3], x[4], x[7]] for led, x in bal_issues],
            col_widths=[34, 20, 20, 46, 17, 17, 32],
            col_aligns=["L", "L", "L", "L", "R", "R", "L"],
            row_font_size=7,
            row_height=4.5,
        )

    matched = sum(m.matched for m in result.movements)
    pdf.ln(2)
    pdf.sub_title("Internal Money Movement")
    pdf.body_text(
        f"{matched} of {len(result.movements)} transfers / card payments were matched to an offsetting "
        "credit in another covered account (by transfer reference number or autopay). Unmatched entries "
        "moved money to accounts without statements (e.g. a business checking account), were card payments "
        "outside the \u00b15-day window, or fell in months with no statement coverage.",
        size=8,
    )


# ---------------------------------------------------------------------------
# Spending overview
# ---------------------------------------------------------------------------


def _cash_and_debt_change(ledgers: list[AccountLedger]) -> tuple[Decimal, Decimal]:
    """(Change in cash & savings, reduction in credit-card debt)."""
    cash_change = Decimal("0")
    debt_reduction = Decimal("0")
    for ledger in ledgers:
        net = sum((t.amount if t.is_credit else -t.amount) for t in ledger.transactions)
        if "CARD" in ledger.account_type.upper():
            debt_reduction += net
        else:
            cash_change += net
    return cash_change, debt_reduction


def _flow_split(result: ConsolidatedResult) -> dict[str, Decimal]:
    """One decomposition of total debits, shared by cover and dashboard."""
    ledger_by_acct = {led.account_number: led for led in result.ledgers}
    all_tx = result.all_transactions
    total_credits = sum((t.amount for t in all_tx if t.is_credit), Decimal("0"))
    total_debits = sum((t.amount for t in all_tx if not t.is_credit), Decimal("0"))
    matched_card = Decimal("0")
    matched_transfer = Decimal("0")
    for m in result.movements:
        if m.matched:
            to = ledger_by_acct.get(m.to_account)
            if to is not None and "CARD" in to.account_type.upper():
                matched_card += m.amount
            else:
                matched_transfer += m.amount
    debt_total = sum((t.amount for t in all_tx if not t.is_credit and t.category == DEBT_CATEGORY), Decimal("0"))
    transfer_total = sum(
        (t.amount for t in all_tx if not t.is_credit and t.category == TRANSFER_CATEGORY), Decimal("0")
    )
    return {
        "total_credits": total_credits,
        "total_debits": total_debits,
        "matched_card": matched_card,
        "matched_transfer": matched_transfer,
        "other_debt": max(debt_total - matched_card, Decimal("0")),
        "unmatched": max(transfer_total - matched_transfer, Decimal("0")),
        "spending": total_debits - debt_total - transfer_total,
    }


def _spending_overview(
    ledgers: list[AccountLedger],
    movements: list[MovementMatch],
    total_credits: Decimal,
    total_debits: Decimal,
) -> list[list[str]]:
    """Rows separating debt payments / transfers / spending from total debits."""
    flow = _flow_split(ConsolidatedResult(ledgers=ledgers, movements=movements))
    return [
        ["Total Credits (all accounts)", str(total_credits)],
        ["Total Debits (all accounts)", str(total_debits)],
        ["\u2013 Payments to covered credit cards", str(flow["matched_card"])],
        ["\u2013 Other loan/card payments", str(flow["other_debt"])],
        ["\u2013 Internal transfers (matched)", str(flow["matched_transfer"])],
        ["\u2013 Unmatched transfers / outflows", str(flow["unmatched"])],
        ["= Spending (all other debits)", str(flow["spending"])],
    ]


# ---------------------------------------------------------------------------
# Insights renderers (dashboard, budget, recurring, review, corrections)
# ---------------------------------------------------------------------------


def _cadence_label(days: int) -> str:
    if days == 7:
        return "weekly"
    if days == 14:
        return "every 2 weeks"
    if 28 <= days <= 31:
        return "monthly"
    if 14 < days < 28:
        return f"every {days} days"
    if days == 60:
        return "every 2 months"
    if days == 91:
        return "quarterly"
    return f"every {days} days"


def _render_dashboard(pdf: ReportPDF, result: ConsolidatedResult, start: date, end: date) -> None:
    """One-page at-a-glance: income/debt split, snapshot, month-by-month table."""
    pdf.add_page()
    pdf.section_title("Consolidated Dashboard")
    totals = dashboard_totals(result, end)
    flow = _flow_split(result)

    metric_rows = [
        ["Total Credits (all covered accounts)", fmt_dollar(totals["total_credits"])],
        ["  Earned Income (Payroll & cash deposits)", fmt_dollar(totals["earned_income"])],
        ["    less Refunds (returned deposits etc.)", fmt_dollar(-totals["refunds"])],
        ["  Transfers in (from your other accounts)", fmt_dollar(totals["transfers_in"])],
        ["  Other credits (card autopays, reimbursements)", fmt_dollar(totals["other_credits"])],
        ["Total Debits (all covered accounts)", fmt_dollar(totals["total_debits"])],
        ["  Spending (debits excl. transfers & loan/card payments)", fmt_dollar(flow["spending"])],
        ["  Other loan/card payments", fmt_dollar(flow["other_debt"])],
        ["  Unmatched transfers / unresolved outflows", fmt_dollar(flow["unmatched"])],
        ["Net Cash Flow (Earned Income \u2212 Spending)", fmt_dollar(totals["earned_income"] - flow["spending"])],
        [
            "Change in Cash & Card Debt (observed balance movements)",
            fmt_dollar(totals["total_credits"] - totals["total_debits"]),
        ],
    ]
    pdf.draw_table(
        ["Metric", "Amount"],
        metric_rows,
        col_widths=[112, 40],
        col_aligns=["L", "R"],
        row_font_size=7.5,
        row_height=4.8,
    )
    pdf.body_text(
        "\u201cEarned Income\u201d is Payroll/Deposit/Government credits minus refunds - so a returned "
        "security deposit or a subscription cash-back is shown on its own line, not counted as earned. "
        "\u201cSpending\u201d uses the same rule as the cover page: every debit except internal transfers "
        "and loan/card payments, including items still awaiting a category. The final line is the "
        "observed improvement: total credits minus total debits equals the change in cash & savings plus "
        "the reduction in card debt across the covered accounts.",
        size=8,
    )
    pdf.ln(3)

    pdf.sub_title("Current Snapshot \u2013 balances by account")
    snap_rows = []
    for acct in totals["cash_accounts"] + totals["debt_accounts"]:
        kind = "Cash & Savings" if acct["account_type"] != "Credit Card" else "Credit-Card Debt"
        snap_rows.append([kind, acct["label"], acct["as_of"] or "\u2013", fmt_dollar(Decimal(acct["balance"]))])
    pdf.draw_table(
        ["Kind", "Account", "Reported As Of", "Balance"],
        snap_rows,
        col_widths=[38, 58, 32, 34],
        col_aligns=["L", "L", "L", "R"],
        row_font_size=7.5,
        row_height=4.8,
        section_label="Dashboard Snapshot",
    )
    pdf.ln(3)

    pdf.sub_title("Month-by-Month")
    month_rows: list[list[str]] = []
    notes: set[str] = set()
    for (year, month), entry in month_series(result.ledgers).items():
        net = entry["income"] - entry["spending"]
        rate = ""
        if entry["income"] > 0:
            rate = f"{net / entry['income'] * 100:.1f}%"
        month_rows.append(
            [
                f"{date(year, month, 1):%B %Y}",
                fmt_dollar(entry["income"]),
                fmt_dollar(entry["spending"]),
                fmt_dollar(net),
                rate,
                fmt_dollar(entry["cash"]),
                fmt_dollar(entry["debt"]),
            ]
        )
        if entry["refunds"] > 0:
            notes.add("Refunds (e.g. the $300 returned security deposit) are excluded from Income.")
        if entry["cash_accounts"] < entry["cash_accounts_total"]:
            notes.add(
                "Cash & Savings sums only the accounts with a statement that month \u2013 e.g. the savings "
                "account reported no July/August activity. See Missing Statement Coverage on the cover."
            )
    pdf.draw_table(
        ["Month", "Income", "Spending", "Net", "Savings Rate", "Cash & Savings", "Card Debt"],
        month_rows,
        col_widths=[27, 27, 27, 26, 24, 28, 27],
        col_aligns=["L", "R", "R", "R", "R", "R", "R"],
        section_label="Dashboard",
    )
    for note in sorted(notes):
        pdf.body_text(f"\u2022 {note}", size=7.5)
    pdf.body_text(
        "Months outside the covered statements are omitted; Savings Rate is blank when no income was "
        "recorded that month. \u201cIncome\u201d here is earned income (excluding refunds).",
        size=8,
    )


def _paragraph_budget_template(pdf: ReportPDF) -> None:
    pdf.body_text(
        "Budget not configured \u2013 budget-vs-actual is not shown. Create \u201cbudget.yaml\u201d in the "
        "data folder (or pass --budget <path>) with monthly income, per-category caps and an optional "
        "overall spending limit. The schema is documented in the project README.",
        size=8,
    )


def _render_budget(
    pdf: ReportPDF,
    budget: Budget | None,
    result: ConsolidatedResult,
    start: date,
    end: date,
) -> None:
    """Budget vs actual page. Shows guidance when no budget.yaml exists."""
    pdf.add_page()
    pdf.section_title("Budget vs Actual")
    if budget is None or not budget.configured:
        _paragraph_budget_template(pdf)
        return
    pdf.body_text(
        f"Period: {start:%B %Y} through {end:%B %Y}. Budgets are monthly; the \u201cactual\u201d column "
        "is total spending \u00f7 months covered so far.",
        size=8,
    )
    spend = category_spend_totals(result.ledgers)
    cat_rows = budget_category_rows(budget, spend, start, end)
    rows: list[list[str]] = []
    for r in cat_rows:
        if r["budget"] > 0:
            pct = f"{r['variance'] / r['budget'] * 100:+.1f}%"
        else:
            pct = "\u2014"
        rows.append([r["category"], fmt_dollar(r["budget"]), fmt_dollar(r["average"]), fmt_dollar(r["variance"]), pct])
    pdf.sub_title("Category \u2013 Monthly Budget vs Actual")
    pdf.draw_table(
        ["Category", "Monthly Budget", "Monthly Actual", "Over / Under", "% of Budget"],
        rows,
        col_widths=[48, 34, 34, 36, 34],
        col_aligns=["L", "R", "R", "R", "R"],
        section_label="Budget by Category",
    )
    pdf.ln(2)
    pdf.sub_title("Month-by-Month Totals")
    month_rows = [
        [r["month"], fmt_dollar(r["budget"]), fmt_dollar(r["actual"]), "" if r["used"] is None else f"{r['used']}%"]
        for r in budget_month_rows(budget, month_series(result.ledgers))
    ]
    pdf.draw_table(
        ["Month", "Budget", "Actual", "% of Budget Used"],
        month_rows,
        col_widths=[40, 34, 34, 34],
        col_aligns=["L", "R", "R", "R"],
        section_label="Budget by Month",
    )
    if budget.income_monthly > 0:
        inc_total = dashboard_totals(result, end)["income"]
        months = max(1, (end.year - start.year) * 12 + (end.month - start.month) + 1)
        pdf.body_text(
            f"Planned income {fmt_dollar(budget.income_monthly)}/mo vs actual {fmt_dollar(inc_total / Decimal(months))}"
            f"/mo over the covered period.",
            size=8,
        )


def _render_recurring_and_forecast(pdf: ReportPDF, result: ConsolidatedResult, end: date) -> None:
    """Detected recurring payments and a near-term cash forecast."""
    pdf.add_page()
    pdf.section_title("Recurring Payments & Forecast")
    repeating = detect_repeating_payments(result.all_transactions, as_of=end)
    if not repeating:
        pdf.body_text(
            "No recurring patterns were detected. Recurrence requires at least three occurrences of a "
            "payment or income item on a consistent interval with a stable amount.",
            size=9,
        )
        return
    pdf.body_text(
        "Recurring items are detected by grouping by canonical merchant (so a merchant that changed its "
        "payment rail mid-year stays one payee) and requiring at least three occurrences on a consistent "
        "interval (within 25% of the median, weekly to quarterly) with a stable amount (within 10% or $2). "
        "Income = payroll/deposits; everything else is a bill. \u201cInactive\u201d groups last occurred "
        "longer ago than twice their cadence and are NOT forecast.",
        size=8,
    )
    pdf.ln(2)
    pdf.sub_title("Detected Recurring Payments")
    rep_rows = [
        [
            r.payee,
            r.category,
            "Income" if r.income else "Bill",
            _cadence_label(r.cadence_days),
            str(r.occurrences),
            r.amount_range,
            f"{r.last_date:%m/%d/%Y}",
            "Inactive \u2013 verify"
            if not r.active
            else ("Active \u2013 verify cadence" if r.needs_confirm else "Active"),
        ]
        for r in repeating
    ]
    pdf.draw_table(
        ["Payee", "Category", "Type", "Cadence", "# Times", "Typical Amount", "Last", "Status"],
        rep_rows,
        col_widths=[42, 18, 14, 24, 13, 26, 21, 28],
        col_aligns=["L", "L", "L", "L", "R", "R", "R", "L"],
        section_label="Recurring Payments",
    )

    totals = dashboard_totals(result, end)
    cash = totals["cash"]
    as_of_parts = [f"{a['label']} \u2013 as of {a['as_of']}" for a in totals["cash_accounts"]]
    verified = None
    dates = [datetime.strptime(a["as_of"], "%m/%d/%Y").date() for a in totals["cash_accounts"] if a["as_of"]]
    if dates:
        ref = min(dates)
        liquid = [led for led in result.ledgers if led.account_type != "Credit Card"]
        verified = sum((balance_asof(led, ref) for led in liquid), Decimal("0"))
    estimated = estimate_income_groups(result.all_transactions, end)
    forecast = build_forecast(repeating, cash, end, estimated_income=estimated)
    if forecast:
        pdf.ln(2)
        pdf.sub_title(f"Near-Term Cash Forecast (next {FORECAST_HORIZON_DAYS} days)")
        pdf.body_text(
            f"Starting from the latest available balances ({'; '.join(as_of_parts)}) for a combined "
            f"starting cash figure of {fmt_dollar(cash)}. Because the accounts report on different dates, "
            "this is an approximate snapshot, not a single-date figure"
            + (
                f"; as of {ref:%m/%d/%Y} the verified combined balance was {fmt_dollar(verified)}."
                if verified is not None
                else "."
            )
            + " Active detected bills, plus variable income (payroll/deposits) estimated from its observed "
            f"median cadence and amount, are projected for the full {FORECAST_HORIZON_DAYS} days \u2013 "
            "e.g. a 7-day payroll cycle produces ~13 arrivals, each marked \u201c\u2013 estimated\u201d. "
            "This is a projection of selected detected transactions, not a complete cash plan \u2013 "
            "confirm schedules and amounts before relying on it.",
            size=7.5,
        )
        fc_rows = [
            [
                f"{e.event_date:%m/%d/%Y}",
                e.payee,
                "+" if e.income else "\u2212",
                fmt_dollar(e.amount),
                fmt_dollar(e.projected),
            ]
            for e in forecast
        ]
        pdf.draw_table(
            ["Date", "Payee", "In/Out", "Amount", "Projected Cash"],
            fc_rows,
            col_widths=[20, 56, 16, 42, 52],
            col_aligns=["L", "L", "C", "R", "R"],
            section_label="Forecast",
        )

        pdf.ln(1)
        pdf.sub_title("Estimated expenses beyond the detected bills")
        months_covered = max(1, len(month_series(result.ledgers)))
        typical = typical_monthly_spending(result.ledgers, months_covered)
        covered_cats = {r.category for r in repeating if r.active and not r.income and r.category}
        other_rows = [
            [cat, fmt_dollar(amt)]
            for cat, amt in sorted(typical.items(), key=lambda kv: kv[1], reverse=True)
            if cat not in covered_cats and amt > 0
        ]
        pdf.draw_table(
            ["Category", "Typical/Month"],
            other_rows[:10],
            col_widths=[100, 40],
            col_aligns=["L", "R"],
            section_label="Expected Other Spending",
        )
        total_other = sum((amt for cat, amt in typical.items() if cat not in covered_cats), Decimal("0"))
        detected_net = sum((e.amount if e.income else -e.amount for e in forecast), Decimal("0"))
        horizon_month = FORECAST_HORIZON_DAYS / 30.0
        runway_end = cash + detected_net - total_other * Decimal(str(horizon_month))
        pdf.body_text(
            f"The forecast above covers only {len([r for r in repeating if r.active])} detected "
            f"recurring item(s) plus estimated income. Categories without a detected schedule "
            f"(like {', '.join(row[0] for row in other_rows[:3])}) run roughly "
            f"{fmt_dollar(total_other)}/month in the covered period \u2013 combining those with the "
            f"detected line items puts projected cash near {fmt_dollar(runway_end)} at the "
            f"{FORECAST_HORIZON_DAYS}-day mark. Categories with no data at all are omitted.",
            size=7.5,
        )


def _render_items_needing_review(pdf: ReportPDF, result: ConsolidatedResult, mask_personal: bool = False) -> None:
    """Actionable list of transactions and statements that warrant manual review."""
    pdf.add_page()
    pdf.section_title("Items Needing Review")
    rows: list[list[str]] = []

    for ledger in result.ledgers:
        order_only: dict[str, list[list[str]]] = defaultdict(list)
        for mismatch in _running_balance_mismatches(ledger):
            if mismatch[7] == STATUS_ORDER_ONLY:
                order_only[mismatch[0]].append(mismatch)
                continue
            rows.append(
                [
                    _ledger_short(ledger),
                    mismatch[1],
                    mismatch[5],
                    mismatch[2],
                    f"Printed {mismatch[3]} vs recomputed {mismatch[4]} on statement {mismatch[0]}",
                    mismatch[6],
                    mismatch[7],
                ]
            )
        # One informational line per statement whose rows are merely listed
        # out of date order - nothing to correct, but worth knowing when
        # comparing the tables against the paper statement.
        for stmt_date, group in sorted(order_only.items()):
            rows.append(
                [
                    _ledger_short(ledger),
                    stmt_date,
                    "\u2013",
                    f"{len(group)} row(s) on statement {stmt_date}",
                    "Statement lists these rows out of date order; its printed balances chain correctly in "
                    "that order and close to the statement total. Tables show date-order balances.",
                    group[0][6],
                    "Informational",
                ]
            )

    for ledger in result.ledgers:
        for tx in ledger.transactions:
            if tx.category != "Other":
                continue
            desc = _mask_desc(tx.description) if mask_personal else tx.description
            rows.append(
                [
                    _ledger_short(ledger),
                    tx.post_date,
                    fmt_dollar(tx.amount),
                    desc[:60],
                    "Category not recognized \u2013 verify and recategorize",
                    _statement_source_for(ledger, tx),
                    "Unresolved",
                ]
            )

    for ledger in result.ledgers:
        for stmt in ledger.statements:
            ok, _, _, _, _ = _statement_reconciles(stmt)
            if not ok:
                rows.append(
                    [
                        _ledger_short(ledger),
                        stmt.statement_date,
                        "\u2013",
                        _period_note(stmt, ledger)[:60],
                        "Statement printed totals/counts differ from parsed transactions",
                        Path(stmt.file_path).name if stmt.file_path else f"statement {stmt.statement_date}",
                        "Verify arithmetic",
                    ]
                )

    for ledger in result.ledgers:
        for issue in _date_sanity(ledger):
            rows.append(
                [
                    _ledger_short(ledger),
                    issue["post_date"],
                    issue["amount"],
                    issue["description"],
                    f"Transaction {issue['problem']}",
                    issue["source"],
                    "Unresolved",
                ]
            )

    for movement in result.movements:
        if movement.matched:
            continue
        from_ledger = result.ledger_for(movement.from_account)
        label = _ledger_short(from_ledger) if from_ledger else movement.from_account
        rows.append(
            [
                label,
                movement.date,
                fmt_dollar(movement.amount),
                movement.description[:60],
                "Transfer/card payment with no offsetting credit on a covered account",
                "Movement log",
                "Unresolved",
            ]
        )

    if not rows:
        pdf.body_text("No transactions or statements currently require manual review.", size=9)
        return
    pdf.body_text(
        f"{len(rows)} item(s) found. Items surfaced automatically \u2013 confirm each one against the "
        "original statement before changing anything.",
        size=8,
    )
    pdf.draw_table(
        ["Account", "Date", "Amount", "Item", "Finding", "Source", "Status"],
        rows,
        col_widths=[22, 16, 18, 34, 40, 34, 22],
        col_aligns=["L", "L", "R", "L", "L", "L", "L"],
        section_label="Items Needing Review",
    )


def _statement_source_for(ledger: AccountLedger, tx: Transaction) -> str:
    """Locate the statement file whose period includes a transaction."""
    tx_date = _to_date(tx.post_date)
    best: Statement | None = None
    for stmt in ledger.statements:
        end = _to_date(stmt.statement_date)
        start = _to_date(stmt.period_start) if stmt.period_start else None
        if start is not None and start <= tx_date <= end:
            if best is None or _to_date(best.statement_date) > end:
                best = stmt
        elif best is None and end >= tx_date:
            if best is None or _to_date(best.statement_date) > end:
                best = stmt
    if best and best.file_path:
        return Path(best.file_path).name
    return f"statement {best.statement_date}" if best else "\u2013"


def _render_corrections_log(pdf: ReportPDF, result: ConsolidatedResult, mask_personal: bool = False) -> None:
    """Chronological log of data-quality findings and the adjustments applied."""
    pdf.add_page()
    pdf.section_title("Corrections & Adjustments Log")

    def _stmt_source(ledger: AccountLedger, statement_date: str | None) -> str:
        if not statement_date:
            return "\u2013"
        for stmt in ledger.statements:
            if stmt.statement_date == statement_date:
                return Path(stmt.file_path).name if stmt.file_path else f"statement {statement_date}"
        return f"statement {statement_date}"

    entries: list[list[str]] = []

    for ledger in result.ledgers:
        for check in ledger.checks:
            if check.ok:
                continue
            entries.append(
                [
                    check.statement_date,
                    _ledger_short(ledger),
                    "Ending balance",
                    f"Printed {fmt_dollar(check.expected)} vs recomputed {fmt_dollar(check.computed)}",
                    "Recomputed balance used \u2013 verify: may indicate a parse error or missing row",
                    _stmt_source(ledger, check.statement_date),
                ]
            )

    for ledger in result.ledgers:
        for mismatch in _running_balance_mismatches(ledger):
            if mismatch[7] == STATUS_ORDER_ONLY:
                action = (
                    "Date-order balance shown; printed sequence (statement row order) reconciles \u2013 no data change"
                )
            else:
                action = "Balance column shows the recomputed value; printed kept in audit CSV \u2013 verify"
            entries.append(
                [
                    mismatch[1],
                    _ledger_short(ledger),
                    "Running balance",
                    f"{mismatch[2]} \u2013 printed {mismatch[3]} vs recomputed {mismatch[4]} ({mismatch[5]})",
                    action,
                    mismatch[6],
                ]
            )

    for ledger in result.ledgers:
        for dup in ledger.duplicates:
            desc = _mask_desc(dup.transaction.description) if mask_personal else dup.transaction.description
            entries.append(
                [
                    dup.transaction.post_date,
                    _ledger_short(ledger),
                    "Duplicate row",
                    desc[:50],
                    "Removed (earliest overlapping statement already lists this transaction)",
                    _stmt_source(ledger, dup.statement_date),
                ]
            )

    for ledger in result.ledgers:
        for stmt in ledger.statements:
            ok, _, _, _, _ = _statement_reconciles(stmt)
            if not ok:
                entries.append(
                    [
                        stmt.statement_date,
                        _ledger_short(ledger),
                        "Statement arithmetic",
                        "Printed credits/debits or counts differ from parsed values",
                        "Parsed transaction stream treated as authoritative",
                        _stmt_source(ledger, stmt.statement_date),
                    ]
                )

    if not entries:
        pdf.body_text("No corrections or adjustments were applied while building this report.", size=9)
        return

    pdf.body_text(
        "This report reconstructs balances and totals from the parsed transaction stream. When a "
        "statement disagrees, the recomputed (corrected) value is used, the printed value is preserved in "
        "the audit CSV, and the discrepancy is logged here with its source file so every adjustment has "
        "provenance. A matching closing total does not by itself certify every transaction balance - "
        "running-balance rows are logged for verification separately.",
        size=8,
    )
    entries.sort(key=lambda r: (r[0], r[1]))
    pdf.draw_table(
        ["Date", "Account", "Type", "Finding", "Action Taken", "Source"],
        entries,
        col_widths=[20, 28, 22, 50, 40, 26],
        col_aligns=["L", "L", "L", "L", "L", "L"],
        section_label="Corrections & Adjustments",
    )


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------


def generate_report(
    statements: list[Statement],
    output_path: str | Path,
    mode: str = "combined",
    target_month: int | None = None,
    target_year: int | None = None,
    audit_path: str | Path | None = None,
    mask_personal: bool = False,
    allow_mismatch: bool = False,
    transactions_csv_path: str | Path | None = None,
    budget_path: str | Path | None = None,
) -> bool:
    """Generate the personal financial report PDF (and optional audit CSV).

    Returns True when every post-render export check passed. The PDF is
    written either way; failures are printed to stderr so they are never
    silent.
    """
    pdf = ReportPDF("Personal Financial Report")

    # Filter by year/month if requested
    if target_year:
        statements = [s for s in statements if s.year == target_year]
    if target_month:
        statements = [s for s in statements if s.month == target_month]

    if not statements:
        print("No statements found matching filters.", file=sys.stderr)
        sys.exit(1)

    statements.sort(key=lambda s: (s.year, s.month, s.account_number))
    categorize_transactions(statements)

    # Per-statement reconciliation
    reconciled = _reconcile_statements(statements)
    if not reconciled:
        print("WARNING: reconciliation failed (see above).", file=sys.stderr)
        if not allow_mismatch:
            print(
                "Aborting. Use --allow-mismatch to force report generation.",
                file=sys.stderr,
            )
            sys.exit(1)

    result = consolidate(statements)

    end_stmt = max(result.all_statements, key=lambda s: _to_date(s.statement_date))
    first_stmt = min(result.all_statements, key=lambda s: _to_date(s.statement_date))
    period_end_year, period_end_month = end_stmt.year, end_stmt.month

    if first_stmt.period_start:
        cover_start_d = _to_date(first_stmt.period_start)
    else:
        tx_dates = [_to_date(tx.post_date) for tx in result.all_transactions]
        cover_start_d = min(tx_dates, default=_to_date(first_stmt.statement_date))
    cover_end_d = _to_date(end_stmt.statement_date)

    # ---- COVER PAGE ----
    pdf.add_page()
    pdf.ln(15)
    pdf.set_font("DJV", "B", 24)
    pdf.set_text_color(44, 62, 80)
    pdf.cell(0, 12, "Personal Financial Report", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)

    pdf.set_font("DJV", "", 11)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(
        0,
        7,
        f"{cover_start_d:%B %Y}  \u2013  {cover_end_d:%B %Y}",
        align="C",
        new_x="LMARGIN",
        new_y="NEXT",
    )
    n_stats = len(result.all_statements)
    n_accs = len(result.ledgers)
    dup_total = sum(len(led.duplicates) for led in result.ledgers)
    pdf.cell(
        0,
        7,
        f"{n_stats} statement(s) across {n_accs} account(s)"
        + (f" \u2013 {dup_total} duplicate row(s) removed" if dup_total else ""),
        align="C",
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.ln(10)

    # Accounts summary
    pdf.sub_title("Accounts \u2013 Ending Balances (as of statement date)")
    acct_rows = [
        [_ledger_short(led), led.account_type, led.as_of, fmt_dollar(led.ending_balance)] for led in result.ledgers
    ]
    pdf.draw_table(
        ["Account", "Type", "As of", "Ending Balance"],
        acct_rows,
        col_widths=[55, 35, 32, 35],
        col_aligns=["L", "L", "L", "R"],
        row_font_size=8,
        row_height=5.5,
    )
    pdf.ln(2)

    # Combined summary
    total_credits_val = sum((t.amount for t in result.all_transactions if t.is_credit), Decimal("0"))
    total_debits_val = sum((t.amount for t in result.all_transactions if not t.is_credit), Decimal("0"))
    net_flow = total_credits_val - total_debits_val
    cash_change, debt_reduction = _cash_and_debt_change(result.ledgers)

    summary_rows = [
        ["Total Credits", fmt_dollar(total_credits_val)],
        ["Total Debits", fmt_dollar(total_debits_val)],
        ["Change in Cash & Savings", fmt_dollar(cash_change)],
        ["Credit-Card Debt Reduction", fmt_dollar(debt_reduction)],
        ["= Change in Cash less Card Debt", fmt_dollar(net_flow)],
    ]
    cw = [pdf.w - pdf.l_margin - pdf.r_margin - 50, 50]
    for label, val in summary_rows:
        pdf.set_fill_color(245, 245, 245)
        pdf.set_font("DJV", "B", 10)
        pdf.set_text_color(50, 50, 50)
        pdf.cell(cw[0], 7, f"  {label}", fill=True)
        pdf.set_font("DJV", "", 10)
        pdf.cell(cw[1], 7, val, fill=True, align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.body_text(
        "\u201cCash\u201d is checking plus savings; the card-debt reduction is that amount lowered across the "
        "covered credit-card statements. The two components cover different account periods. This is not "
        "income or savings: it is the sum of balance changes across the covered accounts.",
        size=8,
    )
    pdf.ln(3)

    # Keep the spending block together on the cover instead of leaving a
    # dangling paragraph fragment on the next page.
    if pdf.get_y() + 85 > pdf.h - pdf.b_margin:
        pdf.add_page()

    # Spending split
    pdf.sub_title("Where Money Went")
    split_rows = [
        [label, fmt_dollar(Decimal(val))]
        for label, val in _spending_overview(
            result.ledgers,
            result.movements,
            total_credits_val,
            total_debits_val,
        )
    ]
    pdf.draw_table(
        ["Component", "Amount"],
        split_rows,
        col_widths=[70, 40],
        col_aligns=["L", "R"],
        row_font_size=8,
        row_height=5.5,
    )
    checks_total = sum(
        (
            tx.amount
            for led in result.ledgers
            for tx in led.transactions
            if tx.category == "Checks" and not tx.is_credit
        ),
        Decimal("0"),
    )
    pdf.body_text(
        "\u201cInternal transfers\u201d are Web/Zelle/PayPal transfers matched to a credit on another covered "
        "account. \u201cPayments to covered credit cards\u201d are debits that offset the \u201cPAYMENT - "
        "THANK YOU\u201d credits on the Capital One statements. \u201cOther loan/card payments\u201d and "
        "\u201cUnmatched transfers/outflows\u201d moved money to accounts without statements (Chime, JD Byrider, "
        "a business checking account) for which underlying spending is not visible. Checks "
        f"({fmt_dollar(checks_total)}) and other method-based rows remain inside Spending, so the "
        "spending figure is provisional until those outflows are confirmed.",
        size=8,
    )
    pdf.ln(3)

    # Coverage warnings
    if pdf.get_y() + 42 > pdf.h - pdf.b_margin:
        pdf.add_page()
    gaps = [
        (_ledger_short(ledger), label)
        for ledger in result.ledgers
        for label in missing_coverage(ledger, period_end_year, period_end_month)
    ]
    if gaps:
        pdf.sub_title("Missing Statement Coverage")
        pdf.draw_table(
            ["Account", "Calendar Month with No Data"],
            [[a, m] for a, m in gaps],
            col_widths=[55, 55],
            col_aligns=["L", "L"],
            row_font_size=8,
            row_height=5.5,
        )
        pdf.body_text(
            "The report ends as of the latest statement date, but the account/months above have no "
            "statement. Balances and totals for those months are not included.",
            size=8,
        )
    else:
        pdf.body_text("Statement coverage: complete through the report end date.", size=9)

    # Reconciliation summary on cover
    if allow_mismatch:
        pdf.body_text("Reconciliation: SKIPPED (--allow-mismatch)", size=9)
    elif reconciled:
        pdf.body_text("Reconciliation: PASSED (statement totals and reconstructed balances)", size=9)
    else:
        pdf.body_text("Reconciliation: FAILED (see Reconciliation & Data Quality page)", size=9)

    # ---- PERIOD OVERVIEW CHARTS ----
    if mode in ("combined", "yearly"):
        _render_dashboard(pdf, result, cover_start_d, cover_end_d)
        _render_budget(pdf, load_budget(budget_path), result, cover_start_d, cover_end_d)

        aggregated = _merge_ledgers_by_month(result.ledgers)
        period_label = f"{cover_start_d:%B %Y} \u2013 {cover_end_d:%B %Y}"

        pdf.add_page(orientation="L")
        pdf.section_title(f"{period_label} \u2013 Credits vs Debits (Calendar Months)")
        chart_buf = chart_credits_vs_debits(aggregated)
        pdf.embed_chart(
            chart_buf,
            w=pdf.w - pdf.l_margin - pdf.r_margin,
            caption="Scope: all covered accounts, duplicates removed, bucketed by transaction post date.",
        )
        chart_buf.close()

        pdf.add_page(orientation="L")
        pdf.section_title(f"{period_label} \u2013 Weekly Average Balance")
        weekly_chart = chart_weekly_balance_ledgers(result.ledgers)
        pdf.embed_chart(
            weekly_chart,
            w=pdf.w - pdf.l_margin - pdf.r_margin,
            caption="Daily balance per account is reconstructed from each statement's beginning balance "
            "after deduplicating overlaps; the covered period is "
            f"{cover_start_d:%B %d, %Y} through {cover_end_d:%B %d, %Y}.",
        )
        weekly_chart.close()

        pdf.add_page(orientation="L")
        pdf.section_title(f"{period_label} \u2013 Debits by Category per Month (Calendar Months)")
        cat_month_chart = chart_category_by_month(aggregated)
        pdf.embed_chart(cat_month_chart, w=pdf.w - pdf.l_margin - pdf.r_margin)
        cat_month_chart.close()

        pdf.add_page()
        pdf.section_title(f"{period_label} Overview")
        cat_pie = chart_category_pie(aggregated)
        pdf.embed_chart(cat_pie, w=140)
        cat_pie.close()

        pdf.sub_title("Debits by Category")
        cat_rows = build_category_table_rows(aggregated)
        cw_cat = [55, 35, 25]
        pdf.draw_table(
            ["Category", "Amount", "Share"],
            cat_rows,
            col_widths=cw_cat,
            col_aligns=["L", "R", "R"],
            section_label="Category Breakdown",
        )

        pdf.add_page()
        pdf.sub_title("Top Payees by Total Debits (All Covered Accounts)")
        merchant_rows = _build_top_merchants_from_tx(result.all_transactions, top_n=15, mask_personal=mask_personal)
        cw_merch = [10, 120, 35]
        pdf.draw_table(
            ["#", "Payee", "Total"],
            merchant_rows,
            col_widths=cw_merch,
            col_aligns=["R", "L", "R"],
            section_label="Top Payees by Total Debits",
        )

    # ---- INSIGHTS: REVIEW + RECURRING/FORECAST ----
    if mode in ("combined", "yearly"):
        _render_items_needing_review(pdf, result, mask_personal=mask_personal)
        _render_recurring_and_forecast(pdf, result, cover_end_d)

    # ---- RECONCILIATION PANEL ----
    _render_reconciliation(pdf, result, period_end_year, period_end_month, mask_personal=mask_personal)
    _render_corrections_log(pdf, result, mask_personal=mask_personal)

    # ---- MONTHLY DETAIL ----
    if mode in ("combined", "monthly"):
        for ledger in result.ledgers:
            checks_by_month = _checks_by_month(ledger)
            run_balances = running_balance_map(ledger)
            for month in ledger.months:
                # The header must be in place BEFORE add_page() so the page's
                # header reflects the section it starts, never the previous one.
                pdf.header_extra = f"{_ledger_short(ledger)} \u2013 {month.label}"
                pdf.add_page()
                pdf.section_title(f"{_ledger_short(ledger)} \u2013 {month.label}")
                covering = _ledger_month_statements(ledger, month.year, month.month)
                if covering:
                    lines = "; ".join(f"ending {s.statement_date} (period {_period_note(s, ledger)})" for s in covering)
                    pdf.body_text(f"Statement(s) containing this month: {lines}", size=8)

                month_start = date(month.year, month.month, 1)
                month_end = _to_terminal(month.year, month.month) - timedelta(days=1)
                bal_start = balance_asof(ledger, month_start - timedelta(days=1))
                bal_end = balance_asof(ledger, month_end)
                as_of = ledger.as_of or ledger.last_tx_date
                partial = as_of and _to_date(as_of) < month_end
                summary = [
                    ["Account Type", ledger.account_type or "N/A"],
                    ["Starting Balance", fmt_dollar(bal_start)],
                    ["Total Credits", fmt_dollar(month.credits)],
                    ["Total Debits", fmt_dollar(month.debits)],
                    ["Ending Balance", fmt_dollar(bal_end)],
                    ["Net Change", fmt_dollar(bal_end - bal_start)],
                    [
                        "Credit Transactions",
                        str(sum(1 for t in month.transactions if t.is_credit)),
                    ],
                    [
                        "Debit Transactions",
                        str(sum(1 for t in month.transactions if not t.is_credit)),
                    ],
                ]
                if partial:
                    summary.append(["Coverage", f"Partial \u2013 data through {as_of}"])

                first_supported = _month_first_supported(ledger, month.year, month.month)
                if first_supported > month_start:
                    pdf.chart_note = (
                        f"Statement coverage begins {first_supported:%B %d, %Y}; earlier dates in the month "
                        "are not plotted."
                    )
                else:
                    pdf.chart_note = ""
                pdf.sub_title("Account Summary")
                pdf.draw_table(
                    ["Metric", "Amount"],
                    summary,
                    col_widths=[55, 40],
                    col_aligns=["L", "R"],
                    row_font_size=8,
                    row_height=4.8,
                )

                # Daily balance chart
                daily_chart = chart_daily_balance_ledger_month(
                    ledger,
                    month.year,
                    month.month,
                    first_supported=first_supported,
                )
                pdf.embed_chart(daily_chart, w=185, caption=pdf.chart_note)
                daily_chart.close()

                # Category breakdown
                cat_totals_m: dict[str, Decimal] = defaultdict(Decimal)
                for tx in month.transactions:
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
                    pdf.draw_table(
                        ["Category", "Amount", "Share"],
                        cat_rows_m,
                        col_widths=cw_cat_m,
                        col_aligns=["L", "R", "R"],
                    )

                # Top merchants for this month
                merchant_m = _build_top_merchants_from_tx(month.transactions, top_n=10, mask_personal=mask_personal)
                if merchant_m:
                    pdf.sub_title("Top Payees by Total Debits")
                    cw_mm = [10, 120, 35]
                    pdf.draw_table(
                        ["#", "Payee", "Total"],
                        merchant_m,
                        col_widths=cw_mm,
                        col_aligns=["R", "L", "R"],
                        section_label="Top Payees by Total Debits",
                    )

                # Transactions — complete, oldest first, so the Balance column reads
                # like a bank ledger (each row's balance follows the previous row's).
                pdf.sub_title("Transactions")
                shown = list(month.transactions)
                csv_ref = ""
                if transactions_csv_path:
                    csv_ref = (
                        f" The same rows for every covered account are in "
                        f"{Path(transactions_csv_path).name}, saved next to this report and attached to this PDF."
                    )
                pdf.body_text(
                    f"All {len(shown)} transactions for this account-month, oldest first. Balance is the running "
                    f"balance recomputed after each row from the corrected transaction sequence.{csv_ref}",
                    size=8,
                )
                tx_rows = []
                for tx in shown:
                    raw_desc = tx.description
                    if mask_personal:
                        raw_desc = _mask_desc(raw_desc)
                    desc = raw_desc[:52] + ("..." if len(raw_desc) > 52 else "")
                    sign = "+" if tx.is_credit else "-"
                    tx_rows.append(
                        [
                            tx.post_date,
                            desc,
                            tx.category[:18],
                            f"{sign}{fmt_dollar(tx.amount)}",
                            fmt_dollar(run_balances[id(tx)]),
                        ]
                    )
                cw_tx = [22, 66, 26, 26, 26]
                pdf.draw_table(
                    ["Date", "Description", "Category", "Amount", "Balance"],
                    tx_rows,
                    col_widths=cw_tx,
                    col_aligns=["L", "L", "L", "R", "R"],
                    section_label="Transactions",
                    header_font_size=8,
                    row_font_size=8,
                    row_height=5.0,
                )

                # Checks cleared in this month
                month_checks = checks_by_month.get((month.year, month.month))
                if month_checks:
                    pdf.sub_title("Checks Cleared")
                    chk_rows = [[c["date"], f"#{c['number']}", fmt_dollar(c["amount"])] for c in month_checks]
                    cw_chk = [30, 30, 30]
                    pdf.draw_table(
                        ["Date", "Check #", "Amount"],
                        chk_rows,
                        col_widths=cw_chk,
                        col_aligns=["L", "C", "R"],
                    )

    # ---- SAVE ----
    run_maps_audit: dict[int, Decimal] = {}
    for ledger in result.ledgers:
        run_maps_audit.update(running_balance_map(ledger))

    attachments = []
    if transactions_csv_path:
        _write_transactions_csv(result, transactions_csv_path, mask_personal)
        attachments.append(transactions_csv_path)
    if audit_path:
        _write_audit_csv(statements, audit_path, mask_personal, run_maps_audit)
        attachments.append(audit_path)

    # Package the companion CSVs inside the PDF so a standalone PDF carries
    # the full audited transaction stream with it.
    for attach in attachments:
        try:
            pdf.embed_file(file_path=str(attach))
        except (OSError, ValueError) as exc:
            print(f"Note: could not attach {attach}: {exc}", file=sys.stderr)

    # Rendering is complete at this point; verify it before serialising so
    # problems are reported ahead of the file being handed off.
    check_problems, check_count = _post_render_checks(result, cover_end_d, pdf)
    if check_problems:
        print(f"Export checks: {check_count} checks, {len(check_problems)} FAILED", file=sys.stderr)
        for problem in check_problems:
            print(f"  ! {problem}", file=sys.stderr)
    else:
        print(f"Export checks: PASSED ({check_count} invariants verified)")

    pdf.output(str(output_path))
    print(f"Report saved to: {output_path}")
    return not check_problems


def _post_render_checks(result: ConsolidatedResult, as_of: date, pdf: ReportPDF | None = None) -> tuple[list[str], int]:
    """Programmatic export checks run after the PDF is written.

    Catches inconsistent totals, month/ledger count mismatches, broken
    running balances and lost/overflowing table rows so a bad report is
    flagged before it is handed off.
    """
    problems: list[str] = []
    checks = 0

    if pdf is not None:
        checks += 1
        if pdf.table_rows_drawn != pdf.table_rows_requested:
            problems.append(
                f"{pdf.table_rows_requested - pdf.table_rows_drawn} table row(s) were not drawn "
                f"({pdf.table_rows_drawn} of {pdf.table_rows_requested})"
            )
        checks += 1
        problems.extend(pdf.render_problems)

    totals = dashboard_totals(result, as_of)
    checks += 1
    parts = totals["earned_income"] + totals["refunds"] + totals["transfers_in"] + totals["other_credits"]
    if parts != totals["total_credits"]:
        problems.append(f"credit decomposition does not sum to Total Credits ({parts} vs {totals['total_credits']})")

    series = month_series(result.ledgers)
    checks += 1
    sum_income = sum((v["income"] + v["refunds"] for v in series.values()), Decimal("0"))
    if sum_income != totals["income_credits"]:
        problems.append(
            f"monthly income series does not sum to income credits ({sum_income} vs {totals['income_credits']})"
        )
    checks += 1
    sum_spending = sum((v["spending"] for v in series.values()), Decimal("0"))
    if sum_spending != totals["spending"]:
        problems.append(
            f"monthly spending series does not sum to total spending ({sum_spending} vs {totals['spending']})"
        )
    for ledger in result.ledgers:
        run = running_balance_map(ledger)
        checks += 1
        allocated = sum(len(month.transactions) for month in ledger.months)
        if allocated != len(ledger.transactions):
            problems.append(
                f"{ledger.label}: {allocated} transactions bucketed into months "
                f"vs {len(ledger.transactions)} in the ledger"
            )
        for tx in ledger.transactions:
            if id(tx) not in run:
                problems.append(
                    f"{ledger.label}: transaction {tx.post_date} {tx.description[:30]} missing running balance"
                )
                break
    return problems, checks


def _mask_desc(description: str) -> str:
    """Redact names and addresses from transaction descriptions."""
    desc = description
    desc = re.sub(r"JACOB PFEIFF", "[NAME REDACTED]", desc, flags=re.IGNORECASE)
    desc = re.sub(r"PFEIFF", "[NAME REDACTED]", desc, flags=re.IGNORECASE)
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


def _write_transactions_csv(
    result: ConsolidatedResult,
    transactions_path: str | Path,
    mask_personal: bool,
) -> None:
    """Write the consolidated, deduplicated transactions to CSV."""
    with open(transactions_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Account", "Account Type", "Date", "Description", "Category", "Amount", "Type", "Balance"])
        for ledger in result.ledgers:
            run_balances = running_balance_map(ledger)
            for tx in sorted(ledger.transactions, key=lambda t: (_to_date(t.post_date), str(t.amount))):
                desc = _mask_desc(tx.description) if mask_personal else tx.description
                amount = tx.amount if tx.is_credit else -tx.amount
                writer.writerow(
                    [
                        ledger.account_number,
                        ledger.account_type,
                        tx.post_date,
                        desc,
                        tx.category,
                        str(amount),
                        "Credit" if tx.is_credit else "Debit",
                        str(run_balances[id(tx)]),
                    ]
                )
    print(f"Transactions CSV saved to: {transactions_path}")


def _duplicate_detail_rows(ledgers: list[AccountLedger], mask_personal: bool) -> list[list[str]]:
    """Individual rows removed as duplicates, for the reconciliation panel."""
    rows: list[list[str]] = []
    for ledger in ledgers:
        for dup in ledger.duplicates:
            tx = dup.transaction
            desc = _mask_desc(tx.description) if mask_personal else tx.description
            rows.append(
                [
                    _ledger_short(ledger),
                    dup.statement_date,
                    tx.post_date,
                    desc[:52] + ("..." if len(desc) > 52 else ""),
                    "Credit" if tx.is_credit else "Debit",
                    fmt_dollar(tx.amount),
                ]
            )
    rows.sort(key=lambda r: (r[0], r[1], r[2], r[3]))
    return rows


def _write_audit_csv(
    statements: list[Statement],
    audit_path: str | Path,
    mask_personal: bool,
    run_maps: dict[int, Decimal] | None = None,
) -> None:
    """Write a CSV of every transaction with its category.

    ``Balance`` is the value the bank printed on the statement (where one was
    printed); ``Running`` is the report's recomputed per-transaction running
    balance, so printed vs report-generated values stay distinguishable.
    """
    run_maps = run_maps or {}
    with open(audit_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["Account", "Statement", "PostDate", "Description", "Amount", "Type", "Balance", "Category", "Running"]
        )
        for stmt in statements:
            for tx in stmt.transactions:
                desc = _mask_desc(tx.description) if mask_personal else tx.description
                amount = tx.amount if tx.is_credit else -tx.amount
                running = run_maps.get(id(tx))
                writer.writerow(
                    [
                        stmt.account_number,
                        stmt.month_label,
                        tx.post_date,
                        desc,
                        str(amount),
                        "Credit" if tx.is_credit else "Debit",
                        str(tx.balance) if tx.balance is not None else "",
                        tx.category,
                        str(running) if running is not None else "",
                    ]
                )
    print(f"Audit file saved to: {audit_path}")

    # Show Other transactions
    other_tx = [(s.month_label, tx) for s in statements for tx in s.transactions if tx.category == "Other"]
    if other_tx:
        print(f"\n{len(other_tx)} transaction(s) categorized as Other:", file=sys.stderr)
        for month, tx in other_tx:
            print(
                f"  [{month}] {tx.post_date}  {tx.description[:90]}  "
                f"{'credit' if tx.is_credit else 'debit'} {tx.amount}",
                file=sys.stderr,
            )
