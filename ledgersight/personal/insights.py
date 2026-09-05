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
    ledger_covers_month,
    month_fully_covered,
)
from ledgersight.personal.models import Statement, Transaction

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
    "XX",  # masked-card memo remnant ("XX0844" with digits stripped)
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
# order / confirmation codes such as "BM8QT1VH" or "FK5B38DL": letters and digits mixed
_ORDER_CODE = re.compile(r"\b(?=[A-Z0-9]*\d)(?=[A-Z0-9]*[A-Z])[A-Z0-9]{6,}\b")


def display_merchant(name: str) -> str:
    """Human-facing payee name: drops a leading memo date such as ``3/17/26``
    and mixed letter/digit order codes that differ on every charge."""
    cleaned = _LEADING_MEMO_DATE.sub("", name)
    cleaned = _ORDER_CODE.sub("", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" ,-")
    return cleaned or name


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
    """Per calendar month: income split, spending, observed change, balances.

    Money fields (Decimal): ``payroll`` (Payroll-category credits), ``deposits``
    (other income-category credits, source not confirmed), ``income``
    (payroll + deposits, i.e. earned income excluding refunds), ``refunds``,
    ``spending`` (unified ``is_spending`` rule), ``total_credits`` /
    ``total_debits`` (every credit/debit that month), ``cash`` and ``debt``
    (month-end balances summed over the cash / card accounts that have a
    statement covering the month).

    Coverage fields: ``balances`` maps each account label to its month-end
    balance or ``None`` when no statement covers the month; ``missing`` lists
    the labels without coverage; ``cash_accounts`` / ``cash_accounts_total``
    count covered vs. all cash accounts (ints).
    """
    non_card_count = sum(1 for led in ledgers if led.account_type != "Credit Card")
    series: dict[tuple[int, int], dict[str, Any]] = defaultdict(
        lambda: {
            "payroll": Decimal("0"),
            "deposits": Decimal("0"),
            "income": Decimal("0"),
            "refunds": Decimal("0"),
            "spending": Decimal("0"),
            "total_credits": Decimal("0"),
            "total_debits": Decimal("0"),
            "cash": Decimal("0"),
            "debt": Decimal("0"),
            "balances": {},
            "missing": [],
            "cash_accounts": 0,
            "cash_accounts_total": non_card_count,
        }
    )
    for ledger in ledgers:
        for month in ledger.months:
            entry = series[(month.year, month.month)]
            for tx in month.transactions:
                if tx.is_credit:
                    entry["total_credits"] += tx.amount
                else:
                    entry["total_debits"] += tx.amount
                if tx.is_credit and tx.category in INCOME_CATEGORIES:
                    if is_refund(tx):
                        entry["refunds"] += tx.amount
                    elif tx.category == "Payroll":
                        entry["payroll"] += tx.amount
                        entry["income"] += tx.amount
                    else:
                        entry["deposits"] += tx.amount
                        entry["income"] += tx.amount
                elif is_spending(tx):
                    entry["spending"] += tx.amount
    # Balances are filled for every month in the series so a month with no
    # activity in one account still shows that account's carried balance -
    # unless no statement covers the month, in which case it is unknown.
    for (year, mon), entry in series.items():
        for ledger in ledgers:
            is_card = ledger.account_type == "Credit Card"
            if ledger_covers_month(ledger, year, mon):
                bal = balance_asof(ledger, _last_day(year, mon))
                entry["balances"][ledger.label] = bal
                if is_card:
                    entry["debt"] += bal
                else:
                    entry["cash"] += bal
                    entry["cash_accounts"] += 1
            else:
                entry["balances"][ledger.label] = None
                entry["missing"].append(ledger.label)
    return dict(sorted(series.items()))


def _liquid_ledgers(result: ConsolidatedResult) -> list[AccountLedger]:
    return [led for led in result.ledgers if led.account_type != "Credit Card"]


def dashboard_totals(result: ConsolidatedResult, as_of: datetime.date) -> dict[str, Any]:
    """Headline dashboard figures, split so income/savings are unambiguous.

    The report-wide definitions live here so every page agrees:

    - ``earned_income`` excludes refunds and transfers-in; it is split into
      ``payroll`` (identified employer deposits) and ``deposits`` (other
      income-category credits whose source is not confirmed).
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
    payroll = sum(
        (t.amount for t in all_tx if t.is_credit and t.category == "Payroll" and not is_refund(t)), Decimal("0")
    )
    return {
        "earned_income": earned,
        "income": earned,
        "payroll": payroll,
        "deposits": earned - payroll,
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
    the estimate; the median gap between distinct pay dates and the median
    daily total in that window produce the 'next expected' date and amount.
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
        # Two deposits on one day are one payday: collapse them before
        # measuring the cadence, or a double payment reads as a 0-day gap and
        # drags a weekly schedule down to "every 6 days".
        per_day: dict[datetime.date, Decimal] = defaultdict(Decimal)
        for t in txs:
            per_day[_to_date(t.post_date)] += t.amount
        if len(per_day) < min_occurrences:
            continue
        dates = sorted(per_day)
        gaps = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
        cadence = int(median(gaps)) if gaps else 7
        if cadence < 3 or cadence > 35:
            continue
        median_amount = Decimal(median(per_day.values())).quantize(Decimal("0.01"), rounding="ROUND_HALF_UP")
        next_event = dates[-1] + datetime.timedelta(days=cadence)
        if next_event <= as_of:
            continue
        estimates.append(
            EstimatedIncome(
                payee=payee,
                occurrences=len(dates),
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


@dataclass
class OutflowBaseline:
    """Average monthly cash outflow for one bucket, from complete months only.

    ``scheduled`` is the monthly equivalent of the active recurring bills that
    fall in this bucket; ``allowance`` is what remains to be budgeted for
    charges that were not individually forecast.
    """

    label: str
    monthly: Decimal
    scheduled: Decimal = Decimal("0")
    months: int = 0

    @property
    def allowance(self) -> Decimal:
        return max(self.monthly - self.scheduled, Decimal("0"))


LOAN_BUCKET = "Loan/card payments"
OUTSIDE_TRANSFER_BUCKET = "Transfers to outside accounts"


def complete_months(ledger: AccountLedger) -> list[tuple[int, int]]:
    """Calendar months whose every day is covered by the ledger's statements."""
    return [(m.year, m.month) for m in ledger.months if month_fully_covered(ledger, m.year, m.month)]


def cash_outflow_baselines(
    result: ConsolidatedResult,
    repeating: Iterable[RepeatingPayment] = (),
) -> list[OutflowBaseline]:
    """Monthly cash outflows by bucket for the cash (non-card) accounts.

    Only months fully covered by statements count, so a partial first or
    last month cannot drag the average down. Buckets are the spending
    categories, plus loan/card payments (they leave the cash accounts even
    when they pay down a covered card) and transfers to accounts outside
    these statements. Internal transfers between covered accounts are not
    outflows and are excluded. Active recurring bills are subtracted per
    bucket so the remaining allowance never double counts them.
    """
    matched_keys = {(m.from_account, m.date, str(m.amount), m.description) for m in result.movements if m.matched}
    totals: dict[str, Decimal] = defaultdict(Decimal)
    months_used: dict[str, set[tuple[int, int]]] = defaultdict(set)
    for ledger in result.ledgers:
        if ledger.account_type == "Credit Card":
            continue
        full = set(complete_months(ledger))
        if not full:
            continue
        for month in ledger.months:
            if (month.year, month.month) not in full:
                continue
            for tx in month.transactions:
                if tx.is_credit:
                    continue
                if tx.category in DEBT_CATEGORIES:
                    bucket = LOAN_BUCKET
                elif tx.category in MOVEMENT_CATEGORIES:
                    if (ledger.account_number, tx.post_date, str(tx.amount), tx.description) in matched_keys:
                        continue
                    bucket = OUTSIDE_TRANSFER_BUCKET
                else:
                    bucket = tx.category or "Other"
                totals[bucket] += tx.amount
                months_used[bucket] |= full
    scheduled: dict[str, Decimal] = defaultdict(Decimal)
    for r in repeating:
        if r.income or not r.active or r.cadence_days <= 0:
            continue
        bucket = LOAN_BUCKET if r.category in DEBT_CATEGORIES else (r.category or "Other")
        scheduled[bucket] += r.typical_amount * Decimal("30.44") / Decimal(r.cadence_days)
    baselines = [
        OutflowBaseline(
            label=bucket,
            monthly=(amount / Decimal(len(months_used[bucket]))).quantize(Decimal("0.01"), rounding="ROUND_HALF_UP"),
            scheduled=scheduled.get(bucket, Decimal("0")).quantize(Decimal("0.01"), rounding="ROUND_HALF_UP"),
            months=len(months_used[bucket]),
        )
        for bucket, amount in totals.items()
    ]
    baselines.sort(key=lambda b: b.monthly, reverse=True)
    return baselines


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


# ---------------------------------------------------------------------------
# Subscription review, check register, bill & debt calendar
# ---------------------------------------------------------------------------


@dataclass
class SubscriptionRow:
    payee: str
    total: Decimal
    charges: int
    first_date: datetime.date
    last_date: datetime.date
    recent_monthly: Decimal  # average per month over the trailing 90 days
    next_expected: datetime.date | None
    cadence_days: int | None
    accounts: str


def subscription_review(
    result: ConsolidatedResult,
    repeating: Iterable[RepeatingPayment],
    as_of: datetime.date,
) -> list[SubscriptionRow]:
    """One row per subscription service: what it costs and when it recurs.

    Groups every Subscriptions-category debit by canonical merchant across
    all accounts (debit card and credit cards alike). ``next_expected`` comes
    from the recurring detector when the service is an active recurring
    bill, otherwise from the median gap when at least two charges exist.
    """
    groups: dict[str, list[tuple[Transaction, str]]] = defaultdict(list)
    for ledger in result.ledgers:
        for tx in ledger.transactions:
            if tx.is_credit or tx.category != "Subscriptions":
                continue
            name = display_merchant(normalize_merchant(tx.description))
            groups[canon_merchant(name)].append((tx, ledger.label))
    recurring_by_canon = {
        canon_merchant(r.payee): r for r in repeating if not r.income and r.category == "Subscriptions"
    }
    window_start = as_of - datetime.timedelta(days=90)
    rows: list[SubscriptionRow] = []
    for canon, entries in groups.items():
        txs = [t for t, _ in entries]
        dates = sorted(_to_date(t.post_date) for t in txs)
        names = [display_merchant(normalize_merchant(t.description)) for t in txs]
        recent = [t.amount for t in txs if window_start < _to_date(t.post_date) <= as_of]
        recent_monthly = (sum(recent, Decimal("0")) / Decimal("3")).quantize(Decimal("0.01"), rounding="ROUND_HALF_UP")
        rec = recurring_by_canon.get(canon)
        cadence: int | None = None
        nxt: datetime.date | None = None
        if rec is not None and rec.active:
            cadence = rec.cadence_days
            nxt = rec.last_date + datetime.timedelta(days=rec.cadence_days)
        elif len(dates) >= 2:
            gaps = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
            med = int(median(gaps))
            if 4 <= med <= 95 and (as_of - dates[-1]).days <= 2 * med:
                cadence = med
                nxt = dates[-1] + datetime.timedelta(days=med)
        while nxt is not None and cadence and nxt <= as_of:
            nxt += datetime.timedelta(days=cadence)
        rows.append(
            SubscriptionRow(
                payee=best_display_name(names) or canon,
                total=sum((t.amount for t in txs), Decimal("0")),
                charges=len(txs),
                first_date=dates[0],
                last_date=dates[-1],
                recent_monthly=recent_monthly,
                next_expected=nxt,
                cadence_days=cadence,
                accounts=", ".join(sorted({lbl[-4:] for _, lbl in entries})),
            )
        )
    rows.sort(key=lambda r: r.total, reverse=True)
    return rows


