"""Cash-basis Profit & Loss calculation."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from ledgersight.categorizer import _EXPENSE_CATEGORIES, _INCOME_CATEGORIES, normalize_merchant
from ledgersight.models import Statement, Transaction
from ledgersight.parsers import safe_pct

_AMOUNT_TOLERANCE = Decimal("0.2")


@dataclass(frozen=True)
class ReversalAllocation:
    """A reversal credit matched to an original debit."""

    debit_idx: int
    amount: Decimal
    original_category: str


def _find_best_reversal_match(
    reversal_tx: Transaction,
    candidates: list[tuple[int, Transaction]],
) -> tuple[int, Transaction] | None:
    reversal_amount = reversal_tx.amount
    reversal_date = reversal_tx.date_obj
    best = None
    best_score = Decimal("-1")

    for idx, dt in candidates:
        if reversal_amount == 0 or dt.amount == 0:
            ratio = Decimal("0")
        else:
            ratio = min(reversal_amount, dt.amount) / max(reversal_amount, dt.amount)
        if reversal_amount < dt.amount and ratio < (Decimal("1") - _AMOUNT_TOLERANCE):
            continue
        delta_days = (reversal_date - dt.date_obj).days
        if delta_days < 0 or delta_days > 90:
            continue
        score = ratio - Decimal(delta_days) / Decimal("36500")
        if score > best_score:
            best_score = score
            best = (idx, dt)

    return best


def build_reversal_allocations(transactions: list[Transaction]) -> dict[int, ReversalAllocation]:
    """Match Payment Reversal credits to original debits across all periods.

    Each eligible reversal is paired with at most one prior debit by merchant
    name and amount. The returned mapping ``reversal_idx -> allocation`` lets
    every period P&L apply the reversal credit against the original debit's
    category in the reversal's own posting period, so monthly/quarterly P&Ls
    sum to the aggregate P&L.
    """
    debit_by_merchant: dict[str, list[tuple[int, Transaction]]] = {}
    for idx, tx in enumerate(transactions):
        if not tx.is_credit and not tx.is_transfer:
            m = normalize_merchant(tx.description)
            debit_by_merchant.setdefault(m, []).append((idx, tx))

    matched_debit_indices: set[int] = set()
    allocations: dict[int, ReversalAllocation] = {}

    for idx, tx in enumerate(transactions):
        if tx.business_category != "Payment Reversal" or not tx.is_credit:
            continue
        merchant = normalize_merchant(tx.description)
        candidates = [
            (dt_idx, dt) for dt_idx, dt in debit_by_merchant.get(merchant, []) if dt_idx not in matched_debit_indices
        ]
        best = _find_best_reversal_match(tx, candidates) if candidates else None
        if best is not None:
            best_idx, best_dt = best
            matched_debit_indices.add(best_idx)
            apply_amount = min(tx.amount, best_dt.amount)
            allocations[id(tx)] = ReversalAllocation(best_idx, apply_amount, best_dt.business_category)
    return allocations


class ProfitAndLoss:
    """Cash-basis Profit & Loss statement built from categorized transactions."""

    def __init__(self, label: str = ""):
        self.label = label
        self.revenue: dict[str, Decimal] = defaultdict(Decimal)
        self.direct_costs: dict[str, Decimal] = defaultdict(Decimal)
        self.operating_expenses: dict[str, Decimal] = defaultdict(Decimal)
        self.other_income: dict[str, Decimal] = defaultdict(Decimal)
        self.other_expense: dict[str, Decimal] = defaultdict(Decimal)
        # ---- Separate non-P&L buckets (each tracked individually) ----
        self.owner_contributions: Decimal = Decimal("0")
        self.owner_distributions: Decimal = Decimal("0")
        self.loan_proceeds: Decimal = Decimal("0")
        self.loan_principal_payments: Decimal = Decimal("0")
        self.payment_reversal_credits: Decimal = Decimal("0")
        self.payment_reversal_debits: Decimal = Decimal("0")
        self.fixed_asset_purchases: Decimal = Decimal("0")
        self.account_transfers_credits: Decimal = Decimal("0")
        self.account_transfers_debits: Decimal = Decimal("0")
        self.credit_card_transfers: Decimal = Decimal("0")
        self.uncategorized_non_pnl_credits: Decimal = Decimal("0")
        self.uncategorized_non_pnl_debits: Decimal = Decimal("0")

    @property
    def non_pnl_credits(self) -> Decimal:
        return (
            self.owner_contributions
            + self.loan_proceeds
            + self.payment_reversal_credits
            + self.account_transfers_credits
            + self.uncategorized_non_pnl_credits
        )

    @property
    def non_pnl_debits(self) -> Decimal:
        return (
            self.owner_distributions
            + self.loan_principal_payments
            + self.fixed_asset_purchases
            + self.account_transfers_debits
            + self.credit_card_transfers
            + self.uncategorized_non_pnl_debits
            + self.payment_reversal_debits
        )

    @property
    def total_revenue(self) -> Decimal:
        return sum(self.revenue.values(), Decimal("0"))

    @property
    def total_direct_costs(self) -> Decimal:
        return sum(self.direct_costs.values(), Decimal("0"))

    @property
    def gross_profit(self) -> Decimal:
        return self.total_revenue - self.total_direct_costs

    @property
    def gross_margin(self) -> str:
        return safe_pct(self.gross_profit, self.total_revenue)

    @property
    def total_operating_expenses(self) -> Decimal:
        return sum(self.operating_expenses.values(), Decimal("0"))

    @property
    def operating_profit(self) -> Decimal:
        return self.gross_profit - self.total_operating_expenses

    @property
    def operating_margin(self) -> str:
        return safe_pct(self.operating_profit, self.total_revenue)

    @property
    def total_other_income(self) -> Decimal:
        return sum(self.other_income.values(), Decimal("0"))

    @property
    def total_other_expense(self) -> Decimal:
        return sum(self.other_expense.values(), Decimal("0"))

    @property
    def net_profit(self) -> Decimal:
        return self.operating_profit + self.total_other_income - self.total_other_expense

    @property
    def net_margin(self) -> str:
        return safe_pct(self.net_profit, self.total_revenue)


_OTHER_INCOME_CATS = {"Interest Income", "Other Income"}
_OTHER_EXPENSE_CATS = {"Loan Interest"}
_DIRECT_COST_CATS = {
    "Fuel",
    "Freight and Shipping",
    "Subcontractors",
    "Direct Labor",
    "Materials and Supplies",
    "Equipment Rental",
    "Tolls and Scale Fees",
    "Other Direct Costs",
}
_INCOME_CAT_SET = set(_INCOME_CATEGORIES.keys()) - _OTHER_INCOME_CATS
_OP_EXPENSE_CATS = set(_EXPENSE_CATEGORIES.keys()) - _DIRECT_COST_CATS - _OTHER_EXPENSE_CATS


def _apply_reversal_to_pl(pl: ProfitAndLoss, allocation: ReversalAllocation) -> None:
    """Reduce the original debit's category by an allocated reversal amount."""
    orig_cat = allocation.original_category
    apply_amount = allocation.amount
    if orig_cat in _DIRECT_COST_CATS:
        pl.direct_costs[orig_cat] -= apply_amount
    elif orig_cat in _OP_EXPENSE_CATS:
        pl.operating_expenses[orig_cat] -= apply_amount
    elif orig_cat in _INCOME_CAT_SET:
        pl.revenue[orig_cat] -= apply_amount
    else:
        pl.payment_reversal_credits += apply_amount


