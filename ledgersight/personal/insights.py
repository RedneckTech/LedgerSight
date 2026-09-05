"""Financial insights: dashboard metrics, budget tracking, recurring
payments, and near-term cash-flow forecasts.

Everything here is pure data math over the consolidated personal models so
it can be tested independently of the PDF rendering layer.
"""

from __future__ import annotations

import datetime
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from statistics import median
from typing import Any

import yaml

from ledgersight.categorizer import normalize_merchant
from ledgersight.personal.consolidation import (
    SPENDING_CATEGORIES,
    AccountLedger,
    ConsolidatedResult,
    _to_date,
    balance_asof,
)
from ledgersight.personal.models import Transaction

INCOME_CATEGORIES = {"Payroll", "Deposit", "Government"}
BILL_EXCLUDED_CATEGORIES = {"Transfers", "Other"}
FORECAST_HORIZON_DAYS = 91

BUDGET_CATEGORIES = sorted(SPENDING_CATEGORIES)


def _last_day(year: int, month: int) -> datetime.date:
    if month == 12:
        return datetime.date(year + 1, 1, 1) - datetime.timedelta(days=1)
    return datetime.date(year, month + 1, 1) - datetime.timedelta(days=1)


def _months_between(start: datetime.date, end: datetime.date) -> int:
    return max(1, (end.year - start.year) * 12 + (end.month - start.month) + 1)


def month_series(ledgers: list[AccountLedger]) -> dict[tuple[int, int], dict[str, Decimal]]:
    """Per calendar month: income, spending, cash balance, card debt."""
    series: dict[tuple[int, int], dict[str, Decimal]] = defaultdict(
        lambda: {
            "income": Decimal("0"),
            "spending": Decimal("0"),
            "cash": Decimal("0"),
            "debt": Decimal("0"),
        }
    )
    for ledger in ledgers:
        is_card = ledger.account_type == "Credit Card"
        for month in ledger.months:
            entry = series[(month.year, month.month)]
            for tx in month.transactions:
                if tx.is_credit and tx.category in INCOME_CATEGORIES:
                    entry["income"] += tx.amount
                elif not tx.is_credit and tx.category in SPENDING_CATEGORIES:
                    entry["spending"] += tx.amount
            bal = balance_asof(ledger, _last_day(month.year, month.month))
            if is_card:
                entry["debt"] += bal
            else:
                entry["cash"] += bal
    return dict(sorted(series.items()))


def dashboard_totals(result: ConsolidatedResult, as_of: datetime.date) -> dict[str, Decimal]:
    """Headline dashboard figures for the covered period."""
    income = sum(
        (t.amount for t in result.all_transactions if t.is_credit and t.category in INCOME_CATEGORIES), Decimal("0")
    )
    spending = sum(
        (t.amount for t in result.all_transactions if not t.is_credit and t.category in SPENDING_CATEGORIES),
        Decimal("0"),
    )
    cash = sum((balance_asof(led, as_of) for led in result.ledgers if led.account_type != "Credit Card"), Decimal("0"))
    debt = sum((balance_asof(led, as_of) for led in result.ledgers if led.account_type == "Credit Card"), Decimal("0"))
    return {"income": income, "spending": spending, "cash": cash, "debt": debt}


# ---------------------------------------------------------------------------
# Recurring payments
# ---------------------------------------------------------------------------


@dataclass
class RepeatingPayment:
    payee: str
    category: str
    income: bool
    cadence_days: int
    amount_min: Decimal
    amount_max: Decimal
    typical_amount: Decimal
    start_date: datetime.date
    last_date: datetime.date
    occurrences: int

    @property
    def amount_range(self) -> str:
        if self.amount_min == self.amount_max:
            return f"{self.typical_amount:.2f}"
        return f"{self.amount_min:.2f}\u2013{self.amount_max:.2f}"


