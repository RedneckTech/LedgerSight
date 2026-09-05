"""Unit tests for ledgersight.personal.report."""

from __future__ import annotations

import unittest
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from ledgersight.personal.categorizer import categorize_transactions
from ledgersight.personal.consolidation import consolidate
from ledgersight.personal.models import Statement, Transaction
from ledgersight.personal.parser import _is_page_artifact  # noqa: F401 (import sanity)
from ledgersight.personal.report import (
    _duplicate_detail_rows,
    _mask_desc,
    _reconcile_statements,
    _write_audit_csv,
    _write_transactions_csv,
    build_category_table_rows,
    build_monthly_table_rows,
    build_top_merchants,
    generate_report,
)


def make_tx(
    post_date: str,
    description: str,
    amount: str,
    is_credit: bool,
    balance: str,
    category: str = "",
) -> Transaction:
    return Transaction(
        post_date=post_date,
        description=description,
        amount=Decimal(amount),
        is_credit=is_credit,
        balance=Decimal(balance),
        category=category,
    )


def make_stmt() -> Statement:
    return Statement(
        statement_date="01/31/2026",
        account_number="XXXXXXXXXXX1234",
        beginning_balance=Decimal("100.00"),
        ending_balance=Decimal("400.00"),
        total_credits=Decimal("500.00"),
        total_debits=Decimal("200.00"),
        credit_count=1,
        debit_count=3,
        transactions=[
            make_tx("01/02/2026", "RICHERS TRUCKING PAYROLL", "500.00", True, "600.00"),
            make_tx("01/05/2026", "WAL-MART STORE", "50.00", False, "550.00"),
            make_tx("01/10/2026", "AMAZON MKTPL", "25.00", False, "525.00"),
            make_tx("01/15/2026", "WAL-MART STORE", "125.00", False, "400.00"),
        ],
    )


def make_card_stmt() -> Statement:
    return Statement(
        statement_date="02/01/2026",
        account_number="XXXXXXXXXXXX0142",
        beginning_balance=Decimal("1172.85"),
        ending_balance=Decimal("1133.97"),
        total_credits=Decimal("100.00"),
        total_debits=Decimal("61.12"),
        credit_count=1,
        debit_count=3,
        transactions=[
            make_tx("01/03/2026", "LOVE'S #0687 JACKSON IL", "15.25", False, "1188.10"),
            make_tx("01/09/2026", "CASEYS #0074 MORNING SUN IA", "13.92", False, "1202.02"),
            make_tx("01/12/2026", "PAYMENT - THANK YOU", "100.00", True, "1102.02"),
            make_tx("01/28/2026", "INTEREST CHARGED", "31.95", False, "1133.97", category="Interest"),
        ],
        fees_charged=Decimal("0.00"),
        interest_charged=Decimal("31.95"),
        account_type="Credit Card",
        institution="Capital One",
    )


class TestBuildMonthlyTableRows(unittest.TestCase):
    def test_rows(self) -> None:
        rows = build_monthly_table_rows(make_stmt())
        by_label = {r[0]: r[1] for r in rows}
        self.assertEqual(by_label["Ending Balance"], "$400.00")
        self.assertEqual(by_label["Overdraft Fees"], "$0.00")
        self.assertEqual(by_label["Returned Item Fees"], "$0.00")
        self.assertEqual(by_label["Credit Transactions"], "1")

    def test_card_rows(self) -> None:
        rows = build_monthly_table_rows(make_card_stmt())
        by_label = {r[0]: r[1] for r in rows}
        self.assertEqual(by_label["Fees Charged"], "$0.00")
        self.assertEqual(by_label["Interest Charged"], "$31.95")
        self.assertNotIn("Overdraft Fees", by_label)
        self.assertNotIn("Returned Item Fees", by_label)


class TestBuildCategoryTableRows(unittest.TestCase):
    def test_totals(self) -> None:
        stmt = make_stmt()
        categorize_transactions([stmt])
        rows = build_category_table_rows([stmt])
        by_cat = {r[0]: r[1] for r in rows}
        self.assertEqual(by_cat["Groceries"], "$175.00")
        self.assertEqual(by_cat["Shopping"], "$25.00")
        self.assertEqual(len(by_cat), 2)


class TestBuildTopMerchants(unittest.TestCase):
    def test_ranks_and_excludes(self) -> None:
        stmt = make_stmt()
        categorize_transactions([stmt])
        rows = build_top_merchants([stmt], top_n=5)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][0], "1")
        self.assertEqual(rows[0][2], "$175.00")


