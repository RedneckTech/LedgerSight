"""Matplotlib chart generators for financial reports."""
from __future__ import annotations

import io
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

from ledgersight.categorizer import normalize_merchant
from ledgersight.models import Statement, Transaction

if TYPE_CHECKING:
    from business_financial_report import ProfitAndLoss, ProjectionResult


def _empty_chart_buf(msg: str = "No data available") -> io.BytesIO:
    """Create a chart with a text notice."""
    fig, ax = plt.subplots(figsize=(8, 3))
    ax.text(0.5, 0.5, msg, ha="center", va="center", fontsize=12, color="#888")
    ax.axis("off")
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def chart_revenue_vs_expenses_monthly(statements: list[Statement], pls: dict[str, ProfitAndLoss]) -> io.BytesIO:
    """Chart revenue vs expenses by month."""
    if not pls:
        return _empty_chart_buf("No P&L data for chart")
    keys = sorted(pls.keys())
    labels = [pls[k].label for k in keys]
    revs = [float(pls[k].total_revenue) for k in keys]
    exps = [float(pls[k].total_direct_costs + pls[k].total_operating_expenses) for k in keys]

    fig, ax = plt.subplots(figsize=(12, 7))
    x = range(len(labels))
    w = 0.35
    ax.bar([i - w / 2 for i in x], revs, w, label="Revenue", color="#27ae60")
    ax.bar([i + w / 2 for i in x], exps, w, label="Expenses", color="#e74c3c")
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, fontsize=8, rotation=45, ha="right")
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"${v:,.0f}"))
    ax.set_ylabel("Amount ($)")
    ax.set_title("Revenue vs Expenses by Month", fontsize=11, fontweight="bold")
    ax.legend()
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def chart_profit_monthly(pls: dict[str, ProfitAndLoss]) -> io.BytesIO:
    """Chart gross and net profit by month."""
    if not pls:
        return _empty_chart_buf("No P&L data for chart")
    keys = sorted(pls.keys())
    labels = [pls[k].label for k in keys]
    gp = [float(pls[k].gross_profit) for k in keys]
    np_vals = [float(pls[k].net_profit) for k in keys]

    fig, ax = plt.subplots(figsize=(12, 7))
    x = range(len(labels))
    ax.plot(x, gp, "o-", label="Gross Profit", color="#27ae60", linewidth=2)
    ax.plot(x, np_vals, "s-", label="Net Profit", color="#2980b9", linewidth=2)
    ax.axhline(y=0, color="#e74c3c", linewidth=0.5, linestyle="--", alpha=0.5)
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, fontsize=8, rotation=45, ha="right")
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"${v:,.0f}"))
    ax.set_title("Gross & Net Profit by Month", fontsize=11, fontweight="bold")
    ax.legend()
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def chart_net_cash_flow(statements: list[Statement]) -> io.BytesIO:
    """Chart monthly net cash flow."""
    if not statements:
        return _empty_chart_buf("No statement data for chart")
    months = [s.month_label for s in statements]
    flows = [
        float(s.total_credits - s.total_debits) for s in statements
    ]

    fig, ax = plt.subplots(figsize=(12, 7))
    x = range(len(months))
    colors = ["#27ae60" if f >= 0 else "#e74c3c" for f in flows]
    ax.bar(x, flows, color=colors)
    ax.axhline(y=0, color="black", linewidth=0.5)
    ax.set_xticks(list(x))
    ax.set_xticklabels(months, fontsize=8, rotation=45, ha="right")
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"${v:,.0f}"))
    ax.set_title("Monthly Net Cash Flow", fontsize=11, fontweight="bold")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def chart_balance_trend(statements: list[Statement]) -> io.BytesIO:
    """Chart bank balance trend."""
    if not statements:
        return _empty_chart_buf("No statement data for chart")
    all_bals: list[tuple[date, float]] = []
    for s in statements:
        for db in s.daily_balances:
            dt = datetime.strptime(db["date"], "%m/%d/%Y").date()
            all_bals.append((dt, float(db["balance"])))
    if not all_bals:
        return _empty_chart_buf("No daily balance data")

    all_bals.sort(key=lambda x: x[0])
    dates = [b[0] for b in all_bals]
    vals = [b[1] for b in all_bals]

    fig, ax = plt.subplots(figsize=(12, 7))
    ax.plot(dates, vals, color="#2c3e50", linewidth=1.4, alpha=0.85)
    ax.fill_between(dates, 0, vals, alpha=0.08, color="#2c3e50")
    ax.axhline(y=0, color="#e74c3c", linewidth=0.5, linestyle="--", alpha=0.5)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"${v:,.0f}"))
    ax.set_title("Bank Balance Trend", fontsize=11, fontweight="bold")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.autofmt_xdate(rotation=30, ha="right")
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def chart_expenses_by_category(pl: ProfitAndLoss) -> io.BytesIO:
    """Pie chart of expenses by category."""
    exp_by_cat: dict[str, float] = {}
    for cat, val in pl.direct_costs.items():
        if val > 0:
            exp_by_cat[f"[COGS] {cat}"] = float(val)
    for cat, val in pl.operating_expenses.items():
        if val > 0:
            exp_by_cat[cat] = float(val)
    for cat, val in pl.other_expense.items():
        if val > 0:
            exp_by_cat[cat] = float(val)

    if not exp_by_cat:
        return _empty_chart_buf("No expense data")

    sorted_items = sorted(exp_by_cat.items(), key=lambda x: x[1], reverse=True)
    labels = [k for k, _ in sorted_items[:10]]
    sizes = [v for _, v in sorted_items[:10]]
    if len(sorted_items) > 10:
        other = sum(v for _, v in sorted_items[10:])
        if other > 0:
            labels.append("Other")
            sizes.append(other)

    colors = plt.cm.tab20([i / len(labels) for i in range(len(labels))])
    fig, ax = plt.subplots(figsize=(7, 5))
    wedges, texts, autotexts = ax.pie(
        sizes, labels=None, autopct="%1.1f%%",
        startangle=140, colors=colors,
    )
    for t in autotexts:
        t.set_fontsize(7)
    legend_labels = [f"{l} (${s:,.0f})" for l, s in zip(labels, sizes)]
    ax.legend(wedges, legend_labels, title="Expenses", loc="center left",
              bbox_to_anchor=(1, 0.5), fontsize=7)
    ax.set_title("Expenses by Category", fontsize=11, fontweight="bold")
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def chart_revenue_by_category(pl: ProfitAndLoss) -> io.BytesIO:
    """Pie chart of revenue by category."""
    rev_data = {k: float(v) for k, v in pl.revenue.items() if v > 0}
    for k, v in pl.other_income.items():
        rev_data[k] = float(v)

    if not rev_data:
        return _empty_chart_buf("No revenue data")

    sorted_items = sorted(rev_data.items(), key=lambda x: x[1], reverse=True)
    labels = [k for k, _ in sorted_items[:8]]
    sizes = [v for _, v in sorted_items[:8]]

    colors = plt.cm.Set2([i / max(len(labels), 1) for i in range(len(labels))])
    fig, ax = plt.subplots(figsize=(6, 5))
    wedges, _, autotexts = ax.pie(
        sizes, labels=None, autopct="%1.1f%%",
        startangle=140, colors=colors,
    )
    for t in autotexts:
        t.set_fontsize(7)
    legend_labels = [f"{l} (${s:,.0f})" for l, s in zip(labels, sizes)]
    ax.legend(wedges, legend_labels, title="Revenue", loc="center left",
              bbox_to_anchor=(1, 0.5), fontsize=7)
    ax.set_title("Revenue by Category", fontsize=11, fontweight="bold")
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def chart_cost_by_month(pls: dict[str, ProfitAndLoss]) -> io.BytesIO:
    """Chart direct costs and operating expenses by month."""
    if not pls:
        return _empty_chart_buf("No P&L data")
    keys = sorted(pls.keys())
    labels = [pls[k].label for k in keys]
    dc = [float(pls[k].total_direct_costs) for k in keys]
    oe = [float(pls[k].total_operating_expenses) for k in keys]

    fig, ax = plt.subplots(figsize=(12, 7))
    x = range(len(labels))
    w = 0.35
    ax.bar([i - w / 2 for i in x], dc, w, label="Direct Costs (COGS)", color="#e74c3c")
    ax.bar([i + w / 2 for i in x], oe, w, label="Operating Expenses", color="#d35400")
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, fontsize=8, rotation=45, ha="right")
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"${v:,.0f}"))
    ax.set_title("Direct Costs & Operating Expenses by Month", fontsize=11, fontweight="bold")
    ax.legend(fontsize=8)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def chart_projection(
    historical_rev: list[Decimal],
    projections: list[ProjectionResult],
    historical_labels: list[str],
) -> io.BytesIO:
    """Chart actual vs projected revenue."""
    if not projections:
        return _empty_chart_buf("No projection data")

    fig, ax = plt.subplots(figsize=(12, 7))
    x_hist = range(len(historical_rev))
    if historical_rev:
        ax.bar(x_hist, [float(r) for r in historical_rev],
               label="Actual Revenue", color="#27ae60", alpha=0.7)

    offset = len(historical_rev)
    colors = {"conservative": "#e74c3c", "base": "#2980b9", "growth": "#8e44ad"}
    linestyles = {"conservative": "--", "base": "-", "growth": ":"}

    for pr in projections:
        x_proj = range(offset, offset + pr.months)
        clr = colors.get(pr.scenario, "#888888")
        ls = linestyles.get(pr.scenario, "-")
        ax.plot(x_proj, [float(r) for r in pr.monthly_revenue],
                color=clr, linestyle=ls, linewidth=2,
                label=f"{pr.scenario.title()} Projection")

    # Divide line
    if historical_rev and projections:
        ax.axvline(x=offset - 0.5, color="#888", linestyle=":", linewidth=1)
        ax.text(offset - 0.5, ax.get_ylim()[1] * 0.95, "Projection ->",
                ha="right", fontsize=8, color="#888")

    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"${v:,.0f}"))
    ax.set_title("Revenue: Actual vs Projected", fontsize=11, fontweight="bold")
    ax.legend(fontsize=8)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def chart_top_vendors(
    statements: list[Statement], top_n: int = 10,
) -> io.BytesIO:
    """Bar chart of top vendors by expense."""
    vendor_totals: dict[str, float] = defaultdict(float)
    for s in statements:
        for tx in s.transactions:
            if not tx.is_credit and tx.include_in_pnl:
                merchant = tx.merchant or normalize_merchant(tx.description)
                vendor_totals[merchant] += float(tx.amount)

    sorted_v = sorted(vendor_totals.items(), key=lambda x: x[1], reverse=True)[:top_n]
    if not sorted_v:
        return _empty_chart_buf("No vendor data")

    names = [v[0][:25] for v in sorted_v]
    vals = [v[1] for v in sorted_v]

    fig, ax = plt.subplots(figsize=(10, 6))
    ax.barh(range(len(names)), vals, color="#d35400")
    ax.set_yticks(range(len(names)))
    ax.set_yticklabels(names, fontsize=8)
    ax.invert_yaxis()
    ax.set_xlabel("Total ($)")
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"${v:,.0f}"))
    ax.set_title("Top Vendors by Expense", fontsize=11, fontweight="bold")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def chart_top_revenue_sources(
    statements: list[Statement], top_n: int = 10,
) -> io.BytesIO:
    """Bar chart of top revenue sources."""
    source_totals: dict[str, float] = defaultdict(float)
    for s in statements:
        for tx in s.transactions:
            if tx.is_credit and tx.include_in_pnl:
                merchant = tx.merchant or normalize_merchant(tx.description)
                source_totals[merchant] += float(tx.amount)

    sorted_v = sorted(source_totals.items(), key=lambda x: x[1], reverse=True)[:top_n]
    if not sorted_v:
        return _empty_chart_buf("No revenue source data")

    names = [v[0][:25] for v in sorted_v]
    vals = [v[1] for v in sorted_v]

    fig, ax = plt.subplots(figsize=(10, 6))
    ax.barh(range(len(names)), vals, color="#27ae60")
    ax.set_yticks(range(len(names)))
    ax.set_yticklabels(names, fontsize=8)
    ax.invert_yaxis()
    ax.set_xlabel("Total ($)")
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"${v:,.0f}"))
    ax.set_title("Top Revenue Sources", fontsize=11, fontweight="bold")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf
