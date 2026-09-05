#!/usr/bin/env python3
"""Personal Financial Report Generator — compatibility wrapper.

This module exists for backward compatibility with existing scripts and
workflows. New code should use the integrated profile mode instead:

    ledgersight --cli --profile personal ...
    ledgersight --profile personal       (TUI)

The underlying implementation lives in ledgersight.personal.*.
"""

from __future__ import annotations

from ledgersight.parsers import (  # noqa: F401
    extract_text,
    find_pdfs,
    fmt_dollar,
    parse_amount,
)
from ledgersight.personal.categorizer import (
    BANK_FEE_PRECEDENCE_PATTERNS,  # noqa: F401
    CATEGORY_COLORS,  # noqa: F401
    CATEGORY_RULES,  # noqa: F401
    SUBSCRIPTION_PRECEDENCE_PATTERNS,  # noqa: F401
    categorize,  # noqa: F401
    categorize_transactions,  # noqa: F401
)
from ledgersight.personal.charts import (
    chart_category_by_month,  # noqa: F401
    chart_category_pie,  # noqa: F401
    chart_credits_vs_debits,  # noqa: F401
    chart_daily_balance_single,  # noqa: F401
    chart_weekly_balance,  # noqa: F401
)
from ledgersight.personal.cli import main
from ledgersight.personal.models import Statement, Transaction  # noqa: F401
from ledgersight.personal.parser import _is_page_artifact, parse_statement  # noqa: F401
from ledgersight.personal.report import (
    EXCLUDED_MERCHANT_CATS,  # noqa: F401
    _mask_desc,  # noqa: F401
    _reconcile_statements,  # noqa: F401
    _write_audit_csv,  # noqa: F401
    build_category_table_rows,  # noqa: F401
    build_monthly_table_rows,  # noqa: F401
    build_top_merchants,  # noqa: F401
    generate_report,  # noqa: F401
)

if __name__ == "__main__":
    main()
