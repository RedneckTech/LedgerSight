"""Unit tests for ledgersight.personal.insights."""

from __future__ import annotations

import datetime
import unittest
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from ledgersight.personal.consolidation import consolidate
from ledgersight.personal.insights import (
    budget_category_rows,
    budget_month_rows,
    build_forecast,
    canon_merchant,
    category_spend_totals,
    dashboard_totals,
    detect_repeating_payments,
    estimate_income_groups,
    is_refund,
    is_spending,
    load_budget,
    month_series,
)
from ledgersight.personal.models import Statement, Transaction


def make_tx(
    post_date: str,
    description: str,
    amount: str,
    is_credit: bool,
    balance: str,
    category: str = "",
) -> Transaction:
    return Transaction(
        post_date=post_date,
        description=description,
        amount=Decimal(amount),
        is_credit=is_credit,
        balance=Decimal(balance),
        category=category,
    )


def make_stmt(
    statement_date: str,
    account_number: str,
    beginning: str,
    ending: str,
    transactions: list[Transaction],
    account_type: str = "Checking",
    institution: str = "First Interstate",
) -> Statement:
    credits = sum((t.amount for t in transactions if t.is_credit), Decimal("0"))
    debits = sum((t.amount for t in transactions if not t.is_credit), Decimal("0"))
    return Statement(
        statement_date=statement_date,
        account_number=account_number,
        beginning_balance=Decimal(beginning),
        ending_balance=Decimal(ending),
        total_credits=credits,
        total_debits=debits,
        credit_count=sum(t.is_credit for t in transactions),
        debit_count=sum(not t.is_credit for t in transactions),
        transactions=transactions,
        account_type=account_type,
        institution=institution,
    )


def _dashboard_statements() -> list[Statement]:
    return [
        make_stmt(
            "01/31/2026",
            "XXXXXXXXXXX1234",
            "1000.00",
            "2685.00",
            [
                make_tx("01/02/2026", "RICHERS TRUCKING PAYROLL", "2000.00", True, "3000.00", "Payroll"),
                make_tx("01/05/2026", "WAL-MART STORE", "300.00", False, "2700.00", "Shopping"),
                make_tx("01/10/2026", "NETFLIX.COM", "15.00", False, "2685.00", "Subscriptions"),
            ],
        ),
        make_stmt(
            "01/31/2026",
            "XXXXXXXXXXXX0142",
            "500.00",
            "545.00",
            [make_tx("01/06/2026", "AMAZON MKTPL", "45.00", False, "545.00", "Shopping")],
            account_type="Credit Card",
            institution="Capital One",
        ),
        make_stmt(
            "02/28/2026",
            "XXXXXXXXXXX1234",
            "2685.00",
            "4625.00",
            [
                make_tx("02/04/2026", "RICHERS TRUCKING PAYROLL", "2000.00", True, "4685.00", "Payroll"),
                make_tx("02/20/2026", "AMAZON MKTPL", "60.00", False, "4625.00", "Shopping"),
            ],
        ),
        make_stmt(
            "02/28/2026",
            "XXXXXXXXXXXX0142",
            "545.00",
            "585.00",
            [make_tx("02/07/2026", "AMAZON MKTPL", "40.00", False, "585.00", "Shopping")],
            account_type="Credit Card",
            institution="Capital One",
        ),
    ]


