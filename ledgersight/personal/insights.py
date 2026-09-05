"""Financial insights: dashboard metrics, budget tracking, recurring
payments, and near-term cash-flow forecasts.

Everything here is pure data math over the consolidated personal models so
it can be tested independently of the PDF rendering layer.
"""

from __future__ import annotations

import datetime
import re
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
    DEBT_CATEGORIES,
    MOVEMENT_CATEGORIES,
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

# A single definition of "spending": every debit except internal transfers
# (money moved between the user's own accounts) and loan/card payments.
# Everything else - including items that remain uncategorized ("Other") -
# counts as spending, exactly matching the cover-page "Where Money Went"
# figure, so no two pages can disagree.
NON_SPENDING_CATEGORIES = MOVEMENT_CATEGORIES | DEBT_CATEGORIES

_REFUND_MARKERS = ("REFUND", "REFUNDED", "REVERSAL")

# Tokens that describe the payment rail rather than the merchant, dropped
# when grouping payees so "PAYPAL INST XFER HIDIVE" (March) and
# "PAYPAL PURCHASE HIDIVE" (August) collapse into one HIDIVE merchant.
_MERCHANT_NOISE_TOKENS = {
    "PAYPAL",
    "INST",
    "XFER",
    "TRANSFER",
    "PURCHASE",
    "WEB",
    "ACH",
    "ZELLE",
    "POS",
    "DEBIT",
    "CARD",
    "WITHDRAWAL",
    "BANK",
}


def is_spending(tx: Transaction) -> bool:
    """True when a debit is ordinary spending (not a transfer or loan payment)."""
    return not tx.is_credit and tx.category not in NON_SPENDING_CATEGORIES


def is_refund(tx: Transaction) -> bool:
    """True for credits that return money (e.g. a returned security deposit)."""
    return tx.is_credit and any(m in tx.description.upper() for m in _REFUND_MARKERS)


def canon_merchant(name: str) -> str:
    """Merchant identity with payment-rail tokens removed.

    Used for grouping so merchant-name changes (a subscript moved from
    "INST XFER" to "PURCHASE" without changing the underlying service) are
    treated as one payee. Only alphabetic tokens are kept so statement
    reference numbers do not split one merchant into many groups.
    """
    tokens = sorted(
        {t for t in re.split(r"[\W_]+", name.upper()) if t and t.isalpha() and t not in _MERCHANT_NOISE_TOKENS}
    )
    return " ".join(tokens)


_LEADING_MEMO_DATE = re.compile(r"^\d{1,2}/\d{1,2}/\d{2,4}\s+")


def display_merchant(name: str) -> str:
    """Human-facing payee name: drops a leading memo date such as ``3/17/26``."""
    return _LEADING_MEMO_DATE.sub("", name).strip() or name


def best_display_name(names: Iterable[str]) -> str:
    """Pick the cleanest variant of a merchant name from a group.

    Fewest digits wins (reference numbers and memo dates lose to the plain
    merchant string), then the longer, more descriptive name.
    """
    candidates = [n for n in names if n]
    if not candidates:
        return ""
    return min(candidates, key=lambda n: (sum(c.isdigit() for c in n), -len(n), n))


BUDGET_CATEGORIES = sorted(SPENDING_CATEGORIES)


def _last_day(year: int, month: int) -> datetime.date:
    if month == 12:
        return datetime.date(year + 1, 1, 1) - datetime.timedelta(days=1)
    return datetime.date(year, month + 1, 1) - datetime.timedelta(days=1)


def _months_between(start: datetime.date, end: datetime.date) -> int:
    return max(1, (end.year - start.year) * 12 + (end.month - start.month) + 1)


