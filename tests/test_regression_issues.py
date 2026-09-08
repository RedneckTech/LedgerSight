"""Regression tests for the review-round issues."""

from __future__ import annotations

import csv
import tempfile
import tomllib
import unittest
from decimal import Decimal
from pathlib import Path

from ledgersight.business.pl import build_monthly_pls, build_pl
from ledgersight.constants import MONEY_RE
from ledgersight.models import BusinessConfig, CategoryRule
from ledgersight.parsers import parse_amount
from ledgersight.personal.consolidation import (
    _dedupe,
    _dedupe_statements,
)
from ledgersight.personal.consolidation import (
    consolidate as personal_consolidate,
)
from ledgersight.personal.models import Statement as PersonalStatement
from ledgersight.personal.models import Transaction as PersonalTransaction
from ledgersight.personal.parser import (
    _card_post_date_for,
    parse_capone_statement,
    parse_statement,
)
from ledgersight.personal.report import (
    _filter_result_by_period,
    _statement_reconciles,
    _write_audit_csv,
    _write_transactions_csv,
    generate_report,
)
from ledgersight.redaction import DataRedactor
from ledgersight.tui.screens.config_editor import _save_config_to_toml
from tests.conftest import make_stmt, make_tx


def make_personal_tx(
    post_date: str = "01/15/2023",
    description: str = "TEST",
    amount: str = "100.00",
    is_credit: bool = True,
    balance: str = "500.00",
    category: str = "",
) -> PersonalTransaction:
    return PersonalTransaction(
        post_date=post_date,
        description=description,
        amount=Decimal(amount),
        is_credit=is_credit,
        balance=Decimal(balance),
        category=category,
    )


def make_personal_stmt(
    statement_date: str = "01/31/2023",
    account_number: str = "XXXX1234",
    beginning_balance: str = "100.00",
    ending_balance: str = "500.00",
    total_credits: str = "500.00",
    total_debits: str = "100.00",
    credit_count: int = 2,
    debit_count: int = 1,
    transactions: list | None = None,
    file_path: str = "",
    account_type: str = "Checking",
    institution: str = "First Interstate",
) -> PersonalStatement:
    return PersonalStatement(
        statement_date=statement_date,
        account_number=account_number,
        beginning_balance=Decimal(beginning_balance),
        ending_balance=Decimal(ending_balance),
        total_credits=Decimal(total_credits),
        total_debits=Decimal(total_debits),
        credit_count=credit_count,
        debit_count=debit_count,
        transactions=transactions or [],
        file_path=file_path,
        account_type=account_type,
        institution=institution,
    )


def sample_card_text() -> str:
    return "\n".join(
        [
            "JACOB C PFEIFF Platinum Card | World Mastercard ending in 0142",
            "Jan 1, 2026 - Feb 1, 2026 | 31 days in Billing Cycle",
            "",
            "Previous Balance $1,172.85",
            "New Balance",
            "$1,133.97",
            "Payments - $100.00",
            "Other Credits $0.00",
            "Transactions + $29.17",
            "Cash Advances + $0.00",
            "Fees Charged + $0.00",
            "Interest Charged + $31.95",
            "New Balance = $1,133.97",
            "",
            "JACOB C PFEIFF #0142: Payments, Credits and Adjustments",
            "Jan 10 Jan 12 PAYMENT - THANK YOU $100.00",
            "",
            "JACOB C PFEIFF #0142: Transactions",
            "Jan 2 Jan 3 LOVE'S #0687 JACKSON IL $15.25",
            "Jan 8 Jan 9 CASEYS #0074 MORNING SUN IA $13.92",
            "",
            "Total Transactions for This Period $29.17",
            "",
            "Interest Charged",
            "Interest Charge on Purchases $31.95",
            "Total Interest for This Period $31.95",
        ]
    )


