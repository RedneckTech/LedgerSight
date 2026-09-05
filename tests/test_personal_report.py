"""Unit tests for ledgersight.personal.report."""

from __future__ import annotations

import csv
import unittest
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from ledgersight.personal.categorizer import categorize, categorize_transactions, clean_memo_prefix
from ledgersight.personal.consolidation import consolidate, running_balance_map
from ledgersight.personal.models import Statement, Transaction
from ledgersight.personal.parser import _is_page_artifact  # noqa: F401 (import sanity)
from ledgersight.personal.report import (
    STATUS_ORDER_ONLY,
    _cash_and_debt_change,
    _duplicate_detail_rows,
    _mask_desc,
    _month_first_supported,
    _period_note,
    _printed_sequence_consistent,
    _reconcile_statements,
    _running_balance_mismatches,
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


class TestMaskMemoPrefix(unittest.TestCase):
    def test_strips_mask_prefix(self) -> None:
        self.assertEqual(
            clean_memo_prefix("XXXXXX3608 2/24/26 CLOUD FACTORY VA BURLINGTON IA 05160363 000641"),
            "CLOUD FACTORY VA BURLINGTON IA 05160363 000641",
        )

    def test_keeps_bare_mask(self) -> None:
        self.assertEqual(clean_memo_prefix("XXXXXX6781 6/28/26"), "XXXXXX6781 6/28/26")

    def test_purchase_not_transfer(self) -> None:
        self.assertEqual(categorize("XXXXXX3608 8/19/26 LOVE'S #0687 INS SOUTH JACKSON IL"), "Fuel")
        self.assertEqual(categorize("XXXXXX3608 2/24/26 CLOUD FACTORY VA BURLINGTON IA"), "Shopping")
        self.assertEqual(categorize("XXXXXX2136 3/23/26 PILOT #0067 CARTERSVILLE GA"), "Fuel")
        self.assertEqual(categorize("XXXXXX3608 4/24/26 CASEYS #4308 CHARITON IA"), "Fuel")

    def test_bare_mask_is_transfer(self) -> None:
        self.assertEqual(categorize("XXXXXX6781 6/28/26"), "Transfers")

    def test_subscription_removed_from_restaurants(self) -> None:
        self.assertEqual(categorize("DD *DOORDASHDASHPASS SAN FRANCISCO CA"), "Subscriptions")

    def test_steam_and_vape_shop_are_shopping(self) -> None:
        self.assertEqual(categorize("WL STEAM PURCHA SEATTLE WA 10670326 943571"), "Shopping")
        self.assertEqual(categorize("CLOUD FACTORY VAPES BURLINGTON IA"), "Shopping")


class TestRunningBalanceMismatches(unittest.TestCase):
    def test_detects_printed_vs_recomputed(self) -> None:
        stmt = make_stmt()
        stmt.beginning_balance = Decimal("189.25")
        stmt.transactions = [
            make_tx("12/20/2025", "FLYING J 737", "11.67", False, "177.58"),
            make_tx("12/31/2025", "PILOT 1135", "26.43", False, "198.01"),
        ]
        result = consolidate([stmt])
        rows = _running_balance_mismatches(result.ledgers[0])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][3], "$198.01")
        self.assertEqual(rows[0][4], "$151.15")
        self.assertEqual(rows[0][5], "$26.43")
        self.assertEqual(rows[0][7], "Verify \u2013 possible parse error or missing row")

    def test_shared_post_date_rows_each_checked(self) -> None:
        stmt = make_stmt()
        stmt.beginning_balance = Decimal("500.00")
        stmt.transactions = [
            make_tx("06/24/2026", "RICHERS TRUCKING PAYROLL", "400.00", True, "900.00"),
            make_tx("06/24/2026", "WAL-MART STORE", "40.00", False, "860.00"),
        ]
        result = consolidate([stmt])
        rows = _running_balance_mismatches(result.ledgers[0])
        self.assertEqual(rows, [])

    def test_shared_post_date_rows_mismatch(self) -> None:
        stmt = make_stmt()
        stmt.beginning_balance = Decimal("500.00")
        stmt.transactions = [
            make_tx("06/24/2026", "RICHERS TRUCKING PAYROLL", "400.00", True, "900.00"),
            make_tx("06/24/2026", "WAL-MART STORE", "40.00", False, "400.00"),
        ]
        result = consolidate([stmt])
        rows = _running_balance_mismatches(result.ledgers[0])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][3], "$400.00")
        self.assertEqual(rows[0][4], "$860.00")

    def test_out_of_date_order_statement_is_informational(self) -> None:
        # Mirrors a Capital One statement that lists January rows before the
        # December ones: printed balances chain in row order (189.25 -> 230.29
        # -> ... -> 198.01 = closing), so date-order recomputation differs but
        # nothing is missing.
        stmt = make_card_stmt()
        stmt.statement_date = "01/17/2026"
        stmt.beginning_balance = Decimal("189.25")
        stmt.ending_balance = Decimal("198.01")
        stmt.transactions = [
            make_tx("01/02/2026", "PHILLIPS 66", "41.04", False, "230.29"),
            make_tx("01/12/2026", "CAPITAL ONE AUTOPAY PYMT", "100.00", True, "130.29"),
            make_tx("12/20/2025", "FLYING J 737", "14.75", False, "145.04"),
            make_tx("12/31/2025", "PILOT 1135", "52.97", False, "198.01"),
        ]
        stmt.total_credits = Decimal("100.00")
        stmt.total_debits = Decimal("108.76")
        stmt.credit_count, stmt.debit_count = 1, 3
        result = consolidate([stmt])
        rows = _running_balance_mismatches(result.ledgers[0])
        self.assertTrue(rows)
        self.assertTrue(all(r[7] == STATUS_ORDER_ONLY for r in rows))
        self.assertTrue(_printed_sequence_consistent(stmt, is_card=True))

    def test_ignores_rows_within_period(self) -> None:
        stmt = make_stmt()
        stmt.period_start = "12/31/2025"
        result = consolidate([stmt])
        rows = _running_balance_mismatches(result.ledgers[0])
        self.assertEqual(rows, [])