def month_series(ledgers: list[AccountLedger]) -> dict[tuple[int, int], dict[str, Any]]:
    """Per calendar month: income, spending, cash balance, card debt.

    ``income`` is earned income only (income-category credits minus refunds);
    refunds are reported separately. ``spending`` uses the unified definition
    (``is_spending``). ``cash_accounts`` / ``cash_accounts_total`` let the
    renderer flag months where some accounts have no statement (the counters
    are ints, unlike the Decimal money fields).
    """
    non_card_count = sum(1 for led in ledgers if led.account_type != "Credit Card")
    series: dict[tuple[int, int], dict[str, Any]] = defaultdict(
        lambda: {
            "income": Decimal("0"),
            "refunds": Decimal("0"),
            "spending": Decimal("0"),
            "cash": Decimal("0"),
            "debt": Decimal("0"),
            "cash_accounts": 0,
            "cash_accounts_total": non_card_count,
        }
    )
    for ledger in ledgers:
        is_card = ledger.account_type == "Credit Card"
        for month in ledger.months:
            entry = series[(month.year, month.month)]
            for tx in month.transactions:
                if tx.is_credit and tx.category in INCOME_CATEGORIES:
                    if is_refund(tx):
                        entry["refunds"] += tx.amount
                    else:
                        entry["income"] += tx.amount
                elif is_spending(tx):
                    entry["spending"] += tx.amount
            bal = balance_asof(ledger, _last_day(month.year, month.month))
            if is_card:
                entry["debt"] += bal
            else:
                entry["cash"] += bal
                entry["cash_accounts"] += 1
    return dict(sorted(series.items()))


def _liquid_ledgers(result: ConsolidatedResult) -> list[AccountLedger]:
    return [led for led in result.ledgers if led.account_type != "Credit Card"]


def dashboard_totals(result: ConsolidatedResult, as_of: datetime.date) -> dict[str, Any]:
    """Headline dashboard figures, split so income/savings are unambiguous.

    The report-wide definitions live here so every page agrees:

    - ``earned_income`` excludes refunds and transfers-in.
    - ``spending`` (unified) includes uncategorized "Other" debits.
    - ``savings`` is the observed change in cash & savings plus the reduction
      in credit-card debt over the covered period - the number that survives
      after also paying down the uncovered loans and unresolved outflows.
    """
    all_tx = result.all_transactions
    total_credits = sum((t.amount for t in all_tx if t.is_credit), Decimal("0"))
    total_debits = sum((t.amount for t in all_tx if not t.is_credit), Decimal("0"))

    income_total = sum((t.amount for t in all_tx if t.is_credit and t.category in INCOME_CATEGORIES), Decimal("0"))
    refunds = sum(
        (t.amount for t in all_tx if t.is_credit and t.category in INCOME_CATEGORIES and is_refund(t)),
        Decimal("0"),
    )
    transfers_in = sum((t.amount for t in all_tx if t.is_credit and t.category in MOVEMENT_CATEGORIES), Decimal("0"))
    other_credits = total_credits - income_total - transfers_in

    spending = sum((t.amount for t in all_tx if is_spending(t)), Decimal("0"))

    cash_accounts: list[dict[str, str]] = [
        {
            "label": led.label,
            "account_type": led.account_type,
            "as_of": led.as_of or led.last_tx_date,
            "balance": str(balance_asof(led, as_of)),
        }
        for led in _liquid_ledgers(result)
    ]
    debt_accounts: list[dict[str, str]] = [
        {
            "label": led.label,
            "account_type": "Credit Card",
            "as_of": led.as_of or led.last_tx_date,
            "balance": str(balance_asof(led, as_of)),
        }
        for led in result.ledgers
        if led.account_type == "Credit Card"
    ]
    cash = sum((Decimal(a["balance"]) for a in cash_accounts), Decimal("0"))
    debt = sum((Decimal(a["balance"]) for a in debt_accounts), Decimal("0"))
    earned = income_total - refunds
    return {
        "earned_income": earned,
        "income": earned,
        "income_credits": income_total,
        "refunds": refunds,
        "transfers_in": transfers_in,
        "other_credits": other_credits,
        "total_credits": total_credits,
        "total_debits": total_debits,
        "spending": spending,
        "cash": cash,
        "debt": debt,
        "cash_accounts": cash_accounts,
        "debt_accounts": debt_accounts,
    }


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
    active: bool = True
    needs_confirm: bool = False

    @property
    def amount_range(self) -> str:
        if self.amount_min == self.amount_max:
            return f"{self.typical_amount:.2f}"
        return f"{self.amount_min:.2f}\u2013{self.amount_max:.2f}"


