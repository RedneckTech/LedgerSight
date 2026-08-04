import unittest
from datetime import date

from tests.conftest import make_stmt, make_tx


class TestTransaction(unittest.TestCase):
    def test_signed_amount_credit(self):
        tx = make_tx(amount="100.00", is_credit=True)
        self.assertEqual(tx.signed_amount, 100.00)

    def test_signed_amount_debit(self):
        tx = make_tx(amount="50.00", is_credit=False)
        self.assertEqual(tx.signed_amount, -50.00)

    def test_date_obj(self):
        tx = make_tx(post_date="06/15/2023")
        self.assertEqual(tx.date_obj, date(2023, 6, 15))


class TestStatement(unittest.TestCase):
    def test_month_parsing(self):
        stmt = make_stmt(statement_date="06/30/2023")
        self.assertEqual(stmt.month, 6)

    def test_year_parsing(self):
        stmt = make_stmt(statement_date="12/31/2023")
        self.assertEqual(stmt.year, 2023)

    def test_quarter_q1(self):
        stmt = make_stmt(statement_date="03/31/2023")
        self.assertEqual(stmt.quarter, 1)

    def test_quarter_q3(self):
        stmt = make_stmt(statement_date="08/31/2023")
        self.assertEqual(stmt.quarter, 3)

    def test_quarter_q4(self):
        stmt = make_stmt(statement_date="12/31/2023")
        self.assertEqual(stmt.quarter, 4)

    def test_month_label(self):
        stmt = make_stmt(statement_date="01/31/2023")
        self.assertEqual(stmt.month_label, "January 2023")
