"""Consolidate parsed statements into per-account ledgers.

Multiple bank statements can overlap (e.g. First Interstate prints savings
statements that cover a several-month window), so a transaction may be
listed in more than one statement. This module deduplicates transactions
across statements, reconstructs a per-account balance timeline, validates
it against every statement's reported ending balance, buckets activity by
calendar month, and pairs internal money movement between accounts.
"""

from __future__ import annotations

import datetime
import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from ledgersight.personal.models import Statement, Transaction

MOVEMENT_CATEGORIES = {"Transfers"}
DEBT_CATEGORIES = {"Loan/Credit Payment"}
SPENDING_CATEGORIES = {
    "Auto Care",
    "Bank Fees",
    "Checks",
    "Fuel",
    "Groceries",
    "Government",
    "Insurance",
    "Rent",
    "Restaurants",
    "Shopping",
    "Subscriptions",
    "Utilities",
    "Interest",
}
UNCATEGORIZED = {"Other"}


def _to_date(value: str) -> datetime.date:
    return datetime.datetime.strptime(value, "%m/%d/%Y").date()


@dataclass
class BalanceCheck:
    """Comparison of the reconstructed balance against a statement's report."""

    statement_date: str
    expected: Decimal
    computed: Decimal

    @property
    def ok(self) -> bool:
        return self.computed == self.expected


@dataclass
class DuplicateRecord:
    """A transaction dropped because an earlier statement also listed it."""

    statement_date: str
    transaction: Transaction


@dataclass
class MonthActivity:
    """All transactions posted during one calendar month for one account."""

    year: int
    month: int
    transactions: list[Transaction] = field(default_factory=list)

    @property
    def credits(self) -> Decimal:
        return sum((t.amount for t in self.transactions if t.is_credit), Decimal("0"))

    @property
    def debits(self) -> Decimal:
        return sum((t.amount for t in self.transactions if not t.is_credit), Decimal("0"))

    @property
    def label(self) -> str:
        return f"{datetime.date(self.year, self.month, 1):%B %Y}"


@dataclass
class MovementMatch:
    """An internal transfer or card payment matched across two accounts.

    ``basis`` records how the pairing was established: ``"reference"`` (same
    six-digit transfer reference on both sides), ``"autopay"`` (card payment
    and the card's matching credit) or ``"amount+date"`` (equal and opposite
    entries within two days with no shared reference - strong candidates
    that still deserve a glance at the paper statements). Unmatched
    movements carry ``basis=""``. ``unmatched_reason`` explains what is known
    about an unmatched debit: the destination account is not covered, or the
    destination's statement for that month is missing.
    """

    from_account: str
    to_account: str
    date: str
    description: str
    amount: Decimal
    matched: bool
    basis: str = ""
    counter_date: str = ""
    counter_description: str = ""
    unmatched_reason: str = ""


@dataclass
class AccountLedger:
    """Reconstructed activity for a single account."""

    institution: str
    account_number: str
    account_type: str
    statements: list[Statement] = field(default_factory=list)
    transactions: list[Transaction] = field(default_factory=list)
    duplicates: list[DuplicateRecord] = field(default_factory=list)
    checks: list[BalanceCheck] = field(default_factory=list)
    months: list[MonthActivity] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"{self.institution} ****{self.account_number[-4:]}"

    @property
    def last_statement(self) -> Statement | None:
        return self.statements[-1] if self.statements else None

    @property
    def as_of(self) -> str:
        stmt = self.last_statement
        return stmt.statement_date if stmt else ""

    @property
    def ending_balance(self) -> Decimal:
        stmt = self.last_statement
        return stmt.ending_balance if stmt else Decimal("0")

    @property
    def first_tx_date(self) -> str:
        return self.transactions[0].post_date if self.transactions else ""

    @property
    def last_tx_date(self) -> str:
        return self.transactions[-1].post_date if self.transactions else ""


