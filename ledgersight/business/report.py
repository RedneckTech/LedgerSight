"""Business financial report PDF builder and orchestration."""
from __future__ import annotations

import hashlib
import logging
import re
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from ledgersight.models import Statement, Transaction, BusinessConfig, ReconciliationResult
from ledgersight.charts import (
    _empty_chart_buf, chart_revenue_vs_expenses_monthly, chart_profit_monthly,
    chart_net_cash_flow, chart_balance_trend, chart_expenses_by_category,
    chart_revenue_by_category, chart_cost_by_month, chart_projection,
    chart_top_vendors, chart_top_revenue_sources,
)
from ledgersight.categorizer import TransactionCategorizer, normalize_merchant
from ledgersight.constants import _PNL_DISCLAIMER, _CPA_DISCLAIMER, _QUARTER_MONTHS
from ledgersight.exports import CSVExporter, get_fixed_asset_candidates
from ledgersight.parsers import (
    parse_amount, safe_pct, safe_div, _parse_daily_date, fmt_dollar,
)
from ledgersight.pdf_renderer import ReportPDF
from ledgersight.business.pl import ProfitAndLoss, build_pl, build_monthly_pls, build_quarterly_pls
from ledgersight.business.kpis import FinancialKPIs, calculate_kpis
from ledgersight.business.projections import ProjectionResult, ProjectionEngine
from ledgersight.business.periods import _fiscal_quarter_months
from ledgersight.reconciliation import reconcile_all

logger = logging.getLogger("ledgersight.business.report")