def build_pl(
    transactions: list[Transaction],
    label: str = "",
    reversal_allocations: dict[int, ReversalAllocation] | None = None,
) -> ProfitAndLoss:
    """Build a P&L from a list of categorized transactions.

    Non-P&L transactions are routed to separate accounting buckets
    (owner, loan, transfers, fixed assets) rather than combined into
    generic credit/debit totals.

    Payment reversals are matched against original debits by merchant
    name. When ``reversal_allocations`` is provided, the reversal credit
    reduces the original debit's category in the reversal's own posting
    period, so aggregate, monthly, and quarterly P&Ls stay consistent.
    """
    if reversal_allocations is None:
        reversal_allocations = build_reversal_allocations(transactions)

    pl = ProfitAndLoss(label=label)
    for tx in transactions:
        cat = tx.business_category

        # ---- Transfers ----
        if tx.is_transfer:
            if tx.is_credit:
                pl.account_transfers_credits += tx.amount
            else:
                if cat == "Credit Card Payment":
                    pl.credit_card_transfers += tx.amount
                else:
                    pl.account_transfers_debits += tx.amount
            continue

        # ---- Owner-related ----
        if tx.is_owner_related:
            if tx.is_credit:
                pl.owner_contributions += tx.amount
            else:
                pl.owner_distributions += tx.amount
            continue

        # ---- Loan proceeds / principal (excluded from P&L) ----
        if tx.is_loan and not tx.include_in_pnl:
            if tx.is_credit:
                pl.loan_proceeds += tx.amount
            else:
                pl.loan_principal_payments += tx.amount
            continue

        # ---- Payment reversals — apply in the reversal's own period ----
        if cat == "Payment Reversal" and tx.is_credit:
            alloc = reversal_allocations.get(id(tx))
            if alloc is not None:
                _apply_reversal_to_pl(pl, alloc)
                remainder = tx.amount - alloc.amount
                if remainder > 0:
                    pl.payment_reversal_credits += remainder
            else:
                pl.payment_reversal_credits += tx.amount
            continue
        elif cat == "Payment Reversal":
            pl.payment_reversal_debits += tx.amount
            continue

        # ---- Fixed assets ----
        if tx.is_fixed_asset:
            pl.fixed_asset_purchases += tx.amount
            continue

        # ---- Other explicitly excluded ----
        if not tx.include_in_pnl:
            if tx.is_credit:
                pl.uncategorized_non_pnl_credits += tx.amount
            else:
                pl.uncategorized_non_pnl_debits += tx.amount
            continue

        # ---- P&L classification ----
        if cat in _INCOME_CAT_SET:
            pl.revenue[cat] += tx.amount if tx.is_credit else -tx.amount
        elif cat in _DIRECT_COST_CATS:
            # Credits (refunds, rebates, returns) reduce the expense
            signed = tx.amount if not tx.is_credit else -tx.amount
            pl.direct_costs[cat] += signed
        elif cat in _OP_EXPENSE_CATS:
            signed = tx.amount if not tx.is_credit else -tx.amount
            pl.operating_expenses[cat] += signed
        elif cat in _OTHER_INCOME_CATS:
            pl.other_income[cat] += tx.amount if tx.is_credit else -tx.amount
        elif cat in _OTHER_EXPENSE_CATS:
            signed = tx.amount if not tx.is_credit else -tx.amount
            pl.other_expense[cat] += signed
        else:
            # Put uncategorized expense-like debits in operating expenses
            if not tx.is_credit:
                pl.operating_expenses[cat] += tx.amount
            else:
                pl.revenue[cat] += tx.amount

    return pl


