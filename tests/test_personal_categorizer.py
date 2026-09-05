"""Unit tests for ledgersight.personal.categorizer."""

from __future__ import annotations

import unittest
from decimal import Decimal

from ledgersight.personal.categorizer import categorize, categorize_transactions
from ledgersight.personal.models import Statement, Transaction


class TestCategorize(unittest.TestCase):
    def test_payroll(self) -> None:
        self.assertEqual(categorize("RICHERS TRUCKING PAYROLL 1234"), "Payroll")

    def test_groceries(self) -> None:
        self.assertEqual(categorize("WAL-MART SUPERCENTER #1234"), "Groceries")

    def test_shopping(self) -> None:
        self.assertEqual(categorize("AMAZON MKTPL PLACE ORDER 1234"), "Shopping")

    def test_fuel(self) -> None:
        self.assertEqual(categorize("PILOT TRAVEL CENTER 1234"), "Fuel")

    def test_subscription_precedence(self) -> None:
        self.assertEqual(categorize("PAYPAL PURCHASE HIDIVE 1234"), "Subscriptions")

    def test_bank_fee_precedence(self) -> None:
        self.assertEqual(categorize("MASTERCARD CROSS BORDER FEE"), "Bank Fees")

    def test_sales_tax_bank_fee(self) -> None:
        self.assertEqual(categorize("02/20 SALES TAX"), "Bank Fees")

    def test_overdraft_bank_fee(self) -> None:
        self.assertEqual(categorize("OVERDRAFT FEE CHARGED"), "Bank Fees")

    def test_unknown_is_other(self) -> None:
        self.assertEqual(categorize("SOMETHING COMPLETELY UNKNOWN"), "Other")

    def test_transfers(self) -> None:
        self.assertEqual(categorize("PAYPAL INST XFER 1234"), "Transfers")


class TestCategorizeTransactions(unittest.TestCase):
    def _make_stmt(self) -> Statement:
        return Statement(
            statement_date="01/31/2026",
            account_number="XXXXXXXXXXX1234",
            beginning_balance=Decimal("100.00"),
            ending_balance=Decimal("400.00"),
            total_credits=Decimal("500.00"),
            total_debits=Decimal("200.00"),
            credit_count=1,
            debit_count=3,
            transactions=[
                Transaction(
                    post_date="01/02/2026",
                    description="RICHERS TRUCKING PAYROLL",
                    amount=Decimal("500.00"),
                    is_credit=True,
                    balance=Decimal("600.00"),
                ),
                Transaction(
                    post_date="01/05/2026",
                    description="WAL-MART",
                    amount=Decimal("50.00"),
                    is_credit=False,
                    balance=Decimal("550.00"),
                ),
            ],
        )

    def test_assigns_categories_in_place(self) -> None:
        stmt = self._make_stmt()
        categorize_transactions([stmt])
        self.assertEqual(stmt.transactions[0].category, "Payroll")
        self.assertEqual(stmt.transactions[1].category, "Groceries")


if __name__ == "__main__":
    unittest.main()
