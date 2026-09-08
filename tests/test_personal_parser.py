"""Unit tests for ledgersight.personal.parser."""

from __future__ import annotations

import unittest
from decimal import Decimal

from ledgersight.personal.models import Statement
from ledgersight.personal.parser import (
    _is_page_artifact,
    parse_personal_statement,
    parse_statement,
)

DESC_ZONE = 49
DEBIT_COL = 60
CREDIT_COL = 78
BALANCE_COL = 96


def activity_row(
    post_date: str,
    description: str,
    amount: str,
    is_credit: bool,
    balance: str,
) -> str:
    amount_col = CREDIT_COL if is_credit else DEBIT_COL
    line = f"{post_date} "
    line += description[:DESC_ZONE].ljust(DESC_ZONE)
    gap = amount_col - len(line)
    if gap > 0:
        line += " " * gap
    line += amount
    line += " " * (BALANCE_COL - len(line))
    line += balance
    return line


def sample_statement_text() -> str:
    header = f"{'Post Date  Description':<60}{'Debits':<18}{'Credits':<18}{'Balance'}"
    lines = [
        "JACOB PFEIFF",
        "XXXXXXXXXXX1234\tBASIC CHECKING",
        "",
        "Statement Ending 01/31/2026",
        "Account Summary",
        "Beginning Balance $100.00",
        "1 Credit     This Period   $500.00",
        "3 Debit      This Period   $200.00",
        "Ending Balance $400.00",
        "",
        "Account Activity",
        header,
        activity_row("01/02/2026", "DEPOSIT PAYROLL", "$500.00", True, "$600.00"),
        activity_row("01/05/2026", "WAL-MART", "$50.00", False, "$550.00"),
        activity_row("01/10/2026", "AMAZON MKTPL", "$25.00", False, "$525.00"),
        activity_row("01/15/2026", "ZELLE WEB XFER", "$125.00", False, "$400.00"),
        "Checks Cleared",
        "Daily Balances",
        "01/02/2026 $600.00",
        "01/05/2026 $550.00",
        "01/10/2026 $525.00",
        "01/15/2026 $400.00",
    ]
    return "\n".join(lines)


def sample_card_statement_text() -> str:
    return "\n".join(
        [
            "JACOB C PFEIFF Platinum Card | World Mastercard ending in 0142",
            "Jan 1, 2026 - Feb 1, 2026 | 31 days in Billing Cycle",
            "",
            "Previous Balance $1,172.85",
            "New Balance",
            "$1,133.97",
            "Payments - $100.00",
            "Other Credits $0.00",
            "Transactions + $29.17",
            "Cash Advances + $0.00",
            "Fees Charged + $0.00",
            "Interest Charged + $31.95",
            "New Balance = $1,133.97",
            "",
            "JACOB C PFEIFF #0142: Payments, Credits and Adjustments",
            "Jan 10 Jan 12 PAYMENT - THANK YOU $100.00",
            "",
            "JACOB C PFEIFF #0142: Transactions",
            "Jan 2 Jan 3 LOVE'S #0687 JACKSON IL $15.25",
            "Jan 8 Jan 9 CASEYS #0074 MORNING SUN IA $13.92",
            "",
            "Total Transactions for This Period $29.17",
            "",
            "Interest Charged",
            "Interest Charge on Purchases $31.95",
            "Total Interest for This Period $31.95",
        ]
    )


class TestIsPageArtifact(unittest.TestCase):
    def test_statement_ending(self) -> None:
        self.assertTrue(_is_page_artifact("Statement Ending 01/31/2026", "Statement Ending 01/31/2026"))

    def test_header_line(self) -> None:
        header = "Post Date  Description Debits Credits Balance"
        self.assertTrue(_is_page_artifact(header, header))

    def test_name_running_header(self) -> None:
        self.assertTrue(_is_page_artifact("JACOB PFEIFF XXXXXXXXXXXX1234", "JACOB PFEIFF XXXXXXXXXXXX1234"))

    def test_normal_line(self) -> None:
        self.assertFalse(_is_page_artifact("WAL-MART", "WAL-MART"))


