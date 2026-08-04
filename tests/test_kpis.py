import unittest
from decimal import Decimal

from ledgersight.business.kpis import calculate_kpis
from ledgersight.business.pl import build_pl
from tests.conftest import make_stmt, make_tx


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
        pl = build_pl([tx for s in stmts for tx in s.transactions])
        kpis = calculate_kpis(pl, 1, stmts)
        self.assertEqual(kpis.total_revenue, Decimal("500.00"))
        self.assertEqual(kpis.net_income, Decimal("200.00"))
        self.assertEqual(kpis.avg_monthly_revenue, Decimal("500.00"))
        self.assertNotEqual(kpis.gross_margin, "N/A")
