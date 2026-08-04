import unittest
from decimal import Decimal

from ledgersight.business.pl import (
    ProfitAndLoss,
    build_monthly_pls,
    build_pl,
    build_quarterly_pls,
)
from ledgersight.categorizer import TransactionCategorizer
from ledgersight.models import CategoryRule
from tests.conftest import make_stmt, make_tx


class TestProfitAndLoss(unittest.TestCase):
    def test_empty_pl(self):
        pl = ProfitAndLoss(label="Empty")
        self.assertEqual(pl.total_revenue, Decimal("0"))
        self.assertEqual(pl.gross_profit, Decimal("0"))
        self.assertEqual(pl.net_profit, Decimal("0"))
        self.assertEqual(pl.gross_margin, "N/A")

    def test_revenue_only(self):
        pl = ProfitAndLoss()
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
        pl = build_pl(txs)
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
        pl = build_pl(txs)
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
        pl = build_pl(txs)
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
        pl = build_pl(txs)
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
        pl = build_pl(txs)
        self.assertEqual(pl.total_revenue, Decimal("1000.00"))
        self.assertEqual(pl.net_profit, Decimal("1000.00"))

    def test_loan_interest_included_in_expense(self):
        custom = [
            CategoryRule(pattern=r"\bLOAN\s+INTEREST\b",
                            category="Loan Interest",
                            tax_category="Interest Expense",
                            is_loan=True, include_in_pnl=True, priority=1),
        ]
        cat = TransactionCategorizer(custom_rules=custom)
        tx = make_tx(description="LOAN INTEREST PAYMENT", amount="50.00",
                     is_credit=False)
        cat.categorize(tx)
        self.assertTrue(tx.include_in_pnl,
                        "Loan interest should be included in P&L")
        self.assertTrue(tx.is_loan,
                        "Loan interest should be flagged as loan")


    def test_interest_income_goes_to_other_income(self):
        txs = [
            make_tx(description="REVENUE", amount="1000.00", is_credit=True,
                    business_category="Service Revenue", include_in_pnl=True),
            make_tx(description="INTEREST", amount="50.00", is_credit=True,
                    business_category="Interest Income", include_in_pnl=True),
        ]
        pl = build_pl(txs)
        self.assertEqual(pl.total_revenue, Decimal("1000.00"),
                         "Interest Income should not be in operating revenue")
        self.assertEqual(pl.total_other_income, Decimal("50.00"),
                         "Interest Income should be in other income")
        self.assertEqual(pl.net_profit, Decimal("1050.00"),
                         "Net profit should include other income")

    def test_other_income_not_counted_as_operating_revenue(self):
        txs = [
            make_tx(description="REVENUE", amount="1000.00", is_credit=True,
                    business_category="Service Revenue", include_in_pnl=True),
            make_tx(description="OTHER", amount="100.00", is_credit=True,
                    business_category="Other Income", include_in_pnl=True),
        ]
        pl = build_pl(txs)
        self.assertEqual(pl.total_revenue, Decimal("1000.00"),
                         "Other Income should not be operating revenue")
        self.assertNotIn("Other Income", pl.revenue,
                         "Other Income should not appear in revenue dict")
        self.assertIn("Other Income", pl.other_income,
                      "Other Income should appear in other_income dict")

    def test_loan_interest_goes_to_other_expense(self):
        txs = [
            make_tx(description="REVENUE", amount="1000.00", is_credit=True,
                    business_category="Service Revenue", include_in_pnl=True),
            make_tx(description="INTEREST", amount="30.00", is_credit=False,
                    business_category="Loan Interest", include_in_pnl=True),
        ]
        pl = build_pl(txs)
        self.assertEqual(pl.total_operating_expenses, Decimal("0"),
                         "Loan Interest should not be operating expense")
        self.assertEqual(pl.total_other_expense, Decimal("30.00"),
                         "Loan Interest should be in other expense")
        self.assertEqual(pl.net_profit, Decimal("970.00"),
                         "Net profit deducts other expense")

    def test_reversal_matches_same_amount_and_nearest_date(self):
        txs = [
            make_tx(post_date="01/01/2023", description="VENDOR A",
                    amount="900.00", is_credit=False,
                    business_category="Materials and Supplies", include_in_pnl=True),
            make_tx(post_date="01/02/2023", description="VENDOR A",
                    amount="150.00", is_credit=False,
                    business_category="Materials and Supplies", include_in_pnl=True),
            make_tx(post_date="01/03/2023", description="VENDOR A",
                    amount="150.00", is_credit=True,
                    business_category="Payment Reversal", include_in_pnl=False),
        ]
        pl = build_pl(txs)
        self.assertEqual(pl.direct_costs["Materials and Supplies"], Decimal("900.00"),
                         "Should only reduce the $150 debit, not the $900 one")
        self.assertEqual(pl.payment_reversals, Decimal("0"))

    def test_reversal_does_not_reuse_matched_debit(self):
        txs = [
            make_tx(post_date="01/01/2023", description="VENDOR B",
                    amount="150.00", is_credit=False,
                    business_category="Fuel", include_in_pnl=True),
            make_tx(post_date="01/02/2023", description="VENDOR B",
                    amount="150.00", is_credit=True,
                    business_category="Payment Reversal", include_in_pnl=False),
            make_tx(post_date="01/03/2023", description="VENDOR B",
                    amount="150.00", is_credit=True,
                    business_category="Payment Reversal", include_in_pnl=False),
        ]
        pl = build_pl(txs)
        self.assertEqual(pl.direct_costs["Fuel"], Decimal("0"),
                         "First $150 reversal should cancel the $150 debit")
        self.assertEqual(pl.payment_reversals, Decimal("150.00"),
                         "Second $150 reversal should go to review bucket")

    def test_reversal_no_close_match_goes_to_review_bucket(self):
        txs = [
            make_tx(post_date="01/01/2023", description="VENDOR C",
                    amount="50.00", is_credit=True,
                    business_category="Payment Reversal", include_in_pnl=False),
        ]
        pl = build_pl(txs)
        self.assertEqual(pl.payment_reversals, Decimal("50.00"),
                         "Reversal with no matching debits goes to review bucket")

    def test_reversal_does_not_match_future_debit(self):
        txs = [
            make_tx(post_date="01/25/2025", description="VENDOR D", amount="150.00",
                    is_credit=False, business_category="Materials and Supplies", include_in_pnl=True),
            make_tx(post_date="01/20/2025", description="VENDOR D", amount="150.00",
                    is_credit=True, business_category="Payment Reversal", include_in_pnl=True),
        ]
        pl = build_pl(txs)
        self.assertEqual(pl.total_direct_costs, Decimal("150.00"))
        self.assertGreater(pl.payment_reversals, Decimal("0"))

    def test_reversal_larger_than_debit(self):
        txs = [
            make_tx(post_date="01/15/2025", description="VENDOR E", amount="150.00",
                    is_credit=False, business_category="Materials and Supplies", include_in_pnl=True),
            make_tx(post_date="01/20/2025", description="VENDOR E", amount="200.00",
                    is_credit=True, business_category="Payment Reversal", include_in_pnl=True),
        ]
        pl = build_pl(txs)
        self.assertEqual(pl.total_direct_costs, Decimal("0"))
        self.assertGreater(pl.payment_reversals, Decimal("0"))

    def test_reversal_lookback_limit(self):
        txs = [
            make_tx(post_date="01/01/2025", description="VENDOR F", amount="150.00",
                    is_credit=False, business_category="Materials and Supplies", include_in_pnl=True),
            make_tx(post_date="05/01/2025", description="VENDOR F", amount="150.00",
                    is_credit=True, business_category="Payment Reversal", include_in_pnl=True),
        ]
        pl = build_pl(txs)
        self.assertEqual(pl.total_direct_costs, Decimal("150.00"))
        self.assertGreater(pl.payment_reversals, Decimal("0"))


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
        pls = build_monthly_pls(stmts)
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
        qpls = build_quarterly_pls(stmts)
        self.assertEqual(qpls[(2023, 1)].total_revenue, Decimal("1000.00"))
        self.assertEqual(qpls[(2023, 2)].total_revenue, Decimal("500.00"))