class TestReconciliationDetectsMissingRows(unittest.TestCase):
    def test_missing_card_transaction_fails_reconciliation(self) -> None:
        stmt = parse_capone_statement(sample_card_text())
        self.assertTrue(_statement_reconciles(stmt)[0])
        # Remove a purchase; the printed summary still expects the original totals.
        stmt.transactions = [tx for tx in stmt.transactions if "CASEYS" not in tx.description]
        ok, _, _, _, _, calc, exp, bal_ok = _statement_reconciles(stmt)
        self.assertFalse(ok)
        self.assertFalse(bal_ok)
        self.assertEqual(exp, Decimal("1133.97"))
        self.assertEqual(calc, Decimal("1120.05"))

    def test_negative_checking_balance_reconciles(self) -> None:
        text = "\n".join(
            [
                "JACOB PFEIFF",
                "XXXXXXXXXXX1234\tBASIC CHECKING",
                "",
                "Statement Ending 01/31/2026",
                "Account Summary",
                "Beginning Balance - $100.00",
                "0 Credit     This Period   $0.00",
                "1 Debit      This Period   $50.00",
                "Ending Balance - $150.00",
                "",
                "Account Activity",
                "Post Date  Description Debits Credits Balance",
                "01/05/2026 WAL-MART $50.00 -$150.00",
            ]
        )
        stmt = parse_statement(text)
        self.assertEqual(stmt.beginning_balance, Decimal("-100.00"))
        self.assertEqual(stmt.ending_balance, Decimal("-150.00"))
        ok, *_ = _statement_reconciles(stmt)
        self.assertTrue(ok)

    def test_generate_report_rejects_mismatch(self) -> None:
        stmt = parse_capone_statement(sample_card_text())
        stmt.transactions = [tx for tx in stmt.transactions if "CASEYS" not in tx.description]
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "report.pdf"
            with self.assertRaises(SystemExit):
                generate_report([stmt], pdf, mode="yearly", target_year=2026)

    def test_generate_report_allow_mismatch_returns_false(self) -> None:
        stmt = parse_capone_statement(sample_card_text())
        stmt.transactions = [tx for tx in stmt.transactions if "CASEYS" not in tx.description]
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "report.pdf"
            result = generate_report(
                [stmt],
                pdf,
                mode="yearly",
                target_year=2026,
                allow_mismatch=True,
            )
            self.assertFalse(result)


class TestDuplicateStatements(unittest.TestCase):
    def test_exact_duplicate_statements_deduplicated(self) -> None:
        stmt = parse_capone_statement(sample_card_text())
        dup = parse_capone_statement(sample_card_text())
        unique = _dedupe_statements([stmt, dup])
        self.assertEqual(len(unique), 1)

    def test_duplicate_transactions_across_same_date_statements_dropped(self) -> None:
        stmt = parse_capone_statement(sample_card_text())
        dup = parse_capone_statement(sample_card_text())
        unique, dropped = _dedupe([stmt, dup])
        self.assertEqual(len(unique), len(stmt.transactions))
        self.assertEqual(len(dropped), len(stmt.transactions))

    def test_legitimate_same_day_repeats_preserved(self) -> None:
        stmt = make_personal_stmt()
        stmt.transactions = [
            make_personal_tx("01/02/2026", "COFFEE SHOP", "5.00", False, "95.00"),
            make_personal_tx("01/02/2026", "GROCERY", "30.00", False, "65.00"),
            make_personal_tx("01/02/2026", "COFFEE SHOP", "5.00", False, "60.00"),
        ]
        unique, dropped = _dedupe([stmt])
        self.assertEqual(len(unique), 3)
        self.assertEqual(len(dropped), 0)
        self.assertEqual([tx.description for tx in unique], ["COFFEE SHOP", "GROCERY", "COFFEE SHOP"])


class TestPeriodFiltersUsePostingDates(unittest.TestCase):
    def test_january_card_activity_on_february_statement_included(self) -> None:
        stmt = parse_capone_statement(sample_card_text())
        result = personal_consolidate([stmt])
        filtered = _filter_result_by_period(result, target_year=2026, target_month=1)
        dates = [tx.post_date for tx in filtered.all_transactions]
        self.assertIn("01/03/2026", dates)
        self.assertIn("01/09/2026", dates)
        self.assertNotIn("02/12/2026", dates)

    def test_generate_report_month_filters_by_post_date(self) -> None:
        stmt = parse_capone_statement(sample_card_text())
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "report.pdf"
            tx_csv = Path(tmp) / "report_transactions.csv"
            audit_csv = Path(tmp) / "report_audit.csv"
            ok = generate_report(
                [stmt],
                pdf,
                mode="monthly",
                target_year=2026,
                target_month=1,
                transactions_csv_path=tx_csv,
                audit_path=audit_csv,
            )
            self.assertTrue(ok)
            rows = list(csv.DictReader(tx_csv.open()))
            self.assertTrue(all(r["Date"].startswith("01/") for r in rows))
            audit = list(csv.DictReader(audit_csv.open()))
            self.assertTrue(any(r["InReport"] == "No" for r in audit))


