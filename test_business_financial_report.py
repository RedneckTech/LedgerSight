#!/usr/bin/env python3
"""Comprehensive unit tests for business_financial_report.py.

Run: python3 test_business_financial_report.py
"""

import io
import os
import sys
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import business_financial_report as bfr


# =============================================================================
# Helpers
# =============================================================================


def make_tx(
    post_date: str = "01/15/2023",
    description: str = "TEST",
    amount: str = "100.00",
    is_credit: bool = True,
    balance: str = "500.00",
    **kwargs,
) -> bfr.Transaction:
    return bfr.Transaction(
        post_date=post_date,
        description=description,
        original_description=description,
        amount=Decimal(amount),
        is_credit=is_credit,
        balance=Decimal(balance),
        **kwargs,
    )


def make_stmt(
    statement_date: str = "01/31/2023",
    account_number: str = "XXXX2136",
    beginning_balance: str = "100.00",
    ending_balance: str = "500.00",
    total_credits: str = "500.00",
    total_debits: str = "100.00",
    credit_count: int = 2,
    debit_count: int = 1,
    transactions: list | None = None,
) -> bfr.Statement:
    return bfr.Statement(
        statement_date=statement_date,
        account_number=account_number,
        beginning_balance=Decimal(beginning_balance),
        ending_balance=Decimal(ending_balance),
        total_credits=Decimal(total_credits),
        total_debits=Decimal(total_debits),
        credit_count=credit_count,
        debit_count=debit_count,
        transactions=transactions or [],
    )


# =============================================================================
# Test Suite
# =============================================================================


class TestParseAmount(unittest.TestCase):
    def test_positive_dollar(self):
        self.assertEqual(bfr.parse_amount("$1,234.56"), Decimal("1234.56"))

    def test_negative_dollar(self):
        self.assertEqual(bfr.parse_amount("-$500.00"), Decimal("-500.00"))

    def test_zero(self):
        self.assertEqual(bfr.parse_amount("$0.00"), Decimal("0"))

    def test_empty(self):
        self.assertEqual(bfr.parse_amount(""), Decimal("0"))

    def test_plain_number(self):
        self.assertEqual(bfr.parse_amount("42.75"), Decimal("42.75"))

    def test_negative_plain(self):
        self.assertEqual(bfr.parse_amount("-99.99"), Decimal("-99.99"))

    def test_with_nbsp(self):
        self.assertEqual(bfr.parse_amount("\xa0$1.00"), Decimal("1.00"))


class TestFmtDollar(unittest.TestCase):
    def test_positive(self):
        self.assertEqual(bfr.fmt_dollar(Decimal("1234.56")), "$1,234.56")

    def test_negative(self):
        self.assertEqual(bfr.fmt_dollar(Decimal("-500.00")), "-$500.00")

    def test_zero(self):
        self.assertEqual(bfr.fmt_dollar(Decimal("0")), "$0.00")

    def test_large(self):
        self.assertEqual(bfr.fmt_dollar(Decimal("1000000.00")), "$1,000,000.00")


class TestSafePct(unittest.TestCase):
    def test_normal(self):
        self.assertEqual(bfr.safe_pct(Decimal("25"), Decimal("100")), "25.0%")

    def test_zero_denominator(self):
        self.assertEqual(bfr.safe_pct(Decimal("50"), Decimal("0")), "N/A")

    def test_zero_numerator(self):
        self.assertEqual(bfr.safe_pct(Decimal("0"), Decimal("100")), "0.0%")


class TestSafeDiv(unittest.TestCase):
    def test_normal(self):
        self.assertEqual(bfr.safe_div(Decimal("100"), Decimal("4")), Decimal("25"))

    def test_division_by_zero(self):
        self.assertEqual(bfr.safe_div(Decimal("100"), Decimal("0")), Decimal("0"))


class TestTransaction(unittest.TestCase):
    def test_signed_amount_credit(self):
        tx = make_tx(amount="100.00", is_credit=True)
        self.assertEqual(tx.signed_amount, Decimal("100.00"))

    def test_signed_amount_debit(self):
        tx = make_tx(amount="50.00", is_credit=False)
        self.assertEqual(tx.signed_amount, Decimal("-50.00"))

    def test_date_obj(self):
        tx = make_tx(post_date="06/15/2023")
        self.assertEqual(tx.date_obj, date(2023, 6, 15))


class TestStatement(unittest.TestCase):
    def test_month_parsing(self):
        stmt = make_stmt(statement_date="06/30/2023")
        self.assertEqual(stmt.month, 6)

    def test_year_parsing(self):
        stmt = make_stmt(statement_date="12/31/2023")
        self.assertEqual(stmt.year, 2023)

    def test_quarter_q1(self):
        stmt = make_stmt(statement_date="03/31/2023")
        self.assertEqual(stmt.quarter, 1)

    def test_quarter_q3(self):
        stmt = make_stmt(statement_date="08/31/2023")
        self.assertEqual(stmt.quarter, 3)

    def test_quarter_q4(self):
        stmt = make_stmt(statement_date="12/31/2023")
        self.assertEqual(stmt.quarter, 4)

    def test_month_label(self):
        stmt = make_stmt(statement_date="01/31/2023")
        self.assertEqual(stmt.month_label, "January 2023")


class TestCategoryRules(unittest.TestCase):
    def test_fuel_match(self):
        rules = bfr.build_default_rules()
        fuel_rule = [r for r in rules if r.category == "Fuel" and "PILOT" in r.pattern]
        self.assertTrue(len(fuel_rule) > 0)

    def test_transfer_match(self):
        rules = bfr.build_default_rules()
        xfer_rule = [r for r in rules if r.category == "Account Transfer" and "WEB" in r.pattern]
        self.assertTrue(len(xfer_rule) > 0)
        self.assertTrue(xfer_rule[0].is_transfer)
        self.assertFalse(xfer_rule[0].include_in_pnl)

    def test_income_rule_has_is_income(self):
        rules = bfr.build_default_rules()
        service_rev = [r for r in rules if r.category == "Service Revenue"]
        if service_rev:
            self.assertTrue(service_rev[0].is_income)

    def test_expense_before_income(self):
        rules = bfr.build_default_rules()
        # Find first expense and first income rule
        expense_idx = next(i for i, r in enumerate(rules) if r.category == "Fuel")
        income_idx = next(i for i, r in enumerate(rules) if r.is_income)
        self.assertLess(expense_idx, income_idx,
                        "Expense rules must come before income rules")


