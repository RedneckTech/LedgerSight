"""Personal report CLI (headless mode)."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from ledgersight.parsers import extract_text
from ledgersight.personal.parser import parse_personal_statement
from ledgersight.personal.report import generate_report

SCRIPT_DIR = Path.cwd()

EXCLUDED_REPORT_PATTERNS = ("personal_financial_report", "business_financial_report")


def _find_statement_pdfs(directory: Path) -> list[Path]:
    """Recursively find statement PDFs, excluding generated reports."""
    pdfs = []
    for p in sorted(directory.rglob("*.pdf")):
        name_lower = p.name.lower()
        if any(pat in name_lower for pat in EXCLUDED_REPORT_PATTERNS):
            continue
        pdfs.append(p)
    return pdfs


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate PDF personal financial reports from First Interstate and Capital One statement PDFs."
    )
    parser.add_argument(
        "--year",
        type=int,
        help="Filter to a specific year (e.g. 2026)",
    )
    parser.add_argument(
        "--month",
        type=int,
        choices=range(1, 13),
        metavar="1-12",
        help="Filter to a specific month (1-12). Requires --year or infers from statements.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=str,
        default=None,
        help="Output PDF path (default: personal_financial_report_<year>.pdf "
        "or personal_financial_report_<month>_<year>.pdf)",
    )
    parser.add_argument(
        "-d",
        "--directory",
        type=str,
        default=str(SCRIPT_DIR),
        help="Directory containing statement PDFs (default: current directory)",
    )
    parser.add_argument(
        "--mode",
        type=str,
        choices=["combined", "monthly", "yearly"],
        default=None,
        help="Report mode: combined (yearly+monthly), yearly only, or monthly only",
    )
    parser.add_argument(
        "--audit",
        action="store_true",
        help="Write an audit CSV with every transaction and its category",
    )
    parser.add_argument(
        "--csv",
        action="store_true",
        help="Write a consolidated, deduplicated transactions CSV (one row per transaction)",
    )
    parser.add_argument(
        "--mask",
        action="store_true",
        help="Redact personal names and addresses in output",
    )
    parser.add_argument(
        "--allow-mismatch",
        action="store_true",
        help="Generate report even if reconciliation fails",
    )
    parser.add_argument(
        "--budget",
        type=str,
        default=None,
        help="Path to a budget.yaml with monthly income/category budgets "
        "(default: <directory>/budget.yaml when it exists)",
    )
    args = parser.parse_args()

    pdf_files = _find_statement_pdfs(Path(args.directory))
    if not pdf_files:
        print(f"No PDF files found in {args.directory}", file=sys.stderr)
        sys.exit(1)

    statements = []
    for pdf_path in pdf_files:
        text = extract_text(pdf_path)
        stmt = parse_personal_statement(text, file_path=str(pdf_path))
        if stmt.statement_date and stmt.transactions:
            statements.append(stmt)

    if not statements:
        print("No valid statements found.", file=sys.stderr)
        sys.exit(1)

    print(
        f"Loaded {len(statements)} statement(s) from {sorted({s.account_label for s in statements})}",
        file=sys.stderr,
    )

    # Determine mode
    if args.mode and args.month and args.mode == "yearly":
        print("Error: --mode yearly and --month are incompatible.", file=sys.stderr)
        sys.exit(1)
    if args.mode:
        mode = args.mode
    elif args.month:
        mode = "monthly"
    elif args.year and not args.month:
        mode = "yearly"
    else:
        mode = "combined"

    # Auto-detect year for --month when --year is missing
    if args.month and not args.year:
        matching_years = sorted({s.year for s in statements if s.month == args.month})
        if not matching_years:
            print(f"No statements found for month {args.month}.", file=sys.stderr)
            sys.exit(1)
        args.year = matching_years[-1]
        if len(matching_years) > 1:
            print(
                f"Note: --month without --year, using latest year {args.year} "
                f"(found: {matching_years}). Use --year to override.",
                file=sys.stderr,
            )

    # Determine output filename
    if args.output:
        output = args.output
    elif args.month:
        yr = args.year or statements[0].year
        output = os.path.join(args.directory, f"personal_financial_report_{args.month:02d}_{yr}.pdf")
    elif args.year:
        output = os.path.join(args.directory, f"personal_financial_report_{args.year}.pdf")
    else:
        output = os.path.join(args.directory, "personal_financial_report.pdf")

    # Determine audit path
    audit_path = None
    if args.audit:
        base = os.path.splitext(output)[0]
        audit_path = f"{base}_audit.csv"

    transactions_csv_path = None
    if args.csv:
        base = os.path.splitext(output)[0]
        transactions_csv_path = f"{base}_transactions.csv"

    if args.budget:
        budget_path = args.budget
    else:
        default_budget = os.path.join(args.directory, "budget.yaml")
        budget_path = default_budget if os.path.exists(default_budget) else None

    generate_report(
        statements,
        output,
        mode=mode,
        target_year=args.year,
        target_month=args.month,
        audit_path=audit_path,
        mask_personal=args.mask,
        allow_mismatch=args.allow_mismatch,
        transactions_csv_path=transactions_csv_path,
        budget_path=budget_path,
    )