@dataclass
class ConsolidatedResult:
    """Everything needed to render a consolidated personal report."""

    ledgers: list[AccountLedger]
    movements: list[MovementMatch] = field(default_factory=list)

    @property
    def all_statements(self) -> list[Statement]:
        return [s for ledger in self.ledgers for s in ledger.statements]

    @property
    def all_transactions(self) -> list[Transaction]:
        return [t for ledger in self.ledgers for t in ledger.transactions]

    def ledger_for(self, account_number: str) -> AccountLedger | None:
        for ledger in self.ledgers:
            if ledger.account_number == account_number:
                return ledger
        return None


def _statement_content_hash(stmt: Statement) -> str:
    """Stable hash of a statement's identity-bearing content.

    Used to drop exact file duplicates and equivalent statements with
    different filenames before transaction-level deduplication.
    """
    parts = [
        stmt.institution,
        stmt.account_number,
        stmt.statement_date,
        str(stmt.beginning_balance),
        str(stmt.ending_balance),
        str(stmt.total_credits),
        str(stmt.total_debits),
        str(stmt.credit_count),
        str(stmt.debit_count),
    ]
    tx_parts = []
    for tx in sorted(stmt.transactions, key=lambda t: (t.post_date, t.source_row, str(t.amount))):
        tx_parts.append(f"{tx.post_date}|{tx.description}|{str(tx.amount)}|{tx.is_credit}|{tx.source_row}")
    payload = "\n".join(parts + tx_parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _dedupe_statements(statements: list[Statement]) -> list[Statement]:
    """Drop duplicate statement objects (exact file duplicates or equivalent PDFs).

    Preserves the first occurrence ordered by statement date then file path.
    """
    ordered = sorted(statements, key=lambda s: (_to_date(s.statement_date), s.file_path or "", s.account_number))
    seen: set[str] = set()
    unique: list[Statement] = []
    for stmt in ordered:
        h = _statement_content_hash(stmt)
        if h in seen:
            continue
        seen.add(h)
        unique.append(stmt)
    return unique


def _dedupe(statements: list[Statement]) -> tuple[list[Transaction], list[DuplicateRecord]]:
    """Remove transactions repeated across statements while preserving order.

    Statements are first deduplicated by content hash so exact file copies
    do not create phantom transactions. Remaining statements are processed
    in date order; the first occurrence of an identical transaction (same
    post date, description, amount and sign) is kept and later occurrences
    are recorded as duplicates. Legitimate repeated charges within a single
    statement are always kept, preserving their original source sequence.
    """
    ordered = sorted(statements, key=lambda s: (_to_date(s.statement_date), s.file_path or "", s.account_number))
    seen: set[tuple] = set()
    unique: list[Transaction] = []
    dropped: list[DuplicateRecord] = []
    for stmt in ordered:
        stmt_seen: set[tuple] = set()
        for tx in stmt.transactions:
            key = (tx.post_date, tx.description, str(tx.amount), tx.is_credit)
            if key in seen and key not in stmt_seen:
                dropped.append(DuplicateRecord(statement_date=stmt.statement_date, transaction=tx))
                continue
            unique.append(tx)
            seen.add(key)
            stmt_seen.add(key)
    return unique, dropped


def balance_asof(ledger: AccountLedger, on_date: datetime.date) -> Decimal:
    """Reconstructed balance on a date from the earliest beginning balance.

    This is a day-level (end-of-date) series used by the charts and the
    account summaries. It is NOT the per-transaction running balance, which
    ``running_balance_map`` provides: when several transactions share a post
    date, ``balance_asof`` reports the combined end-of-day value.
    """
    is_card = ledger.account_type == "Credit Card"
    balance = ledger.statements[0].beginning_balance
    for tx in ledger.transactions:
        if _to_date(tx.post_date) > on_date:
            continue
        if is_card:
            balance -= tx.amount if tx.is_credit else -tx.amount
        else:
            balance += tx.amount if tx.is_credit else -tx.amount
    return balance


def running_balance_map(ledger: AccountLedger) -> dict[int, Decimal]:
    """Balance after EACH transaction in the ledger's stable sequence.

    The ledger's transaction order is the deduplicated, date-sorted stream
    (``consolidate``), so same-date entries keep the order in which the
    earliest statement printed them. Each transaction maps (by object id) to
    the balance immediately after it was applied, which is what a bank's
    per-row running balance means. ``Transaction`` is an eq-dataclass (not
    hashable), so the map is keyed by identity, not value.
    """
    is_card = ledger.account_type == "Credit Card"
    balances: dict[int, Decimal] = {}
    if not ledger.transactions:
        return balances
    first = _to_date(ledger.transactions[0].post_date)
    balance = balance_asof(ledger, first - datetime.timedelta(days=1))
    for tx in ledger.transactions:
        if is_card:
            balance -= tx.amount if tx.is_credit else -tx.amount
        else:
            balance += tx.amount if tx.is_credit else -tx.amount
        balances[id(tx)] = balance
    return balances


def _month_buckets(transactions: list[Transaction]) -> list[MonthActivity]:
    by_month: dict[tuple[int, int], list[Transaction]] = defaultdict(list)
    for tx in transactions:
        d = _to_date(tx.post_date)
        by_month[(d.year, d.month)].append(tx)
    return [
        MonthActivity(year=y, month=m, transactions=sort_key)
        for (y, m), sort_key in sorted(by_month.items(), key=lambda kv: (kv[0][0], kv[0][1]))
    ]


def _tx_key(tx: Transaction) -> tuple:
    """Identity used to avoid assigning one credit to two matched debits."""
    return (tx.post_date, tx.description, str(tx.amount), tx.is_credit)


_TRANSFER_DEBIT_MARKERS = ("WEB XFER", "CAPITAL ONE", "AUTOPAY", "MISCELLANEOUS DEBIT")
_TRANSFER_CREDIT_MARKERS = ("XFER", "TRANSFER", "DEPOSIT")


def _ledger_covers_date(ledger: AccountLedger, on_date: datetime.date) -> bool:
    """True when one of the ledger's statements spans ``on_date``."""
    return any(start <= on_date <= end for start, end in statement_windows(ledger))


def statement_windows(ledger: AccountLedger) -> list[tuple[datetime.date, datetime.date]]:
    """(start, end) of every statement; starts are inferred when not printed.

    Card statements print only a closing date. Consecutive statements abut,
    so the start is the day after the previous close when that close is
    within a monthly cycle; otherwise the first transaction date is used.
    """
    ends = sorted(_to_date(s.statement_date) for s in ledger.statements)
    windows: list[tuple[datetime.date, datetime.date]] = []
    for stmt in ledger.statements:
        end = _to_date(stmt.statement_date)
        if stmt.period_start:
            start = _to_date(stmt.period_start)
        else:
            earlier = [e for e in ends if e < end]
            if earlier and (end - max(earlier)).days <= 35:
                start = max(earlier) + datetime.timedelta(days=1)
            else:
                dates = [_to_date(t.post_date) for t in stmt.transactions]
                start = min(dates) if dates else end
        windows.append((start, end))
    return windows


def ledger_covers_month(ledger: AccountLedger, year: int, month: int) -> bool:
    """True when at least one statement window overlaps the calendar month."""
    first = datetime.date(year, month, 1)
    last = (datetime.date(year + 1, 1, 1) if month == 12 else datetime.date(year, month + 1, 1)) - datetime.timedelta(
        days=1
    )
    return any(start <= last and end >= first for start, end in statement_windows(ledger))


def month_fully_covered(ledger: AccountLedger, year: int, month: int) -> bool:
    """True when statement windows span every day of the calendar month.

    Used to pick "complete" months for monthly baselines: the first and last
    months of a statement run are usually partial and would understate
    averages.
    """
    first = datetime.date(year, month, 1)
    last = (datetime.date(year + 1, 1, 1) if month == 12 else datetime.date(year, month + 1, 1)) - datetime.timedelta(
        days=1
    )
    windows = sorted(statement_windows(ledger))
    day = first
    for start, end in windows:
        if end < day:
            continue
        if start > day:
            return False
        day = end + datetime.timedelta(days=1)
        if day > last:
            return True
    return day > last


def match_movements(ledgers: list[AccountLedger]) -> list[MovementMatch]:
    """Pair up transfers and card payments between accounts.

    Returns one MovementMatch per debit that looks like money leaving an
    account (transfer, teller "miscellaneous debit" or card payment).
    ``matched`` is True when an offsetting credit exists in another covered
    account, established in this order of confidence:

    1. ``reference`` - both sides carry the same six-digit transfer reference;
    2. ``autopay`` - a card payment and the card's matching payment credit;
    3. ``amount+date`` - an equal and opposite transfer-like credit within two
       days when neither side prints a usable reference (the descriptions
       may have been damaged by the statement layout).

    A credit is only used to satisfy a single debit. Unmatched debits get an
    ``unmatched_reason`` when the destination can be inferred from the
    description (account not covered, or its statement for that month is
    missing).
    """
    transfer_ref = r"(?<!\d)(\d{6})(?!\d)"
    by_suffix = {led.account_number[-4:]: led for led in ledgers}

    def _is_transfer_debit(tx: Transaction) -> bool:
        upper = tx.description.upper()
        return any(marker in upper for marker in _TRANSFER_DEBIT_MARKERS) or tx.category in MOVEMENT_CATEGORIES

    def _counterbalance(
        ledger: AccountLedger, debit: Transaction, used_credits: set[tuple], allow_fallback: bool
    ) -> tuple[AccountLedger, Transaction, str] | None:
        tx_date = _to_date(debit.post_date)
        refs = set(re.findall(transfer_ref, debit.description))
        upper = debit.description.upper()
        is_card_payment = "CAPITAL ONE" in upper or "AUTOPAY" in upper
        named = re.search(r"X{4,}(\d{4})", debit.description)
        named_suffix = named.group(1) if named else ""
        rank = {"reference": 0, "autopay": 0, "amount+date": 1}
        best: tuple[int, int, AccountLedger, Transaction, str] | None = None
        for cand_ledger in ledgers:
            if cand_ledger.account_number == ledger.account_number:
                continue
            for ctx in cand_ledger.transactions:
                if _tx_key(ctx) in used_credits or not ctx.is_credit or ctx.amount != debit.amount:
                    continue
                gap = abs((_to_date(ctx.post_date) - tx_date).days)
                if gap > 5:
                    continue
                cupper = ctx.description.upper()
                basis = ""
                if is_card_payment:
                    if "CAPITAL ONE" in cupper or "AUTOPAY" in cupper or "PAYMENT" in cupper:
                        basis = "autopay"
                elif refs and (set(re.findall(transfer_ref, ctx.description)) & refs):
                    basis = "reference"
                elif (
                    allow_fallback
                    and gap <= 2
                    and cand_ledger.account_type != "Credit Card"
                    # A debit that names its destination ("... XXXXXX2136") can
                    # only pair with that account.
                    and (not named_suffix or cand_ledger.account_number.endswith(named_suffix))
                    and (ctx.category in MOVEMENT_CATEGORIES or any(m in cupper for m in _TRANSFER_CREDIT_MARKERS))
                ):
                    basis = "amount+date"
                if not basis:
                    continue
                key = (rank[basis], gap)
                if best is None or key < (best[0], best[1]):
                    best = (key[0], key[1], cand_ledger, ctx, basis)
        if best is None:
            return None
        return best[2], best[3], best[4]

    def _unmatched_reason(debit: Transaction) -> str:
        if "PAYPAL" in debit.description.upper():
            return "moved to a PayPal balance, which is not a covered account"
        m = re.search(r"X{4,}(\d{4})", debit.description)
        if not m:
            return "no offsetting credit found in the covered accounts"
        suffix = m.group(1)
        dest = by_suffix.get(suffix)
        if dest is None:
            return f"destination account ****{suffix} is not covered by any statement"
        if not _ledger_covers_date(dest, _to_date(debit.post_date)):
            return f"{dest.label} has no statement covering {_to_date(debit.post_date):%B %Y}"
        return f"no offsetting credit found in {dest.label}"

    # Pass 1 pairs every debit that can be tied by reference number or
    # autopay; only then does pass 2 try amount+date for what is left, so a
    # low-confidence pairing can never steal a credit that belongs to a
    # reference-matched transfer.
    debits = [
        (ledger, tx) for ledger in ledgers for tx in ledger.transactions if not tx.is_credit and _is_transfer_debit(tx)
    ]
    paired: dict[int, tuple[AccountLedger, Transaction, str]] = {}
    used_credits: set[tuple] = set()
    for allow_fallback in (False, True):
        for ledger, tx in debits:
            if id(tx) in paired:
                continue
            counter = _counterbalance(ledger, tx, used_credits, allow_fallback)
            if counter is not None:
                used_credits.add(_tx_key(counter[1]))
                paired[id(tx)] = counter

    matches: list[MovementMatch] = []
    for ledger, tx in debits:
        counter = paired.get(id(tx))
        if counter is not None:
            counter_ledger, ctx, basis = counter
            if basis == "amount+date":
                # A teller "MISCELLANEOUS DEBIT" paired with a plain "DEPOSIT"
                # is an internal transfer on both sides: neither is spending
                # or income once the pairing is established.
                if tx.category not in DEBT_CATEGORIES:
                    tx.category = "Transfers"
                if ctx.category not in DEBT_CATEGORIES:
                    ctx.category = "Transfers"
            matches.append(
                MovementMatch(
                    from_account=ledger.account_number,
                    to_account=counter_ledger.account_number,
                    date=tx.post_date,
                    description=tx.description,
                    amount=tx.amount,
                    matched=True,
                    basis=basis,
                    counter_date=ctx.post_date,
                    counter_description=ctx.description,
                )
            )
        else:
            matches.append(
                MovementMatch(
                    from_account=ledger.account_number,
                    to_account="",
                    date=tx.post_date,
                    description=tx.description,
                    amount=tx.amount,
                    matched=False,
                    unmatched_reason=_unmatched_reason(tx),
                )
            )
    return matches


def consolidate(statements: list[Statement]) -> ConsolidatedResult:
    """Group statements into per-account ledgers and reconcile them."""
    groups: dict[tuple[str, str], list[Statement]] = defaultdict(list)
    for stmt in statements:
        key = (stmt.institution, stmt.account_number)
        groups[key].append(stmt)

    ledgers: list[AccountLedger] = []
    for (institution, account_number), group in groups.items():
        stmts = _dedupe_statements(sorted(group, key=lambda s: _to_date(s.statement_date)))
        unique, dropped = _dedupe(stmts)
        unique.sort(key=lambda t: _to_date(t.post_date))
        ledger = AccountLedger(
            institution=institution,
            account_number=account_number,
            account_type=stmts[0].account_type,
            statements=stmts,
            transactions=unique,
            duplicates=dropped,
            months=_month_buckets(unique),
        )
        for stmt in stmts:
            ledger.checks.append(
                BalanceCheck(
                    statement_date=stmt.statement_date,
                    expected=stmt.ending_balance,
                    computed=balance_asof(ledger, _to_date(stmt.statement_date)),
                )
            )
        ledgers.append(ledger)

    ledgers.sort(key=lambda led: (led.institution, led.account_number))
    movements = match_movements(ledgers)
    return ConsolidatedResult(ledgers=ledgers, movements=movements)


def missing_coverage(ledger: AccountLedger, through_year: int, through_month: int) -> list[str]:
    """Calendar months with no statement coverage for an account.

    Returns human-readable labels like ``"July 2026"``.
    """
    if not ledger.statements:
        return []
    covered: set[tuple[int, int]] = {
        (_to_date(s.statement_date).year, _to_date(s.statement_date).month) for s in ledger.statements
    }
    covered |= {(d.year, d.month) for d in (_to_date(t.post_date) for t in ledger.transactions)}
    start_year, start_month = min(covered)
    missing: list[str] = []
    y, m = start_year, start_month
    while (y, m) <= (through_year, through_month):
        if (y, m) not in covered:
            missing.append(f"{datetime.date(y, m, 1):%B %Y}")
        m += 1
        if m > 12:
            m = 1
            y += 1
    return missing
