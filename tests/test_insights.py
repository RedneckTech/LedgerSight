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
    category_spend_totals,
    dashboard_totals,
    detect_repeating_payments,
    estimate_income_groups,
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
        self.assertEqual(len(est), 1)
        self.assertEqual(est[0].event_date, datetime.date(2026, 4, 4))
        self.assertEqual(est[0].projected, Decimal("3884.51"))


def _rp(
    payee: str,
    income: bool,
    cadence: int,
    lo: str,
    hi: str,
    last: datetime.date,
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
