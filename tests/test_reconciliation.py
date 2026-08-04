import unittest
from decimal import Decimal

from ledgersight.reconciliation import reconcile_all, reconcile_statement
from tests.conftest import make_stmt, make_tx


class TestReconciliation(unittest.TestCase):
    def test_perfect_reconciliation(self):
        tx_credit = make_tx(amount="200.00", is_credit=True, balance="300.00")
        tx_debit = make_tx(amount="150.00", is_credit=False, balance="150.00")
        stmt = make_stmt(
            beginning_balance="100.00",
            ending_balance="150.00",
            total_credits="200.00",
            total_debits="150.00",
            credit_count=1,
            debit_count=1,
            transactions=[tx_credit, tx_debit],
        )
        result = reconcile_statement(stmt)
        self.assertTrue(result.passed)
        self.assertTrue(result.balance_ok)
        self.assertEqual(result.parsed_credit_count, 1)
        self.assertEqual(result.parsed_debit_count, 1)

    def test_count_mismatch(self):
        stmt = make_stmt(
            beginning_balance="100.00",
            ending_balance="150.00",
            total_credits="200.00",
            total_debits="150.00",
            credit_count=5,
            debit_count=3,
            transactions=[
                make_tx(amount="200.00", is_credit=True, balance="300.00"),
                make_tx(amount="150.00", is_credit=False, balance="150.00"),
            ],
        )
        result = reconcile_statement(stmt)
        self.assertFalse(result.passed)

    def test_amount_tolerance(self):
        tx = make_tx(amount="100.01", is_credit=True, balance="200.01")
        stmt = make_stmt(
            beginning_balance="100.00",
            ending_balance="200.00",
            total_credits="100.00",
            total_debits="0.00",
            credit_count=1,
            debit_count=0,
            transactions=[tx],
        )
        result = reconcile_statement(stmt, tolerance=Decimal("0.02"))
        self.assertTrue(result.passed)

    def test_allow_mismatch(self):
        tx = make_tx(amount="10.00", is_credit=True, balance="110.00")
        stmt = make_stmt(
            beginning_balance="100.00",
            ending_balance="500.00",
            total_credits="1000.00",
            total_debits="0.00",
            credit_count=10,
            debit_count=0,
            transactions=[tx],
        )
        results, all_ok, forced = reconcile_all([stmt], allow_mismatch=True)
        self.assertFalse(all_ok)
        self.assertTrue(forced)
