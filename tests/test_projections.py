import unittest
from decimal import Decimal

from ledgersight.business.projections import ProjectionEngine


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
        self.engine = ProjectionEngine(self.config)

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
