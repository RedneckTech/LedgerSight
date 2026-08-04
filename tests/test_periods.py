import unittest
from datetime import date

from ledgersight.business.periods import (
    _fiscal_quarter_months,
    fiscal_quarter_range,
    get_period_months,
    get_quarter_periods,
)


class TestFinancialPeriods(unittest.TestCase):
    def test_monthly_periods(self):
        periods = get_period_months(2023, 1)
        self.assertEqual(len(periods), 12)
        self.assertEqual(periods[0].label, "January 2023")
        self.assertEqual(periods[-1].label, "December 2023")

    def test_quarterly_periods(self):
        periods = get_quarter_periods(2023)
        self.assertEqual(len(periods), 4)
        self.assertTrue(periods[0].label.startswith("Q1"))
        self.assertTrue(periods[3].label.startswith("Q4"))

    def test_calendar_year_quarter_range(self):
        start, end = fiscal_quarter_range(2025, 2, fiscal_year_start=1)
        self.assertEqual(start, date(2025, 4, 1))
        self.assertEqual(end, date(2025, 6, 30))

    def test_october_fiscal_q2_uses_jan_to_mar_next_year(self):
        start, end = fiscal_quarter_range(2025, 2, fiscal_year_start=10)
        self.assertEqual(start, date(2026, 1, 1))
        self.assertEqual(end, date(2026, 3, 31))

    def test_october_fiscal_q1_same_calendar_year(self):
        start, end = fiscal_quarter_range(2025, 1, fiscal_year_start=10)
        self.assertEqual(start, date(2025, 10, 1))
        self.assertEqual(end, date(2025, 12, 31))

    def test_july_fiscal_q4_crosses_year(self):
        start, end = fiscal_quarter_range(2025, 4, fiscal_year_start=7)
        self.assertEqual(start, date(2026, 4, 1))
        self.assertEqual(end, date(2026, 6, 30))

    def test_april_fiscal_q1(self):
        start, end = fiscal_quarter_range(2025, 1, fiscal_year_start=4)
        self.assertEqual(start, date(2025, 4, 1))
        self.assertEqual(end, date(2025, 6, 30))

    def test_jan_fiscal_q4_previous_year(self):
        start, end = fiscal_quarter_range(2025, 4, fiscal_year_start=2)
        self.assertEqual(start, date(2025, 11, 1))
        self.assertEqual(end, date(2026, 1, 31))
