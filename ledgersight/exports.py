"""CSV export utilities for audit, P&L, and CPA packages."""
from __future__ import annotations
import csv
import logging
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

from ledgersight.categorizer import normalize_merchant
from ledgersight.models import (
    BusinessConfig,
    ReconciliationResult,
    Statement,
    Transaction,
)
from ledgersight.redaction import DataRedactor

if TYPE_CHECKING:
    from ledgersight.business.pl import ProfitAndLoss, ProjectionResult

logger = logging.getLogger("ledgersight.exports")

def _safe_csv_cell(value: object) -> str:
    text = str(value)
    if text and text[0] in ("=", "+", "-", "@"):
        return "'" + text
    return text


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

    def __init__(
        self,
        statements: list[Statement],
        config: BusinessConfig,
        pl: ProfitAndLoss,
        projections: list[ProjectionResult] | None = None,
        recon_results: list[ReconciliationResult] | None = None,
        redactor: DataRedactor | None = None,
    ):
        self.statements = statements
        self.config = config
        self.pl = pl
        self.projections = projections or []
        self.recon_results = recon_results or []
        self.redactor = redactor

    def export_audit(self, path: Path):
        """Export full transaction audit CSV."""
        redactor = self.redactor or DataRedactor(mask_personal=False)
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "Statement", "PostDate", "Description", "Amount", "Type", "Balance",
                "BusinessCategory", "TaxCategory", "P&LIncluded", "Deductibility",
                "CPAReview", "ReviewReason", "Merchant", "IsTransfer",
                "IsOwnerRelated", "IsFixedAsset", "IsLoan", "SourceStatement",
            ])
            for stmt in self.statements:
                for tx in stmt.transactions:
                    desc = redactor.description(tx.description)
                    merchant = tx.merchant or normalize_merchant(tx.description)
                    merchant = redactor.merchant(merchant)
                    writer.writerow([
                        stmt.month_label,
                        tx.post_date,
                        _safe_csv_cell(desc),
                        str(tx.amount if tx.is_credit else -tx.amount),
                        "Credit" if tx.is_credit else "Debit",
                        str(tx.balance),
                        _safe_csv_cell(tx.business_category),
                        _safe_csv_cell(tx.tax_category),
                        "Yes" if tx.include_in_pnl else "No",
                        _safe_csv_cell(tx.deductibility),
                        "Yes" if tx.cpa_review else "No",
                        _safe_csv_cell(tx.review_reason),
                        _safe_csv_cell(merchant),
                        "Yes" if tx.is_transfer else "No",
                        "Yes" if tx.is_owner_related else "No",
                        "Yes" if tx.is_fixed_asset else "No",
                        "Yes" if tx.is_loan else "No",
                        _safe_csv_cell(redactor.source_path(tx.source_statement)),
                    ])
        logger.info("Audit CSV saved to: %s", path)

    def export_pl(self, path: Path):
        """Export P&L CSV."""
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Category", "Type", "Amount"])
            for cat, val in sorted(self.pl.revenue.items()):
                writer.writerow([_safe_csv_cell(cat), "Revenue", str(val)])
            for cat, val in sorted(self.pl.direct_costs.items()):
                writer.writerow([_safe_csv_cell(cat), "COGS/Direct Cost", str(val)])
            for cat, val in sorted(self.pl.operating_expenses.items()):
                writer.writerow([_safe_csv_cell(cat), "Operating Expense", str(val)])
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
        redactor = self.redactor or DataRedactor(mask_personal=False)

        self._write_csv(output_dir / "cpa_revenue_detail.csv",
                        ["Date", "Description", "Category", "Amount"],
                        [[tx.post_date, redactor.description(tx.description), tx.business_category, str(tx.amount)]
                         for s in self.statements for tx in s.transactions
                         if tx.is_credit and tx.include_in_pnl])

        self._write_csv(output_dir / "cpa_expense_detail.csv",
                        ["Date", "Description", "Category", "TaxCategory", "Amount", "Deductibility", "CPAReview"],
                        [[tx.post_date, redactor.description(tx.description), tx.business_category, tx.tax_category,
                          str(tx.amount), tx.deductibility, "Yes" if tx.cpa_review else "No"]
                         for s in self.statements for tx in s.transactions
                         if not tx.is_credit and tx.include_in_pnl])

        self._write_csv(output_dir / "cpa_fixed_assets.csv",
                        ["Date", "Vendor", "Description", "Amount"],
                        [[tx.post_date, redactor.merchant(normalize_merchant(tx.description)),
                          redactor.description(tx.description), str(tx.amount)]
                         for tx in get_fixed_asset_candidates(self.statements)])

        self._write_csv(output_dir / "cpa_loan_activity.csv",
                        ["Date", "Description", "Category", "Amount", "Type"],
                        [[tx.post_date, redactor.description(tx.description), tx.business_category,
                          str(tx.amount), "Credit" if tx.is_credit else "Debit"]
                         for s in self.statements for tx in s.transactions
                         if tx.is_loan or "LOAN" in tx.description.upper()])

        self._write_csv(output_dir / "cpa_owner_activity.csv",
                        ["Date", "Description", "Category", "Amount", "Type"],
                        [[tx.post_date, redactor.description(tx.description), tx.business_category,
                          str(tx.amount), "Contribution" if tx.is_credit else "Draw"]
                         for s in self.statements for tx in s.transactions
                         if tx.is_owner_related or tx.business_category in ("Owner Contribution", "Owner Draw or Distribution")])

        self._write_csv(output_dir / "cpa_uncategorized.csv",
                        ["Date", "Description", "Amount", "Type", "ReviewReason"],
                        [[tx.post_date, redactor.description(tx.description), str(tx.amount),
                          "Credit" if tx.is_credit else "Debit", tx.review_reason]
                         for s in self.statements for tx in s.transactions
                         if tx.cpa_review])

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
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            for row in rows:
                writer.writerow([_safe_csv_cell(cell) for cell in row])

    def export_category_template(self, path: Path):
        """Export a CSV with all detected merchants and their current categories."""
        redactor = self.redactor or DataRedactor(mask_personal=False)
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

        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Merchant", "CurrentBusinessCategory", "CurrentTaxCategory",
                             "TransactionCount", "TotalAmount",
                             "SuggestedBusinessCategory", "SuggestedTaxCategory"])
            for m, info in sorted(merchants.items(), key=lambda x: x[1]["total"], reverse=True):
                writer.writerow([_safe_csv_cell(redactor.merchant(m)), _safe_csv_cell(info["category"]),
                                 _safe_csv_cell(info["tax_category"]),
                                 str(info["count"]), str(info["total"]), "", ""])
        logger.info("Category template saved to: %s", path)
