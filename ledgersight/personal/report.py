"""Personal financial report generation (PDF + optional audit CSV)."""

from __future__ import annotations

import csv
import re
import sys
from collections import defaultdict
from datetime import datetime
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
    chart_daily_balance_single,
    chart_weekly_balance,
)
from ledgersight.personal.models import Statement

EXCLUDED_MERCHANT_CATS = {
    "Transfers",
    "Checks",
    "Bank Fees",
    "Loan/Credit Payment",
    "Other",
}


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


def _aggregate_by_month(statements: list[Statement]) -> list[Statement]:
    """Collapse multiple accounts into one synthetic Statement per month."""
    buckets: dict[tuple[int, int], list[Statement]] = {}
    for s in statements:
        buckets.setdefault((s.year, s.month), []).append(s)

    result: list[Statement] = []
    for year, month in sorted(buckets):
        group = buckets[(year, month)]
        merged = [tx for s in group for tx in s.transactions]
        result.append(
            Statement(
                statement_date=f"{month:02d}/01/{year}",
                account_number="",
                beginning_balance=sum((s.beginning_balance for s in group), Decimal("0")),
                ending_balance=sum((s.ending_balance for s in group), Decimal("0")),
                total_credits=sum((s.total_credits for s in group), Decimal("0")),
                total_debits=sum((s.total_debits for s in group), Decimal("0")),
                credit_count=sum(s.credit_count for s in group),
                debit_count=sum(s.debit_count for s in group),
                transactions=merged,
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


def build_top_merchants(
    statements: list[Statement],
    top_n: int = 15,
    mask_personal: bool = False,
) -> list[list[str]]:
    """Build ranked payee rows by total debits."""
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
    """Verify parsed transaction totals against statement summaries."""
    passed = True
    for stmt in statements:
        parsed_credits = sum(tx.amount for tx in stmt.transactions if tx.is_credit)
        parsed_debits = sum(tx.amount for tx in stmt.transactions if not tx.is_credit)
        parsed_count = len(stmt.transactions)
        expected_count = stmt.credit_count + stmt.debit_count

        if parsed_count != expected_count or parsed_credits != stmt.total_credits or parsed_debits != stmt.total_debits:
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
    output_path: str | Path,
    mode: str = "combined",
    target_month: int | None = None,
    target_year: int | None = None,
    audit_path: str | Path | None = None,
    mask_personal: bool = False,
    allow_mismatch: bool = False,
) -> None:
    """Generate the personal financial report PDF (and optional audit CSV)."""
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

    accounts = sorted({s.account_label for s in statements})

    # ---- COVER PAGE ----
    pdf.add_page()
    pdf.ln(15)
    pdf.set_font("DJV", "B", 24)
    pdf.set_text_color(44, 62, 80)
    pdf.cell(0, 12, "Personal Financial Report", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)

    pdf.set_font("DJV", "", 11)
    pdf.set_text_color(100, 100, 100)
    date_range = f"{statements[0].month_label}  to  {statements[-1].month_label}"
    pdf.cell(0, 7, date_range, align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(
        0,
        7,
        f"{len(statements)} statement(s) across {len(accounts)} account(s)",
        align="C",
        new_x="LMARGIN",
        new_y="NEXT",
    )
    if mask_personal:
        pdf.cell(0, 7, "Accounts: [REDACTED]", align="C", new_x="LMARGIN", new_y="NEXT")
    else:
        for label in accounts:
            pdf.cell(0, 7, f"Account: {label}", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(10)

    # Summary box
    total_credits_val = sum((s.total_credits for s in statements), Decimal("0"))
    total_debits_val = sum((s.total_debits for s in statements), Decimal("0"))
    net_flow = total_credits_val - total_debits_val

    summary_rows = [
        ["Total Credits", fmt_dollar(total_credits_val)],
        ["Total Debits", fmt_dollar(total_debits_val)],
        ["Net Account Change", fmt_dollar(net_flow)],
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
        pdf.body_text(f"Transactions parsed: {parsed_tx} of {expected_tx} expected")

    if allow_mismatch:
        pdf.body_text("Reconciliation: SKIPPED (--allow-mismatch)")
    elif reconciled:
        pdf.body_text("Reconciliation: PASSED")
    else:
        pdf.body_text("Reconciliation: FAILED")

    # Compare debits/credits totals
    parsed_credits = sum(sum(tx.amount for tx in s.transactions if tx.is_credit) for s in statements)
    parsed_debits = sum(sum(tx.amount for tx in s.transactions if not tx.is_credit) for s in statements)
    if (parsed_credits != total_credits_val) or (parsed_debits != total_debits_val):
        print(
            f"WARNING: credit totals differ: parsed={parsed_credits} vs reported={total_credits_val}",
            file=sys.stderr,
        )
        print(
            f"WARNING: debit totals differ: parsed={parsed_debits} vs reported={total_debits_val}",
            file=sys.stderr,
        )

    # Latest balance per account
    pdf.sub_title("Accounts \u2013 Latest Ending Balance")
    latest: dict[tuple[str, str], Statement] = {}
    for s in statements:
        key = (s.institution, s.account_number)
        if key not in latest or (s.year, s.month) > (latest[key].year, latest[key].month):
            latest[key] = s
    acct_rows = [[_account_short(s), s.account_type, fmt_dollar(s.ending_balance)] for s in latest.values()]
    cw_acct = [60, 45, 40]
    pdf.draw_table(
        ["Account", "Type", "Ending Balance"],
        acct_rows,
        col_widths=cw_acct,
        col_aligns=["L", "L", "R"],
    )
    pdf.ln(2)

    # Monthly balances mini-table on cover
    pdf.sub_title("Monthly Balances by Account")
    bal_rows = []
    for s in statements:
        bal_rows.append(
            [
                _account_short(s),
                s.month_label,
                fmt_dollar(s.beginning_balance),
                fmt_dollar(s.ending_balance),
                fmt_dollar(s.ending_balance - s.beginning_balance),
            ]
        )
    cw_bal = [52, 30, 34, 34, 34]
    pdf.draw_table(
        ["Account", "Month", "Start Balance", "End Balance", "Change"],
        bal_rows,
        col_widths=cw_bal,
        col_aligns=["L", "L", "R", "R", "R"],
    )

    # ---- PERIOD OVERVIEW CHARTS ----
    if mode in ("combined", "yearly"):
        aggregated = _aggregate_by_month(statements)
        period_label = statements[0].month_label
        if len(statements) > 1:
            period_label = f"{statements[0].month_label} \u2013 {statements[-1].month_label}"
            pdf.add_page(orientation="L")
            pdf.section_title(f"{period_label} \u2013 Credits vs Debits")
            chart_buf = chart_credits_vs_debits(aggregated)
            pdf.embed_chart(chart_buf, w=pdf.w - pdf.l_margin - pdf.r_margin)
            chart_buf.close()

            pdf.add_page(orientation="L")
            pdf.section_title(f"{period_label} \u2013 Weekly Average Balance, Year to Date")
            weekly_chart = chart_weekly_balance(statements)
            pdf.embed_chart(weekly_chart, w=pdf.w - pdf.l_margin - pdf.r_margin)
            weekly_chart.close()

            pdf.add_page(orientation="L")
            pdf.section_title(f"{period_label} \u2013 Debits by Category per Month")
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
        pdf.sub_title("Top Payees by Total Debits (All Periods)")
        merchant_rows = build_top_merchants(statements, top_n=15, mask_personal=mask_personal)
        cw_merch = [10, 120, 35]
        pdf.draw_table(
            ["#", "Payee", "Total"],
            merchant_rows,
            col_widths=cw_merch,
            col_aligns=["R", "L", "R"],
            section_label="Top Payees by Total Debits",
        )

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
            pdf.draw_table(
                ["Metric", "Amount"],
                month_rows,
                col_widths=cw_month,
                col_aligns=["L", "R"],
            )

            # Daily balance chart for this month
            if stmt.daily_balances:
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
                pdf.draw_table(
                    ["Category", "Amount", "Share"],
                    cat_rows_m,
                    col_widths=cw_cat_m,
                    col_aligns=["L", "R", "R"],
                )

            # Top merchants for this month
            stmt_wrapper = [stmt]
            merchant_m = build_top_merchants(stmt_wrapper, top_n=10, mask_personal=mask_personal)
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
                tx_rows.append(
                    [
                        tx.post_date,
                        desc,
                        tx.category[:18],
                        f"{sign}{fmt_dollar(tx.amount)}",
                        fmt_dollar(tx.balance),
                    ]
                )
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
                chk_rows = [[c["date"], f"#{c['number']}", fmt_dollar(c["amount"])] for c in stmt.checks_cleared]
                cw_chk = [30, 30, 30]
                pdf.draw_table(
                    ["Date", "Check #", "Amount"],
                    chk_rows,
                    col_widths=cw_chk,
                    col_aligns=["L", "C", "R"],
                )

    # ---- SAVE ----
    pdf.output(str(output_path))
    print(f"Report saved to: {output_path}")

    # ---- AUDIT ----
    if audit_path:
        _write_audit_csv(statements, audit_path, mask_personal)


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


def _write_audit_csv(
    statements: list[Statement],
    audit_path: str | Path,
    mask_personal: bool,
) -> None:
    """Write a CSV of every transaction with its category."""
    with open(audit_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Account", "Statement", "PostDate", "Description", "Amount", "Type", "Balance", "Category"])
        for stmt in statements:
            for tx in stmt.transactions:
                desc = _mask_desc(tx.description) if mask_personal else tx.description
                writer.writerow(
                    [
                        stmt.account_number,
                        stmt.month_label,
                        tx.post_date,
                        desc,
                        str(tx.amount) if tx.is_credit else str(-tx.amount),
                        "Credit" if tx.is_credit else "Debit",
                        str(tx.balance),
                        tx.category,
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
