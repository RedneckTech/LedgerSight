"""Key Performance Indicators calculation."""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal

from ledgersight.models import Statement, Transaction
from ledgersight.parsers import safe_pct

# Import ProfitAndLoss — use TYPE_CHECKING for forward reference
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from ledgersight.business.pl import ProfitAndLoss


@dataclass
class FinancialKPIs:
    """Key performance indicators from bank-statement data."""

    gross_margin: str = "N/A"
    operating_margin: str = "N/A"
    net_margin: str = "N/A"
    expense_to_revenue: str = "N/A"
    avg_monthly_revenue: Decimal = Decimal("0")
    avg_monthly_expenses: Decimal = Decimal("0")
    avg_monthly_net: Decimal = Decimal("0")
    avg_transaction_value: Decimal = Decimal("0")
    largest_income: Decimal = Decimal("0")
    largest_expense: Decimal = Decimal("0")
    min_monthly_balance: Decimal = Decimal("0")
    max_monthly_balance: Decimal = Decimal("0")
    total_revenue: Decimal = Decimal("0")
    total_expenses: Decimal = Decimal("0")
    net_income: Decimal = Decimal("0")
    cash_runway_months: str = "N/A"


def calculate_kpis(pl: ProfitAndLoss, month_count: int, statements: list[Statement]) -> FinancialKPIs:
    """Calculate KPIs from P&L and statement data."""
    kpi = FinancialKPIs()
    kpi.gross_margin = pl.gross_margin
    kpi.operating_margin = pl.operating_margin
    kpi.net_margin = pl.net_margin
    kpi.total_revenue = pl.total_revenue
    kpi.total_expenses = pl.total_direct_costs + pl.total_operating_expenses
    kpi.net_income = pl.net_profit
    kpi.expense_to_revenue = safe_pct(kpi.total_expenses, pl.total_revenue)

    if month_count > 0:
        kpi.avg_monthly_revenue = pl.total_revenue / month_count
        kpi.avg_monthly_expenses = kpi.total_expenses / month_count
        kpi.avg_monthly_net = pl.net_profit / month_count

    all_tx = [tx for stmt in statements for tx in stmt.transactions]
    if all_tx:
        amounts = [tx.amount for tx in all_tx]
        kpi.avg_transaction_value = sum(amounts, Decimal("0")) / len(amounts)
        kpi.largest_income = max(
            (tx.amount for tx in all_tx if tx.is_credit),
            default=Decimal("0"),
        )
        kpi.largest_expense = max(
            (tx.amount for tx in all_tx if not tx.is_credit),
            default=Decimal("0"),
        )

    if statements:
        all_bals: list[Decimal] = []
        for s in statements:
            all_bals.append(s.ending_balance)
            for db in s.daily_balances:
                all_bals.append(db["balance"])
        if all_bals:
            kpi.min_monthly_balance = min(all_bals)
            kpi.max_monthly_balance = max(all_bals)

    if kpi.avg_monthly_expenses > 0 and statements:
        last_bal = statements[-1].ending_balance
        if last_bal > 0:
            runway = float(last_bal / kpi.avg_monthly_expenses)
            kpi.cash_runway_months = f"{runway:.1f}"

    return kpi