class TestParseStatement(unittest.TestCase):
    def test_parses_statement(self) -> None:
        stmt = parse_statement(sample_statement_text(), file_path="fake.pdf")
        self.assertIsInstance(stmt, Statement)
        self.assertEqual(stmt.statement_date, "01/31/2026")
        self.assertEqual(stmt.account_number, "XXXXXXXXXXX1234")
        self.assertEqual(stmt.beginning_balance, Decimal("100.00"))
        self.assertEqual(stmt.ending_balance, Decimal("400.00"))
        self.assertEqual(stmt.total_credits, Decimal("500.00"))
        self.assertEqual(stmt.total_debits, Decimal("200.00"))
        self.assertEqual(stmt.credit_count, 1)
        self.assertEqual(stmt.debit_count, 3)
        self.assertEqual(stmt.file_path, "fake.pdf")

    def test_parses_transactions(self) -> None:
        stmt = parse_statement(sample_statement_text())
        self.assertEqual(len(stmt.transactions), 4)

        credit = stmt.transactions[0]
        self.assertEqual(credit.post_date, "01/02/2026")
        self.assertEqual(credit.description, "DEPOSIT PAYROLL")
        self.assertEqual(credit.amount, Decimal("500.00"))
        self.assertTrue(credit.is_credit)
        self.assertEqual(credit.balance, Decimal("600.00"))

        debit = stmt.transactions[1]
        self.assertEqual(debit.post_date, "01/05/2026")
        self.assertEqual(debit.description, "WAL-MART")
        self.assertEqual(debit.amount, Decimal("50.00"))
        self.assertFalse(debit.is_credit)
        self.assertEqual(debit.balance, Decimal("550.00"))

    def test_empty_text_returns_empty_statement(self) -> None:
        stmt = parse_statement("")
        self.assertEqual(stmt.transactions, [])
        self.assertEqual(stmt.account_number, "")


class TestParseCapOneStatement(unittest.TestCase):
    def test_parses_summary(self) -> None:
        stmt = parse_personal_statement(sample_card_statement_text(), file_path="card.pdf")
        self.assertIsInstance(stmt, Statement)
        self.assertEqual(stmt.account_type, "Credit Card")
        self.assertEqual(stmt.institution, "Capital One")
        self.assertEqual(stmt.account_number, "XXXXXXXXXXXX0142")
        self.assertEqual(stmt.statement_date, "02/01/2026")
        self.assertEqual(stmt.beginning_balance, Decimal("1172.85"))
        self.assertEqual(stmt.ending_balance, Decimal("1133.97"))
        self.assertEqual(stmt.total_credits, Decimal("100.00"))
        self.assertEqual(stmt.total_debits, Decimal("61.12"))
        self.assertEqual(stmt.credit_count, 1)
        self.assertEqual(stmt.debit_count, 3)
        self.assertEqual(stmt.fees_charged, Decimal("0.00"))
        self.assertEqual(stmt.interest_charged, Decimal("31.95"))

    def test_parses_transactions_and_reconciles(self) -> None:
        stmt = parse_personal_statement(sample_card_statement_text())
        descs = {(tx.description, tx.is_credit): tx for tx in stmt.transactions}
        self.assertIn(("PAYMENT - THANK YOU", True), descs)
        self.assertIn(("CASEYS #0074 MORNING SUN IA", False), descs)
        self.assertIn(("LOVE'S #0687 JACKSON IL", False), descs)
        synthetic = descs[("INTEREST CHARGED", False)]
        self.assertEqual(synthetic.amount, Decimal("31.95"))
        self.assertEqual(synthetic.category, "Interest")

        balance = stmt.beginning_balance
        for tx in sorted(stmt.transactions, key=lambda t: (t.post_date, t.amount)):
            balance = balance - tx.amount if tx.is_credit else balance + tx.amount
            self.assertEqual(tx.balance, balance)
        self.assertEqual(balance, stmt.ending_balance)


class TestParsePersonalStatementDispatch(unittest.TestCase):
    def test_fi_checking(self) -> None:
        stmt = parse_personal_statement(sample_statement_text())
        self.assertEqual(stmt.account_type, "Checking")
        self.assertEqual(stmt.institution, "First Interstate")

    def test_fi_checking_with_savings_in_description(self) -> None:
        text = sample_statement_text() + "\nXX0844 XXX 256815 WEB XFER FROM REGULAR SAVINGS\n"
        stmt = parse_personal_statement(text)
        self.assertEqual(stmt.account_type, "Checking")

    def test_card_routes_to_capone(self) -> None:
        stmt = parse_personal_statement(sample_card_statement_text())
        self.assertEqual(stmt.account_type, "Credit Card")