class TestReconcile(unittest.TestCase):
    def test_matching(self) -> None:
        self.assertTrue(_reconcile_statements([make_stmt()]))

    def test_mismatch(self) -> None:
        stmt = make_stmt()
        stmt.total_debits = Decimal("999.00")
        self.assertFalse(_reconcile_statements([stmt]))


class TestMaskDesc(unittest.TestCase):
    def test_name(self) -> None:
        self.assertEqual(_mask_desc("PAYMENT TO JACOB PFEIFF"), "PAYMENT TO [NAME REDACTED]")

    def test_address(self) -> None:
        self.assertEqual(_mask_desc("123 MAIN ST"), "[ADDRESS REDACTED]")

    def test_id(self) -> None:
        self.assertEqual(_mask_desc("XXXXXXXX1234"), "[ID REDACTED]")

    def test_unchanged(self) -> None:
        self.assertEqual(_mask_desc("AMAZON MKTPL"), "AMAZON MKTPL")


class TestWriteAuditCsv(unittest.TestCase):
    def test_audit_rows(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "audit.csv"
            _write_audit_csv([make_stmt()], path, mask_personal=True)
            lines = path.read_text().splitlines()
        self.assertEqual(lines[0], "Account,Statement,PostDate,Description,Amount,Type,Balance,Category")
        self.assertEqual(len(lines), 5)
        credit_line = [line for line in lines if line.split(",")[5] == "Credit"][0]
        self.assertTrue(credit_line.split(",")[4] == "500.00")
        debit_lines = [line for line in lines if line.split(",")[5] == "Debit"]
        self.assertEqual(len(debit_lines), 3)
        self.assertTrue(all(line.split(",")[4].startswith("-") for line in debit_lines))


class TestWriteTransactionsCsv(unittest.TestCase):
    def test_deduplicates_and_rows(self) -> None:
        stmt2 = make_stmt()
        stmt2.statement_date = "03/31/2026"
        stmt2.transactions = [
            make_tx("01/02/2026", "RICHERS TRUCKING PAYROLL", "500.00", True, "600.00"),
            make_tx("03/05/2026", "WAL-MART STORE", "40.00", False, "750.00"),
        ]
        result = consolidate([make_stmt(), stmt2])
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "tx.csv"
            _write_transactions_csv(result, path, mask_personal=True)
            lines = path.read_text().splitlines()
        self.assertEqual(lines[0], "Account,Account Type,Date,Description,Category,Amount,Type,Balance")
        self.assertEqual(len(lines), 6)
        payroll = [line for line in lines if "RICHERS" in line]
        self.assertEqual(len(payroll), 1)
        mask_line = payroll[0]
        self.assertEqual(mask_line.split(",")[3], "RICHERS TRUCKING PAYROLL")


class TestDuplicateDetailRows(unittest.TestCase):
    def test_marks_overlap_duplicates(self) -> None:
        stmt2 = make_stmt()
        stmt2.statement_date = "03/31/2026"
        stmt2.transactions = [make_tx("01/02/2026", "RICHERS TRUCKING PAYROLL", "500.00", True, "600.00")]
        result = consolidate([make_stmt(), stmt2])
        rows = _duplicate_detail_rows(result.ledgers, mask_personal=True)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], "03/31/2026")
        self.assertEqual(rows[0][4], "Credit")
        self.assertEqual(rows[0][5], "$500.00")


class TestGenerateReport(unittest.TestCase):
    def test_generates_pdf_and_audit(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            pdf = tmp_p / "report.pdf"
            csv_path = tmp_p / "report_audit.csv"
            tx_csv = tmp_p / "report_transactions.csv"
            generate_report(
                [make_stmt()],
                pdf,
                mode="yearly",
                target_year=2026,
                audit_path=csv_path,
                transactions_csv_path=tx_csv,
            )
            self.assertTrue(pdf.exists())
            self.assertGreater(pdf.stat().st_size, 10000)
            self.assertTrue(csv_path.exists())
            self.assertGreater(len(csv_path.read_text().splitlines()), 1)
            self.assertTrue(tx_csv.exists())
            self.assertEqual(len(tx_csv.read_text().splitlines()), 5)

    def test_multi_account_audit(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            pdf = tmp_p / "report.pdf"
            csv_path = tmp_p / "report_audit.csv"
            generate_report(
                [make_stmt(), make_card_stmt()],
                pdf,
                mode="yearly",
                target_year=2026,
                audit_path=csv_path,
            )
            lines = csv_path.read_text().splitlines()
            self.assertEqual(lines[0].split(",")[0], "Account")
            accounts = {line.split(",")[0] for line in lines[1:]}
            self.assertEqual(accounts, {"XXXXXXXXXXX1234", "XXXXXXXXXXXX0142"})
            self.assertTrue(pdf.exists())


if __name__ == "__main__":
    unittest.main()