_SCRIPT_HASH = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:8]


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
        elif self.mode in ("combined", "yearly"):
            pdf.ln(4)
            pdf.set_font("DJV", "I", 8)
            pdf.set_text_color(130, 130, 130)
            pdf.multi_cell(
                0, 4,
                "Financial projections not requested. "
                "Use --projections to include forward-looking estimates."
            )

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
        if len(self.statements) < 12:
            pdf.set_font("DJV", "B", 8)
            pdf.set_text_color(180, 120, 40)
            pdf.cell(0, 5,
                     f"Partial-year report \u2014 {12 - len(self.statements)} month(s) not included",
                     align="C", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(8)

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

        if self.forced_generation:
            pdf.body_text(
                "Reconciliation: FAILED \u2014 report generation was forced "
                "(--allow-mismatch). Financial totals are NOT validated.", size=8,
            )
        elif self.all_reconciled:
            pdf.body_text("Reconciliation: PASSED", size=8)
        else:
            pdf.body_text("Reconciliation: FAILED - see Data Quality section", size=8)

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
                f"PRELIMINARY P&L \u2014 {cpa_review_count} of {total_tx} transactions "
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

        total_tx = sum(len(s.transactions) for s in self.statements)
        cpa_review_count = sum(
            1 for s in self.statements for tx in s.transactions if tx.cpa_review
        )
        classified_count = total_tx - cpa_review_count
        classified_pct = (classified_count / max(total_tx, 1)) * 100
        unclass_credits = sum(
            tx.amount for s in self.statements for tx in s.transactions
            if tx.cpa_review and tx.is_credit
        )
        unclass_debits = sum(
            tx.amount for s in self.statements for tx in s.transactions
            if tx.cpa_review and not tx.is_credit
        )
        pnl_status = "Preliminary \u2014 classification incomplete" if classified_pct < 80 else "Substantially classified"
        if classified_pct < 50:
            pnl_status = "Highly Preliminary \u2014 majority unclassified"

        pdf.ln(4)
        pdf.sub_title("Classification Quality")
        class_rows = [
            ("Transactions Classified", f"{classified_count} of {total_tx} ({classified_pct:.0f}%)"),
            ("Transactions Requiring Review", str(cpa_review_count)),
            ("Unclassified Credits", fmt_dollar(unclass_credits)),
            ("Unclassified Debits", fmt_dollar(unclass_debits)),
            ("P&L Status", pnl_status),
        ]
        pdf.draw_kv_table(class_rows)

    def _data_quality(self, pdf: ReportPDF):
        pdf.add_page()
        pdf.section_title("Data Quality and Reconciliation Status")

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
        period_label = "YTD Total" if len(keys) >= 12 else f"Period Total ({keys[0][:7]} to {keys[-1][:7]})"
        headers = ["Category"] + month_labels_short + [period_label]
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

        _add_section("Revenue", "R", lambda pl: pl.revenue, lambda pl: pl.total_revenue)
        _add_section("Direct Costs / COGS", "D", lambda pl: pl.direct_costs, lambda pl: pl.total_direct_costs)
        _add_section("Operating Expenses", "O", lambda pl: pl.operating_expenses, lambda pl: pl.total_operating_expenses)

        pdf.sub_title("Profit Summary")
        sum_headers = ["Metric"] + month_labels_short + [period_label]
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
        if not self.monthly_pls:
            return
        for key in sorted(self.monthly_pls.keys()):
            pl = self.monthly_pls[key]
            pdf.add_page()
            pdf.section_title(f"P&L Statement {pl.label}")
            pdf.body_text_small(_PNL_DISCLAIMER)

            total_rev = pl.total_revenue

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

            pdf.set_font("DJV", "B", 10)
            pdf.set_fill_color(44, 62, 80)
            pdf.set_text_color(255, 255, 255)
            pdf.cell(60, 7, "Net Profit (Loss)", fill=True)
            pdf.cell(35, 7, fmt_dollar(pl.net_profit), align="R", fill=True)
            pdf.cell(30, 7, pl.net_margin, align="R", fill=True)
            pdf.ln(12)

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

        total_tx = sum(len(s.transactions) for s in self.statements)
        cpa_review_count = sum(
            1 for s in self.statements for tx in s.transactions if tx.cpa_review
        )
        classified_pct = ((total_tx - cpa_review_count) / max(total_tx, 1)) * 100
        preliminary = classified_pct < 80

        if preliminary:
            pdf.set_font("DJV", "B", 8)
            pdf.set_text_color(180, 60, 60)
            pdf.multi_cell(
                0, 4.5,
                f"WARNING: Only {classified_pct:.0f}% of transactions are classified. "
                f"KPIs below are based on currently classified data and may change materially."
            )
            pdf.ln(3)

        margin_label = "Gross Margin*" if preliminary else "Gross Margin"
        op_margin_label = "Operating Margin*" if preliminary else "Operating Margin"
        net_margin_label = "Net Margin*" if preliminary else "Net Margin"
        runway_label = ("Bank-Account Runway (classified expenses only)"
                        if preliminary else "Cash Runway Estimate (months)")
        avg_rev_label = "Avg Monthly Revenue*" if preliminary else "Avg Monthly Revenue"
        avg_exp_label = "Avg Monthly Expenses*" if preliminary else "Avg Monthly Expenses"
        avg_net_label = "Avg Monthly Net Profit*" if preliminary else "Avg Monthly Net Profit"

        rows = [
            (margin_label, kpi.gross_margin),
            (op_margin_label, kpi.operating_margin),
            (net_margin_label, kpi.net_margin),
            ("Expense-to-Revenue Ratio", kpi.expense_to_revenue),
            (avg_rev_label, fmt_dollar(kpi.avg_monthly_revenue)),
            (avg_exp_label, fmt_dollar(kpi.avg_monthly_expenses)),
            (avg_net_label, fmt_dollar(kpi.avg_monthly_net)),
            ("Average Transaction Value", fmt_dollar(kpi.avg_transaction_value)),
            ("Largest Bank Credit", fmt_dollar(kpi.largest_income)),
            ("Largest Bank Debit", fmt_dollar(kpi.largest_expense)),
            ("Min Monthly Balance", fmt_dollar(kpi.min_monthly_balance)),
            ("Max Monthly Balance", fmt_dollar(kpi.max_monthly_balance)),
            (runway_label, kpi.cash_runway_months),
            ("Total Revenue", fmt_dollar(kpi.total_revenue)),
            ("Total Expenses", fmt_dollar(kpi.total_expenses)),
            ("Net Income (Cash Basis)", fmt_dollar(kpi.net_income)),
        ]
        pdf.draw_kv_table(rows)

        if preliminary:
            pdf.body_text_small("* Preliminary \u2014 based on currently classified transactions only.")

    def _projections(self, pdf: ReportPDF):
        if not self.projections:
            return

        pr = next(
            (item for item in self.projections if item.scenario == "base"),
            self.projections[0],
        )

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
        pdf.add_page()
        pdf.section_title("Transaction Detail")
        if self.full_detail:
            pdf.body_text_small(
                f"Complete transaction listing for all {len(self.statements)} months."
            )
        else:
            pdf.body_text_small(
                f"Transaction detail excerpt \u2014 up to 50 transactions per statement. "
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

        total_tx = sum(len(s.transactions) for s in self.statements)
        cpa_review_count = sum(
            1 for s in self.statements for tx in s.transactions if tx.cpa_review
        )
        classification_pct = (cpa_review_count / max(total_tx, 1)) * 100
        partial_year = len(self.statements) < 12
        classification_low = classification_pct > 50 or partial_year

        unclass_credits = pl.uncategorized_non_pnl_credits
        unclass_debits = pl.uncategorized_non_pnl_debits

        reserve_withheld = (
            classification_low
            or (pl.net_profit <= 0)
            or unclass_credits > pl.total_revenue * Decimal("2")
            or unclass_debits > (pl.total_direct_costs + pl.total_operating_expenses) * Decimal("2")
        )

        if reserve_withheld:
            reasons = []
            if partial_year:
                reasons.append("partial-year report")
            if classification_pct > 50:
                reasons.append("classification incomplete")
            if unclass_credits > pl.total_revenue * Decimal("2"):
                reasons.append("unclassified credits exceed classified revenue")
            if unclass_debits > (pl.total_direct_costs + pl.total_operating_expenses) * Decimal("2"):
                reasons.append("unclassified debits exceed classified expenses")
            if not reasons:
                reasons.append("net loss \u2014 no reserve needed")
            reserve_label = f"Tax reserve withheld \u2014 {'; '.join(reasons)}"
            reserve_val = Decimal("0")
        else:
            reserve_pct = Decimal(str(
                self.config.projection_config.get("tax_reserve_pct", 0.25)
                if self.config.projection_config else 0.25
            ))
            reserve_label = f"Estimated Tax Reserve ({float(reserve_pct) * 100:.0f}%)"
            reserve_val = pl.net_profit * reserve_pct

        rows = [
            ("Total Gross Receipts / Revenue", fmt_dollar(pl.total_revenue)),
            ("Cost of Goods Sold / Direct Costs", fmt_dollar(pl.total_direct_costs)),
            ("Gross Profit", fmt_dollar(pl.gross_profit)),
            ("Operating Expenses", fmt_dollar(pl.total_operating_expenses)),
            ("Interest Income", fmt_dollar(pl.total_other_income)),
            ("Interest Expense", fmt_dollar(pl.total_other_expense)),
            ("Net Cash-Basis Profit (Loss)", fmt_dollar(pl.net_profit)),
            ("", ""),
            (reserve_label, fmt_dollar(reserve_val) if reserve_val else "-"),
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

        tax_groups: dict[str, dict[str, object]] = {}
        for s in self.statements:
            for tx in s.transactions:
                if tx.is_credit or tx.is_transfer:
                    continue
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

        has_factoring_like = any(
            "APEX" in tx.description.upper() or "FACTOR" in tx.description.upper()
            for s in self.statements for tx in s.transactions
            if tx.is_credit
        )
        if has_factoring_like:
            pdf.ln(4)
            pdf.set_font("DJV", "B", 8)
            pdf.set_text_color(180, 120, 40)
            pdf.multi_cell(
                0, 4.5,
                "Note: Incoming wires from factoring or settlement companies may "
                "represent net funding rather than gross revenue. Factoring fees, "
                "reserve withholdings, releases, chargebacks, and adjustments should "
                "be reconciled against settlement reports before final tax filing."
            )

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
            pdf.sub_title("Other Large Transactions \u2014 Not Suggested as Assets")
            oth_headers = ["Date", "Vendor", "Description", "Amount", "Likely Reason", "CPA Note"]
            oth_rows = []
            for tx in other[:30]:
                cat = tx.business_category
                if tx.is_loan or cat in ("Loan Proceeds", "Loan Principal Payment", "Loan Interest"):
                    reason = "Loan/Financing"
                elif tx.is_owner_related or cat in ("Owner Contribution", "Owner Draw or Distribution"):
                    reason = "Owner Activity"
                elif "Insurance" in cat or "PROG" in tx.description.upper():
                    reason = "Insurance Payment"
                elif cat in ("Fuel",):
                    reason = "Fuel Purchase"
                elif "Rent" in cat or "Lease" in cat:
                    reason = "Rent/Lease"
                elif cat in ("Repairs and Maintenance", "Equipment Maintenance", "Vehicle Expense"):
                    reason = "Repair/Maintenance"
                elif tx.is_transfer or cat in ("Account Transfer", "Credit Card Payment"):
                    reason = "Account Transfer"
                elif cat in ("Payroll", "Payroll Taxes"):
                    reason = "Payroll"
                elif cat == "Bank and Merchant Fees":
                    reason = "Bank Fees"
                elif cat in ("Software and Cloud Services", "Telephone and Internet"):
                    reason = "Technology/Utilities"
                else:
                    reason = "Unidentified \u2014 CPA review"
                note = "CPA review" if tx.amount >= Decimal("2500") else "Review"
                oth_rows.append([
                    tx.post_date,
                    normalize_merchant(tx.description)[:25],
                    self._mask_text(tx.description)[:35],
                    fmt_dollar(tx.amount),
                    reason,
                    note,
                ])
            pdf.draw_table(oth_headers, oth_rows, col_widths=cw,
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

    if target_year:
        statements = [s for s in statements if s.year == target_year]
    if target_month:
        statements = [s for s in statements if s.month == target_month]
    if target_quarter:
        fys = config.fiscal_year_start
        fy_quarter_months = _fiscal_quarter_months(target_quarter, fys)
        statements = [s for s in statements if s.month in fy_quarter_months]

    if not statements:
        logger.error("No statements found matching filters.")
        sys.exit(1)

    statements.sort(key=lambda s: (s.year, s.month))

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
                sorted_tx = sorted(
                    filtered_tx,
                    key=lambda tx: (tx.date_obj, tx.sequence),
                )
                first_tx = sorted_tx[0]
                last_tx = sorted_tx[-1]
                period_start_balance = first_tx.balance - first_tx.signed_amount
                period_end_balance = last_tx.balance
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

    categorizer = TransactionCategorizer(config.custom_rules, config.merchant_aliases)
    categorizer.categorize_all(statements)
    categorizer.mark_credit_deposits(statements)

    recon_results, all_reconciled, forced_generation = reconcile_all(
        statements, allow_mismatch=allow_mismatch,
    )
    if forced_generation:
        logger.warning(
            "Reconciliation FAILED for %d statement(s) \u2014 report generation forced by --allow-mismatch. "
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

    pl = build_pl(
        [tx for s in report_statements for tx in s.transactions],
        label=f"Cash-Basis P&L - {config.display_name()}",
    )
    monthly_pls = build_monthly_pls(report_statements)
    quarterly_pls = build_quarterly_pls(report_statements, config.fiscal_year_start)
    kpis = calculate_kpis(pl, len(report_statements), report_statements)

    projections = None
    projection_status = "not_requested"
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
