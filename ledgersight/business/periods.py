"""Financial period helpers for P&L and projections."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from ledgersight.constants import _QUARTER_MONTHS
from ledgersight.models import FinancialPeriod, Statement


def _fiscal_quarter_months(quarter: int, fiscal_year_start: int = 1) -> list[int]:
    """Return the calendar months for a given fiscal quarter.

    fiscal_year_start is the calendar month (1-12) that begins the fiscal year.
    """
    fy_start_0 = (fiscal_year_start - 1) % 12  # zero-based
    q_start_0 = fy_start_0 + (quarter - 1) * 3
    return [(q_start_0 + i) % 12 + 1 for i in range(3)]


def statement_fiscal_year(stmt: Statement, fy_start: int) -> int:
    """Return the fiscal year a statement belongs to."""
    return stmt.year if stmt.month >= fy_start else stmt.year - 1


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


def fiscal_quarter_range(
    fiscal_year: int,
    quarter: int,
    fiscal_year_start: int = 1,
) -> tuple[date, date]:
    """Return the (start_date, end_date) for a fiscal quarter.

    Handles fiscal years that cross calendar-year boundaries.
    For example, with fiscal_year_start=10 and fiscal_year=2025:
        Q1 = Oct 2025 – Dec 2025
        Q2 = Jan 2026 – Mar 2026
    """
    months = _fiscal_quarter_months(quarter, fiscal_year_start)
    if not months:
        raise ValueError(f"Invalid quarter: {quarter}")

    start_month = months[0]
    end_month = months[-1]

    # Determine the calendar year for the start month.
    # If the month is >= fiscal_year_start, it belongs to fiscal_year's calendar year.
    # Otherwise it belongs to the next calendar year.
    if start_month >= fiscal_year_start:
        start_year = fiscal_year
    else:
        start_year = fiscal_year + 1

    start = date(start_year, start_month, 1)

    # End: last day of the last month
    if end_month == 12:
        end = date(start_year if end_month >= start_month else start_year + 1, 12, 31)
    else:
        end_year = start_year if end_month >= start_month else start_year + 1
        next_month = date(end_year, end_month, 1) + timedelta(days=32)
        end = date(next_month.year, next_month.month, 1) - timedelta(days=1)

    return (start, end)


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