_CHECK_RE = re.compile(r"\bCHECK\s*#?\s*(\d{3,6})\b", re.IGNORECASE)


def load_check_annotations(path: str | Path | None) -> dict[int, dict[str, str]]:
    """Read checks.yaml: ``{5130: {payee: Landlord, category: Rent, note: ...}}``.

    Returns an empty mapping when the file is absent or malformed.
    """
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        return {}
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return {}
    if not isinstance(raw, dict):
        return {}
    source = raw.get("checks", raw)
    if not isinstance(source, dict):
        return {}
    out: dict[int, dict[str, str]] = {}
    for number, value in source.items():
        try:
            key = int(str(number).lstrip("#"))
        except ValueError:
            continue
        if isinstance(value, str):
            out[key] = {"payee": value}
        elif isinstance(value, dict):
            out[key] = {k: str(v) for k, v in value.items() if v is not None}
    return out


def apply_check_annotations(statements: Iterable[Statement], annotations: dict[int, dict[str, str]]) -> int:
    """Attach payee/purpose to check transactions; returns how many were annotated.

    The check number stays in the description as the payment method
    ("CHECK # 5130 - Landlord"); the category becomes the purpose when the
    annotation names one, so spending by category reflects what the check
    paid for instead of how it was paid.
    """
    if not annotations:
        return 0
    count = 0
    for stmt in statements:
        for tx in stmt.transactions:
            m = _CHECK_RE.search(tx.description)
            if not m:
                continue
            ann = annotations.get(int(m.group(1)))
            if not ann:
                continue
            payee = ann.get("payee", "")
            if payee and payee not in tx.description:
                tx.description = f"{tx.description} \u2013 {payee}"
            if ann.get("category"):
                tx.category = ann["category"]
            count += 1
    return count


@dataclass
class CheckRow:
    number: int
    date: str
    amount: Decimal
    payee: str
    purpose: str
    account: str
    source: str


def check_register(result: ConsolidatedResult, annotations: dict[int, dict[str, str]]) -> list[CheckRow]:
    """Every cleared check with its payee/purpose when annotated."""
    rows: list[CheckRow] = []
    seen: set[tuple[str, int]] = set()
    for ledger in result.ledgers:
        for tx in ledger.transactions:
            m = _CHECK_RE.search(tx.description)
            if not m or tx.is_credit:
                continue
            number = int(m.group(1))
            if (ledger.account_number, number) in seen:
                continue
            seen.add((ledger.account_number, number))
            ann = annotations.get(number, {})
            purpose = ann.get("category") or (tx.category if tx.category != "Checks" else "")
            stmt = next((s for s in ledger.statements if any(t is tx for t in s.transactions)), None)
            name = Path(stmt.file_path).name if stmt and stmt.file_path else ""
            where = f"p.{tx.source_page} row {tx.source_row}" if tx.source_page else ""
            rows.append(
                CheckRow(
                    number=number,
                    date=tx.post_date,
                    amount=tx.amount,
                    payee=ann.get("payee", ""),
                    purpose=purpose,
                    account=ledger.label,
                    source=" ".join(part for part in (name, where) if part),
                )
            )
    rows.sort(key=lambda r: (_to_date(r.date), r.number))
    return rows


@dataclass
class DebtRow:
    label: str
    as_of: str
    balance: Decimal
    credit_limit: Decimal
    apr: Decimal
    minimum_payment: Decimal
    due_date: str
    autopay: bool
    last_payment_date: str
    last_payment_amount: Decimal

    @property
    def utilization(self) -> Decimal | None:
        if self.credit_limit <= 0:
            return None
        return (self.balance / self.credit_limit * Decimal("100")).quantize(Decimal("0.1"))


