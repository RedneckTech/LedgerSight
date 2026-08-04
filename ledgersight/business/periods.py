"""Financial period helpers for P&L and projections."""
from __future__ import annotations
from datetime import date, datetime, timedelta

from ledgersight.constants import _QUARTER_MONTHS
from ledgersight.models import FinancialPeriod


def _fiscal_quarter_months(quarter: int, fiscal_year_start: int = 1) -> list[int]:
    """Return the calendar months for a given fiscal quarter.

    fiscal_year_start is the calendar month (1-12) that begins the fiscal year.
    """
    fy_start_0 = (fiscal_year_start - 1) % 12  # zero-based
    q_start_0 = fy_start_0 + (quarter - 1) * 3
    return [(q_start_0 + i) % 12 + 1 for i in range(3)]


def get_period_months(year: int, month: int) -> list[FinancialPeriod]:
    """Build list of monthly periods for a year."""
    periods = []
    for m in range(1, 13):
        sd = date(year, m, 1)
        if m == 12:
            ed = date(year, 12, 31)
        else:
            ed = date(year, m + 1, 1) - timedelta(days=1)
        periods.append(FinancialPeriod(
            label=datetime(year, m, 1).strftime("%B %Y"),
            start_date=sd,
            end_date=ed,
            months=1,
            year=year,
        ))
    return periods


def get_quarter_periods(year: int) -> list[FinancialPeriod]:
    """Build list of quarterly periods."""
    periods = []
    labels = ["Q1", "Q2", "Q3", "Q4"]
    for qi, (label, months) in enumerate(zip(labels, _QUARTER_MONTHS.values())):
        m_start = months[0]
        m_end = months[-1]
        sd = date(year, m_start, 1)
        if m_end == 12:
            ed = date(year, 12, 31)
        else:
            ed = date(year, m_end + 1, 1) - timedelta(days=1)
        periods.append(FinancialPeriod(
            label=f"{label} {year}",
            start_date=sd,
            end_date=ed,
            months=3,
            year=year,
        ))
    return periods
