from __future__ import annotations
from decimal import Decimal

from ledgersight.models import Statement, Transaction


def make_tx(
    post_date: str = "01/15/2023",
    description: str = "TEST",
    amount: str = "100.00",
    is_credit: bool = True,
    balance: str = "500.00",
    **kwargs,
) -> Transaction:
    return Transaction(
        post_date=post_date,
        description=description,
        original_description=description,
        amount=Decimal(amount),
        is_credit=is_credit,
        balance=Decimal(balance),
        **kwargs,
    )


def make_stmt(
    statement_date: str = "01/31/2023",
    account_number: str = "XXXX2136",
    beginning_balance: str = "100.00",
    ending_balance: str = "500.00",
    total_credits: str = "500.00",
    total_debits: str = "100.00",
    credit_count: int = 2,
    debit_count: int = 1,
    transactions: list | None = None,
) -> Statement:
    return Statement(
        statement_date=statement_date,
        account_number=account_number,
        beginning_balance=Decimal(beginning_balance),
        ending_balance=Decimal(ending_balance),
        total_credits=Decimal(total_credits),
        total_debits=Decimal(total_debits),
        credit_count=credit_count,
        debit_count=debit_count,
        transactions=transactions or [],
    )
