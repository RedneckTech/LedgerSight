import unittest
from decimal import Decimal

from ledgersight.business.pl import ProfitAndLoss
from ledgersight.business.projections import ProjectionResult
from ledgersight.charts import (
    _empty_chart_buf,
    chart_balance_trend,
    chart_expenses_by_category,
    chart_net_cash_flow,
    chart_profit_monthly,
    chart_projection,
    chart_revenue_by_category,
    chart_revenue_vs_expenses_monthly,
    chart_top_revenue_sources,
    chart_top_vendors,
)
from tests.conftest import make_stmt, make_tx


class TestCharts(unittest.TestCase):
    def test_empty_chart_returns_bytes(self):
        buf = _empty_chart_buf("No data")
        data = buf.read()
        self.assertGreater(len(data), 0)
        self.assertTrue(data.startswith(b"\x89PNG"))

    def test_chart_revenue_expenses_empty(self):
        buf = chart_revenue_vs_expenses_monthly([], {})
        self.assertIsNotNone(buf)

    def test_chart_net_cash_flow_empty(self):
        buf = chart_net_cash_flow([])
        self.assertIsNotNone(buf)

    def test_chart_balance_trend_empty(self):
        buf = chart_balance_trend([])
        self.assertIsNotNone(buf)

    def test_chart_profit_monthly(self):
        pls = {
            "2023-01": ProfitAndLoss(label="Jan 2023"),
        }
        pls["2023-01"].revenue["Sales"] = Decimal("1000")
        pls["2023-01"].direct_costs["Fuel"] = Decimal("200")
        pls["2023-01"].operating_expenses["Insurance"] = Decimal("100")
        buf = chart_profit_monthly(pls)
        data = buf.read()
        self.assertGreater(len(data), 0)
        self.assertTrue(data.startswith(b"\x89PNG"))

    def test_chart_expenses_by_category(self):
        pl = ProfitAndLoss()
        pl.operating_expenses["Fuel"] = Decimal("500")
        pl.operating_expenses["Insurance"] = Decimal("300")
        buf = chart_expenses_by_category(pl)
        data = buf.read()
        self.assertGreater(len(data), 0)
        self.assertTrue(data.startswith(b"\x89PNG"))

    def test_chart_revenue_by_category(self):
        pl = ProfitAndLoss()
        pl.revenue["Service Revenue"] = Decimal("1000")
        buf = chart_revenue_by_category(pl)
        self.assertIsNotNone(buf)

    def test_chart_top_vendors(self):
        stmt = make_stmt(transactions=[
            make_tx(description="VENDOR A", amount="500.00", is_credit=False,
                    include_in_pnl=True),
            make_tx(description="VENDOR B", amount="300.00", is_credit=False,
                    include_in_pnl=True),
        ])
        buf = chart_top_vendors([stmt])
        self.assertIsNotNone(buf)

    def test_chart_top_revenue_sources(self):
        stmt = make_stmt(transactions=[
            make_tx(description="CUSTOMER A", amount="1000.00", is_credit=True,
                    include_in_pnl=True),
        ])
        buf = chart_top_revenue_sources([stmt])
        self.assertIsNotNone(buf)

    def test_chart_projection(self):
        hist = [Decimal("1000"), Decimal("1100")]
        proj = ProjectionResult(
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
        buf = chart_projection(hist, [proj], ["Jan", "Feb"])
        self.assertIsNotNone(buf)