class TestTuiConfigSavePreservesSettings(unittest.TestCase):
    def test_rules_and_aliases_survive_save(self) -> None:
        config = BusinessConfig(
            business_name="Test LLC",
            custom_rules=[
                CategoryRule(pattern="KEEP", category="Keep", tax_category="Tax", priority=5),
                CategoryRule(pattern="FOO", category="Bar", tax_category="Baz", priority=10),
            ],
            merchant_aliases={"OLD NAME*": "New Name"},
            projection_config={"monthly_revenue_growth": 0.05},
            document_checklist={"bank_statements": "provided"},
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            # Pre-populate without a [cpa] section and with an unknown section.
            path.write_text('[general]\nbusiness_name = "Old"\n\n[unknown_section]\nvalue = 1\n')
            _save_config_to_toml(config, path)
            with open(path, "rb") as f:
                data = tomllib.load(f)
            self.assertEqual(data["general"]["business_name"], "Test LLC")
            self.assertIn("rules", data)
            self.assertEqual(len(data["rules"]), 2)
            self.assertIn("merchant_aliases", data)
            self.assertIn("unknown_section", data)
            self.assertEqual(data["unknown_section"]["value"], 1)

    def test_string_escaping_round_trips(self) -> None:
        config = BusinessConfig(
            business_name='Test "Quoted" LLC',
            address="123 Main St \\ Suite 4",
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            _save_config_to_toml(config, path)
            with open(path, "rb") as f:
                data = tomllib.load(f)
            self.assertEqual(data["general"]["business_name"], 'Test "Quoted" LLC')
            self.assertEqual(data["general"]["address"], "123 Main St \\ Suite 4")


class TestPlReversalAllocationAcrossPeriods(unittest.TestCase):
    def test_cross_month_reversal_sums_to_aggregate(self) -> None:
        txs = [
            make_tx(
                post_date="01/15/2025",
                description="VENDOR",
                amount="100.00",
                is_credit=False,
                business_category="Fuel",
                include_in_pnl=True,
            ),
            make_tx(
                post_date="02/10/2025",
                description="VENDOR",
                amount="100.00",
                is_credit=True,
                business_category="Payment Reversal",
                include_in_pnl=False,
            ),
        ]
        aggregate = build_pl(txs)
        self.assertEqual(aggregate.total_direct_costs, Decimal("0"))
        monthly = build_monthly_pls([make_stmt(transactions=txs)])
        self.assertEqual(monthly["2025-01"].direct_costs["Fuel"], Decimal("100.00"))
        self.assertEqual(monthly["2025-02"].direct_costs["Fuel"], Decimal("-100.00"))
        self.assertEqual(
            monthly["2025-01"].total_direct_costs + monthly["2025-02"].total_direct_costs,
            aggregate.total_direct_costs,
        )


class TestNegativeBalanceParsing(unittest.TestCase):
    def test_spaced_minus_sign(self) -> None:
        self.assertEqual(parse_amount("- $100.00"), Decimal("-100.00"))

    def test_parentheses(self) -> None:
        self.assertEqual(parse_amount("($100.00)"), Decimal("-100.00"))

    def test_money_re_matches_negative_summary(self) -> None:
        m = MONEY_RE.search("Beginning Balance - $100.00")
        self.assertIsNotNone(m)
        self.assertEqual(parse_amount(m.group()), Decimal("-100.00"))


class TestMaskingCoversOutputPaths(unittest.TestCase):
    def test_csv_descriptions_and_sources_redacted(self) -> None:
        stmt = make_personal_stmt(
            statement_date="01/31/2026",
            file_path="/home/Jacob Pfeiff/statements/jan.pdf",
            total_credits="0",
            total_debits="100",
            credit_count=0,
            debit_count=1,
            ending_balance="0",
        )
        stmt.transactions = [
            make_personal_tx("01/02/2026", "PAYMENT TO JACOB PFEIFF", "100.00", False, "0.00"),
        ]
        redactor = DataRedactor(mask_personal=True, redact_names=["JACOB PFEIFF"])
        with tempfile.TemporaryDirectory() as tmp:
            audit = Path(tmp) / "audit.csv"
            _write_audit_csv([stmt], audit, redactor=redactor)
            text = audit.read_text()
            self.assertNotIn("JACOB PFEIFF", text.upper())
            self.assertNotIn("Jacob Pfeiff", text)
            self.assertNotIn("jan.pdf", text)

    def test_formula_like_description_is_neutralized(self) -> None:
        stmt = make_personal_stmt(
            statement_date="01/31/2026",
            total_credits="0",
            total_debits="100",
            credit_count=0,
            debit_count=1,
            ending_balance="0",
        )
        stmt.transactions = [
            make_personal_tx("01/02/2026", "=1+1", "100.00", False, "0.00"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            tx_csv = Path(tmp) / "tx.csv"
            result = personal_consolidate([stmt])
            _write_transactions_csv(_filter_result_by_period(result, None, None), tx_csv)
            text = tx_csv.read_text()
            self.assertIn("'=1+1", text)


class TestLeapDayParsing(unittest.TestCase):
    def test_leap_day_on_february_statement(self) -> None:
        self.assertEqual(_card_post_date_for("Feb 29", 2024, 2), "02/29/2024")

    def test_december_transaction_on_january_statement(self) -> None:
        self.assertEqual(_card_post_date_for("Dec 30", 2026, 1), "12/30/2025")


class TestDevDependencies(unittest.TestCase):
    def test_pyproject_includes_types_pyyaml_and_tomli_w(self) -> None:
        path = Path(__file__).resolve().parents[1] / "pyproject.toml"
        text = path.read_text()
        self.assertIn('"types-PyYAML"', text)
        self.assertIn('"tomli-w>=1.0"', text)


if __name__ == "__main__":
    unittest.main()