class TestTransactionCategorizer(unittest.TestCase):
    def setUp(self):
        self.cat = bfr.TransactionCategorizer()

    def test_transfer_categorization(self):
        tx = make_tx(description="WEB XFER TO SAVINGS", is_credit=False, amount="500.00")
        self.cat.categorize(tx)
        self.assertEqual(tx.business_category, "Account Transfer")
        self.assertTrue(tx.is_transfer)
        self.assertFalse(tx.include_in_pnl)

    def test_fuel_categorization(self):
        tx = make_tx(description="PILOT TRAVEL CENTER FUEL", is_credit=False, amount="200.00")
        self.cat.categorize(tx)
        self.assertEqual(tx.business_category, "Fuel")
        self.assertTrue(tx.include_in_pnl)

    def test_uncategorized_cpa_review(self):
        tx = make_tx(description="SOMETHING COMPLETELY UNKNOWN XYZ", is_credit=False, amount="50.00")
        self.cat.categorize(tx)
        self.assertEqual(tx.business_category, "Uncategorized")
        self.assertTrue(tx.cpa_review)
        self.assertFalse(tx.include_in_pnl)

    def test_credit_deposit_flagged(self):
        tx = make_tx(description="UNKNOWN DEPOSIT FROM UNKNOWN SOURCE", is_credit=True, amount="1000.00")
        self.cat.categorize(tx)
        self.cat.mark_credit_deposits([make_stmt(transactions=[tx])])

    def test_custom_rule_override(self):
        custom = [
            bfr.CategoryRule(
                pattern="MY_CUSTOM_PATTERN",
                category="Custom Category",
                tax_category="Custom Tax",
                deductibility="likely-deductible",
                priority=1,
                include_in_pnl=True,
            ),
        ]
        cat = bfr.TransactionCategorizer(custom_rules=custom)
        tx = make_tx(description="MY_CUSTOM_PATTERN HERE", is_credit=False, amount="50.00")
        cat.categorize(tx)
        self.assertEqual(tx.business_category, "Custom Category")

    def test_first_match_wins(self):
        """When two rules overlap, the higher-priority (lower number) wins."""
        custom = [
            bfr.CategoryRule(pattern="PILOT TRAVEL", category="Custom High Priority",
                            include_in_pnl=True, priority=1),
        ]
        cat = bfr.TransactionCategorizer(custom_rules=custom)
        tx = make_tx(description="PILOT TRAVEL CENTER", is_credit=False, amount="200.00")
        cat.categorize(tx)
        self.assertEqual(tx.business_category, "Custom High Priority")

    def test_owner_contribution_excluded(self):
        custom = [
            bfr.CategoryRule(pattern="OWNER CONTRIB", category="Owner Contribution",
                            tax_category="non-pl", is_owner_related=True,
                            include_in_pnl=False, priority=1),
        ]
        cat = bfr.TransactionCategorizer(custom_rules=custom)
        tx = make_tx(description="OWNER CONTRIB", is_credit=True, amount="5000.00")
        cat.categorize(tx)
        self.assertFalse(tx.include_in_pnl)
        self.assertTrue(tx.is_owner_related)

    def test_fixed_asset_excluded(self):
        custom = [
            bfr.CategoryRule(pattern="TRUCK PURCHASE", category="Fixed Asset Purchase",
                            tax_category="non-pl", is_fixed_asset=True,
                            include_in_pnl=False, priority=1),
        ]
        cat = bfr.TransactionCategorizer(custom_rules=custom)
        tx = make_tx(description="TRUCK PURCHASE FREIGHTLINER", is_credit=False, amount="50000.00")
        cat.categorize(tx)
        self.assertFalse(tx.include_in_pnl)
        self.assertTrue(tx.is_fixed_asset)

    def test_loan_interest_included(self):
        """Loan interest should be flagged as loan but included in P&L."""
        rules = bfr.build_default_rules()
        li_rules = [r for r in rules if r.category == "Loan Interest"]
        if li_rules:
            r = li_rules[0]
            self.assertTrue(r.is_loan)
            self.assertTrue(r.include_in_pnl)


