"""Personal report charts (matplotlib -> PNG byte buffers)."""

from __future__ import annotations

import io
from collections import defaultdict
from datetime import date, datetime

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

from ledgersight.personal.categorizer import CATEGORY_COLORS
from ledgersight.personal.consolidation import AccountLedger
from ledgersight.personal.models import Statement


def _empty_png_buf(msg: str = "No data available") -> io.BytesIO:
    """Create a chart with a text notice."""
    fig, ax = plt.subplots(figsize=(8, 3))
    ax.text(0.5, 0.5, msg, ha="center", va="center", fontsize=12, color="#888")
    ax.axis("off")
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def chart_credits_vs_debits(statements: list[Statement]) -> io.BytesIO:
    """Bar chart of credits vs debits by month."""
    months = [s.month_label for s in statements]
    credits = [float(s.total_credits) for s in statements]
    debits = [float(s.total_debits) for s in statements]

    fig, ax = plt.subplots(figsize=(12, 7))
    x = range(len(months))
    w = 0.35
    bars1 = ax.bar([i - w / 2 for i in x], credits, w, label="Credits", color="#27ae60")
    bars2 = ax.bar([i + w / 2 for i in x], debits, w, label="Debits", color="#e74c3c")

    for bar in bars1:
        h = bar.get_height()
        ax.text(
            bar.get_x() + bar.get_width() / 2.0,
            h + 10,
            f"${h:,.0f}",
            ha="center",
            va="bottom",
            fontsize=7,
        )
    for bar in bars2:
        h = bar.get_height()
        ax.text(
            bar.get_x() + bar.get_width() / 2.0,
            h + 10,
            f"${h:,.0f}",
            ha="center",
            va="bottom",
            fontsize=7,
        )

    ax.set_xticks(list(x))
    ax.set_xticklabels(months, fontsize=8)
    ax.set_ylabel("Amount ($)", fontsize=9)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"${v:,.0f}"))
    ax.legend(fontsize=8)
    ax.set_title("Credits vs Debits by Month", fontsize=11, fontweight="bold")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def chart_category_pie(statements: list[Statement]) -> io.BytesIO:
    """Pie chart of debits by category."""
    cat_totals: dict[str, float] = defaultdict(float)
    for s in statements:
        for tx in s.transactions:
            if not tx.is_credit:
                cat_totals[tx.category] += float(tx.amount)

    cat_totals = {k: v for k, v in cat_totals.items() if v > 0}
    if not cat_totals:
        return _empty_png_buf("No debit transactions")

    total = sum(cat_totals.values())
    threshold = total * 0.03

    main_items = [(k, v) for k, v in cat_totals.items() if v >= threshold]
    small_total = sum(v for k, v in cat_totals.items() if v < threshold)

    sorted_main = sorted(main_items, key=lambda x: x[1], reverse=True)
    labels = [k for k, _ in sorted_main]
    sizes = [v for _, v in sorted_main]

    if small_total > 0:
        labels.append("Other (<3% each)")
        sizes.append(small_total)

    colors = [CATEGORY_COLORS.get(label, "#bdc3c7") for label in labels]
    if small_total > 0:
        colors[-1] = "#bdc3c7"

    fig, ax = plt.subplots(figsize=(6, 4.5))
    wedges, texts, autotexts = ax.pie(
        sizes,
        labels=None,
        autopct="%1.1f%%",
        startangle=140,
        colors=colors,
        pctdistance=0.75,
    )
    for t in autotexts:
        t.set_fontsize(7)

    legend_labels = [f"{label}  (${s:,.0f})" for label, s in zip(labels, sizes)]
    ax.legend(
        wedges,
        legend_labels,
        title="Categories",
        loc="center left",
        bbox_to_anchor=(1, 0.5),
        fontsize=7,
        title_fontsize=8,
    )
    ax.set_title("Debits by Category", fontsize=11, fontweight="bold")
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def chart_weekly_balance(statements: list[Statement]) -> io.BytesIO:
    """Weekly average balance, year to date."""
    week_bals: dict[tuple[int, int], list[float]] = {}
    for s in statements:
        for db in s.daily_balances:
            dt = datetime.strptime(db["date"], "%m/%d/%Y")
            iso = dt.isocalendar()
            key = (iso[0], iso[1])
            week_bals.setdefault(key, []).append(float(db["balance"]))

    if not week_bals:
        return _empty_png_buf("No daily balance data")

    points = sorted((k, sum(bals) / len(bals)) for k, bals in week_bals.items())
    week_keys = [p[0] for p in points]
    balances = [p[1] for p in points]
    x_labels = [f"{y}-W{wk:02d}" for y, wk in week_keys]
    positions = list(range(len(points)))

    fig, ax = plt.subplots(figsize=(12, 7))
    ax.plot(
        positions,
        balances,
        color="#2c3e50",
        linewidth=1.6,
        marker="o",
        markersize=3,
        alpha=0.85,
    )
    ax.fill_between(positions, 0, balances, alpha=0.08, color="#2c3e50")
    ax.axhline(y=0, color="#e74c3c", linewidth=0.5, linestyle="--", alpha=0.5)
    step = max(1, len(positions) // 15)
    shown_positions = positions[::step]
    ax.set_xticks(shown_positions)
    ax.set_xticklabels(
        [x_labels[i] for i in shown_positions],
        rotation=45,
        ha="right",
        fontsize=8,
    )
    ax.set_ylabel("Average Weekly Balance ($)", fontsize=9)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"${v:,.0f}"))
    ax.set_title("Weekly Average Balance \u2013 Year to Date", fontsize=11, fontweight="bold")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.set_xlim(-0.5, len(points) - 0.5)
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def chart_daily_balance_single(stmt: Statement) -> io.BytesIO:
    """Daily balance line chart for a single statement."""
    if not stmt.daily_balances:
        return _empty_png_buf("No daily balance data")

    points = sorted((datetime.strptime(db["date"], "%m/%d/%Y"), float(db["balance"])) for db in stmt.daily_balances)
    date_objs = [p[0] for p in points]
    balances = [p[1] for p in points]
    date_nums = mdates.date2num(date_objs)

    fig, ax = plt.subplots(figsize=(9, 3.2))
    ax.plot(
        date_nums,
        balances,
        color="#2c3e50",
        linewidth=1.4,
        marker="o",
        markersize=3,
        alpha=0.85,
    )
    ax.fill_between(date_nums, 0, balances, alpha=0.08, color="#2c3e50")
    ax.axhline(y=0, color="#e74c3c", linewidth=0.5, linestyle="--", alpha=0.5)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
    ax.set_ylabel("Balance ($)", fontsize=9)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"${v:,.0f}"))
    ax.set_title(f"Daily Balance \u2013 {stmt.month_label}", fontsize=11, fontweight="bold")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.autofmt_xdate(rotation=30, ha="right")
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def chart_weekly_balance_ledgers(ledgers: list[AccountLedger]) -> io.BytesIO:
    """Weekly average balances: cash & savings, card balances owed, and net.

    Cash (checking + savings) and card debt are plotted as separate series
    instead of one meaningless "combined" total; a third line shows the net
    position (cash minus card debt). Each account's daily balance is rebuilt
    from its earliest statement, and a day only counts for a series when
    every account in that group has a statement covering it - weeks where an
    account is missing are drawn dotted.
    """
    from datetime import timedelta

    from ledgersight.personal.consolidation import _to_date, balance_asof, statement_windows

    if not ledgers:
        return _empty_png_buf("No ledger data")

    cash_ledgers = [led for led in ledgers if led.account_type != "Credit Card" and led.transactions]
    card_ledgers = [led for led in ledgers if led.account_type == "Credit Card" and led.transactions]
    windows = {led.account_number: statement_windows(led) for led in ledgers}

    def covered(led: AccountLedger, day: date) -> bool:
        return any(start <= day <= end for start, end in windows[led.account_number])

    all_days: list[date] = []
    starts = [_to_date(led.first_tx_date) for led in ledgers if led.transactions]
    ends = [_to_date(led.as_of or led.last_tx_date) for led in ledgers if led.transactions]
    if not starts:
        return _empty_png_buf("No daily balance data")
    day = min(starts)
    while day <= max(ends):
        all_days.append(day)
        day += timedelta(days=1)

    def series(group: list[AccountLedger]) -> tuple[dict[date, float], dict[date, bool]]:
        values: dict[date, float] = {}
        complete: dict[date, bool] = {}
        for d in all_days:
            active = [led for led in group if covered(led, d)]
            if not active:
                continue
            values[d] = sum(float(balance_asof(led, d)) for led in active)
            complete[d] = len(active) == len(group)
        return values, complete

    cash_vals, cash_ok = series(cash_ledgers)
    card_vals, card_ok = series(card_ledgers)
    if not cash_vals and not card_vals:
        return _empty_png_buf("No daily balance data")

    def weekly(
        values: dict[date, float], ok: dict[date, bool]
    ) -> tuple[list[tuple[int, int]], list[float], list[bool]]:
        buckets: dict[tuple[int, int], list[float]] = defaultdict(list)
        flags: dict[tuple[int, int], bool] = defaultdict(lambda: True)
        for d, v in values.items():
            iso = d.isocalendar()
            buckets[(iso[0], iso[1])].append(v)
            flags[(iso[0], iso[1])] &= ok[d]
        keys = sorted(buckets)
        return keys, [sum(buckets[k]) / len(buckets[k]) for k in keys], [flags[k] for k in keys]

    cash_keys, cash_avg, cash_flags = weekly(cash_vals, cash_ok)
    card_keys, card_avg, card_flags = weekly(card_vals, card_ok)
    all_keys = sorted(set(cash_keys) | set(card_keys))
    pos = {k: i for i, k in enumerate(all_keys)}
    x_labels = [f"{y}-W{wk:02d}" for y, wk in all_keys]

    fig, ax = plt.subplots(figsize=(12, 7))

    def draw(keys: list[tuple[int, int]], avg: list[float], flags: list[bool], color: str, label: str) -> None:
        if not keys:
            return
        xs = [pos[k] for k in keys]
        # solid where every account in the group is covered, dotted otherwise
        for i in range(len(xs)):
            if i == 0:
                continue
            style = "-" if flags[i] and flags[i - 1] else ":"
            ax.plot(xs[i - 1 : i + 1], avg[i - 1 : i + 1], color=color, linewidth=1.8, linestyle=style)
        ax.plot([], [], color=color, linewidth=1.8, label=label)
        ax.scatter(xs, avg, color=color, s=9, zorder=3)

    draw(cash_keys, cash_avg, cash_flags, "#2c3e50", "Cash & savings (checking + savings)")
    draw(card_keys, card_avg, card_flags, "#c0392b", "Card balances owed (Capital One)")
    net_keys = [k for k in all_keys if k in set(cash_keys) and k in set(card_keys)]
    if net_keys:
        cash_map = dict(zip(cash_keys, cash_avg))
        card_map = dict(zip(card_keys, card_avg))
        ok_map = {k: f for k, f in zip(cash_keys, cash_flags)}
        ok_map_card = {k: f for k, f in zip(card_keys, card_flags)}
        net_avg = [cash_map[k] - card_map[k] for k in net_keys]
        net_flags = [ok_map[k] and ok_map_card[k] for k in net_keys]
        draw(net_keys, net_avg, net_flags, "#7f8c8d", "Net position (cash \u2212 card debt)")
    ax.axhline(y=0, color="#999999", linewidth=0.6, linestyle="--", alpha=0.7)
    step = max(1, len(all_keys) // 15)
    shown = list(range(0, len(all_keys), step))
    ax.set_xticks(shown)
    ax.set_xticklabels([x_labels[i] for i in shown], rotation=45, ha="right", fontsize=8)
    ax.set_ylabel("Average weekly balance ($)", fontsize=9)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"${v:,.0f}"))
    ax.set_title("Weekly Average Balances \u2013 cash, card debt and net position", fontsize=11, fontweight="bold")
    ax.legend(loc="upper left", fontsize=8, frameon=False)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.set_xlim(-0.5, len(all_keys) - 0.5)
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def chart_daily_balance_ledger_month(
    ledger: AccountLedger,
    year: int,
    month: int,
    first_supported: date | None = None,
) -> io.BytesIO:
    """Daily balance line chart for a single account during a calendar month.

    ``first_supported`` is the first day of the month backed by statement
    coverage; the line starts there instead of at day 1 so unsupported
    dates are not silently drawn.
    """
    from datetime import date, timedelta

    from ledgersight.personal.consolidation import _to_date, balance_asof

    if not ledger.transactions:
        return _empty_png_buf("No daily balance data")

    start = date(year, month, 1)
    if month == 12:
        end = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        end = date(year, month + 1, 1) - timedelta(days=1)
    as_of = _to_date(ledger.as_of or ledger.last_tx_date)
    last_day = min(end, as_of)
    if first_supported is not None:
        start = max(start, first_supported)
    if start > last_day:
        return _empty_png_buf("No data for this month")

    day = start
    date_objs: list[date] = []
    balances: list[float] = []
    while day <= last_day:
        date_objs.append(day)
        balances.append(float(balance_asof(ledger, day)))
        day += timedelta(days=1)
    if not date_objs:
        return _empty_png_buf("No data for this month")

    title = f"Daily Balance \u2013 {datetime(year, month, 1):%B %Y} \u2013 {_label(ledger)}"
    if start != date(year, month, 1):
        title += f" \u2013 from {start:%m/%d}"
    date_nums = mdates.date2num(date_objs)
    fig, ax = plt.subplots(figsize=(9, 2.1))
    ax.plot(
        date_nums,
        balances,
        color="#2c3e50",
        linewidth=1.4,
        marker="o",
        markersize=3,
        alpha=0.85,
    )
    ax.fill_between(date_nums, 0, balances, alpha=0.08, color="#2c3e50")
    ax.axhline(y=0, color="#e74c3c", linewidth=0.5, linestyle="--", alpha=0.5)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
    ax.set_ylabel("Balance ($)", fontsize=9)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"${v:,.0f}"))
    ax.set_title(title, fontsize=11, fontweight="bold")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.autofmt_xdate(rotation=30, ha="right")
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def _label(ledger: AccountLedger) -> str:
    inst = ledger.institution or "Account"
    return f"{inst} ****{ledger.account_number[-4:]}" if ledger.account_number else inst