def _recurring_statements() -> list[Statement]:
    return [
        make_stmt(
            "03/31/2026",
            "XXXXXXXXXXX1234",
            "1000.00",
            "8953.53",
            [
                make_tx("01/02/2026", "RICHERS TRUCKING PAYROLL", "2000.00", True, "3000.00", "Payroll"),
                make_tx("01/05/2026", "NETFLIX.COM", "15.49", False, "2984.51", "Subscriptions"),
                make_tx("01/16/2026", "RICHERS TRUCKING PAYROLL", "2000.00", True, "4984.51", "Payroll"),
                make_tx("01/30/2026", "RICHERS TRUCKING PAYROLL", "2000.00", True, "6984.51", "Payroll"),
                make_tx("02/05/2026", "NETFLIX.COM", "15.49", False, "6969.02", "Subscriptions"),
                make_tx("02/09/2026", "MISC STORE", "50.00", False, "6919.02", "Shopping"),
                make_tx("02/13/2026", "RICHERS TRUCKING PAYROLL", "2000.00", True, "8919.02", "Payroll"),
                make_tx("03/03/2026", "MISC STORE", "20.00", False, "8899.02", "Shopping"),
                make_tx("03/05/2026", "NETFLIX.COM", "15.49", False, "8883.53", "Subscriptions"),
                make_tx("03/07/2026", "MISC STORE", "100.00", False, "8783.53", "Shopping"),
                make_tx("01/20/2026", "SPORADIC GYM", "40.00", False, "8713.53", "Shopping"),
                make_tx("02/02/2026", "SPORADIC GYM", "40.00", False, "8673.53", "Shopping"),
                make_tx("03/04/2026", "SPORADIC GYM", "40.00", False, "8633.53", "Shopping"),
            ],
        )
    ]


class TestDashboard(unittest.TestCase):
    def setUp(self) -> None:
        self.result = consolidate(_dashboard_statements())

    def test_month_series_income_spending(self) -> None:
        series = month_series(self.result.ledgers)
        self.assertEqual(series[(2026, 1)]["income"], Decimal("2000.00"))
        self.assertEqual(series[(2026, 1)]["spending"], Decimal("360.00"))
        self.assertEqual(series[(2026, 2)]["spending"], Decimal("100.00"))

    def test_month_series_balances(self) -> None:
        series = month_series(self.result.ledgers)
        self.assertEqual(series[(2026, 1)]["cash"], Decimal("2685.00"))
        self.assertEqual(series[(2026, 1)]["debt"], Decimal("545.00"))
        self.assertEqual(series[(2026, 2)]["debt"], Decimal("585.00"))

    def test_dashboard_totals(self) -> None:
        totals = dashboard_totals(self.result, datetime.date(2026, 2, 28))
        self.assertEqual(totals["income"], Decimal("4000.00"))
        self.assertEqual(totals["spending"], Decimal("460.00"))
        self.assertEqual(totals["cash"], Decimal("4625.00"))
        self.assertEqual(totals["debt"], Decimal("585.00"))


class TestDetectRepeatingPayments(unittest.TestCase):
    def setUp(self) -> None:
        result = consolidate(_recurring_statements())
        self.tx = result.all_transactions

    def test_detects_payroll_and_subscription(self) -> None:
        found = detect_repeating_payments(self.tx)
        by_payee = {r.payee: r for r in found}
        self.assertIn("RICHERS TRUCKING PAYROLL", by_payee)
        self.assertIn("NETFLIX.COM", by_payee)
        payroll = by_payee["RICHERS TRUCKING PAYROLL"]
        self.assertTrue(payroll.income)
        self.assertEqual(payroll.cadence_days, 14)
        self.assertEqual(payroll.occurrences, 4)
        self.assertEqual(payroll.typical_amount, Decimal("2000.00"))

    def test_excludes_irregular_dates_and_amounts(self) -> None:
        found = detect_repeating_payments(self.tx)
        payees = {r.payee for r in found}
        self.assertNotIn("SPORADIC GYM", payees)
        self.assertNotIn("MISC STORE", payees)