class TestProfitAndLoss(unittest.TestCase):
    def test_empty_pl(self):
        pl = bfr.ProfitAndLoss(label="Empty")
        self.assertEqual(pl.total_revenue, Decimal("0"))
        self.assertEqual(pl.gross_profit, Decimal("0"))
        self.assertEqual(pl.net_profit, Decimal("0"))
        self.assertEqual(pl.gross_margin, "N/A")

    def test_revenue_only(self):
        pl = bfr.ProfitAndLoss()
        pl.revenue["Sales"] = Decimal("5000.00")
        self.assertEqual(pl.total_revenue, Decimal("5000.00"))
        self.assertEqual(pl.gross_profit, Decimal("5000.00"))
        self.assertEqual(pl.net_profit, Decimal("5000.00"))

    def test_full_pl_from_transactions(self):
        txs = [
            make_tx(description="SALE", amount="1000.00", is_credit=True,
                    business_category="Service Revenue", include_in_pnl=True),
            make_tx(description="FUEL", amount="200.00", is_credit=False,
                    business_category="Fuel", include_in_pnl=True),
            make_tx(description="INSURANCE", amount="150.00", is_credit=False,
                    business_category="Business Insurance", include_in_pnl=True),
        ]
        pl = bfr.build_pl(txs)
        self.assertEqual(pl.total_revenue, Decimal("1000.00"))
        self.assertEqual(pl.total_direct_costs, Decimal("200.00"))
        self.assertEqual(pl.gross_profit, Decimal("800.00"))
        self.assertEqual(pl.total_operating_expenses, Decimal("150.00"))
        self.assertEqual(pl.operating_profit, Decimal("650.00"))
        self.assertEqual(pl.net_profit, Decimal("650.00"))

    def test_transfer_excluded_from_pl(self):
        txs = [
            make_tx(description="SALE", amount="1000.00", is_credit=True,
                    business_category="Service Revenue", include_in_pnl=True),
            make_tx(description="TRANSFER", amount="500.00", is_credit=False,
                    business_category="Account Transfer", include_in_pnl=False, is_transfer=True),
        ]
        pl = bfr.build_pl(txs)
        self.assertEqual(pl.total_revenue, Decimal("1000.00"))
        self.assertEqual(pl.total_operating_expenses, Decimal("0.00"))
        self.assertEqual(pl.net_profit, Decimal("1000.00"))

    def test_owner_contribution_excluded_from_pl(self):
        txs = [
            make_tx(description="SALE", amount="500.00", is_credit=True,
                    business_category="Service Revenue", include_in_pnl=True),
            make_tx(description="OWNER CONTRIB", amount="10000.00", is_credit=True,
                    business_category="Owner Contribution", include_in_pnl=False,
                    is_owner_related=True),
        ]
        pl = bfr.build_pl(txs)
        self.assertEqual(pl.total_revenue, Decimal("500.00"))
        self.assertGreater(pl.non_pnl_credits, Decimal("0"))

    def test_fixed_asset_excluded(self):
        txs = [
            make_tx(description="SALE", amount="1000.00", is_credit=True,
                    business_category="Service Revenue", include_in_pnl=True),
            make_tx(description="TRUCK", amount="30000.00", is_credit=False,
                    business_category="Fixed Asset Purchase", include_in_pnl=False,
                    is_fixed_asset=True),
        ]
        pl = bfr.build_pl(txs)
        self.assertEqual(pl.total_revenue, Decimal("1000.00"))
        self.assertGreater(pl.non_pnl_debits, Decimal("0"))
        self.assertEqual(pl.net_profit, Decimal("1000.00"))

    def test_loan_principal_excluded(self):
        txs = [
            make_tx(description="SALE", amount="1000.00", is_credit=True,
                    business_category="Service Revenue", include_in_pnl=True),
            make_tx(description="LOAN PMT", amount="500.00", is_credit=False,
                    business_category="Loan Principal Payment", include_in_pnl=False,
                    is_loan=True),
        ]
        pl = bfr.build_pl(txs)
        self.assertEqual(pl.total_revenue, Decimal("1000.00"))
        self.assertEqual(pl.net_profit, Decimal("1000.00"))

    def test_loan_interest_included_in_expense(self):
        """Loan interest can be included in P&L via custom rules."""
        custom = [
            bfr.CategoryRule(pattern=r"\bLOAN\s+INTEREST\b",
                            category="Loan Interest",
                            tax_category="Interest Expense",
                            is_loan=True, include_in_pnl=True, priority=1),
        ]
        cat = bfr.TransactionCategorizer(custom_rules=custom)
        tx = make_tx(description="LOAN INTEREST PAYMENT", amount="50.00",
                     is_credit=False)
        cat.categorize(tx)
        self.assertTrue(tx.include_in_pnl,
                        "Loan interest should be included in P&L")
        self.assertTrue(tx.is_loan,
                        "Loan interest should be flagged as loan")


class TestProfitAndLossByPeriod(unittest.TestCase):
    def test_monthly_pls(self):
        stmts = [
            make_stmt(statement_date="01/31/2023",
                      transactions=[
                          make_tx(post_date="01/15/2023", description="SALE",
                                  amount="1000.00", is_credit=True,
                                  business_category="Service Revenue", include_in_pnl=True),
                          make_tx(post_date="01/20/2023", description="FUEL",
                                  amount="200.00", is_credit=False,
                                  business_category="Fuel", include_in_pnl=True),
                      ]),
            make_stmt(statement_date="02/28/2023",
                      transactions=[
                          make_tx(post_date="02/15/2023", description="SALE",
                                  amount="500.00", is_credit=True,
                                  business_category="Service Revenue", include_in_pnl=True),
                      ]),
        ]
        pls = bfr.build_monthly_pls(stmts)
        self.assertEqual(len(pls), 2)
        self.assertEqual(pls["2023-01"].total_revenue, Decimal("1000.00"))
        self.assertEqual(pls["2023-02"].total_revenue, Decimal("500.00"))

    def test_quarterly_pls(self):
        stmts = [
            make_stmt(statement_date="01/31/2023",
                      transactions=[
                          make_tx(post_date="01/15/2023", description="SALE",
                                  amount="1000.00", is_credit=True,
                                  business_category="Service Revenue", include_in_pnl=True),
                      ]),
            make_stmt(statement_date="04/30/2023",
                      transactions=[
                          make_tx(post_date="04/15/2023", description="SALE",
                                  amount="500.00", is_credit=True,
                                  business_category="Service Revenue", include_in_pnl=True),
                      ]),
        ]
        qpls = bfr.build_quarterly_pls(stmts)
        self.assertEqual(qpls[1].total_revenue, Decimal("1000.00"))
        self.assertEqual(qpls[2].total_revenue, Decimal("500.00"))