def detect_repeating_payments(
    transactions: Iterable[Transaction],
    min_occurrences: int = 3,
) -> list[RepeatingPayment]:
    """Find payments and income that recur on a regular cadence.

    Transactions are grouped by normalized merchant. Eligible credits are
    income categories (payroll/deposits); eligible debits are everything
    except internal transfers. A group counts as recurring when it shows at
    least ``min_occurrences`` entries on a consistent interval (all gaps
    within 25% of the median, cadence 4-92 days) with a stable amount (no
    more than 10%, or $2, variation).
    """
    groups: dict[str, list[Transaction]] = defaultdict(list)
    for tx in transactions:
        if tx.is_credit:
            if tx.category not in INCOME_CATEGORIES:
                continue
        elif tx.category in BILL_EXCLUDED_CATEGORIES:
            continue
        groups[normalize_merchant(tx.description)].append(tx)

    found: list[RepeatingPayment] = []
    for payee, txs in groups.items():
        if len(txs) < min_occurrences:
            continue
        dates = sorted(_to_date(t.post_date) for t in txs)
        intervals = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
        med = int(median(intervals))
        tolerance = max(3, round(med * 0.25))
        if not all(abs(gap - med) <= tolerance for gap in intervals):
            continue
        if med < 4 or med > 92:
            continue
        amounts = [t.amount for t in txs]
        lo, hi = min(amounts), max(amounts)
        if hi - lo > max(Decimal("2.00"), lo * Decimal("0.10")):
            continue
        first = txs[0]
        typical = Decimal(sum(amounts, Decimal("0")) / len(amounts)).quantize(Decimal("0.01"), rounding="ROUND_HALF_UP")
        found.append(
            RepeatingPayment(
                payee=payee,
                category=first.category or ("Income" if first.is_credit else ""),
                income=first.is_credit,
                cadence_days=med,
                amount_min=lo,
                amount_max=hi,
                typical_amount=typical,
                start_date=dates[0],
                last_date=dates[-1],
                occurrences=len(dates),
            )
        )
    found.sort(key=lambda r: (r.last_date, r.payee))
    return found


# ---------------------------------------------------------------------------
# Near-term forecast
# ---------------------------------------------------------------------------


@dataclass
class ForecastEvent:
    event_date: datetime.date
    payee: str
    income: bool
    amount: Decimal
    projected: Decimal = Decimal("0")


@dataclass
class EstimatedIncome:
    """Variable income (e.g. per-mile trucker pay) summarized for a forecast."""

    payee: str
    occurrences: int
    median_amount: Decimal
    median_cadence_days: int
    last_date: datetime.date
    next_event: datetime.date


def estimate_income_groups(
    transactions: Iterable[Transaction],
    as_of: datetime.date,
    window_days: int = 63,
    min_occurrences: int = 3,
) -> list[EstimatedIncome]:
    """Estimate the next arrival of income that is too variable for strict
    recurring detection (pay-by-mile trucking checks, irregular deposits).

    Only the trailing ``window_days`` are analyzed so recent behavior drives
    the estimate; the median gap and median amount of the group's entries in
    that window produce a single 'next expected' date.
    """
    groups: dict[str, list[Transaction]] = defaultdict(list)
    window_start = as_of - datetime.timedelta(days=window_days)
    for tx in transactions:
        if not tx.is_credit or tx.category not in INCOME_CATEGORIES:
            continue
        d = _to_date(tx.post_date)
        if d < window_start or d > as_of:
            continue
        groups[normalize_merchant(tx.description)].append(tx)

    estimates: list[EstimatedIncome] = []
    for payee, txs in groups.items():
        if len(txs) < min_occurrences:
            continue
        dates = sorted(_to_date(t.post_date) for t in txs)
        gaps = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
        cadence = int(median(gaps)) if gaps else 7
        if cadence < 3 or cadence > 35:
            continue
        amounts = [t.amount for t in txs]
        median_amount = Decimal(median(amounts)).quantize(Decimal("0.01"), rounding="ROUND_HALF_UP")
        next_event = dates[-1] + datetime.timedelta(days=cadence)
        if next_event <= as_of:
            continue
        estimates.append(
            EstimatedIncome(
                payee=payee,
                occurrences=len(txs),
                median_amount=median_amount,
                median_cadence_days=cadence,
                last_date=dates[-1],
                next_event=next_event,
            )
        )
    estimates.sort(key=lambda e: e.next_event)
    return estimates