class TestCashAndDebtChange(unittest.TestCase):
    def test_splits_cash_and_card(self) -> None:
        result = consolidate([make_stmt(), make_card_stmt()])
        cash, debt = _cash_and_debt_change(result.ledgers)
        self.assertEqual(cash, Decimal("300.00"))
        self.assertEqual(debt, Decimal("38.88"))
        self.assertEqual(cash + debt, Decimal("338.88"))


class TestMonthFirstSupported(unittest.TestCase):
    def test_respects_period_start(self) -> None:
        stmt = make_stmt()
        stmt.period_start = "12/27/2025"
        stmt.transactions = [make_tx("12/27/2025", "WAL-MART", "50.00", False, "50.00")]
        result = consolidate([stmt])
        first = _month_first_supported(result.ledgers[0], 2025, 12)
        self.assertEqual(first.isoformat(), "2025-12-27")

    def test_full_month_covered_by_straddling_statement(self) -> None:
        stmt = make_stmt()
        stmt.statement_date = "06/30/2026"
        stmt.period_start = "05/30/2026"
        result = consolidate([stmt])
        first = _month_first_supported(result.ledgers[0], 2026, 6)
        self.assertEqual(first.isoformat(), "2026-06-01")

    def test_jan_covered_by_nov_feb_statement(self) -> None:
        stmt = make_stmt()
        stmt.statement_date = "02/27/2026"
        stmt.period_start = "11/29/2025"
        result = consolidate([stmt])
        first = _month_first_supported(result.ledgers[0], 2026, 1)
        self.assertEqual(first.isoformat(), "2026-01-01")

    def test_half_covered_month_clips_to_start(self) -> None:
        stmt = make_stmt()
        stmt.statement_date = "01/15/2026"
        stmt.period_start = "01/08/2026"
        result = consolidate([stmt])
        first = _month_first_supported(result.ledgers[0], 2026, 1)
        self.assertEqual(first.isoformat(), "2026-01-08")

    def test_card_period_inferred_from_prior_closing_date(self) -> None:
        # Capital One prints only a closing date; consecutive statements abut,
        # so the March statement (closing 03/28) starts 02/26 and covers all
        # of March 1-28 even though its first transaction is on 03/23.
        feb = make_card_stmt()
        feb.statement_date = "02/25/2026"
        feb.transactions = [make_tx("02/10/2026", "CASEYS", "10.00", False, "1182.85")]
        feb.ending_balance = Decimal("1182.85")
        mar = make_card_stmt()
        mar.statement_date = "03/28/2026"
        mar.beginning_balance = Decimal("1182.85")
        mar.transactions = [make_tx("03/23/2026", "LOVE'S", "12.00", False, "1194.85")]
        mar.ending_balance = Decimal("1194.85")
        result = consolidate([feb, mar])
        ledger = result.ledgers[0]
        self.assertEqual(_month_first_supported(ledger, 2026, 3).isoformat(), "2026-03-01")
        self.assertIn("02/26/2026", _period_note(mar, ledger))
        self.assertIn("inferred", _period_note(mar, ledger))
        # The first statement has nothing to abut against: fall back honestly.
        self.assertIn("start", _period_note(feb, ledger))

    def test_gap_between_statements_is_not_bridged(self) -> None:
        jan = make_card_stmt()
        jan.statement_date = "01/28/2026"
        may = make_card_stmt()
        may.statement_date = "05/28/2026"
        may.transactions = [make_tx("05/08/2026", "LOVE'S", "12.00", False, "1145.97")]
        result = consolidate([jan, may])
        ledger = result.ledgers[0]
        self.assertEqual(_month_first_supported(ledger, 2026, 5).isoformat(), "2026-05-08")