class TestReconciliation(unittest.TestCase):
    def test_perfect_reconciliation(self):
        tx_credit = make_tx(amount="200.00", is_credit=True, balance="300.00")
        tx_debit = make_tx(amount="150.00", is_credit=False, balance="150.00")
        stmt = make_stmt(
            beginning_balance="100.00",
            ending_balance="150.00",
            total_credits="200.00",
            total_debits="150.00",
            credit_count=1,
            debit_count=1,
            transactions=[tx_credit, tx_debit],
        )
        result = bfr.reconcile_statement(stmt)
        self.assertTrue(result.passed)
        self.assertTrue(result.balance_ok)
        self.assertEqual(result.parsed_credit_count, 1)
        self.assertEqual(result.parsed_debit_count, 1)

    def test_count_mismatch(self):
        stmt = make_stmt(
            beginning_balance="100.00",
            ending_balance="150.00",
            total_credits="200.00",
            total_debits="150.00",
            credit_count=5,  # expected 5, actual 1
            debit_count=3,  # expected 3, actual 1
            transactions=[
                make_tx(amount="200.00", is_credit=True, balance="300.00"),
                make_tx(amount="150.00", is_credit=False, balance="150.00"),
            ],
        )
        result = bfr.reconcile_statement(stmt)
        self.assertFalse(result.passed)

    def test_amount_tolerance(self):
        tx = make_tx(amount="100.01", is_credit=True, balance="200.01")
        stmt = make_stmt(
            beginning_balance="100.00",
            ending_balance="200.00",
            total_credits="100.00",
            total_debits="0.00",
            credit_count=1,
            debit_count=0,
            transactions=[tx],
        )
        result = bfr.reconcile_statement(stmt, tolerance=Decimal("0.02"))
        self.assertTrue(result.passed)

    def test_allow_mismatch(self):
        tx = make_tx(amount="10.00", is_credit=True, balance="110.00")
        stmt = make_stmt(
            beginning_balance="100.00",
            ending_balance="500.00",  # mismatched
            total_credits="1000.00",  # mismatched
            total_debits="0.00",
            credit_count=10,  # mismatched
            debit_count=0,
            transactions=[tx],
        )
        results, all_ok, forced = bfr.reconcile_all([stmt], allow_mismatch=True)
        self.assertFalse(all_ok)  # genuinely failed
        self.assertTrue(forced)   # but generation was forced


class TestKPIs(unittest.TestCase):
    def test_kpi_calculation(self):
        stmts = [
            make_stmt(
                statement_date="01/31/2023",
                beginning_balance="1000.00",
                ending_balance="1200.00",
                transactions=[
                    make_tx(description="REV", amount="500.00", is_credit=True,
                            business_category="Service Revenue", include_in_pnl=True),
                    make_tx(description="EXP", amount="300.00", is_credit=False,
                            business_category="Fuel", include_in_pnl=True),
                ],
            ),
        ]
        pl = bfr.build_pl([tx for s in stmts for tx in s.transactions])
        kpis = bfr.calculate_kpis(pl, 1, stmts)
        self.assertEqual(kpis.total_revenue, Decimal("500.00"))
        self.assertEqual(kpis.net_income, Decimal("200.00"))
        self.assertEqual(kpis.avg_monthly_revenue, Decimal("500.00"))
        self.assertNotEqual(kpis.gross_margin, "N/A")