def detect_repeating_payments(
    transactions: Iterable[Transaction],
    as_of: datetime.date | None = None,
    min_occurrences: int = 3,
) -> list[RepeatingPayment]:
    """Find payments and income that recur on a regular cadence.

    Transactions are grouped by canonical merchant, so a merchant that
    changed its payment rail mid-year ("PAYPAL INST XFER HIDIVE" ->
    "PAYPAL PURCHASE HIDIVE") stays one payee. Eligible credits are income
    categories (payroll/deposits); eligible debits are everything except
    internal transfers. A group counts as recurring when it shows at least
    ``min_occurrences`` entries on a consistent interval (all gaps within 25%
    of the median, cadence 4-92 days) with a stable amount (no more than 10%,
    or $2, variation).

    ``active`` marks whether the pattern is still current relative to
    ``as_of`` (last occurrence within twice the cadence, or 60 days,
    whichever is larger); stale groups are reported but excluded from the
    forecast. ``needs_confirm`` flags long cadences that are worth a manual
    look.
    """
    groups: dict[str, list[Transaction]] = defaultdict(list)
    for tx in transactions:
        if tx.is_credit:
            if tx.category not in INCOME_CATEGORIES:
                continue
        elif tx.category in BILL_EXCLUDED_CATEGORIES:
            continue
        groups[canon_merchant(normalize_merchant(tx.description))].append(tx)

    found: list[RepeatingPayment] = []
    for _key, txs in groups.items():
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
        names = {display_merchant(normalize_merchant(t.description)) for t in txs}
        active = True
        if as_of is not None:
            active = (as_of - dates[-1]).days <= max(med * 2, 60)
        found.append(
            RepeatingPayment(
                payee=best_display_name(names) or _key,
                category=first.category or ("Income" if first.is_credit else ""),
                income=first.is_credit,
                cadence_days=med,
                amount_min=lo,
                amount_max=hi,
                typical_amount=typical,
                start_date=dates[0],
                last_date=dates[-1],
                occurrences=len(dates),
                active=active,
                needs_confirm=med >= 60,
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
    active_payees = {r.payee for r in repeating if r.active}
    horizon = as_of + datetime.timedelta(days=horizon_days)
    for r in repeating:
        if not r.active:
            continue
        nxt = r.last_date + datetime.timedelta(days=r.cadence_days)
        while nxt <= horizon:
            events.append(ForecastEvent(event_date=nxt, payee=r.payee, income=r.income, amount=r.typical_amount))
            nxt += datetime.timedelta(days=r.cadence_days)
    for inc in estimated_income:
        if inc.payee in active_payees or inc.next_event > horizon:
            continue
        # Variable income repeats on its observed cadence across the whole
        # horizon (e.g. ~13 per-mile trucking paydays in 91 days), not just a
        # single "next" arrival.
        nxt = inc.next_event
        while nxt <= horizon:
            events.append(
                ForecastEvent(
                    event_date=nxt,
                    payee=f"{inc.payee} \u2013 estimated",
                    income=True,
                    amount=inc.median_amount,
                )
            )
            nxt += datetime.timedelta(days=inc.median_cadence_days)
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


def typical_monthly_spending(
    ledgers: Iterable[AccountLedger],
    months: int,
) -> dict[str, Decimal]:
    """Average monthly debits by category, using the unified spending rule.

    Transfers and loan/card payments are excluded so the result can be
    combined with the recurring-bill forecast without double counting.
    """
    per_category: dict[str, Decimal] = defaultdict(Decimal)
    for ledger in ledgers:
        for tx in ledger.transactions:
            if is_spending(tx) and tx.category:
                per_category[tx.category] += tx.amount
    if months < 1:
        return dict(per_category)
    return {name: amount / Decimal(months) for name, amount in per_category.items()}


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