def build_forecast(
    repeating: Iterable[RepeatingPayment],
    starting_cash: Decimal,
    as_of: datetime.date,
    horizon_days: int = FORECAST_HORIZON_DAYS,
    estimated_income: Iterable[EstimatedIncome] = (),
) -> list[ForecastEvent]:
    """Project cash movements forward from the last observed occurrence.

    Assumes every recurring payment/income repeats on its observed cadence
    with its typical amount, and appends estimated variable-income arrivals.
    This is an estimate, not a commitment.
    """
    events: list[ForecastEvent] = []
    strict_payees = {r.payee for r in repeating}
    horizon = as_of + datetime.timedelta(days=horizon_days)
    for r in repeating:
        nxt = r.last_date + datetime.timedelta(days=r.cadence_days)
        while nxt <= horizon:
            events.append(ForecastEvent(event_date=nxt, payee=r.payee, income=r.income, amount=r.typical_amount))
            nxt += datetime.timedelta(days=r.cadence_days)
    for inc in estimated_income:
        if inc.payee in strict_payees or inc.next_event > horizon:
            continue
        events.append(
            ForecastEvent(
                event_date=inc.next_event, payee=f"{inc.payee} \u2013 estimated", income=True, amount=inc.median_amount
            )
        )
    events = [e for e in events if e.event_date > as_of]
    events.sort(key=lambda e: e.event_date)
    running = starting_cash
    for e in events:
        running += e.amount if e.income else -e.amount
        e.projected = running
    return events


# ---------------------------------------------------------------------------
# Budget vs actual
# ---------------------------------------------------------------------------


@dataclass
class Budget:
    income_monthly: Decimal
    spending_monthly: Decimal
    categories: dict[str, Decimal]

    @property
    def configured(self) -> bool:
        return self.income_monthly > 0 or self.spending_monthly > 0 or any(self.categories.values())

    @property
    def total_monthly(self) -> Decimal:
        total = sum(self.categories.values(), Decimal("0"))
        budget = self.spending_monthly if self.spending_monthly > 0 else total
        return budget if budget > 0 else total


def _as_decimal(value: Any) -> Decimal:
    try:
        result = Decimal(str(value))
    except InvalidOperation, ValueError:
        return Decimal("0")
    return result


def load_budget(path: str | Path | None) -> Budget | None:
    """Load a budget.yaml definition. Returns None when absent/invalid."""
    if not path:
        return None
    budget_path = Path(path)
    if not budget_path.exists():
        return None
    try:
        raw = yaml.safe_load(budget_path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return None
    if not isinstance(raw, dict):
        return None

    categories_raw = raw.get("categories", {})
    categories: dict[str, Decimal] = {}
    if isinstance(categories_raw, dict):
        for name, value in categories_raw.items():
            amount = _as_decimal(value)
            if amount > 0:
                categories[str(name)] = amount
    return Budget(
        income_monthly=_as_decimal(raw.get("income_monthly")),
        spending_monthly=_as_decimal(raw.get("spending_monthly")),
        categories=categories,
    )


def category_spend_totals(ledgers: Iterable[AccountLedger]) -> dict[str, Decimal]:
    """Total debits by category across the covered period."""
    totals: dict[str, Decimal] = defaultdict(Decimal)
    for ledger in ledgers:
        for tx in ledger.transactions:
            if tx.is_credit:
                continue
            totals[tx.category] += tx.amount
    return dict(totals)


def budget_category_rows(
    budget: Budget,
    spend_totals: dict[str, Decimal],
    start: datetime.date,
    end: datetime.date,
) -> list[dict[str, Any]]:
    """Per-category monthly budget vs average monthly actual."""
    months = _months_between(start, end)
    rows: list[dict[str, Any]] = []
    for name in sorted(set(BUDGET_CATEGORIES) | set(budget.categories) | set(spend_totals)):
        plan = budget.categories.get(name, Decimal("0"))
        total = spend_totals.get(name, Decimal("0"))
        if plan == 0 and total == 0:
            continue
        actual = total / Decimal(months)
        rows.append(
            {
                "category": name,
                "budget": plan,
                "total": total,
                "average": actual,
                "variance": plan - actual,
            }
        )
    rows.sort(key=lambda r: r["category"])
    return rows


def budget_month_rows(
    budget: Budget,
    series: dict[tuple[int, int], dict[str, Decimal]],
) -> list[dict[str, Any]]:
    """Overall monthly budget vs actual spending per calendar month."""
    rows: list[dict[str, Any]] = []
    plan = budget.total_monthly
    for (year, month), entry in series.items():
        actual = entry["spending"]
        if plan > 0:
            used = (actual / plan * Decimal("100")).quantize(Decimal("0.1"))
        else:
            used = None
        rows.append(
            {
                "month": f"{datetime.date(year, month, 1):%B %Y}",
                "budget": plan,
                "actual": actual,
                "used": used,
            }
        )
    return rows