def debt_log(result: ConsolidatedResult) -> list[DebtRow]:
    """Per card: balance, limit, APR, minimum due and autopay evidence."""
    rows: list[DebtRow] = []
    for ledger in result.ledgers:
        if ledger.account_type != "Credit Card":
            continue
        stmt = ledger.last_statement
        if stmt is None:
            continue
        payments = [t for t in ledger.transactions if t.is_credit and t.category in DEBT_CATEGORIES]
        payments.sort(key=lambda t: _to_date(t.post_date))
        last = payments[-1] if payments else None
        autopay = any(
            m.matched and m.basis == "autopay" and m.to_account == ledger.account_number for m in result.movements
        ) or any("AUTOPAY" in t.description.upper() for t in payments[-3:])
        rows.append(
            DebtRow(
                label=ledger.label,
                as_of=stmt.statement_date,
                balance=stmt.ending_balance,
                credit_limit=stmt.credit_limit,
                apr=stmt.apr_purchases,
                minimum_payment=stmt.minimum_payment,
                due_date=stmt.payment_due_date,
                autopay=autopay,
                last_payment_date=last.post_date if last else "",
                last_payment_amount=last.amount if last else Decimal("0"),
            )
        )
    return rows


@dataclass
class CalendarItem:
    when: datetime.date
    item: str
    amount: Decimal
    kind: str  # "Bill", "Card minimum", "Payday (estimated)"
    status: str
    basis: str


def bill_calendar(
    result: ConsolidatedResult,
    repeating: Iterable[RepeatingPayment],
    estimated_income: Iterable[EstimatedIncome],
    as_of: datetime.date,
    days: int = 60,
) -> list[CalendarItem]:
    """Dated list of what is expected to leave (and arrive) over ``days``."""
    horizon = as_of + datetime.timedelta(days=days)
    items: list[CalendarItem] = []
    for r in repeating:
        if not r.active or r.cadence_days <= 0:
            continue
        nxt = r.last_date + datetime.timedelta(days=r.cadence_days)
        while nxt <= as_of:
            nxt += datetime.timedelta(days=r.cadence_days)
        while nxt <= horizon:
            if r.income:
                items.append(CalendarItem(nxt, r.payee, r.typical_amount, "Payday", "Recurring", "detected schedule"))
            else:
                status = "Auto-charge (card/ACH)" if r.category != "Checks" else "Manual"
                items.append(
                    CalendarItem(nxt, r.payee, r.typical_amount, "Bill", status, _cadence_words(r.cadence_days))
                )
            nxt += datetime.timedelta(days=r.cadence_days)
    for inc in estimated_income:
        nxt = inc.next_event
        while nxt <= horizon:
            items.append(
                CalendarItem(
                    nxt,
                    f"{display_merchant(inc.payee)} \u2013 estimated",
                    inc.median_amount,
                    "Payday (estimated)",
                    "Estimated",
                    f"median of last {inc.occurrences} pay dates",
                )
            )
            nxt += datetime.timedelta(days=inc.median_cadence_days)
    for debt in debt_log(result):
        if not debt.due_date:
            continue
        due = _to_date(debt.due_date)
        if as_of < due <= horizon:
            items.append(
                CalendarItem(
                    due,
                    f"{debt.label} \u2013 minimum payment",
                    debt.minimum_payment,
                    "Card minimum",
                    "Autopay detected" if debt.autopay else "Manual \u2013 schedule",
                    f"statement dated {debt.as_of}",
                )
            )
    items.sort(key=lambda i: (i.when, i.kind, i.item))
    return items


def _cadence_words(days: int) -> str:
    if 6 <= days <= 8:
        return "weekly"
    if 13 <= days <= 15:
        return "every two weeks"
    if 27 <= days <= 32:
        return "monthly"
    if 85 <= days <= 95:
        return "quarterly"
    return f"every {days} days"