class TestRefundHandling(unittest.TestCase):
    def test_refund_excluded_from_income(self) -> None:
        stmts = [
            make_stmt(
                "01/31/2026",
                "XXXXXXXXXXX1234",
                "1000.00",
                "3700.00",
                [
                    make_tx("01/02/2026", "RICHERS TRUCKING PAYROLL", "2000.00", True, "3000.00", "Payroll"),
                    make_tx("01/10/2026", "REFUNDED SECURITY DEPOSIT", "300.00", True, "3300.00", "Deposit"),
                    make_tx("01/15/2026", "WAL-MART STORE", "100.00", False, "3200.00", "Shopping"),
                    make_tx("01/20/2026", "TRANSFER FROM SAVINGS", "500.00", True, "3700.00", "Transfers"),
                ],
            )
        ]
        result = consolidate(stmts)
        series = month_series(result.ledgers)
        totals = dashboard_totals(result, datetime.date(2026, 1, 31))
        self.assertEqual(series[(2026, 1)]["income"], Decimal("2000.00"))
        self.assertEqual(series[(2026, 1)]["refunds"], Decimal("300.00"))
        self.assertEqual(totals["income"], Decimal("2000.00"))
        self.assertEqual(totals["income_credits"], Decimal("2300.00"))
        self.assertEqual(totals["refunds"], Decimal("300.00"))
        self.assertEqual(totals["spending"], Decimal("100.00"))
        self.assertTrue(is_refund(make_tx("01/10/2026", "REFUNDED SECURITY DEPOSIT", "300.00", True, "0.00")))

    def test_unified_spending_definition(self) -> None:
        stmts = [
            make_stmt(
                "01/31/2026",
                "XXXXXXXXXXX1234",
                "1000.00",
                "500.00",
                [
                    make_tx("01/05/2026", "CAPITAL ONE AUTOPAY PYMT", "200.00", False, "800.00", "Loan/Credit Payment"),
                    make_tx("01/10/2026", "TRANSFER TO CHECKING", "150.00", False, "650.00", "Transfers"),
                    make_tx("01/15/2026", "UNCATEGORIZED STORE", "150.00", False, "500.00", "Other"),
                ],
            )
        ]
        result = consolidate(stmts)
        totals = dashboard_totals(result, datetime.date(2026, 1, 31))
        self.assertEqual(totals["spending"], Decimal("150.00"))
        self.assertFalse(is_spending(make_tx("01/05/2026", "TRANSFER", "1.00", False, "0.00", "Transfers")))
        self.assertFalse(is_spending(make_tx("01/05/2026", "AUTOPAY", "1.00", False, "0.00", "Loan/Credit Payment")))
        self.assertTrue(is_spending(make_tx("01/05/2026", "STORE", "1.00", False, "0.00", "Other")))


class TestCanonMerchant(unittest.TestCase):
    def test_payment_rail_tokens_are_merged(self) -> None:
        self.assertEqual(
            canon_merchant("PAYPAL INST XFER HIDIVE 1234567"),
            canon_merchant("PAYPAL PURCHASE HIDIVE 7654321"),
        )

    def test_rail_change_stays_one_payee(self) -> None:
        tx = [
            make_tx("06/05/2026", "PAYPAL INST XFER HIDIVE 1234567", "12.99", False, "2987.01", "Subscriptions"),
            make_tx("07/05/2026", "PAYPAL INST XFER HIDIVE 1234567", "12.99", False, "2974.02", "Subscriptions"),
            make_tx("08/05/2026", "PAYPAL PURCHASE HIDIVE 7654321", "12.99", False, "2961.03", "Subscriptions"),
        ]
        found = detect_repeating_payments(tx)
        self.assertEqual(len(found), 1)
        self.assertIn("HIDIVE", found[0].payee)
        self.assertEqual(found[0].occurrences, 3)


class TestActiveRecurring(unittest.TestCase):
    def test_inactive_excluded_from_forecast(self) -> None:
        stale = _rp("STALE GYM", False, 14, "40.00", "40.00", datetime.date(2026, 1, 1), active=False)
        netflix = _rp("NETFLIX.COM", False, 29, "15.49", "15.49", datetime.date(2026, 3, 5), active=True)
        events = build_forecast([stale, netflix], Decimal("1000.00"), datetime.date(2026, 3, 31))
        self.assertTrue(all(e.payee != "STALE GYM" for e in events))

    def test_long_cadence_marked_needs_confirm(self) -> None:
        tx = [
            make_tx("03/25/2026", "CHATGPT SUBSCRIPTION", "20.00", False, "1000.00", "Subscriptions"),
            make_tx("06/08/2026", "CHATGPT SUBSCRIPTION", "20.00", False, "1000.00", "Subscriptions"),
            make_tx("08/22/2026", "CHATGPT SUBSCRIPTION", "20.00", False, "1000.00", "Subscriptions"),
        ]
        found = detect_repeating_payments(tx, as_of=datetime.date(2026, 8, 28))
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0].needs_confirm)
        self.assertTrue(found[0].active)