def wrapped_statement_text() -> str:
    """Real First Interstate layout: a two-line description prints its first
    line ABOVE the dated row and its second line BELOW it; one-line
    descriptions sit on the dated row. Mirrors the June 2026 statements."""
    header = f"{'Post Date  Description':<60}{'Debits':<18}{'Credits':<18}{'Balance'}"
    pad = " " * 13
    lines = [
        "JACOB PFEIFF",
        "XXXXXXXXXXX6781\tBASIC CHECKING",
        "Statement Ending 06/26/2026",
        "Account Summary",
        "05/27/2026 Beginning Balance $283.94",
        "2 Credit     This Period   $370.00",
        "4 Debit      This Period   $157.50",
        "Ending Balance $496.44",
        "",
        "Account Activity",
        header,
        activity_row("06/03/2026", "MISCELLANEOUS DEBIT", "$110.00", False, "$173.94"),
        f"{pad}XX0844 DEBIT CARD 06/02 08:35 Audible L746J95Q",
        activity_row("06/03/2026", "", "$14.67", False, "$159.27"),
        f"{pad}NEWARK NJ 41860326 812703",
        f"{pad}XX0844 DEBIT CARD 06/22 21:31 OPENAI CHATGPT SAN",
        activity_row("06/23/2026", "", "$21.40", False, "$137.87"),
        f"{pad}FRANCISCO CA 07122175 826535",
        activity_row("06/24/2026", "DEPOSIT", "$10.00", True, "$147.87"),
        f"{pad}649104 WEB XFER FROM REGULAR SAVINGS",
        activity_row("06/24/2026", "", "$360.00", True, "$507.87"),
        f"{pad}XXXXXX3608 6/24/26",
        "",
        "       45D13DB3BB49974181EE10A316F35A62                 20260626          Checking Account Statements",
        "JACOB PFEIFF                                 XXXXXXXXXXX6781           Statement Ending 06/26/2026",
        "BASIC CHECKING - XXXXXXXXXXX6781 (continued)",
        "Account Activity (continued)",
        header,
        f"{pad}125 S Roosevelt Ave BURLINGTON IA 12667016 200547",
        activity_row("06/25/2026", "", "$11.43", False, "$496.44"),
        f"{pad}XX0844 DEBIT CARD 06/24 10:32",
        "06/26/2026 Ending Balance $496.44",
        "Checks Cleared",
        "Daily Balances",
        "06/03/2026 $159.27   06/23/2026 $137.87",
    ]
    return "\n".join(lines)


class TestWrappedDescriptions(unittest.TestCase):
    def setUp(self) -> None:
        self.stmt = parse_statement(wrapped_statement_text())
        self.by_amount = {str(t.amount): t for t in self.stmt.transactions}

    def test_row_count(self) -> None:
        self.assertEqual(len(self.stmt.transactions), 6)

    def test_placeholder_row_keeps_only_its_own_text(self) -> None:
        self.assertEqual(self.by_amount["110.00"].description, "MISCELLANEOUS DEBIT")

    def test_two_line_descriptions_are_reassembled(self) -> None:
        self.assertEqual(
            self.by_amount["14.67"].description,
            "XX0844 DEBIT CARD 06/02 08:35 Audible L746J95Q NEWARK NJ 41860326 812703",
        )
        self.assertEqual(
            self.by_amount["21.40"].description,
            "XX0844 DEBIT CARD 06/22 21:31 OPENAI CHATGPT SAN FRANCISCO CA 07122175 826535",
        )

    def test_inline_description_not_polluted_by_neighbours(self) -> None:
        self.assertEqual(self.by_amount["10.00"].description, "DEPOSIT")
        self.assertEqual(
            self.by_amount["360.00"].description,
            "649104 WEB XFER FROM REGULAR SAVINGS XXXXXX3608 6/24/26",
        )

    def test_continued_page_record(self) -> None:
        self.assertEqual(
            self.by_amount["11.43"].description,
            "125 S Roosevelt Ave BURLINGTON IA 12667016 200547 XX0844 DEBIT CARD 06/24 10:32",
        )

    def test_daily_balances_not_parsed_as_activity(self) -> None:
        self.assertTrue(
            all(t.post_date != "06/23/2026" or t.amount == Decimal("21.40") for t in self.stmt.transactions)
        )


if __name__ == "__main__":
    unittest.main()


class TestCardTerms(unittest.TestCase):
    def test_reads_due_date_minimum_limit_and_apr(self) -> None:
        text = sample_card_statement_text() + "\n".join(
            [
                "",
                "  Payment Due Date                                  For online and phone payments",
                "  Feb 22, 2026",
                "  New Balance                                       Minimum Payment Due",
                "",
                "  $1,133.97                                         $25.00",
                "                                                    Credit Limit                 $1,300.00",
                "Payment Due Date: Feb 22, 2026                                      Account ending in 0142",
                "Type of Balance                     Annual Percentage Rate (APR)",
                "Purchases                                     30.49% P            $1,134.36",
            ]
        )
        stmt = parse_personal_statement(text)
        self.assertEqual(stmt.payment_due_date, "02/22/2026")
        self.assertEqual(stmt.minimum_payment, Decimal("25.00"))
        self.assertEqual(stmt.credit_limit, Decimal("1300.00"))
        self.assertEqual(stmt.apr_purchases, Decimal("30.49"))

    def test_source_page_and_row_are_tracked(self) -> None:
        stmt = parse_statement(wrapped_statement_text())
        by_amount = {str(t.amount): t for t in stmt.transactions}
        self.assertEqual(by_amount["110.00"].source_row, 1)
        self.assertEqual(by_amount["110.00"].source_page, 1)
        self.assertEqual(by_amount["11.43"].source_row, 6)
