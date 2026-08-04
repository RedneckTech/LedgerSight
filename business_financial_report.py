#!/usr/bin/env python3
"""Business Financial Report Generator — compatibility wrapper.

This module exists for backward compatibility with existing scripts and tests.
New code should import from ledgersight.* directly.
"""

from __future__ import annotations

from ledgersight.business.cli import main
from ledgersight.business.kpis import FinancialKPIs, calculate_kpis
from ledgersight.business.periods import _fiscal_quarter_months, get_period_months, get_quarter_periods
from ledgersight.business.pl import (
    ProfitAndLoss,
    build_pl,
    build_monthly_pls,
    build_pl_by_period,
    build_quarterly_pls,
)
from ledgersight.business.projections import ProjectionEngine, ProjectionResult
from ledgersight.business.report import build_report
from ledgersight.categorizer import (
    TransactionCategorizer,
    build_default_rules,
    normalize_merchant,
)
from ledgersight.charts import (
    _empty_chart_buf,
    chart_balance_trend,
    chart_cost_by_month,
    chart_expenses_by_category,
    chart_net_cash_flow,
    chart_profit_monthly,
    chart_projection,
    chart_revenue_by_category,
    chart_revenue_vs_expenses_monthly,
    chart_top_revenue_sources,
    chart_top_vendors,
)
from ledgersight.config import EXAMPLE_TOML, generate_example_config, load_config
from ledgersight.constants import (
    MONEY_RE,
    RC_TOLERANCE,
    VALID_DEDUCTIBILITY,
    VALID_ENTITY_TYPES,
    _ACTIVITY_SECTION_ENDINGS,
    _CPA_DISCLAIMER,
    _DEFAULT_CONFIG,
    _PNL_DISCLAIMER,
    _QUARTER_MONTHS,
)
from ledgersight.exports import CSVExporter, get_fixed_asset_candidates
from ledgersight.models import (
    BusinessConfig,
    CategoryRule,
    FinancialPeriod,
    ReconciliationResult,
    Statement,
    Transaction,
)
from ledgersight.parsers import (
    _clean_description,
    _is_page_artifact,
    _is_section_ending,
    _parse_daily_date,
    check_pdftotext,
    extract_text,
    file_hash,
    find_pdfs,
    fmt_dollar,
    fmt_dollar_plain,
    parse_amount,
    parse_statement,
    safe_div,
    safe_float,
    safe_pct,
)
from ledgersight.pdf_renderer import ReportPDF
from ledgersight.reconciliation import reconcile_all, reconcile_statement


if __name__ == "__main__":
    main()