class TestEstimateIncome(unittest.TestCase):
    def test_estimates_next_variable_income(self) -> None:
        tx = [
            make_tx("08/04/2026", "RICHERS TRUCKING PAYROLL", "900.00", True, "1.00", "Payroll"),
            make_tx("08/11/2026", "RICHERS TRUCKING PAYROLL", "750.00", True, "1.00", "Payroll"),
            make_tx("08/18/2026", "RICHERS TRUCKING PAYROLL", "1200.00", True, "1.00", "Payroll"),
            make_tx("08/25/2026", "RICHERS TRUCKING PAYROLL", "950.00", True, "1.00", "Payroll"),
        ]
        estimates = estimate_income_groups(tx, datetime.date(2026, 8, 28))
        self.assertEqual(len(estimates), 1)
        est = estimates[0]
        self.assertEqual(est.median_cadence_days, 7)
        self.assertEqual(est.median_amount, Decimal("925.00"))
        self.assertEqual(est.next_event, datetime.date(2026, 9, 1))

    def test_requires_recent_occurrences(self) -> None:
        tx = [
            make_tx("01/10/2026", "RICHERS TRUCKING PAYROLL", "950.00", True, "1.00", "Payroll"),
            make_tx("05/20/2026", "RICHERS TRUCKING PAYROLL", "950.00", True, "1.00", "Payroll"),
            make_tx("08/25/2026", "RICHERS TRUCKING PAYROLL", "950.00", True, "1.00", "Payroll"),
        ]
        self.assertEqual(estimate_income_groups(tx, datetime.date(2026, 8, 28)), [])


class TestBuildForecast(unittest.TestCase):
    def test_projects_sorted_events(self) -> None:
        repeating = [
            _rp("NETFLIX.COM", False, 29, "15.49", "15.49", datetime.date(2026, 3, 5)),
            _rp("RICHERS TRUCKING PAYROLL", True, 14, "2000.00", "2000.00", datetime.date(2026, 2, 13)),
        ]
        events = build_forecast(repeating, Decimal("3000.00"), datetime.date(2026, 3, 31))
        self.assertTrue(all(e.event_date > datetime.date(2026, 3, 31) for e in events))
        self.assertEqual(events[0].payee, "NETFLIX.COM")
        self.assertEqual(events[0].event_date, datetime.date(2026, 4, 3))
        self.assertEqual(events[0].projected, Decimal("2984.51"))
        payroll = [e for e in events if e.payee == "RICHERS TRUCKING PAYROLL"]
        self.assertEqual(payroll[0].event_date, datetime.date(2026, 4, 10))
        self.assertEqual(payroll[0].projected, Decimal("4984.51"))

    def test_appends_estimated_income(self) -> None:
        events = build_forecast(
            [_rp("NETFLIX.COM", False, 29, "15.49", "15.49", datetime.date(2026, 3, 5))],
            Decimal("3000.00"),
            datetime.date(2026, 3, 31),
            estimated_income=[_ei("RICHERS TRUCKING PAYROLL", datetime.date(2026, 4, 4), Decimal("900.00"))],
        )
        est = [e for e in events if e.payee.endswith("estimated")]
        self.assertGreaterEqual(len(est), 10)
        self.assertEqual(est[0].event_date, datetime.date(2026, 4, 4))
        self.assertEqual(est[1].event_date, datetime.date(2026, 4, 11))
        self.assertEqual(est[0].projected, Decimal("3884.51"))


def _rp(
    payee: str,
    income: bool,
    cadence: int,
    lo: str,
    hi: str,
    last: datetime.date,
    active: bool = True,
    needs_confirm: bool = False,
):
    from ledgersight.personal.insights import RepeatingPayment

    amount = Decimal(hi)
    return RepeatingPayment(
        payee=payee,
        category="Income" if income else "Subscriptions",
        income=income,
        cadence_days=cadence,
        amount_min=Decimal(lo),
        amount_max=Decimal(hi),
        typical_amount=amount,
        start_date=datetime.date(2026, 1, 1),
        last_date=last,
        occurrences=4,
        active=active,
        needs_confirm=needs_confirm,
    )


def _ei(payee: str, next_event: datetime.date, amount: Decimal):
    from ledgersight.personal.insights import EstimatedIncome

    return EstimatedIncome(
        payee=payee,
        occurrences=4,
        median_amount=amount,
        median_cadence_days=7,
        last_date=next_event - datetime.timedelta(days=7),
        next_event=next_event,
    )


class TestBudget(unittest.TestCase):
    def test_load_budget_valid(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "budget.yaml"
            path.write_text("income_monthly: 4000\ncategories:\n  Fuel: 300\n  Rent: 800\n", encoding="utf-8")
            budget = load_budget(str(path))
            assert budget is not None
            self.assertTrue(budget.configured)
            self.assertEqual(budget.income_monthly, Decimal("4000"))
            self.assertEqual(budget.categories["Fuel"], Decimal("300"))
            self.assertEqual(budget.total_monthly, Decimal("1100"))

    def test_load_budget_missing_returns_none(self) -> None:
        self.assertIsNone(load_budget("/nonexistent/budget.yaml"))
        self.assertIsNone(load_budget(None))

    def test_load_budget_all_zero_not_configured(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "budget.yaml"
            path.write_text("income_monthly: 0\ncategories:\n  Fuel: 0\n", encoding="utf-8")
            budget = load_budget(str(path))
            assert budget is not None
            self.assertFalse(budget.configured)

    def test_category_rows_variance(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "budget.yaml"
            path.write_text("categories:\n  Fuel: 300\n", encoding="utf-8")
            budget = load_budget(str(path))
            assert budget is not None
            spend = {"Fuel": Decimal("250.00"), "Restaurants": Decimal("100.00")}
            rows = budget_category_rows(budget, spend, datetime.date(2026, 1, 1), datetime.date(2026, 2, 28))
            fuel = next(r for r in rows if r["category"] == "Fuel")
            self.assertEqual(fuel["average"], Decimal("125.00"))
            self.assertEqual(fuel["variance"], Decimal("175.00"))
            restaurants = next(r for r in rows if r["category"] == "Restaurants")
            self.assertEqual(restaurants["budget"], Decimal("0"))

    def test_month_rows_used_percent(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "budget.yaml"
            path.write_text("spending_monthly: 2000\n", encoding="utf-8")
            budget = load_budget(str(path))
            assert budget is not None
            series = {(2026, 1): {"spending": Decimal("1500.00")}}
            rows = budget_month_rows(budget, series)
            self.assertEqual(rows[0]["used"], Decimal("75.0"))


class TestCategorySpendTotals(unittest.TestCase):
    def test_totals_by_category(self) -> None:
        result = consolidate(_dashboard_statements())
        totals = category_spend_totals(result.ledgers)
        self.assertEqual(totals["Shopping"], Decimal("445.00"))
        self.assertEqual(totals["Subscriptions"], Decimal("15.00"))


class TestSameDayPayrollCollapse(unittest.TestCase):
    def test_two_deposits_on_one_day_are_one_payday(self) -> None:
        tx = [
            make_tx("07/28/2026", "RICHERS TRUCKING PAYROLL", "900.00", True, "1.00", "Payroll"),
            make_tx("08/04/2026", "RICHERS TRUCKING PAYROLL", "950.00", True, "1.00", "Payroll"),
            make_tx("08/11/2026", "RICHERS TRUCKING PAYROLL", "600.00", True, "1.00", "Payroll"),
            make_tx("08/11/2026", "RICHERS TRUCKING PAYROLL", "400.00", True, "1.00", "Payroll"),
            make_tx("08/18/2026", "RICHERS TRUCKING PAYROLL", "1000.00", True, "1.00", "Payroll"),
            make_tx("08/25/2026", "RICHERS TRUCKING PAYROLL", "870.00", True, "1.00", "Payroll"),
        ]
        est = estimate_income_groups(tx, datetime.date(2026, 8, 28))
        self.assertEqual(len(est), 1)
        self.assertEqual(est[0].median_cadence_days, 7)  # not 6, despite the double payment
        self.assertEqual(est[0].occurrences, 5)  # distinct pay dates
        self.assertEqual(est[0].median_amount, Decimal("950.00"))  # 08/11 counts as one $1,000 payday
        self.assertEqual(est[0].next_event, datetime.date(2026, 9, 1))


def _two_month_result():
    """Checking statements Jan (full month) and Feb 1-15 (partial) plus a card."""
    stmts = [
        make_stmt(
            "01/31/2026",
            "XXXXXXXXXXX1234",
            "1000.00",
            "1600.00",
            [
                make_tx("01/02/2026", "RICHERS TRUCKING PAYROLL", "2000.00", True, "3000.00", "Payroll"),
                make_tx("01/05/2026", "NETFLIX.COM", "15.00", False, "2985.00", "Subscriptions"),
                make_tx("01/06/2026", "HULU", "85.00", False, "2900.00", "Subscriptions"),
                make_tx("01/10/2026", "CAPITAL ONE CRCARDPMT", "300.00", False, "2600.00", "Loan/Credit Payment"),
                make_tx(
                    "01/12/2026",
                    "111111 WEB XFER TO CLASSIC BUSINESS XXXXXX2136 1/12/26",
                    "500.00",
                    False,
                    "2100.00",
                    "Transfers",
                ),
                make_tx("01/20/2026", "CHECK # 5001", "500.00", False, "1600.00", "Checks"),
            ],
        ),
        make_stmt(
            "02/15/2026",
            "XXXXXXXXXXX1234",
            "1600.00",
            "1585.00",
            [make_tx("02/05/2026", "NETFLIX.COM", "15.00", False, "1585.00", "Subscriptions")],
        ),
        make_stmt(
            "01/28/2026",
            "XXXXXXXXXXXX0142",
            "500.00",
            "260.00",
            [
                make_tx("01/12/2026", "CAPITAL ONE AUTOPAY PYMT", "300.00", True, "200.00", "Loan/Credit Payment"),
                make_tx("01/15/2026", "LOVE'S #0687", "60.00", False, "260.00", "Fuel"),
            ],
            account_type="Credit Card",
            institution="Capital One",
        ),
    ]
    for s in stmts:
        s.period_start = {"01/31/2026": "01/01/2026", "02/15/2026": "02/01/2026", "01/28/2026": ""}[s.statement_date]
    stmts[2].payment_due_date = "02/22/2026"
    stmts[2].minimum_payment = Decimal("25.00")
    stmts[2].credit_limit = Decimal("1300.00")
    stmts[2].apr_purchases = Decimal("30.49")
    return consolidate(stmts)


class TestCoverageAndBaselines(unittest.TestCase):
    def setUp(self) -> None:
        self.result = _two_month_result()
        self.checking = [led for led in self.result.ledgers if led.account_type == "Checking"][0]

    def test_month_completeness(self) -> None:
        from ledgersight.personal.consolidation import ledger_covers_month, month_fully_covered

        self.assertTrue(month_fully_covered(self.checking, 2026, 1))
        self.assertFalse(month_fully_covered(self.checking, 2026, 2))  # statement ends 02/15
        self.assertTrue(ledger_covers_month(self.checking, 2026, 2))
        self.assertFalse(ledger_covers_month(self.checking, 2026, 3))

    def test_baselines_use_complete_months_only(self) -> None:
        from ledgersight.personal.insights import LOAN_BUCKET, OUTSIDE_TRANSFER_BUCKET, cash_outflow_baselines

        by_label = {b.label: b for b in cash_outflow_baselines(self.result)}
        # February (partial) is excluded, so Subscriptions = January only = $100
        self.assertEqual(by_label["Subscriptions"].monthly, Decimal("100.00"))
        self.assertEqual(by_label["Subscriptions"].months, 1)
        self.assertEqual(by_label[LOAN_BUCKET].monthly, Decimal("300.00"))
        self.assertEqual(by_label[OUTSIDE_TRANSFER_BUCKET].monthly, Decimal("500.00"))
        self.assertEqual(by_label["Checks"].monthly, Decimal("500.00"))

    def test_scheduled_bills_reduce_allowance(self) -> None:
        from ledgersight.personal.insights import RepeatingPayment, cash_outflow_baselines

        netflix = RepeatingPayment(
            payee="NETFLIX.COM",
            category="Subscriptions",
            income=False,
            cadence_days=30,
            amount_min=Decimal("15.00"),
            amount_max=Decimal("15.00"),
            typical_amount=Decimal("15.00"),
            start_date=datetime.date(2026, 1, 5),
            last_date=datetime.date(2026, 2, 5),
            occurrences=2,
        )
        subs = [b for b in cash_outflow_baselines(self.result, [netflix]) if b.label == "Subscriptions"][0]
        self.assertEqual(subs.scheduled, Decimal("15.22"))  # 15 * 30.44 / 30
        self.assertEqual(subs.allowance, Decimal("84.78"))

    def test_month_series_marks_missing_accounts(self) -> None:
        series = month_series(self.result.ledgers)
        feb = series[(2026, 2)]
        self.assertIn("Capital One ****0142", feb["missing"])  # card statement covers only Jan
        self.assertIsNone(feb["balances"]["Capital One ****0142"])
        self.assertEqual(feb["balances"]["First Interstate ****1234"], Decimal("1585.00"))
        jan = series[(2026, 1)]
        self.assertEqual(jan["payroll"], Decimal("2000.00"))
        self.assertEqual(jan["deposits"], Decimal("0"))
        self.assertEqual(jan["total_credits"], Decimal("2300.00"))  # payroll + card payment credit

    def test_dashboard_payroll_split(self) -> None:
        totals = dashboard_totals(self.result, datetime.date(2026, 2, 15))
        self.assertEqual(totals["payroll"], Decimal("2000.00"))
        self.assertEqual(totals["deposits"], Decimal("0"))
        self.assertEqual(
            totals["payroll"]
            + totals["deposits"]
            + totals["refunds"]
            + totals["transfers_in"]
            + totals["other_credits"],
            totals["total_credits"],
        )


class TestMovementMatching(unittest.TestCase):
    def test_autopay_and_reason_for_uncovered_destination(self) -> None:
        result = _two_month_result()
        by_desc = {m.description[:20]: m for m in result.movements}
        card = by_desc["CAPITAL ONE CRCARDPM"]
        self.assertTrue(card.matched)
        self.assertEqual(card.basis, "autopay")
        outside = by_desc["111111 WEB XFER TO C"]
        self.assertFalse(outside.matched)
        self.assertIn("****2136", outside.unmatched_reason)
        self.assertIn("not covered", outside.unmatched_reason)

    def test_amount_date_fallback_recategorizes_teller_transfer(self) -> None:
        savings = make_stmt(
            "06/30/2026",
            "XXXXXXXXXXX3608",
            "410.00",
            "400.00",
            [make_tx("06/24/2026", "MISCELLANEOUS DEBIT", "10.00", False, "400.00", "Other")],
            account_type="Savings",
        )
        checking = make_stmt(
            "06/26/2026",
            "XXXXXXXXXXX6781",
            "75.28",
            "85.28",
            [make_tx("06/24/2026", "DEPOSIT", "10.00", True, "85.28", "Deposit")],
        )
        result = consolidate([savings, checking])
        self.assertEqual(len(result.movements), 1)
        m = result.movements[0]
        self.assertTrue(m.matched)
        self.assertEqual(m.basis, "amount+date")
        self.assertEqual(savings.transactions[0].category, "Transfers")
        self.assertEqual(checking.transactions[0].category, "Transfers")

    def test_fallback_never_pairs_with_a_different_named_destination(self) -> None:
        checking = make_stmt(
            "02/26/2026",
            "XXXXXXXXXXX6781",
            "200.00",
            "100.00",
            [
                make_tx(
                    "02/03/2026",
                    "130390 WEB XFER TO CLASSIC BUSINESS XXXXXX2136 2/03/26",
                    "50.00",
                    False,
                    "150.00",
                    "Transfers",
                ),
                make_tx(
                    "02/03/2026",
                    "130893 WEB XFER TO REGULAR SAVINGS XXXXXX3608 2/03/26",
                    "50.00",
                    False,
                    "100.00",
                    "Transfers",
                ),
            ],
        )
        savings = make_stmt(
            "02/27/2026",
            "XXXXXXXXXXX3608",
            "0.00",
            "50.00",
            [
                make_tx(
                    "02/03/2026",
                    "130893 WEB XFER FROM BASIC CHECKING XXXXXX6781 2/03/26",
                    "50.00",
                    True,
                    "50.00",
                    "Transfers",
                )
            ],
            account_type="Savings",
        )
        result = consolidate([checking, savings])
        by_ref = {m.description[:6]: m for m in result.movements}
        self.assertTrue(by_ref["130893"].matched)
        self.assertEqual(by_ref["130893"].basis, "reference")
        self.assertFalse(by_ref["130390"].matched)


class TestSubscriptionsChecksDebt(unittest.TestCase):
    def setUp(self) -> None:
        self.result = _two_month_result()
        self.as_of = datetime.date(2026, 2, 15)

    def test_subscription_review_groups_and_totals(self) -> None:
        from ledgersight.personal.insights import subscription_review

        rows = {r.payee: r for r in subscription_review(self.result, [], self.as_of)}
        self.assertEqual(rows["NETFLIX.COM"].charges, 2)
        self.assertEqual(rows["NETFLIX.COM"].total, Decimal("30.00"))
        self.assertEqual(rows["NETFLIX.COM"].cadence_days, 31)
        self.assertEqual(rows["NETFLIX.COM"].next_expected, datetime.date(2026, 3, 8))
        self.assertEqual(rows["HULU"].recent_monthly, Decimal("28.33"))  # 85 / 3 (within 90 days)

    def test_check_annotations(self) -> None:
        from ledgersight.personal.insights import apply_check_annotations, check_register, load_check_annotations

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "checks.yaml"
            path.write_text("checks:\n  5001:\n    payee: Landlord\n    category: Rent\n")
            ann = load_check_annotations(path)
        self.assertEqual(ann[5001]["payee"], "Landlord")
        statements = self.result.all_statements
        self.assertEqual(apply_check_annotations(statements, ann), 1)
        check_tx = [t for t in self.result.all_transactions if "CHECK" in t.description][0]
        self.assertEqual(check_tx.category, "Rent")
        self.assertIn("Landlord", check_tx.description)
        self.assertIn("CHECK # 5001", check_tx.description)
        rows = check_register(self.result, ann)
        self.assertEqual(rows[0].number, 5001)
        self.assertEqual(rows[0].payee, "Landlord")
        self.assertEqual(rows[0].purpose, "Rent")

    def test_debt_log_and_calendar(self) -> None:
        from ledgersight.personal.insights import bill_calendar, debt_log

        debts = debt_log(self.result)
        self.assertEqual(len(debts), 1)
        d = debts[0]
        self.assertEqual(d.balance, Decimal("260.00"))
        self.assertEqual(d.utilization, Decimal("20.0"))
        self.assertEqual(d.minimum_payment, Decimal("25.00"))
        self.assertTrue(d.autopay)
        self.assertEqual(d.last_payment_amount, Decimal("300.00"))
        items = bill_calendar(self.result, [], [], self.as_of, days=30)
        minimums = [i for i in items if i.kind == "Card minimum"]
        self.assertEqual(len(minimums), 1)
        self.assertEqual(minimums[0].when, datetime.date(2026, 2, 22))
        self.assertEqual(minimums[0].status, "Autopay detected")
