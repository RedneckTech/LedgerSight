"""Personal financial report generation (PDF + optional audit CSV)."""

from __future__ import annotations

import csv
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

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
    apply_check_annotations,
    best_display_name,
    bill_calendar,
    budget_category_rows,
    budget_month_rows,
    build_forecast,
    canon_merchant,
    cash_outflow_baselines,
    category_spend_totals,
    check_register,
    complete_months,
    dashboard_totals,
    debt_log,
    detect_repeating_payments,
    display_merchant,
    estimate_income_groups,
    load_budget,
    load_check_annotations,
    month_series,
    subscription_review,
)
from ledgersight.personal.models import Statement, Transaction
from ledgersight.redaction import DataRedactor

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
        source = _source_ref(stmt, tx)
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


def _tx_in_period(tx: Transaction, target_year: int | None, target_month: int | None) -> bool:
    """True when a transaction's posting date falls inside the requested period."""
    if target_year is None and target_month is None:
        return True
    parts = tx.post_date.split("/")
    tx_year, tx_month = int(parts[2]), int(parts[0])
    if target_year is not None and tx_year != target_year:
        return False
    if target_month is not None and tx_month != target_month:
        return False
    return True


def _filter_result_by_period(
    result: ConsolidatedResult,
    target_year: int | None,
    target_month: int | None,
) -> ConsolidatedResult:
    """Return a result whose activity is limited to the requested period.

    Source statements are retained on the ledgers for reconciliation and
    opening-balance reconstruction, but transactions, months and matched
    movements are rebuilt from rows whose posting date falls in the period.
    """
    if target_year is None and target_month is None:
        return result

    from ledgersight.personal.consolidation import _month_buckets, match_movements

    filtered_ledgers: list[AccountLedger] = []
    for ledger in result.ledgers:
        txs = [tx for tx in ledger.transactions if _tx_in_period(tx, target_year, target_month)]
        filtered_ledgers.append(
            AccountLedger(
                institution=ledger.institution,
                account_number=ledger.account_number,
                account_type=ledger.account_type,
                statements=ledger.statements,
                transactions=txs,
                duplicates=ledger.duplicates,
                checks=ledger.checks,
                months=_month_buckets(txs),
            )
        )
    filtered_ledgers.sort(key=lambda led: (led.institution, led.account_number))
    movements = match_movements(filtered_ledgers)
    return ConsolidatedResult(ledgers=filtered_ledgers, movements=movements)


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
    redactor: DataRedactor | None = None,
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
        desc = _mask_desc(desc, redactor)
        desc_short = desc[:70] + ("..." if len(desc) > 70 else "")
        rows.append([str(rank), desc_short, fmt_dollar(amt)])
    return rows


def build_top_merchants(
    statements: list[Statement],
    top_n: int = 15,
    redactor: DataRedactor | None = None,
) -> list[list[str]]:
    """Build ranked payee rows by total debits."""
    return _build_top_merchants_from_tx([tx for s in statements for tx in s.transactions], top_n, redactor)


def _statement_reconciles(
    stmt: Statement,
) -> tuple[bool, Decimal, Decimal, int, int, Decimal, Decimal, bool]:
    """Per-statement reconciliation: parsed totals/counts and ending balance.

    For checking / savings accounts the balance formula is
    ``beginning + credits - debits = ending``; for credit cards it is
    ``beginning + debits - credits = ending`` because payments reduce the
    balance owed while purchases increase it.
    """
    parsed_credits = sum((tx.amount for tx in stmt.transactions if tx.is_credit), Decimal("0"))
    parsed_debits = sum((tx.amount for tx in stmt.transactions if not tx.is_credit), Decimal("0"))
    parsed_count = len(stmt.transactions)
    expected_count = stmt.credit_count + stmt.debit_count
    count_ok = parsed_count == expected_count
    credit_total_ok = parsed_credits == stmt.total_credits
    debit_total_ok = parsed_debits == stmt.total_debits

    is_card = stmt.account_type == "Credit Card"
    calculated_ending = (
        stmt.beginning_balance + parsed_debits - parsed_credits
        if is_card
        else stmt.beginning_balance + parsed_credits - parsed_debits
    )
    balance_ok = abs(calculated_ending - stmt.ending_balance) <= Decimal("0.005")

    ok = count_ok and credit_total_ok and debit_total_ok and balance_ok
    return (
        ok,
        parsed_credits,
        parsed_debits,
        parsed_count,
        expected_count,
        calculated_ending,
        stmt.ending_balance,
        balance_ok,
    )


def _reconcile_statements(statements: list[Statement]) -> bool:
    """Verify parsed transaction totals/counts and ending balances."""
    passed = True
    for stmt in statements:
        (
            ok,
            parsed_credits,
            parsed_debits,
            parsed_count,
            expected_count,
            calculated_ending,
            expected_ending,
            balance_ok,
        ) = _statement_reconciles(stmt)
        if not ok:
            passed = False
            parts: list[str] = []
            if parsed_count != expected_count:
                parts.append(f"parsed={parsed_count} expected={expected_count}")
            if parsed_credits != stmt.total_credits:
                parts.append(f"credits={parsed_credits}/{stmt.total_credits}")
            if parsed_debits != stmt.total_debits:
                parts.append(f"debits={parsed_debits}/{stmt.total_debits}")
            if not balance_ok:
                parts.append(f"ending={calculated_ending}/{expected_ending}")
            print(
                f"WARNING: {stmt.month_label} reconciliation failed " + " ".join(parts),
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
        for tx in stmt.transactions:
            source = _source_ref(stmt, tx)
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
    redactor: DataRedactor | None = None,
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
        "credit/debit summary. \u201cBalances\u201d validates the statement\u2019s opening balance plus/minus its "
        "credit/debit totals against the printed ending balance.",
        size=8,
    )
    pdf.ln(2)

    pdf.sub_title("Statement Arithmetic")
    rows_all: list[list[str]] = []
    for ledger in result.ledgers:
        for stmt in ledger.statements:
            ok, parsed_credits, parsed_debits, parsed_count, expected_count, calc_end, exp_end, bal_ok = (
                _statement_reconciles(stmt)
            )
            rows_all.append(
                [
                    _ledger_short(ledger),
                    stmt.statement_date,
                    _period_note(stmt, ledger),
                    "PASSED" if ok else "FAILED",
                    f"{parsed_count}/{expected_count}",
                    fmt_dollar(calc_end),
                    fmt_dollar(exp_end),
                    "OK" if bal_ok else "FAIL",
                ]
            )
    pdf.draw_table(
        [
            "Account",
            "Period End",
            "Statement Period",
            "Result",
            "Tx (parsed/expected)",
            "Calc Ending",
            "Printed Ending",
            "Balance",
        ],
        rows_all,
        col_widths=[36, 22, 44, 16, 26, 24, 24, 16],
        col_aligns=["L", "L", "L", "L", "R", "R", "R", "L"],
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
        dup_detail = _duplicate_detail_rows(result.ledgers, redactor)
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
    basis_counts = Counter(m.basis for m in result.movements if m.matched)
    reason_counts = Counter(m.unmatched_reason for m in result.movements if not m.matched)
    pdf.ln(2)
    pdf.sub_title("Internal Money Movement")
    basis_text = ", ".join(
        f"{n} by {label}"
        for key, label in (
            ("reference", "transfer reference number"),
            ("autopay", "card autopay"),
            ("amount+date", "equal amount within two days (no shared reference \u2013 verify)"),
        )
        if (n := basis_counts.get(key, 0))
    )
    pdf.body_text(
        f"{matched} of {len(result.movements)} transfers / card payments were matched to an offsetting "
        f"credit in another covered account ({basis_text}).",
        size=8,
    )
    if reason_counts:
        reason_rows = [
            [
                reason,
                str(n),
                fmt_dollar(sum((m.amount for m in result.movements if m.unmatched_reason == reason), Decimal("0"))),
            ]
            for reason, n in sorted(reason_counts.items(), key=lambda kv: -kv[1])
        ]
        pdf.draw_table(
            ["Why the remaining movements are unmatched", "Count", "Total"],
            reason_rows,
            col_widths=[120, 20, 32],
            col_aligns=["L", "R", "R"],
            row_font_size=7,
            row_height=4.5,
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


def _income_to_cash_bridge(totals: dict[str, Any], flow: dict[str, Decimal]) -> list[tuple[str, Decimal]]:
    """Walk from income to the observed change in cash & card debt.

    Every line is a real movement in the covered accounts, so the bridge
    sums exactly to total credits minus total debits (an export check
    verifies this). It exists because "income less categorized spending"
    alone says nothing about how much cash was kept: loan payments and
    transfers to outside accounts still have to leave.
    """
    income_less_spending = totals["earned_income"] - flow["spending"]
    return [
        ("Income (payroll + other deposits)", totals["earned_income"]),
        ("less categorized spending", -flow["spending"]),
        ("= Income less categorized spending", income_less_spending),
        ("less loan/card payments to lenders outside these statements", -flow["other_debt"]),
        ("less transfers to outside accounts / unresolved outflows", -flow["unmatched"]),
        ("plus refunds received", totals["refunds"]),
        ("plus transfers in from outside accounts", totals["transfers_in"] - flow["matched_transfer"]),
        ("plus other credits (net of payments to the covered cards)", totals["other_credits"] - flow["matched_card"]),
        ("= Observed change in cash & card debt", totals["total_credits"] - totals["total_debits"]),
    ]


def _render_dashboard(
    pdf: ReportPDF,
    result: ConsolidatedResult,
    start: date,
    end: date,
    redactor: DataRedactor | None = None,
) -> None:
    """One-page at-a-glance: credit/debit breakdown, income-to-cash bridge,
    snapshot, month-by-month table and month-end balances by account."""
    pdf.add_page()
    pdf.section_title("Consolidated Dashboard")
    totals = dashboard_totals(result, end)
    flow = _flow_split(result)

    metric_rows = [
        ["Total Credits (all covered accounts)", fmt_dollar(totals["total_credits"])],
        ["  Payroll (identified employer deposits)", fmt_dollar(totals["payroll"])],
        ["  Other deposits (source not confirmed)", fmt_dollar(totals["deposits"])],
        ["  Refunds (returned deposits etc.)", fmt_dollar(totals["refunds"])],
        ["  Transfers in (from your other accounts)", fmt_dollar(totals["transfers_in"])],
        ["  Other credits (card payment credits, adjustments)", fmt_dollar(totals["other_credits"])],
        ["Total Debits (all covered accounts)", fmt_dollar(totals["total_debits"])],
        ["  Categorized spending (every debit except transfers & loan/card payments)", fmt_dollar(flow["spending"])],
        ["  Payments to the covered Capital One cards", fmt_dollar(flow["matched_card"])],
        ["  Other loan/card payments (lenders outside these statements)", fmt_dollar(flow["other_debt"])],
        ["  Transfers to your other covered accounts (matched)", fmt_dollar(flow["matched_transfer"])],
        ["  Transfers to outside accounts / unresolved outflows", fmt_dollar(flow["unmatched"])],
    ]
    pdf.draw_table(
        ["Credits and debits, broken down", "Amount"],
        metric_rows,
        col_widths=[118, 34],
        col_aligns=["L", "R"],
        row_font_size=7.5,
        row_height=4.8,
    )
    pdf.body_text(
        "The indented lines sum to the total above them. \u201cOther deposits\u201d are Deposit/Government "
        "credits that are not identified payroll - confirm their source before treating them as earned. "
        "Refunds (e.g. the $300 returned security deposit) are listed on their own line and are not income. "
        "\u201cCategorized spending\u201d is the same figure as the cover page: every debit except internal "
        "transfers and loan/card payments, including items still awaiting a category.",
        size=7.5,
    )
    pdf.ln(2)

    pdf.sub_title("From income to the observed change in cash")
    bridge = _income_to_cash_bridge(totals, flow)
    pdf.draw_table(
        ["Step", "Amount"],
        [[label, fmt_dollar(amount)] for label, amount in bridge],
        col_widths=[118, 34],
        col_aligns=["L", "R"],
        row_font_size=7.5,
        row_height=4.8,
    )
    pdf.body_text(
        "\u201cIncome less categorized spending\u201d is not a savings figure: loan payments to lenders "
        "outside these statements and transfers to outside accounts still have to leave. The last line is "
        "what actually happened to the covered accounts over the period - the change in cash & savings plus "
        "the reduction in card debt.",
        size=7.5,
    )
    pdf.ln(2)

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
    pdf.ln(2)

    series = month_series(result.ledgers)
    pdf.sub_title("Month-by-Month")
    month_rows: list[list[str]] = []
    incomplete_months: list[str] = []
    for (year, month), entry in series.items():
        net = entry["income"] - entry["spending"]
        observed = entry["total_credits"] - entry["total_debits"]
        cash_cell = fmt_dollar(entry["cash"])
        missing_cash = [lbl for lbl in entry["missing"] if "Capital One" not in lbl]
        if missing_cash:
            cash_cell += " \u2020"
            incomplete_months.append(f"{date(year, month, 1):%B %Y}")
        month_rows.append(
            [
                f"{date(year, month, 1):%b %Y}",
                fmt_dollar(entry["payroll"]),
                fmt_dollar(entry["deposits"]),
                fmt_dollar(entry["spending"]),
                fmt_dollar(net),
                fmt_dollar(observed),
                cash_cell,
                fmt_dollar(entry["debt"]),
            ]
        )
    pdf.draw_table(
        [
            "Month",
            "Payroll",
            "Other Deposits",
            "Spending",
            "Income \u2212 Spending",
            "Observed Change",
            "Cash & Savings",
            "Card Debt",
        ],
        month_rows,
        col_widths=[20, 22, 24, 22, 26, 26, 24, 22],
        col_aligns=["L", "R", "R", "R", "R", "R", "R", "R"],
        section_label="Dashboard",
        header_font_size=6.5,
        row_font_size=7,
    )
    pdf.body_text(
        "\u201cIncome \u2212 Spending\u201d is income less categorized spending for the month, before loan "
        "payments and transfers to outside accounts - it is not a savings rate. \u201cObserved Change\u201d "
        "is every credit minus every debit that month across the covered accounts, i.e. how the combined "
        "cash and card position actually moved. Balances are month-end values.",
        size=7.5,
    )
    if incomplete_months:
        pdf.body_text(
            "\u2020 Combined cash for these months covers only the accounts with a statement; the missing "
            "account's activity and balance are unknown (see the table below), so the combined figure is "
            "not comparable with other months.",
            size=7.5,
        )

    pdf.ln(2)
    pdf.sub_title("Month-end balances by account")
    labels = [led.label for led in result.ledgers]
    short = {led.label: _ledger_short(led) for led in result.ledgers}
    bal_rows: list[list[str]] = []
    for (year, month), entry in series.items():
        row = [f"{date(year, month, 1):%b %Y}"]
        for label in labels:
            bal = entry["balances"].get(label)
            row.append("no statement" if bal is None else fmt_dollar(bal))
        bal_rows.append(row)
    widths = [22] + [Decimal(164) / len(labels)] * len(labels) if labels else [22]
    pdf.draw_table(
        ["Month"] + [short[label] for label in labels],
        bal_rows,
        col_widths=[float(w) for w in widths],
        col_aligns=["L"] + ["R"] * len(labels),
        section_label="Balances by Account",
        header_font_size=6.5,
        row_font_size=7,
    )
    pdf.body_text(
        "\u201cno statement\u201d means the statement for that account and month was not provided, so its "
        "activity and balance are unknown - not that the account was inactive. Card figures are balances owed.",
        size=7.5,
    )


def _paragraph_budget_template(pdf: ReportPDF) -> None:
    pdf.body_text(
        "Budget vs Actual: not configured, so that page is omitted. To enable it, create "
        "\u201cbudget.yaml\u201d in the data folder (or pass --budget <path>) with monthly income, "
        "per-category caps and an optional overall spending limit - schema in the project README.",
        size=7.5,
    )


def _render_budget(
    pdf: ReportPDF,
    budget: Budget | None,
    result: ConsolidatedResult,
    start: date,
    end: date,
) -> None:
    """Budget vs actual page. Skipped (with a one-line note on the dashboard)
    when no budget.yaml is configured, instead of printing a near-empty page."""
    if budget is None or not budget.configured:
        _paragraph_budget_template(pdf)
        return
    pdf.add_page()
    pdf.section_title("Budget vs Actual")
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


def _render_recurring_and_forecast(
    pdf: ReportPDF,
    result: ConsolidatedResult,
    end: date,
    redactor: DataRedactor | None = None,
) -> None:
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
            _mask_desc(r.payee, redactor),
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
        pdf.sub_title(f"Near-Term Cash Forecast (next {FORECAST_HORIZON_DAYS} days) \u2013 scheduled items")
        income_notes = "; ".join(
            (
                f"{_mask_desc(display_merchant(e.payee), redactor)}: {e.occurrences} distinct pay dates "
                f"in the last 9 weeks, median every {e.median_cadence_days} days, "
                f"median {fmt_dollar(e.median_amount)} per payday"
            )
            for e in estimated
        )
        pdf.body_text(
            f"Starting from the latest available balances ({'; '.join(as_of_parts)}) for a combined "
            f"starting cash figure of {fmt_dollar(cash)}. Because the accounts report on different dates, "
            "this is an approximate snapshot, not a single-date figure"
            + (
                f"; as of {ref:%m/%d/%Y} the verified combined balance was {fmt_dollar(verified)}."
                if verified is not None
                else "."
            )
            + " Active detected bills and estimated variable income are projected for the full "
            f"{FORECAST_HORIZON_DAYS} days. Income cadence is measured between distinct pay dates (two "
            "deposits on one day count as one payday) and marked \u201c\u2013 estimated\u201d"
            + (f" ({income_notes})." if income_notes else ".")
            + " Confirm the schedule with the employer before relying on it.",
            size=7.5,
        )
        fc_rows = [
            [
                f"{e.event_date:%m/%d/%Y}",
                _mask_desc(e.payee, redactor),
                "+" if e.income else "\u2212",
                fmt_dollar(e.amount),
                fmt_dollar(e.projected),
            ]
            for e in forecast
        ]
        pdf.draw_table(
            ["Date", "Payee", "In/Out", "Amount", "Projected Cash (scheduled items only)"],
            fc_rows,
            col_widths=[20, 66, 14, 30, 56],
            col_aligns=["L", "L", "C", "R", "R"],
            section_label="Forecast",
        )

        pdf.ln(1)
        pdf.sub_title("Allowances for everything not individually scheduled")
        baselines = cash_outflow_baselines(result, repeating)
        horizon_months = Decimal(FORECAST_HORIZON_DAYS) / Decimal("30.44")
        per_ledger_labels = []
        for led in result.ledgers:
            if led.account_type == "Credit Card":
                continue
            complete = complete_months(led)
            if complete:
                per_ledger_labels.append(
                    f"{_ledger_short(led)}: {date(complete[0][0], complete[0][1], 1):%b %Y} \u2013 "
                    f"{date(complete[-1][0], complete[-1][1], 1):%b %Y}, {len(complete)} months"
                )
            else:
                per_ledger_labels.append(f"{_ledger_short(led)}: no complete months")
        months_label = "; ".join(per_ledger_labels) or "no complete months"
        allow_rows = [
            [
                b.label,
                fmt_dollar(b.monthly),
                fmt_dollar(b.scheduled),
                fmt_dollar(b.allowance),
                fmt_dollar((b.allowance * horizon_months).quantize(Decimal("0.01"))),
            ]
            for b in baselines
            if b.monthly > 0
        ]
        total_allowance_month = sum((b.allowance for b in baselines), Decimal("0"))
        total_allowance_horizon = (total_allowance_month * horizon_months).quantize(Decimal("0.01"))
        allow_rows.append(
            [
                "Total",
                fmt_dollar(sum((b.monthly for b in baselines), Decimal("0"))),
                fmt_dollar(sum((b.scheduled for b in baselines), Decimal("0"))),
                fmt_dollar(total_allowance_month),
                fmt_dollar(total_allowance_horizon),
            ]
        )
        pdf.draw_table(
            [
                "Cash outflow bucket",
                "Avg / complete month",
                "Of which scheduled above",
                "Allowance / month",
                f"Allowance / {FORECAST_HORIZON_DAYS} days",
            ],
            allow_rows,
            col_widths=[54, 32, 36, 32, 32],
            col_aligns=["L", "R", "R", "R", "R"],
            section_label="Forecast Allowances",
            header_font_size=6.5,
        )
        pdf.body_text(
            f"Averages use only the complete statement months of each cash account ({months_label}); "
            "partial first/last months are excluded. Loan/card payments and transfers to accounts outside "
            "these statements are included because they leave the cash accounts; transfers between the "
            "covered accounts are not. The scheduled column removes what the line items above already "
            "cover, so nothing is counted twice.",
            size=7.5,
        )

        scheduled_income = sum((e.amount for e in forecast if e.income), Decimal("0"))
        scheduled_bills = sum((e.amount for e in forecast if not e.income), Decimal("0"))
        adjusted_end = cash + scheduled_income - scheduled_bills - total_allowance_horizon
        summary_rows = [
            ["Starting cash (combined, approximate)", fmt_dollar(cash)],
            ["+ Scheduled income (estimated paydays)", fmt_dollar(scheduled_income)],
            ["\u2212 Scheduled bills (active recurring)", fmt_dollar(scheduled_bills)],
            [
                f"= Projected cash from scheduled items only (day {FORECAST_HORIZON_DAYS})",
                fmt_dollar(forecast[-1].projected),
            ],
            [
                f"\u2212 Allowances for everything else ({FORECAST_HORIZON_DAYS} days)",
                fmt_dollar(total_allowance_horizon),
            ],
            [f"= Projected cash including allowances (day {FORECAST_HORIZON_DAYS})", fmt_dollar(adjusted_end)],
        ]
        pdf.ln(1)
        pdf.draw_table(
            ["Projection summary", "Amount"],
            summary_rows,
            col_widths=[118, 34],
            col_aligns=["L", "R"],
            row_font_size=7.5,
        )
        pdf.body_text(
            "The scheduled-items line is optimistic by construction: it books every expected payday but "
            "only the handful of bills that repeat exactly. The line including allowances is the planning "
            "figure. Both assume the recent pay pattern continues.",
            size=7.5,
        )


# ---------------------------------------------------------------------------
# Action summary, subscription review, bills & debt calendar, check register
# ---------------------------------------------------------------------------


def _income_baseline(result: ConsolidatedResult) -> tuple[Decimal, int]:
    """Average earned income per complete month of the main cash account."""
    cash = [led for led in result.ledgers if led.account_type != "Credit Card"]
    if not cash:
        return Decimal("0"), 0
    main = max(cash, key=lambda led: len(led.transactions))
    full = set(complete_months(main))
    if not full:
        return Decimal("0"), 0
    series = month_series(result.ledgers)
    total = sum((series[ym]["income"] for ym in full if ym in series), Decimal("0"))
    return (total / Decimal(len(full))).quantize(Decimal("0.01")), len(full)


def _render_action_summary(
    pdf: ReportPDF,
    result: ConsolidatedResult,
    end: date,
    check_annotations: dict[int, dict[str, str]],
    redactor: DataRedactor | None = None,
) -> None:
    """One page up front: the next decisions, what is due soon, what is missing."""
    pdf.add_page()
    pdf.section_title("Action Summary")
    pdf.body_text(
        "The three decisions this report points to, what is due in the next 30 days, and the information "
        "still missing. Every figure below is developed in the pages that follow; the account-by-month "
        "ledger is the appendix.",
        size=8,
    )

    repeating = detect_repeating_payments(result.all_transactions, as_of=end)
    estimated = estimate_income_groups(result.all_transactions, end)
    baselines = cash_outflow_baselines(result, repeating)
    income_avg, income_months = _income_baseline(result)
    outflow_avg = sum((b.monthly for b in baselines), Decimal("0"))
    gap = income_avg - outflow_avg
    subs = subscription_review(result, repeating, end)
    subs_recent = sum((r.recent_monthly for r in subs), Decimal("0"))
    subs_active = sum(1 for r in subs if r.recent_monthly > 0)
    debts = debt_log(result)
    debt_total = sum((d.balance for d in debts), Decimal("0"))
    checks = check_register(result, check_annotations)
    checks_unknown = [c for c in checks if not c.payee]
    totals = dashboard_totals(result, end)

    pdf.sub_title("Decisions to make")
    decisions: list[list[str]] = []
    if income_avg > 0:
        verb = "exceeds" if gap < 0 else "leaves"
        decisions.append(
            [
                "1",
                "Set the monthly plan",
                f"Over the {income_months} complete months, income averaged {fmt_dollar(income_avg)}/month and "
                f"cash outflows {fmt_dollar(outflow_avg)}/month - spending {verb} income by "
                f"{fmt_dollar(abs(gap))}/month. Pick the allowance buckets to cut "
                "(Recurring Payments & Forecast page).",
            ]
        )
    if subs:
        decisions.append(
            [
                str(len(decisions) + 1),
                "Keep or cancel subscriptions",
                f"{subs_active} services charged in the last 90 days, about {fmt_dollar(subs_recent)}/month "
                f"({fmt_dollar(sum((r.total for r in subs), Decimal('0')))} over the period). Mark each row "
                "of the Subscription Review keep/cancel.",
            ]
        )
    if debts:
        worst = max(debts, key=lambda d: d.utilization or Decimal("0"))
        util = f"{worst.utilization}% of its limit" if worst.utilization is not None else "limit not printed"
        decisions.append(
            [
                str(len(decisions) + 1),
                "Decide the card payoff order",
                f"Card balances total {fmt_dollar(debt_total)} at up to "
                f"{max(d.apr for d in debts)}% APR; {worst.label} sits at {util}. Minimums and due dates are "
                "in the Bills & Debt Calendar - decide which card gets any extra payment.",
            ]
        )
    pdf.draw_table(
        ["#", "Decision", "Why now"],
        decisions[:3],
        col_widths=[8, 44, 134],
        col_aligns=["R", "L", "L"],
        row_font_size=7.5,
    )

    pdf.ln(2)
    pdf.sub_title("Due in the next 30 days")
    upcoming = [c for c in bill_calendar(result, repeating, estimated, end, days=30) if c.kind != "Payday (estimated)"]
    paydays = [c for c in bill_calendar(result, repeating, estimated, end, days=30) if "Payday" in c.kind]
    if upcoming:
        pdf.draw_table(
            ["Date", "Item", "Amount", "Kind", "Status"],
            [[f"{c.when:%m/%d/%Y}", c.item, fmt_dollar(c.amount), c.kind, c.status] for c in upcoming],
            col_widths=[22, 74, 24, 30, 36],
            col_aligns=["L", "L", "R", "L", "L"],
            row_font_size=7.5,
        )
    pdf.body_text(
        f"{len(upcoming)} scheduled outflow(s) totalling "
        f"{fmt_dollar(sum((c.amount for c in upcoming), Decimal('0')))}; "
        f"{len(paydays)} estimated payday(s) totalling {fmt_dollar(sum((c.amount for c in paydays), Decimal('0')))}. "
        "Everything not individually scheduled (fuel, food, checks, ...) is covered by the allowances on the "
        "forecast page.",
        size=7.5,
    )

    pdf.ln(2)
    pdf.sub_title("Missing information")
    missing_rows: list[list[str]] = []
    gaps = [
        (_ledger_short(led), label) for led in result.ledgers for label in missing_coverage(led, end.year, end.month)
    ]
    for acct, label in gaps:
        missing_rows.append([f"{acct} statement for {label}", "Activity and balance unknown for that month"])
    if totals["deposits"] > 0:
        missing_rows.append(
            [
                f"Source of {fmt_dollar(totals['deposits'])} in deposits that are not identified payroll",
                "Confirm before treating them as earned income",
            ]
        )
    if checks_unknown:
        missing_rows.append(
            [
                f"Payee and purpose of {len(checks_unknown)} check(s), "
                f"{fmt_dollar(sum((c.amount for c in checks_unknown), Decimal('0')))}",
                "Add them to checks.yaml so spending by category reflects what they paid for",
            ]
        )
    outside: dict[str, Decimal] = defaultdict(Decimal)
    for m in result.movements:
        if (
            not m.matched
            and m.unmatched_reason
            and ("not covered" in m.unmatched_reason or "PayPal" in m.unmatched_reason)
        ):
            outside[m.unmatched_reason] += m.amount
    for reason, amount in sorted(outside.items(), key=lambda kv: -kv[1]):
        missing_rows.append(
            [f"{fmt_dollar(amount)} in transfers: {reason}", "Provide those statements or confirm the purpose"]
        )
    other = [t for t in result.all_transactions if t.category == "Other"]
    if other:
        missing_rows.append(
            [
                f"Category for {len(other)} uncategorized item(s), "
                f"{fmt_dollar(sum((t.amount for t in other), Decimal('0')))}",
                "Listed under Items Needing Review",
            ]
        )
    if missing_rows:
        pdf.draw_table(
            ["What is missing", "Why it matters"],
            missing_rows,
            col_widths=[104, 82],
            col_aligns=["L", "L"],
            row_font_size=7.5,
        )
    else:
        pdf.body_text("Nothing outstanding - every account and month is covered and every check is annotated.", size=8)


def _render_subscription_review(
    pdf: ReportPDF,
    result: ConsolidatedResult,
    end: date,
    redactor: DataRedactor | None = None,
) -> None:
    """Every subscription service with its cost, cadence and a keep/cancel column."""
    repeating = detect_repeating_payments(result.all_transactions, as_of=end)
    rows = subscription_review(result, repeating, end)
    if not rows:
        return
    pdf.add_page()
    pdf.section_title("Subscription Review")
    pdf.body_text(
        "Every debit categorized as Subscriptions, grouped by service across all accounts. \u201cRecent $/mo\u201d "
        "is the trailing 90 days divided by three, so a service that was cancelled shows $0.00. \u201cNext "
        "expected\u201d is the detected schedule where one exists (blank when the charges are irregular). The "
        "last column is for your decision.",
        size=8,
    )
    table = [
        [
            _mask_desc(r.payee, redactor),
            r.accounts,
            str(r.charges),
            fmt_dollar(r.total),
            fmt_dollar(r.recent_monthly),
            f"{r.last_date:%m/%d/%Y}",
            f"{r.next_expected:%m/%d/%Y}" if r.next_expected else "\u2013",
            _cadence_label(r.cadence_days) if r.cadence_days else "irregular",
            "",
        ]
        for r in rows
    ]
    table.append(
        [
            "Total",
            "",
            str(sum(r.charges for r in rows)),
            fmt_dollar(sum((r.total for r in rows), Decimal("0"))),
            fmt_dollar(sum((r.recent_monthly for r in rows), Decimal("0"))),
            "",
            "",
            "",
            "",
        ]
    )
    pdf.draw_table(
        ["Service", "Acct", "Charges", "Total", "Recent $/mo", "Last charge", "Next expected", "Cadence", "Keep?"],
        table,
        col_widths=[46, 12, 14, 20, 20, 20, 22, 20, 12],
        col_aligns=["L", "L", "R", "R", "R", "L", "L", "L", "C"],
        section_label="Subscription Review",
        header_font_size=6.5,
        row_font_size=7,
    )


def _render_bills_and_debt(
    pdf: ReportPDF,
    result: ConsolidatedResult,
    end: date,
    redactor: DataRedactor | None = None,
) -> None:
    """Debt log (per card) and a 60-day calendar of bills, minimums and paydays."""
    repeating = detect_repeating_payments(result.all_transactions, as_of=end)
    estimated = estimate_income_groups(result.all_transactions, end)
    debts = debt_log(result)
    calendar = bill_calendar(result, repeating, estimated, end, days=60)
    if not debts and not calendar:
        return
    pdf.add_page()
    pdf.section_title("Bills & Debt Calendar")
    if debts:
        pdf.sub_title("Debt log")
        pdf.draw_table(
            ["Card", "As of", "Balance", "Limit", "Used", "APR", "Min. due", "Due date", "Autopay", "Last payment"],
            [
                [
                    d.label,
                    d.as_of,
                    fmt_dollar(d.balance),
                    fmt_dollar(d.credit_limit) if d.credit_limit else "\u2013",
                    f"{d.utilization}%" if d.utilization is not None else "\u2013",
                    f"{d.apr}%" if d.apr else "\u2013",
                    fmt_dollar(d.minimum_payment) if d.minimum_payment else "\u2013",
                    d.due_date or "\u2013",
                    "detected" if d.autopay else "not seen",
                    f"{fmt_dollar(d.last_payment_amount)} on {d.last_payment_date}"
                    if d.last_payment_date
                    else "\u2013",
                ]
                for d in debts
            ],
            col_widths=[34, 18, 18, 16, 12, 14, 16, 18, 16, 24],
            col_aligns=["L", "L", "R", "R", "R", "R", "R", "L", "L", "L"],
            header_font_size=6.5,
            row_font_size=7,
        )
        pdf.body_text(
            "Balance, limit, APR, minimum and due date are read from each card's latest statement. "
            "\u201cAutopay detected\u201d means a payment from the checking account was matched to the card's "
            "payment credit; it does not confirm the autopay amount covers the minimum - check that the "
            "scheduled payment is at least the minimum due. Paying only the minimum at ~30% APR keeps "
            "interest accruing; the Interest lines in each card month show the cost.",
            size=7.5,
        )
        pdf.ln(2)
    if calendar:
        pdf.sub_title("Next 60 days")
        pdf.body_text(
            "Bills are the active recurring payments; card minimums come from the statements; paydays are "
            "estimated from the recent pay pattern. Add due dates for bills paid by check or from accounts "
            "outside these statements - they cannot be detected here.",
            size=7.5,
        )
        pdf.draw_table(
            ["Date", "Item", "In/Out", "Amount", "Kind", "Status", "Basis"],
            [
                [
                    f"{c.when:%m/%d/%Y}",
                    _mask_desc(c.item, redactor),
                    "+" if "Payday" in c.kind else "\u2212",
                    fmt_dollar(c.amount),
                    c.kind,
                    c.status,
                    c.basis,
                ]
                for c in calendar
            ],
            col_widths=[20, 58, 12, 22, 26, 26, 22],
            col_aligns=["L", "L", "C", "R", "L", "L", "L"],
            section_label="Bills & Debt Calendar",
            header_font_size=6.5,
            row_font_size=7,
        )


def _render_checks(
    pdf: ReportPDF,
    result: ConsolidatedResult,
    annotations: dict[int, dict[str, str]],
    redactor: DataRedactor | None = None,
) -> None:
    """Register of cleared checks with payee/purpose where annotated."""
    rows = check_register(result, annotations)
    if not rows:
        return
    pdf.add_page()
    pdf.section_title("Checks \u2013 Payees and Purposes")
    unknown = [r for r in rows if not r.payee]
    pdf.body_text(
        f"{len(rows)} check(s) cleared for {fmt_dollar(sum((r.amount for r in rows), Decimal('0')))}. The bank "
        "statement records only the check number and amount, so the payee and purpose have to come from you. "
        "The check number stays as the payment method; once a purpose is supplied the amount is categorized "
        "by what it paid for (e.g. Rent) instead of sitting in \u201cChecks\u201d."
        + (
            f" {len(unknown)} check(s) totalling {fmt_dollar(sum((r.amount for r in unknown), Decimal('0')))} still "
            "have no payee - add them to checks.yaml (see README)."
            if unknown
            else ""
        ),
        size=8,
    )
    pdf.draw_table(
        ["Date", "Check #", "Amount", "Payee", "Purpose", "Account", "Source row"],
        [
            [
                r.date,
                f"#{r.number}",
                fmt_dollar(r.amount),
                _mask_desc(r.payee or "unknown \u2013 supply", redactor),
                _mask_desc(r.purpose or "unknown", redactor),
                r.account,
                r.source or "\u2013",
            ]
            for r in rows
        ],
        col_widths=[20, 14, 20, 36, 26, 32, 38],
        col_aligns=["L", "L", "R", "L", "L", "L", "L"],
        section_label="Check Register",
        row_font_size=7,
    )


def _render_items_needing_review(
    pdf: ReportPDF,
    result: ConsolidatedResult,
    mask_personal: bool = False,
    redactor: DataRedactor | None = None,
) -> None:
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
            desc = _mask_desc(tx.description, redactor)
            rows.append(
                [
                    _ledger_short(ledger),
                    tx.post_date,
                    fmt_dollar(tx.amount),
                    desc[:60],
                    "Category not recognized \u2013 verify and recategorize",
                    _statement_source_for(ledger, tx, redactor=redactor),
                    "Unresolved",
                ]
            )

    for ledger in result.ledgers:
        for stmt in ledger.statements:
            ok, _, _, _, _, _, _, _ = _statement_reconciles(stmt)
            if not ok:
                rows.append(
                    [
                        _ledger_short(ledger),
                        stmt.statement_date,
                        "\u2013",
                        _period_note(stmt, ledger)[:60],
                        "Statement printed totals/counts/balance differ from parsed transactions",
                        _redact_filename(redactor, stmt.file_path),
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
                    _mask_desc(issue["description"], redactor),
                    f"Transaction {issue['problem']}",
                    _mask_desc(issue["source"], redactor),
                    "Unresolved",
                ]
            )

    for movement in result.movements:
        from_ledger = result.ledger_for(movement.from_account)
        label = _ledger_short(from_ledger) if from_ledger else movement.from_account
        source = _movement_source(result, movement)
        if movement.matched and movement.basis == "amount+date":
            to_ledger = result.ledger_for(movement.to_account)
            rows.append(
                [
                    label,
                    movement.date,
                    fmt_dollar(movement.amount),
                    movement.description[:60],
                    f"Paired with {_ledger_short(to_ledger) if to_ledger else movement.to_account} credit on "
                    f"{movement.counter_date} ({movement.counter_description[:30]}) by equal amount and date only "
                    "\u2013 no shared reference number",
                    source,
                    "Verify pairing",
                ]
            )
            continue
        if movement.matched:
            continue
        reason = movement.unmatched_reason or "no offsetting credit found in the covered accounts"
        status = "Needs statement" if "no statement covering" in reason else "External \u2013 confirm"
        if reason.startswith("no offsetting credit found"):
            status = "Unresolved"
        rows.append(
            [
                label,
                movement.date,
                fmt_dollar(movement.amount),
                _mask_desc(movement.description[:60], redactor),
                f"Transfer/card payment without an offsetting credit: {reason}",
                _mask_desc(source, redactor),
                status,
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


def _source_ref(
    stmt: Statement | None,
    tx: Transaction | None = None,
    redactor: DataRedactor | None = None,
) -> str:
    """Exact provenance: file name, PDF page and row ordinal when known."""
    if stmt is None:
        return "\u2013"
    name = _redact_filename(redactor, stmt.file_path) if stmt.file_path else f"statement {stmt.statement_date}"
    if tx is not None and tx.source_page:
        return f"{name} p.{tx.source_page} row {tx.source_row}"
    return name


def _statement_for_tx(ledger: AccountLedger, tx: Transaction) -> Statement | None:
    """The statement that lists a transaction (by identity, then by period)."""
    for stmt in ledger.statements:
        if any(t is tx for t in stmt.transactions):
            return stmt
    tx_date = _to_date(tx.post_date)
    best: Statement | None = None
    for stmt in ledger.statements:
        end = _to_date(stmt.statement_date)
        start = _to_date(stmt.period_start) if stmt.period_start else None
        if start is not None and start <= tx_date <= end:
            if best is None or _to_date(best.statement_date) > end:
                best = stmt
        elif best is None and end >= tx_date:
            best = stmt
    return best


def _statement_source_for(
    ledger: AccountLedger,
    tx: Transaction,
    redactor: DataRedactor | None = None,
) -> str:
    """Locate the statement row a transaction was read from."""
    return _source_ref(_statement_for_tx(ledger, tx), tx, redactor=redactor)


def _movement_source(result: ConsolidatedResult, movement: MovementMatch) -> str:
    """Source reference for the debit side of an internal movement."""
    ledger = result.ledger_for(movement.from_account)
    if ledger is None:
        return "\u2013"
    for tx in ledger.transactions:
        if (
            not tx.is_credit
            and tx.post_date == movement.date
            and tx.amount == movement.amount
            and tx.description == movement.description
        ):
            return _statement_source_for(ledger, tx)
    return "\u2013"


def _render_corrections_log(
    pdf: ReportPDF,
    result: ConsolidatedResult,
    mask_personal: bool = False,
    redactor: DataRedactor | None = None,
) -> None:
    """Chronological log of data-quality findings and the adjustments applied."""
    pdf.add_page()
    pdf.section_title("Corrections & Adjustments Log")

    def _stmt_source(ledger: AccountLedger, statement_date: str | None) -> str:
        if not statement_date:
            return "\u2013"
        for stmt in ledger.statements:
            if stmt.statement_date == statement_date:
                return _redact_filename(redactor, stmt.file_path)
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
                    (
                        f"{_mask_desc(mismatch[2], redactor)} \u2013 printed {mismatch[3]} vs recomputed "
                        f"{mismatch[4]} ({mismatch[5]})"
                    ),
                    action,
                    _mask_desc(mismatch[6], redactor),
                ]
            )

    for ledger in result.ledgers:
        for dup in ledger.duplicates:
            desc = _mask_desc(dup.transaction.description, redactor)
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
            ok, _, _, _, _, _, _, _ = _statement_reconciles(stmt)
            if not ok:
                entries.append(
                    [
                        stmt.statement_date,
                        _ledger_short(ledger),
                        "Statement arithmetic",
                        "Printed totals/counts/balance differ from parsed values",
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
    checks_path: str | Path | None = None,
) -> bool:
    """Generate the personal financial report PDF (and optional audit CSV).

    Returns True when every post-render export check passed. The PDF is
    written either way; failures are printed to stderr so they are never
    silent.
    """
    pdf = ReportPDF("Personal Financial Report")

    if not statements:
        print("No statements found.", file=sys.stderr)
        sys.exit(1)

    statements.sort(key=lambda s: (s.year, s.month, s.account_number))
    categorize_transactions(statements)
    check_annotations = load_check_annotations(checks_path)
    apply_check_annotations(statements, check_annotations)

    # Per-statement reconciliation (uses every provided statement)
    reconciled = _reconcile_statements(statements)
    if not reconciled:
        print("WARNING: reconciliation failed (see above).", file=sys.stderr)
        if not allow_mismatch:
            print(
                "Aborting. Use --allow-mismatch to force report generation.",
                file=sys.stderr,
            )
            sys.exit(1)

    full_result = consolidate(statements)
    result = _filter_result_by_period(full_result, target_year, target_month)

    if not result.all_transactions:
        print("No transactions found matching period filters.", file=sys.stderr)
        sys.exit(1)

    redactor = _build_redactor(statements, mask_personal)

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
            ["Account", "Statement not provided for"],
            [[a, m] for a, m in gaps],
            col_widths=[55, 55],
            col_aligns=["L", "L"],
            row_font_size=8,
            row_height=5.5,
        )
        pdf.body_text(
            "These statements were not provided, so the account's activity and balances for those months "
            "are unknown - this is a gap in the data, not evidence of zero activity. Combined figures that "
            "include those months are marked as incomplete wherever they appear.",
            size=8,
        )
    else:
        pdf.body_text("Statement coverage: complete through the report end date.", size=9)

    # Reconciliation summary on cover
    if allow_mismatch:
        pdf.body_text("Reconciliation: UNVALIDATED (--allow-mismatch)", size=9)
    elif reconciled:
        pdf.body_text("Reconciliation: PASSED (statement totals, counts, and balance formulas)", size=9)
    else:
        pdf.body_text("Reconciliation: FAILED (see Reconciliation & Data Quality page)", size=9)

    # ---- ACTION SUMMARY + DASHBOARD ----
    if mode in ("combined", "yearly"):
        _render_action_summary(pdf, result, cover_end_d, check_annotations, redactor=redactor)
        _render_dashboard(pdf, result, cover_start_d, cover_end_d, redactor=redactor)
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
        pdf.section_title(f"{period_label} \u2013 Weekly Average Balances")
        weekly_chart = chart_weekly_balance_ledgers(result.ledgers)
        pdf.embed_chart(
            weekly_chart,
            w=pdf.w - pdf.l_margin - pdf.r_margin,
            caption="Three separate measures, never added together: \u201cCash & savings\u201d is the sum of "
            "the checking and savings balances; \u201cCard balances owed\u201d is the sum of the Capital One "
            "balances (debt, shown as a positive amount); \u201cNet position\u201d is cash minus card debt. "
            "Each is a weekly average of daily balances reconstructed from each statement's beginning "
            "balance after deduplicating overlaps. Dotted segments are weeks where an account in that group "
            "has no statement (e.g. savings in July/August), so the value covers fewer accounts. Covered "
            f"period: {cover_start_d:%B %d, %Y} through {cover_end_d:%B %d, %Y}.",
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
        merchant_rows = _build_top_merchants_from_tx(result.all_transactions, top_n=15, redactor=redactor)
        cw_merch = [10, 120, 35]
        pdf.draw_table(
            ["#", "Payee", "Total"],
            merchant_rows,
            col_widths=cw_merch,
            col_aligns=["R", "L", "R"],
            section_label="Top Payees by Total Debits",
        )

    # ---- INSIGHTS: SUBSCRIPTIONS, BILLS & DEBT, CHECKS, REVIEW, FORECAST ----
    if mode in ("combined", "yearly"):
        _render_subscription_review(pdf, result, cover_end_d, redactor=redactor)
        _render_bills_and_debt(pdf, result, cover_end_d, redactor=redactor)
        _render_checks(pdf, result, check_annotations, redactor=redactor)
        _render_items_needing_review(pdf, result, mask_personal=mask_personal, redactor=redactor)
        _render_recurring_and_forecast(pdf, result, cover_end_d, redactor=redactor)

    # ---- RECONCILIATION PANEL ----
    _render_reconciliation(
        pdf, result, period_end_year, period_end_month, mask_personal=mask_personal, redactor=redactor
    )
    _render_corrections_log(pdf, result, mask_personal=mask_personal, redactor=redactor)

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
                merchant_m = _build_top_merchants_from_tx(month.transactions, top_n=10, redactor=redactor)
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

                # Checks cleared in this month (short table; placed before the long
                # transaction list so it never ends up alone on a spill-over page)
                month_checks = checks_by_month.get((month.year, month.month))
                if month_checks:
                    pdf.sub_title("Checks Cleared")
                    chk_rows = [[c["date"], f"#{c['number']}", fmt_dollar(c["amount"])] for c in month_checks]
                    pdf.draw_table(
                        ["Date", "Check #", "Amount"],
                        chk_rows,
                        col_widths=[30, 30, 30],
                        col_aligns=["L", "C", "R"],
                        section_label="Checks Cleared",
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
                    raw_desc = _mask_desc(tx.description, redactor)
                    desc = raw_desc[:90] + ("..." if len(raw_desc) > 90 else "")
                    sign = "+" if tx.is_credit else "-"
                    tx_rows.append(
                        [
                            tx.post_date,
                            desc,
                            tx.category,
                            f"{sign}{fmt_dollar(tx.amount)}",
                            fmt_dollar(run_balances[id(tx)]),
                        ]
                    )
                cw_tx = [20, 76, 34, 28, 28]
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

    # ---- SAVE ----
    run_maps_audit: dict[int, Decimal] = {}
    seq_maps_audit: dict[int, int] = {}
    for ledger in result.ledgers:
        run_maps_audit.update(running_balance_map(ledger))
        seq_maps_audit.update({id(tx): seq for seq, tx in enumerate(ledger.transactions, start=1)})

    included_tx_ids = {id(tx) for tx in result.all_transactions}

    attachments = []
    if transactions_csv_path:
        _write_transactions_csv(result, transactions_csv_path, redactor=redactor)
        attachments.append(transactions_csv_path)
    if audit_path:
        _write_audit_csv(
            statements,
            audit_path,
            redactor=redactor,
            run_maps=run_maps_audit,
            seq_maps=seq_maps_audit,
            included_tx_ids=included_tx_ids,
        )
        attachments.append(audit_path)

    # Package the companion CSVs inside the PDF so a standalone PDF carries
    # the full audited transaction stream with it. The attachment description
    # is redacted so the PDF metadata does not leak source filenames.
    for attach in attachments:
        try:
            pdf.embed_file(file_path=str(attach), desc=_redact_filename(redactor, attach))
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
    success = reconciled and not check_problems
    if not success:
        print("WARNING: report generation completed with validation failures.", file=sys.stderr)
    return success


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
    flow = _flow_split(result)
    checks += 1
    parts = (
        totals["payroll"] + totals["deposits"] + totals["refunds"] + totals["transfers_in"] + totals["other_credits"]
    )
    if parts != totals["total_credits"]:
        problems.append(f"credit decomposition does not sum to Total Credits ({parts} vs {totals['total_credits']})")
    checks += 1
    debit_parts = (
        flow["spending"] + flow["matched_card"] + flow["other_debt"] + flow["matched_transfer"] + flow["unmatched"]
    )
    if debit_parts != totals["total_debits"]:
        problems.append(f"debit decomposition does not sum to Total Debits ({debit_parts} vs {totals['total_debits']})")
    checks += 1
    bridge = _income_to_cash_bridge(totals, flow)
    steps = sum((amt for label, amt in bridge if not label.startswith("=")), Decimal("0"))
    observed = bridge[-1][1]
    if steps != observed:
        problems.append(f"income-to-cash bridge does not sum to the observed change ({steps} vs {observed})")

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


def _mask_desc(description: str, redactor: DataRedactor | None = None) -> str:
    """Redact names and addresses from transaction descriptions."""
    if redactor is None:
        return description
    return redactor.description(description)


def _extract_person_names(statements: list[Statement]) -> list[str]:
    """Heuristic extraction of personal names printed on statement headers.

    Looks for all-caps name lines in the first few lines of each statement
    text (e.g. ``JACOB PFEIFF`` or ``JACOB C PFEIFF``). These become the
    default redaction targets when ``--mask`` is used without an explicit
    name list.
    """
    names: set[str] = set()
    name_re = re.compile(r"^[A-Z][A-Z\s]+[A-Z]$")
    for stmt in statements:
        if not stmt.file_path:
            continue
        try:
            from ledgersight.parsers import extract_text

            text = extract_text(Path(stmt.file_path))
        except Exception:
            continue
        for line in text.split("\n")[:40]:
            stripped = line.strip()
            if not stripped:
                continue
            if (
                name_re.match(stripped)
                and "STATEMENT" not in stripped
                and "CHECKING" not in stripped
                and "SAVINGS" not in stripped
                and "ACCOUNT" not in stripped
                and "XXXX" not in stripped
                and "BASIC" not in stripped
                and "RETURN" not in stripped
                and len(stripped) > 3
            ):
                names.add(" ".join(stripped.split()))
    return sorted(names)


def _build_redactor(statements: list[Statement], mask_personal: bool) -> DataRedactor:
    """Build a redactor for the personal report.

    Auto-detects names from statement headers and uses consistent pseudonyms
    for source files so masked reports do not leak identities.
    """
    if not mask_personal:
        return DataRedactor(mask_personal=False)
    names = _extract_person_names(statements)
    return DataRedactor(mask_personal=True, redact_names=names, redact_email=True, redact_phone_numbers=True)


def _redact_filename(redactor: DataRedactor | None, path: str | Path) -> str:
    """Return a display name for a source file, redacted when masking is on."""
    name = Path(path).name if path else "statement"
    if redactor is None or not redactor.mask_personal:
        return name
    return redactor.source_path(str(path))


def _write_transactions_csv(
    result: ConsolidatedResult,
    transactions_path: str | Path,
    redactor: DataRedactor | None = None,
) -> None:
    """Write the consolidated, deduplicated transactions to CSV.

    Rows are written in the ledger's calculation order - the same sequence
    the PDF tables and running balances use - and carry that ordinal in
    ``Seq`` so consecutive-row balance checks hold in the export exactly as
    they do in the report. ``Source`` points at the statement file, page and
    row the transaction was read from.
    """
    from ledgersight.exports import _safe_csv_cell

    with open(transactions_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["Account", "Account Type", "Seq", "Date", "Description", "Category", "Amount", "Type", "Balance", "Source"]
        )
        for ledger in result.ledgers:
            run_balances = running_balance_map(ledger)
            for seq, tx in enumerate(ledger.transactions, start=1):
                desc = _mask_desc(tx.description, redactor)
                amount = tx.amount if tx.is_credit else -tx.amount
                writer.writerow(
                    [
                        ledger.account_number,
                        ledger.account_type,
                        seq,
                        tx.post_date,
                        _safe_csv_cell(desc),
                        _safe_csv_cell(tx.category),
                        str(amount),
                        "Credit" if tx.is_credit else "Debit",
                        str(run_balances[id(tx)]),
                        _safe_csv_cell(_statement_source_for(ledger, tx, redactor=redactor)),
                    ]
                )
    print(f"Transactions CSV saved to: {transactions_path}")


def _duplicate_detail_rows(ledgers: list[AccountLedger], redactor: DataRedactor | None = None) -> list[list[str]]:
    """Individual rows removed as duplicates, for the reconciliation panel."""
    rows: list[list[str]] = []
    for ledger in ledgers:
        for dup in ledger.duplicates:
            tx = dup.transaction
            desc = _mask_desc(tx.description, redactor)
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
    redactor: DataRedactor | None = None,
    run_maps: dict[int, Decimal] | None = None,
    seq_maps: dict[int, int] | None = None,
    included_tx_ids: set[int] | None = None,
) -> None:
    """Write a CSV of every parsed row (statement order) with its category.

    ``Balance`` is the value the bank printed on the statement (where one was
    printed); ``Running`` is the report's recomputed per-transaction running
    balance and ``Seq`` the row's position in the ledger's calculation order
    (blank for rows dropped as duplicates), so printed vs report-generated
    values stay distinguishable and the two CSVs can be joined.

    ``InReport`` is ``Yes`` when the row's posting date is inside the requested
    report period and it was included in report totals, otherwise ``No``.
    """
    from ledgersight.exports import _safe_csv_cell

    run_maps = run_maps or {}
    seq_maps = seq_maps or {}
    included_tx_ids = included_tx_ids or set()
    with open(audit_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "Account",
                "Statement",
                "PostDate",
                "Description",
                "Amount",
                "Type",
                "Balance",
                "Category",
                "Running",
                "Seq",
                "Source",
                "InReport",
            ]
        )
        for stmt in statements:
            name = _redact_filename(redactor, stmt.file_path)
            for tx in stmt.transactions:
                desc = _mask_desc(tx.description, redactor)
                amount = tx.amount if tx.is_credit else -tx.amount
                running = run_maps.get(id(tx))
                seq = seq_maps.get(id(tx))
                in_report = "Yes" if id(tx) in included_tx_ids else "No"
                writer.writerow(
                    [
                        stmt.account_number,
                        stmt.month_label,
                        tx.post_date,
                        _safe_csv_cell(desc),
                        str(amount),
                        "Credit" if tx.is_credit else "Debit",
                        str(tx.balance) if tx.balance is not None else "",
                        _safe_csv_cell(tx.category),
                        str(running) if running is not None else "",
                        str(seq) if seq is not None else "",
                        _safe_csv_cell(f"{name} p.{tx.source_page} row {tx.source_row}" if tx.source_page else name),
                        in_report,
                    ]
                )
    print(f"Audit file saved to: {audit_path}")