class TestWriteAuditCsv(unittest.TestCase):
    def test_audit_rows(self) -> None:
        stmt = make_stmt()
        result = consolidate([stmt])
        ledger = result.ledgers[0]
        run_maps = dict(running_balance_map(ledger))
        seq_maps = {id(t): i for i, t in enumerate(ledger.transactions, start=1)}
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "audit.csv"
            _write_audit_csv([stmt], path, mask_personal=True, run_maps=run_maps, seq_maps=seq_maps)
            rows = list(csv.DictReader(path.open()))
        self.assertEqual(
            list(rows[0].keys()),
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
            ],
        )
        self.assertEqual(len(rows), 4)
        credit = [r for r in rows if r["Type"] == "Credit"][0]
        self.assertEqual(credit["Amount"], "500.00")
        self.assertEqual(credit["Running"], "600.00")
        self.assertEqual(credit["Seq"], "1")
        debits = [r for r in rows if r["Type"] == "Debit"]
        self.assertEqual(len(debits), 3)
        self.assertTrue(all(r["Amount"].startswith("-") for r in debits))


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
            rows = list(csv.DictReader(path.open()))
        self.assertEqual(
            list(rows[0].keys()),
            [
                "Account",
                "Account Type",
                "Seq",
                "Date",
                "Description",
                "Category",
                "Amount",
                "Type",
                "Balance",
                "Source",
            ],
        )
        self.assertEqual(len(rows), 5)
        payroll = [r for r in rows if "RICHERS" in r["Description"]]
        self.assertEqual(len(payroll), 1)
        self.assertEqual(payroll[0]["Description"], "RICHERS TRUCKING PAYROLL")

    def test_export_order_matches_running_balances(self) -> None:
        # Same-day rows must be exported in calculation order so consecutive
        # balances chain: balance[i] == balance[i-1] +/- amount[i].
        stmt = make_stmt()
        stmt.transactions = [
            make_tx("05/08/2026", "DOORDASH", "20.00", False, "80.00"),
            make_tx("05/08/2026", "LOVE'S #0687", "30.00", False, "50.00"),
            make_tx("05/08/2026", "RICHERS TRUCKING PAYROLL", "500.00", True, "550.00"),
        ]
        stmt.ending_balance = Decimal("550.00")
        result = consolidate([stmt])
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "tx.csv"
            _write_transactions_csv(result, path, mask_personal=False)
            rows = list(csv.DictReader(path.open()))
        self.assertEqual([r["Seq"] for r in rows], ["1", "2", "3"])
        previous = stmt.beginning_balance
        for r in rows:
            self.assertEqual(Decimal(r["Balance"]), previous + Decimal(r["Amount"]))
            previous = Decimal(r["Balance"])


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


class TestIncomeToCashBridge(unittest.TestCase):
    def test_bridge_sums_to_observed_change(self) -> None:
        import datetime

        from ledgersight.personal.insights import dashboard_totals
        from ledgersight.personal.report import _flow_split, _income_to_cash_bridge

        result = consolidate([make_stmt(), make_card_stmt()])
        totals = dashboard_totals(result, datetime.date(2026, 2, 1))
        flow = _flow_split(result)
        bridge = _income_to_cash_bridge(totals, flow)
        steps = sum((amt for label, amt in bridge if not label.startswith("=")), Decimal("0"))
        self.assertEqual(steps, bridge[-1][1])
        self.assertEqual(bridge[-1][1], totals["total_credits"] - totals["total_debits"])
        self.assertEqual(bridge[0][0], "Income (payroll + other deposits)")
        self.assertEqual(bridge[2][0], "= Income less categorized spending")


class TestGenerateReportWithChecks(unittest.TestCase):
    def test_checks_yaml_recategorizes_and_renders(self) -> None:
        stmt = make_stmt()
        stmt.transactions.append(make_tx("01/20/2026", "CHECK # 5001", "100.00", False, "300.00"))
        stmt.ending_balance = Decimal("300.00")
        stmt.total_debits = Decimal("300.00")
        stmt.debit_count = 4
        with TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            checks = tmp_p / "checks.yaml"
            checks.write_text("5001:\n  payee: Landlord\n  category: Rent\n")
            pdf = tmp_p / "report.pdf"
            tx_csv = tmp_p / "tx.csv"
            ok = generate_report(
                [stmt],
                pdf,
                mode="yearly",
                target_year=2026,
                transactions_csv_path=tx_csv,
                checks_path=checks,
            )
            self.assertTrue(ok)
            rows = list(csv.DictReader(tx_csv.open()))
        check_rows = [r for r in rows if "CHECK # 5001" in r["Description"]]
        self.assertEqual(len(check_rows), 1)
        self.assertEqual(check_rows[0]["Category"], "Rent")
        self.assertIn("Landlord", check_rows[0]["Description"])
