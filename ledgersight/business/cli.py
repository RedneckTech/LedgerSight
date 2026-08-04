"""Business report CLI (headless mode)."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from ledgersight.business.loader import load_statements
from ledgersight.business.periods import _fiscal_quarter_months
from ledgersight.business.report import build_report
from ledgersight.config import generate_example_config, load_config
from ledgersight.constants import _DEFAULT_CONFIG
from ledgersight.exceptions import LedgerSightError
from ledgersight.parsers import find_pdfs

logger = logging.getLogger("ledgersight.business.cli")

SCRIPT_DIR = Path.cwd()


def _run_self_tests() -> None:
    print("Run tests with: python -m pytest tests/ -v")
    sys.exit(0)


def main():
    parser = argparse.ArgumentParser(
        description="Business Financial Report Generator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 business_financial_report.py
  python3 business_financial_report.py --year 2026
  python3 business_financial_report.py --year 2026 --projections
  python3 business_financial_report.py --quarter 2 --year 2026
  python3 business_financial_report.py --mode cpa --year 2026
  python3 business_financial_report.py --audit --export-pl --export-cpa
  python3 business_financial_report.py --config lagoon_transport.toml
  python3 business_financial_report.py --init-config
  python3 business_financial_report.py --self-test
        """,
    )
    parser.add_argument("--config", type=str, default=None,
                        help=f"Path to TOML config (default: {_DEFAULT_CONFIG})")
    parser.add_argument("--init-config", action="store_true",
                        help="Generate example TOML configuration file")
    parser.add_argument("--force", action="store_true",
                        help="Overwrite existing config with --init-config")
    parser.add_argument("--business-name", type=str, default=None,
                        help="Override business name")
    parser.add_argument("--year", type=int,
                        help="Filter to a specific year")
    parser.add_argument("--month", type=int, choices=range(1, 13),
                        help="Filter to a specific month (1-12)")
    parser.add_argument("--quarter", type=int, choices=range(1, 5),
                        help="Filter to a specific quarter (1-4)")
    parser.add_argument("--start-date", type=str, default=None,
                        help="Filter from date YYYY-MM-DD")
    parser.add_argument("--end-date", type=str, default=None,
                        help="Filter to date YYYY-MM-DD")
    parser.add_argument("--mode", type=str,
                        choices=["combined", "monthly", "quarterly", "yearly", "cpa"],
                        default=None,
                        help="Report mode")
    parser.add_argument("-o", "--output", type=str, default=None,
                        help="Output PDF path")
    parser.add_argument("-d", "--directory", type=str, default=None,
                        help="Directory containing statement PDFs")
    parser.add_argument("--audit", action="store_true",
                        help="Export transaction audit CSV")
    parser.add_argument("--export-pl", action="store_true",
                        help="Export P&L CSV")
    parser.add_argument("--export-cpa", action="store_true",
                        help="Export CPA package CSV files")
    parser.add_argument("--export-category-template", action="store_true",
                        help="Export category template CSV for editing")
    parser.add_argument("--projections", action="store_true",
                        help="Include financial projections")
    parser.add_argument("--projection-months", type=int, default=None,
                        help="Number of months to project (overrides config)")
    parser.add_argument("--scenario", type=str,
                        choices=["conservative", "base", "growth", "all"],
                        default="all",
                        help="Projection scenario")
    parser.add_argument("--mask", action="store_true",
                        help="Mask personal information in output")
    parser.add_argument("--mask-ein", action="store_true",
                        help="Mask EIN in output")
    parser.add_argument("--allow-mismatch", action="store_true",
                        help="Generate report even if reconciliation fails")
    parser.add_argument("--allow-review-items", action="store_true",
                        help="Generate report even with uncategorized transactions")
    parser.add_argument("--strict", action="store_true",
                        help="Exit with error on any reconciliation warning")
    parser.add_argument("--verbose", action="store_true",
                        help="Verbose output")
    parser.add_argument("--overwrite", action="store_true",
                        help="Overwrite existing output files")
    parser.add_argument("--full-detail", action="store_true",
                        help="Include every transaction rather than 50 per month")
    parser.add_argument("--debug", action="store_true",
                        help="Debug output")
    parser.add_argument("--self-test", action="store_true",
                        help="Run built-in self-tests")
    args = parser.parse_args()

    log_level = logging.WARNING
    if args.verbose:
        log_level = logging.INFO
    if args.debug:
        log_level = logging.DEBUG
    logging.basicConfig(level=log_level, format="%(levelname)s: %(message)s")

    if args.self_test:
        _run_self_tests()
        return

    if args.init_config:
        cfg_path = Path(args.config or _DEFAULT_CONFIG)
        if not cfg_path.is_absolute():
            cfg_path = SCRIPT_DIR / cfg_path
        generate_example_config(cfg_path, force=args.force)
        return

    if args.config:
        config_path = Path(args.config)
    else:
        default_path = SCRIPT_DIR / _DEFAULT_CONFIG
        if default_path.exists():
            config_path = default_path
        else:
            candidates = sorted(
                SCRIPT_DIR.glob("business_report*.toml"),
                key=lambda p: p.stat().st_mtime, reverse=True,
            )
            config_path = candidates[0] if candidates else default_path
    if not config_path.is_absolute():
        config_path = SCRIPT_DIR / config_path
    config = load_config(config_path, was_explicit=args.config is not None, strict=True)

    if args.business_name:
        config.business_name = args.business_name
    if args.year:
        config.tax_year = args.year
    if args.mask:
        config.mask_ein = True
        config.mask_account = True
    elif args.mask_ein:
        config.mask_ein = True
    if args.projection_months:
        if not config.projection_config:
            config.projection_config = {}
        config.projection_config["projection_months"] = args.projection_months

    directory = Path(args.directory) if args.directory else SCRIPT_DIR
    pdf_files = find_pdfs(directory)
    if not pdf_files:
        logger.error("No PDF files found in %s", directory)
        sys.exit(1)

    load_result = load_statements(pdf_files)
    statements = load_result.statements
    for warning in load_result.warnings:
        logger.warning("%s", warning)
    for error in load_result.errors:
        logger.error("%s", error)

    if not statements:
        logger.error("No valid statements found.")
        sys.exit(1)

    if args.mode:
        mode = args.mode
    elif args.quarter:
        mode = "quarterly"
    elif args.month:
        mode = "monthly"
    elif args.year and not args.month and not args.quarter:
        mode = "combined"
    else:
        mode = "combined"

    if args.month and args.quarter:
        logger.error("--month and --quarter are incompatible.")
        sys.exit(1)
    if args.mode in ("monthly",) and not args.month:
        logger.error("--mode monthly requires --month.")
        sys.exit(1)

    target_year = args.year
    if args.month and not target_year:
        matching_years = sorted({
            s.year for s in statements if s.month == args.month
        })
        if not matching_years:
            logger.error("No statements found for month %s.", args.month)
            sys.exit(1)
        target_year = matching_years[-1]
        if len(matching_years) > 1:
            logger.info(
                "Note: --month without --year, using latest year %s "
                "(found: %s). Use --year to override.",
                target_year, matching_years,
            )

    if args.quarter and not target_year:
        fy_start = config.fiscal_year_start
        q_months = _fiscal_quarter_months(args.quarter, fy_start)
        from ledgersight.business.periods import statement_fiscal_year
        matching_fys = sorted({
            statement_fiscal_year(s, fy_start) for s in statements if s.month in q_months
        })
        if not matching_fys:
            logger.error("No statements found for quarter %s.", args.quarter)
            sys.exit(1)
        target_year = matching_fys[-1]
        if len(matching_fys) > 1:
            logger.info(
                "Note: --quarter without --year, using latest year %s "
                "(found: %s). Use --year to override.",
                target_year, matching_fys,
            )

    if args.output:
        output = args.output
    elif args.month:
        yr = target_year or statements[0].year
        output = f"business_financial_report_{args.month:02d}_{yr}.pdf"
    elif args.quarter:
        yr = target_year or statements[0].year
        output = f"business_financial_report_Q{args.quarter}_{yr}.pdf"
    elif args.year:
        output = f"business_financial_report_{args.year}.pdf"
    else:
        output = "business_financial_report.pdf"

    output_path = Path(output)
    if not output_path.is_absolute():
        output_path = SCRIPT_DIR / output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if output_path.exists() and not args.overwrite:
        logger.error(
            "Output file already exists: %s (use --overwrite to replace)",
            output_path,
        )
        sys.exit(1)

    audit_path = None
    pl_csv_path = None
    cpa_export_dir = None
    category_template_path = None

    base = output_path.stem
    out_dir = output_path.parent

    if args.audit:
        audit_path = out_dir / f"{base}_audit.csv"
    if args.export_pl:
        pl_csv_path = out_dir / f"{base}_pl.csv"
    if args.export_cpa:
        cpa_export_dir = out_dir / f"{base}_cpa"
    if args.export_category_template:
        category_template_path = out_dir / f"{base}_category_template.csv"

    try:
        _ = build_report(
            statements=statements,
            config=config,
            output_path=output_path,
            mode=mode,
            target_month=args.month,
            target_year=target_year,
            target_quarter=args.quarter,
            start_date_str=args.start_date,
            end_date_str=args.end_date,
            audit_path=audit_path,
            pl_csv_path=pl_csv_path,
            cpa_export_dir=cpa_export_dir,
            category_template_path=category_template_path,
            mask_personal=args.mask,
            allow_mismatch=args.allow_mismatch,
            scenario=args.scenario,
            strict=args.strict,
            full_detail=args.full_detail,
            do_projections=args.projections,
            allow_review_items=args.allow_review_items,
            overwrite=args.overwrite,
        )
    except LedgerSightError as exc:
        logger.error("%s", exc)
        sys.exit(1)