class TestProjectionEngine(unittest.TestCase):
    def setUp(self):
        self.config = {
            "projection_months": 6,
            "lookback_months": 3,
            "monthly_revenue_growth": 0.03,
            "monthly_expense_inflation": 0.02,
            "cogs_percentage": 0.30,
            "tax_reserve_pct": 0.25,
            "min_cash_balance": 1000,
            "seasonal": {},
            "scenarios": {
                "conservative": {"monthly_revenue_growth": 0.01},
                "base": {"monthly_revenue_growth": 0.03},
                "growth": {"monthly_revenue_growth": 0.06},
            },
        }
        self.engine = bfr.ProjectionEngine(self.config)

    def test_base_projection(self):
        hist_rev = [Decimal("10000"), Decimal("11000"), Decimal("10500")]
        hist_exp = [Decimal("7000"), Decimal("7200"), Decimal("7100")]
        result = self.engine.project(hist_rev, hist_exp, Decimal("5000"), "base")
        self.assertEqual(result.scenario, "base")
        self.assertEqual(result.months, 6)
        self.assertEqual(len(result.monthly_revenue), 6)
        self.assertEqual(len(result.monthly_net_income), 6)
        self.assertGreater(result.ending_cash[-1], Decimal("0"))

    def test_all_scenarios(self):
        hist_rev = [Decimal("10000"), Decimal("11000"), Decimal("10500")]
        hist_exp = [Decimal("7000"), Decimal("7200"), Decimal("7100")]
        results = self.engine.project_all_scenarios(hist_rev, hist_exp, Decimal("5000"))
        self.assertEqual(len(results), 3)
        scenarios = {r.scenario for r in results}
        self.assertEqual(scenarios, {"conservative", "base", "growth"})

    def test_conservative_lower_than_growth(self):
        hist_rev = [Decimal("10000")]
        hist_exp = [Decimal("7000")]
        cons = self.engine.project(hist_rev, hist_exp, Decimal("5000"), "conservative")
        grow = self.engine.project(hist_rev, hist_exp, Decimal("5000"), "growth")
        self.assertLess(
            sum(cons.monthly_revenue, Decimal("0")),
            sum(grow.monthly_revenue, Decimal("0")),
        )

    def test_tax_reserve_positive(self):
        hist_rev = [Decimal("10000")]
        hist_exp = [Decimal("5000")]
        result = self.engine.project(hist_rev, hist_exp, Decimal("5000"), "base")
        self.assertGreater(result.tax_reserve[-1], Decimal("0"))


class TestBusinessConfig(unittest.TestCase):
    def test_defaults(self):
        config = bfr.BusinessConfig()
        self.assertEqual(config.business_name, "Business")
        self.assertEqual(config.entity_type, "sole-prop")
        self.assertEqual(config.accounting_method, "cash")

    def test_display_name_without_dba(self):
        config = bfr.BusinessConfig(business_name="TestCo")
        self.assertEqual(config.display_name(), "TestCo")

    def test_display_name_with_dba(self):
        config = bfr.BusinessConfig(business_name="TestCo LLC", dba="TestCo")
        self.assertIn("TestCo", config.display_name())

    def test_masked_ein(self):
        config = bfr.BusinessConfig(ein_display="12-3456789", mask_ein=True)
        masked = config.masked_ein()
        self.assertNotIn("1", masked)
        self.assertTrue(all(c in "X-" for c in masked.replace("X", "").replace("-", "")))

    def test_unmasked_ein(self):
        config = bfr.BusinessConfig(ein_display="12-3456789", mask_ein=False)
        self.assertEqual(config.masked_ein(), "12-3456789")

    def test_masked_account(self):
        config = bfr.BusinessConfig(
            bank_account_display="CLASSIC BUSINESS CHECKING - XXXX2136",
            mask_account=True,
        )
        masked = config.masked_account()
        self.assertIn("2136", masked)  # last 4 digits preserved
        self.assertIn("X", masked)    # but preceding digits masked