def chart_category_by_month(statements: list[Statement]) -> io.BytesIO:
    """Debits by category per month."""
    cat_totals: dict[str, float] = defaultdict(float)
    for s in statements:
        for tx in s.transactions:
            if not tx.is_credit:
                cat_totals[tx.category] += float(tx.amount)
    top_cats = sorted(cat_totals, key=lambda c: cat_totals[c], reverse=True)[:8]

    months = [s.month_label for s in statements]
    data: dict[str, list[float]] = {cat: [] for cat in top_cats}
    for s in statements:
        month_cats: dict[str, float] = defaultdict(float)
        for tx in s.transactions:
            if not tx.is_credit:
                month_cats[tx.category] += float(tx.amount)
        for cat in top_cats:
            data[cat].append(month_cats.get(cat, 0))

    if not top_cats:
        return _empty_png_buf("No debit transactions")

    fig, ax = plt.subplots(figsize=(12, 7))
    x = range(len(months))
    w = 0.8 / len(top_cats)
    colors = [CATEGORY_COLORS.get(c, "#bdc3c7") for c in top_cats]

    for idx, cat in enumerate(top_cats):
        offset = (idx - len(top_cats) / 2 + 0.5) * w
        vals = data[cat]
        ax.bar([i + offset for i in x], vals, w, label=cat, color=colors[idx])

    ax.set_xticks(list(x))
    ax.set_xticklabels(months, fontsize=8)
    ax.set_ylabel("Amount ($)", fontsize=9)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"${v:,.0f}"))
    ax.legend(fontsize=6, ncol=2)
    ax.set_title("Debits by Category per Month (Top 8 Categories)", fontsize=11, fontweight="bold")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf
