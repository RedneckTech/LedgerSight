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
    """An internal transfer or card payment matched across two accounts."""

    from_account: str
    to_account: str
    date: str
    description: str
    amount: Decimal
    matched: bool


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


def _dedupe(statements: list[Statement]) -> tuple[list[Transaction], list[DuplicateRecord]]:
    """Remove transactions repeated across statements.

    An identical transaction (same post date, description, amount and sign)
    is kept from the earliest statement that lists it and dropped from all
    later statements. Transactions that merely repeat within a single
    statement (e.g. two same-day charges) are always kept.
    """
    ordered = sorted(statements, key=lambda s: _to_date(s.statement_date))
    by_key: dict[tuple, list[tuple[str, Transaction]]] = defaultdict(list)
    for stmt in ordered:
        for tx in stmt.transactions:
            key = (tx.post_date, tx.description, str(tx.amount), tx.is_credit)
            by_key[key].append((stmt.statement_date, tx))

    unique: list[Transaction] = []
    dropped: list[DuplicateRecord] = []
    for occurrences in by_key.values():
        earliest = min(_to_date(end) for end, _ in occurrences)
        for end_date, tx in occurrences:
            if _to_date(end_date) == earliest:
                unique.append(tx)
            else:
                dropped.append(DuplicateRecord(statement_date=end_date, transaction=tx))
    return unique, dropped


def balance_asof(ledger: AccountLedger, on_date: datetime.date) -> Decimal:
    """Reconstructed balance on a date from the earliest beginning balance."""
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


def match_movements(ledgers: list[AccountLedger]) -> list[MovementMatch]:
    """Pair up transfers and card payments between accounts.

    Returns one MovementMatch per debit that looks like money leaving an
    account (transfer or card payment); ``matched`` is True when a credit
    for the same transfer reference (or card autopay) exists in another
    account. A credit is only used to satisfy a single debit.
    """
    transfer_ref = r"(?<!\d)(\d{6})(?!\d)"

    def _counterbalance(debit: Transaction, used_credits: set[tuple]) -> tuple[AccountLedger, Transaction] | None:
        tx_date = _to_date(debit.post_date)
        refs = set(re.findall(transfer_ref, debit.description))
        is_card_payment = "CAPITAL ONE" in debit.description.upper() or "AUTOPAY" in debit.description.upper()
        best: tuple[AccountLedger, Transaction] | None = None
        for cand_ledger in ledgers:
            if cand_ledger.account_number == ledger.account_number:
                continue
            for ctx in cand_ledger.transactions:
                if _tx_key(ctx) in used_credits:
                    continue
                if not ctx.is_credit or ctx.amount != debit.amount:
                    continue
                if abs((_to_date(ctx.post_date) - tx_date).days) > 5:
                    continue
                cupper = ctx.description.upper()
                if is_card_payment:
                    if not ("CAPITAL ONE" in cupper or "AUTOPAY" in cupper):
                        continue
                elif not (set(re.findall(transfer_ref, ctx.description)) & refs):
                    continue
                if best is None:
                    best = (cand_ledger, ctx)
                elif abs((_to_date(ctx.post_date) - tx_date).days) < abs((_to_date(best[1].post_date) - tx_date).days):
                    best = (cand_ledger, ctx)
        return best

    matches: list[MovementMatch] = []
    used_credits: set[tuple] = set()
    for ledger in ledgers:
        for tx in ledger.transactions:
            if tx.is_credit:
                continue
            upper = tx.description.upper()
            if "WEB XFER" not in upper and "CAPITAL ONE" not in upper and "AUTOPAY" not in upper:
                continue
            counter = _counterbalance(tx, used_credits)
            if counter is not None:
                counter_ledger, ctx = counter
                used_credits.add(_tx_key(ctx))
                matches.append(
                    MovementMatch(
                        from_account=ledger.account_number,
                        to_account=counter_ledger.account_number,
                        date=tx.post_date,
                        description=tx.description,
                        amount=tx.amount,
                        matched=True,
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
        stmts = sorted(group, key=lambda s: _to_date(s.statement_date))
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