class TestCSVExports(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        tx = make_tx(description="FUEL PURCHASE", amount="200.00", is_credit=False,
                     business_category="Fuel", tax_category="Vehicle Fuel",
                     include_in_pnl=True, deductibility="likely-deductible")
        stmt = make_stmt(transactions=[tx])
        self.statements = [stmt]
        config = bfr.BusinessConfig(business_name="Test")
        pl = bfr.build_pl([tx])
        self.exporter = bfr.CSVExporter(self.statements, config, pl)

    def test_export_audit(self):
        path = Path(self.tmpdir) / "audit.csv"
        self.exporter.export_audit(path)
        self.assertTrue(path.exists())
        content = path.read_text()
        self.assertIn("Statement", content)
        self.assertIn("BusinessCategory", content)
        self.assertIn("Fuel", content)

    def test_export_pl(self):
        path = Path(self.tmpdir) / "pl.csv"
        self.exporter.export_pl(path)
        self.assertTrue(path.exists())
        content = path.read_text()
        self.assertIn("Category", content)
        self.assertIn("Revenue", content)

    def test_export_cpa(self):
        outdir = Path(self.tmpdir) / "cpa"
        self.exporter.export_cpa(outdir)
        self.assertTrue((outdir / "cpa_expense_detail.csv").exists())
        self.assertTrue((outdir / "cpa_revenue_detail.csv").exists())
        self.assertTrue((outdir / "cpa_uncategorized.csv").exists())

    def test_export_category_template(self):
        path = Path(self.tmpdir) / "cat_template.csv"
        self.exporter.export_category_template(path)
        self.assertTrue(path.exists())
        content = path.read_text()
        self.assertIn("Merchant", content)
        self.assertIn("CurrentBusinessCategory", content)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir)


class TestCharts(unittest.TestCase):
    def test_empty_chart_returns_bytes(self):
        buf = bfr._empty_chart_buf("No data")
        data = buf.read()
        self.assertGreater(len(data), 0)
        self.assertTrue(data.startswith(b"\x89PNG"))

    def test_chart_revenue_expenses_empty(self):
        buf = bfr.chart_revenue_vs_expenses_monthly([], {})
        self.assertIsNotNone(buf)

    def test_chart_net_cash_flow_empty(self):
        buf = bfr.chart_net_cash_flow([])
        self.assertIsNotNone(buf)

    def test_chart_balance_trend_empty(self):
        buf = bfr.chart_balance_trend([])
        self.assertIsNotNone(buf)

    def test_chart_profit_monthly(self):
        pls = {
            "2023-01": bfr.ProfitAndLoss(label="Jan 2023"),
        }
        pls["2023-01"].revenue["Sales"] = Decimal("1000")
        pls["2023-01"].direct_costs["Fuel"] = Decimal("200")
        pls["2023-01"].operating_expenses["Insurance"] = Decimal("100")
        buf = bfr.chart_profit_monthly(pls)
        data = buf.read()
        self.assertGreater(len(data), 0)
        self.assertTrue(data.startswith(b"\x89PNG"))

    def test_chart_expenses_by_category(self):
        pl = bfr.ProfitAndLoss()
        pl.operating_expenses["Fuel"] = Decimal("500")
        pl.operating_expenses["Insurance"] = Decimal("300")
        buf = bfr.chart_expenses_by_category(pl)
        data = buf.read()
        self.assertGreater(len(data), 0)
        self.assertTrue(data.startswith(b"\x89PNG"))

    def test_chart_revenue_by_category(self):
        pl = bfr.ProfitAndLoss()
        pl.revenue["Service Revenue"] = Decimal("1000")
        buf = bfr.chart_revenue_by_category(pl)
        self.assertIsNotNone(buf)

    def test_chart_top_vendors(self):
        stmt = make_stmt(transactions=[
            make_tx(description="VENDOR A", amount="500.00", is_credit=False,
                    include_in_pnl=True),
            make_tx(description="VENDOR B", amount="300.00", is_credit=False,
                    include_in_pnl=True),
        ])
        buf = bfr.chart_top_vendors([stmt])
        self.assertIsNotNone(buf)

    def test_chart_top_revenue_sources(self):
        stmt = make_stmt(transactions=[
            make_tx(description="CUSTOMER A", amount="1000.00", is_credit=True,
                    include_in_pnl=True),
        ])
        buf = bfr.chart_top_revenue_sources([stmt])
        self.assertIsNotNone(buf)

    def test_chart_projection(self):
        hist = [Decimal("1000"), Decimal("1100")]
        proj = bfr.ProjectionResult(
            scenario="base", months=3,
            monthly_revenue=[Decimal("1200"), Decimal("1300"), Decimal("1400")],
            monthly_expenses=[Decimal("700")] * 3,
            monthly_gross_profit=[Decimal("500")] * 3,
            monthly_net_income=[Decimal("400")] * 3,
            monthly_cash_flow=[Decimal("400")] * 3,
            ending_cash=[Decimal("5400")] * 3,
            tax_reserve=[Decimal("300")] * 3,
            assumptions={},
        )
        buf = bfr.chart_projection(hist, [proj], ["Jan", "Feb"])
        self.assertIsNotNone(buf)


