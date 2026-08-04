"""Bank statement reconciliation."""
from __future__ import annotations
import logging
from decimal import Decimal

from ledgersight.constants import RC_TOLERANCE
from ledgersight.models import ReconciliationResult, Statement

logger = logging.getLogger("ledgersight.reconciliation")


def reconcile_statement(stmt: Statement, tolerance: Decimal = RC_TOLERANCE) -> ReconciliationResult:
    """Reconcile a single statement's parsed data against reported figures."""
    parsed_credits = sum(
        (tx.amount for tx in stmt.transactions if tx.is_credit),
        Decimal("0"),
    )
    parsed_debits = sum(
        (tx.amount for tx in stmt.transactions if not tx.is_credit),
        Decimal("0"),
    )
    parsed_credit_count = sum(1 for tx in stmt.transactions if tx.is_credit)
    parsed_debit_count = sum(1 for tx in stmt.transactions if not tx.is_credit)

    calculated_ending = stmt.beginning_balance + parsed_credits - parsed_debits
    balance_ok = abs(calculated_ending - stmt.ending_balance) <= tolerance

    credits_ok = (
        abs(parsed_credits - stmt.total_credits) <= tolerance
        and parsed_credit_count == stmt.credit_count
    )
    debits_ok = (
        abs(parsed_debits - stmt.total_debits) <= tolerance
        and parsed_debit_count == stmt.debit_count
    )

    warnings: list[str] = []
    if not credits_ok:
        warnings.append(
            f"Credit mismatch: parsed {parsed_credit_count}/{parsed_credits} vs "
            f"expected {stmt.credit_count}/{stmt.total_credits}"
        )
    if not debits_ok:
        warnings.append(
            f"Debit mismatch: parsed {parsed_debit_count}/{parsed_debits} vs "
            f"expected {stmt.debit_count}/{stmt.total_debits}"
        )
    if not balance_ok:
        warnings.append(
            f"Balance mismatch: calculated={calculated_ending} vs ending={stmt.ending_balance}"
        )

    return ReconciliationResult(
        statement_label=stmt.month_label,
        passed=credits_ok and debits_ok and balance_ok,
        parsed_credit_count=parsed_credit_count,
        expected_credit_count=stmt.credit_count,
        parsed_debit_count=parsed_debit_count,
        expected_debit_count=stmt.debit_count,
        parsed_credit_total=parsed_credits,
        expected_credit_total=stmt.total_credits,
        parsed_debit_total=parsed_debits,
        expected_debit_total=stmt.total_debits,
        beginning_balance=stmt.beginning_balance,
        ending_balance=stmt.ending_balance,
        calculated_ending=calculated_ending,
        balance_ok=balance_ok,
        warnings=warnings,
    )


def reconcile_all(
    statements: list[Statement],
    tolerance: Decimal = RC_TOLERANCE,
    allow_mismatch: bool = False,
) -> tuple[list[ReconciliationResult], bool, bool]:
    """Reconcile all statements and check continuity.

    Returns:
        (results, all_reconciled, forced_generation)
        *all_reconciled* is true only when every statement genuinely passes.
        *forced_generation* is true when output is permitted despite failures.
    """
    results = []
    all_reconciled = True
    for stmt in statements:
        result = reconcile_statement(stmt, tolerance)
        results.append(result)
        if not result.passed:
            all_reconciled = False
            logger.warning(
                "Reconciliation failed for %s: %s",
                stmt.month_label, "; ".join(result.warnings),
            )

    # Check continuity between statements
    for i in range(1, len(statements)):
        prev_ending = statements[i - 1].ending_balance
        curr_beginning = statements[i].beginning_balance
        if abs(prev_ending - curr_beginning) > tolerance:
            results.append(ReconciliationResult(
                statement_label=f"Continuity: {statements[i-1].month_label} -> {statements[i].month_label}",
                passed=False,
                parsed_credit_count=0, expected_credit_count=0,
                parsed_debit_count=0, expected_debit_count=0,
                parsed_credit_total=Decimal("0"), expected_credit_total=Decimal("0"),
                parsed_debit_total=Decimal("0"), expected_debit_total=Decimal("0"),
                beginning_balance=prev_ending,
                ending_balance=curr_beginning,
                calculated_ending=prev_ending,
                balance_ok=False,
                warnings=[f"Balance discontinuity: prev ending={prev_ending}, curr beginning={curr_beginning}, gap={curr_beginning - prev_ending}"],
            ))
            all_reconciled = False

    forced = allow_mismatch and not all_reconciled
    return results, all_reconciled, forced