def build_pl_by_period(
    statements: list[Statement],
    period_filter=None,
) -> ProfitAndLoss:
    """Build a P&L from statements, optionally filtered by a date filter function."""
    tx_list: list[Transaction] = []
    for stmt in statements:
        for tx in stmt.transactions:
            if period_filter is None or period_filter(tx):
                tx_list.append(tx)
    allocations = build_reversal_allocations(tx_list)
    return build_pl(tx_list, reversal_allocations=allocations)


def build_monthly_pls(statements: list[Statement]) -> dict[str, ProfitAndLoss]:
    """Build monthly P&L statements keyed by 'YYYY-MM'."""
    all_tx = [tx for stmt in statements for tx in stmt.transactions]
    allocations = build_reversal_allocations(all_tx)
    monthly: defaultdict[str, list[Transaction]] = defaultdict(list)
    for tx in all_tx:
        key = datetime.strptime(tx.post_date, "%m/%d/%Y").strftime("%Y-%m")
        monthly[key].append(tx)
    result = {}
    for key, txs in sorted(monthly.items()):
        yr, mo = key.split("-")
        label = datetime(int(yr), int(mo), 1).strftime("%B %Y")
        result[key] = build_pl(txs, label=label, reversal_allocations=allocations)
    return result


def build_quarterly_pls(
    statements: list[Statement],
    fiscal_year_start: int = 1,
) -> dict[tuple[int, int], ProfitAndLoss]:
    """Build quarterly P&L statements keyed by (fiscal_year, quarter).

    *fiscal_year_start* is the calendar month (1-12) that begins the fiscal year.
    """
    all_tx = [tx for stmt in statements for tx in stmt.transactions]
    allocations = build_reversal_allocations(all_tx)
    quarterly: dict[tuple[int, int], list[Transaction]] = defaultdict(list)
    for tx in all_tx:
        parts = tx.post_date.split("/")
        cal_month = int(parts[0])
        cal_year = int(parts[2])
        # Determine fiscal year and fiscal quarter
        if cal_month >= fiscal_year_start:
            fy = cal_year
            fy_month = cal_month - fiscal_year_start + 1
        else:
            fy = cal_year - 1
            fy_month = cal_month + (12 - fiscal_year_start + 1)
        fq = (fy_month - 1) // 3 + 1
        quarterly[(fy, fq)].append(tx)
    result: dict[tuple[int, int], ProfitAndLoss] = {}
    for (fy, fq), txs in sorted(quarterly.items()):
        result[(fy, fq)] = build_pl(txs, label=f"FY{fy} Q{fq}", reversal_allocations=allocations)
    return result