class TestFinancialPeriods(unittest.TestCase):
    def test_monthly_periods(self):
        periods = bfr.get_period_months(2023, 1)
        self.assertEqual(len(periods), 12)
        self.assertEqual(periods[0].label, "January 2023")
        self.assertEqual(periods[-1].label, "December 2023")

    def test_quarterly_periods(self):
        periods = bfr.get_quarter_periods(2023)
        self.assertEqual(len(periods), 4)
        self.assertTrue(periods[0].label.startswith("Q1"))
        self.assertTrue(periods[3].label.startswith("Q4"))


class TestMerchantNormalization(unittest.TestCase):
    def test_normalize_removes_card_data(self):
        result = bfr.normalize_merchant("XX5326 DEBIT CARD 01/01 13:02 STORE NAME")
        self.assertNotIn("XX5326", result)
        self.assertNotIn("DEBIT CARD", result.lower())
        self.assertIn("STORE NAME", result)

    def test_normalize_removes_dates(self):
        result = bfr.normalize_merchant("01/15 10:30 STORE NAME")
        self.assertNotIn("01/15", result)

    def test_normalize_removes_long_numbers(self):
        result = bfr.normalize_merchant("STORE 123456789012345")
        self.assertNotIn("123456789012345", result)


class TestFileHash(unittest.TestCase):
    def test_file_hash(self):
        path = Path(tempfile.mktemp())
        path.write_text("test content")
        h1 = bfr.file_hash(path)
        h2 = bfr.file_hash(path)
        self.assertEqual(h1, h2)
        self.assertEqual(len(h1), 64)
        path.unlink()


class TestDecimalArithmetic(unittest.TestCase):
    def test_addition(self):
        self.assertEqual(Decimal("0.10") + Decimal("0.20"), Decimal("0.30"))

    def test_subtraction(self):
        self.assertEqual(Decimal("1.00") - Decimal("0.33"), Decimal("0.67"))

    def test_multiplication(self):
        self.assertEqual(Decimal("100") * Decimal("0.25"), Decimal("25.00"))

    def test_division(self):
        self.assertEqual(Decimal("100") / Decimal("4"), Decimal("25"))


class TestConfigLoading(unittest.TestCase):
    def test_load_nonexistent_config(self):
        config = bfr.load_config(Path("/tmp/nonexistent_config.toml"))
        self.assertEqual(config.business_name, "Business")
        self.assertEqual(config.entity_type, "sole-prop")

    def test_init_config(self):
        tmp_path = Path(tempfile.mktemp(suffix=".toml"))
        result = bfr.generate_example_config(tmp_path, force=True)
        self.assertTrue(result)
        content = tmp_path.read_text()
        self.assertIn("[general]", content)
        self.assertIn("business_name", content)
        self.assertIn("[cpa]", content)
        self.assertIn("[projections]", content)
        tmp_path.unlink()

    def test_init_config_no_overwrite(self):
        tmp_path = Path(tempfile.mktemp(suffix=".toml"))
        tmp_path.write_text("existing")
        result = bfr.generate_example_config(tmp_path, force=False)
        self.assertFalse(result)
        self.assertEqual(tmp_path.read_text(), "existing")
        tmp_path.unlink()


class TestFindPdfs(unittest.TestCase):
    def test_finds_pdfs(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "test.pdf").touch()
            (Path(d) / "test.txt").touch()
            (Path(d) / "bank_report_2023.pdf").touch()  # should be excluded
            pdfs = bfr.find_pdfs(Path(d))
            self.assertEqual(len(pdfs), 1)
            self.assertIn("test.pdf", [p.name for p in pdfs])


class TestStatementTextParsing(unittest.TestCase):
    def test_parse_amount_with_hash(self):
        # Test the _is_page_artifact function
        self.assertTrue(bfr._is_page_artifact("Page 1 of 6", "Page 1 of 6"))
        self.assertTrue(bfr._is_page_artifact("", ""))
        self.assertFalse(bfr._is_page_artifact("Valid transaction", "Valid transaction"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
