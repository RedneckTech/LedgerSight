"""Financial projection engine."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any


@dataclass
class ProjectionResult:
    """Result of a financial projection."""

    scenario: str  # conservative, base, growth
    months: int
    monthly_revenue: list[Decimal]
    monthly_expenses: list[Decimal]
    monthly_gross_profit: list[Decimal]
    monthly_net_income: list[Decimal]
    monthly_cash_flow: list[Decimal]
    ending_cash: list[Decimal]
    tax_reserve: list[Decimal]
    assumptions: dict[str, Any]


class ProjectionEngine:
    """Generate financial projections based on historical data."""

    def __init__(self, config: dict[str, Any]):
        self.config = config

    def _avg_monthly(self, values: list[Decimal], lookback: int = 6) -> Decimal:
        if not values:
            return Decimal("0")
        recent = values[-lookback:]
        return sum(recent, Decimal("0")) / len(recent)

    def project(
        self,
        historical_revenue: list[Decimal],
        historical_expenses: list[Decimal],
        starting_cash: Decimal,
        scenario_name: str = "base",
        projection_start: date | None = None,
    ) -> ProjectionResult:
        """Run a projection scenario.

        *projection_start* is the first day of the first projected month
        (defaults to the month after the current real month).
        """
        c = self.config
        months = c.get("projection_months", 12)
        lookback = c.get("lookback_months", 6)

        # Growth rates: scenario-specific overrides base config
        scenarios = c.get("scenarios", {})
        sc = scenarios.get(scenario_name, {})
        rev_growth = Decimal(str(sc.get("monthly_revenue_growth", c.get("monthly_revenue_growth", 0.02))))
        exp_inflation = Decimal(str(c.get("monthly_expense_inflation", 0.02)))
        _payroll_growth = Decimal(str(c.get("payroll_growth", 0.02)))  # noqa: F841
        cogs_pct = Decimal(str(c.get("cogs_percentage", 0.0)))
        tax_reserve_pct = Decimal(str(c.get("tax_reserve_pct", 0.25)))
        min_cash = Decimal(str(c.get("min_cash_balance", 0)))
        seasonal = c.get("seasonal", {})

        # Baseline from averages
        base_rev = self._avg_monthly(historical_revenue, lookback)
        base_exp = self._avg_monthly(historical_expenses, lookback)

        # Determine the start month for seasonal lookups
        if projection_start is None:
            projection_start = date(date.today().year, date.today().month, 1) + timedelta(days=32)
            projection_start = date(projection_start.year, projection_start.month, 1)
        start_month_num = projection_start.month
        _start_year = projection_start.year  # noqa: F841

        monthly_rev: list[Decimal] = []
        monthly_exp: list[Decimal] = []
        monthly_gp: list[Decimal] = []
        monthly_ni: list[Decimal] = []
        monthly_cf: list[Decimal] = []
        ending_cash: list[Decimal] = []
        tax_reserve: list[Decimal] = []
        min_cash_warnings: list[int] = []

        cum_tax_reserve = Decimal("0")
        current_cash = starting_cash

        for i in range(1, months + 1):
            seasonal_month = ((start_month_num + i - 2) % 12) + 1
            season_factor = Decimal(str(seasonal.get(str(seasonal_month), 1.0)))

            rev = base_rev * (1 + rev_growth) ** i * season_factor
            monthly_rev.append(rev.quantize(Decimal("0.01"), ROUND_HALF_UP))

            gross_profit = rev * (1 - cogs_pct)
            monthly_gp.append(gross_profit.quantize(Decimal("0.01"), ROUND_HALF_UP))

            exp = base_exp * (1 + exp_inflation) ** i * season_factor
            monthly_exp.append(exp.quantize(Decimal("0.01"), ROUND_HALF_UP))

            net = gross_profit - exp
            monthly_ni.append(net.quantize(Decimal("0.01"), ROUND_HALF_UP))

            cf = net
            monthly_cf.append(cf.quantize(Decimal("0.01"), ROUND_HALF_UP))
            current_cash += cf
            ending_cash.append(current_cash.quantize(Decimal("0.01"), ROUND_HALF_UP))
            if min_cash > 0 and current_cash < min_cash:
                min_cash_warnings.append(i)

            if net > 0:
                cum_tax_reserve += net * tax_reserve_pct
            tax_reserve.append(cum_tax_reserve.quantize(Decimal("0.01"), ROUND_HALF_UP))

        return ProjectionResult(
            scenario=scenario_name,
            months=months,
            monthly_revenue=monthly_rev,
            monthly_expenses=monthly_exp,
            monthly_gross_profit=monthly_gp,
            monthly_net_income=monthly_ni,
            monthly_cash_flow=monthly_cf,
            ending_cash=ending_cash,
            tax_reserve=tax_reserve,
            assumptions={
                "revenue_growth_rate": f"{float(rev_growth) * 100:.1f}%",
                "expense_inflation": f"{float(exp_inflation) * 100:.1f}%",
                "cogs_percentage": f"{float(cogs_pct) * 100:.1f}%",
                "tax_reserve_pct": f"{float(tax_reserve_pct) * 100:.1f}%",
                "lookback_months": lookback,
                "scenario": scenario_name,
                "min_cash_balance": f"${min_cash:,.2f}",
                "min_cash_warnings": min_cash_warnings,
            },
        )

    def project_all_scenarios(
        self,
        historical_revenue: list[Decimal],
        historical_expenses: list[Decimal],
        starting_cash: Decimal,
        projection_start: date | None = None,
    ) -> list[ProjectionResult]:
        """Run all three scenarios."""
        return [
            self.project(historical_revenue, historical_expenses, starting_cash, "conservative", projection_start),
            self.project(historical_revenue, historical_expenses, starting_cash, "base", projection_start),
            self.project(historical_revenue, historical_expenses, starting_cash, "growth", projection_start),
        ]

    def project_selected(
        self,
        historical_revenue: list[Decimal],
        historical_expenses: list[Decimal],
        starting_cash: Decimal,
        scenario_name: str = "base",
        projection_start: date | None = None,
    ) -> ProjectionResult:
        """Run a single named scenario (conservative, base, or growth)."""
        return self.project(historical_revenue, historical_expenses, starting_cash, scenario_name, projection_start)
