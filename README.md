# LedgerSight

Bank-statement-to-financial-report pipeline with an interactive terminal UI.

Parses First Interstate Bank checking-account PDF statements, categorizes income and expenses, and produces comprehensive financial reports including cash-basis P&L, projections, CPA/tax-preparer packages, charts, and audit exports.

## Features

- **PDF statement parsing** — extracts transactions, balances, and account summaries from bank PDFs via `pdftotext`
- **Configurable categorization** — TOML-based rule engine with regex patterns, merchant aliases, tax categories, and deductibility flags
- **Cash-basis P&L** — monthly, quarterly, and yearly profit & loss with revenue/expense breakdowns
- **Financial projections** — three scenarios (conservative, base, growth) with seasonal adjustments
- **CPA/tax package** — revenue detail, expense detail, fixed assets, loans, owner activity, reconciliation, document checklist
- **Audit CSV export** — full transaction audit trail with categories, flags, and review reasons
- **PDF report generation** — branded multi-page PDF with charts, tables, KPIs, and executive summary
- **Interactive TUI** — Textual-based terminal interface with wizard flow and sidebar navigation
- **Headless CLI** — same functionality via command-line for scripting and automation

## Requirements

- **Python** 3.14+
- **System dependency:** `pdftotext` (from `poppler-utils`)
- **Python packages:** `fpdf2`, `matplotlib`, `textual` (auto-installed)

### Install pdftotext

```bash
# Ubuntu/Debian
sudo apt install poppler-utils

# macOS
brew install poppler

# Fedora
sudo dnf install poppler-utils
```

## Installation

```bash
git clone git@github.com:RedneckTech/LedgerSight.git
cd LedgerSight
python -m venv venv
source venv/bin/activate
pip install -e ".[dev]"
```

## Usage

### Interactive TUI (default)

```bash
ledgersight
# or
./dev_run.sh --tui
```

The TUI walks through: config selection → statement loading → category rules → report options → generate.

**Keybindings:**
| Key | Action |
|-----|--------|
| `Ctrl+B` | Toggle sidebar |
| `Ctrl+→` | Next screen (wizard) |
| `Ctrl+←` | Previous screen (wizard) |
| `Ctrl+G` | Jump to generate |
| `Ctrl+Q` | Quit |

### Headless CLI

```bash
# Generate a full-year report
ledgersight --cli --year 2025

# Single month
ledgersight --cli --year 2025 --month 6

# With projections, audit CSV, and CPA exports
ledgersight --cli --year 2025 --projections --audit --export-pl --export-cpa

# Fiscal quarter (fiscal-year-aware)
ledgersight --cli --year 2025 --quarter 2

# CPA review package only
ledgersight --cli --year 2025 --mode cpa

# Custom config file
ledgersight --cli --config my_business.toml --year 2025

# Generate example config
ledgersight --cli --init-config

# Full help
ledgersight --cli --help
```

### Via dev_run.sh

```bash
./dev_run.sh --tui       # Launch TUI
./dev_run.sh --cli       # Headless mode
./dev_run.sh --test      # Run tests
./dev_run.sh --lint      # Ruff + mypy
./dev_run.sh --fmt       # Auto-format
./dev_run.sh --install   # Reinstall in dev mode
```

### Personal profile (bank + credit-card statements)

The personal profile consolidates First Interstate checking/savings and
Capital One card statement PDFs into one audited report: per-account monthly
ledgers with recomputed running balances, a consolidated dashboard, recurring
payments and a 91-day cash forecast, items needing review, and a corrections
log with source-file provenance.

```bash
# Full report for 2026 from every PDF under data/Personal (searched recursively)
ledgersight --cli --profile personal -d data/Personal --year 2026 --mode combined \
    --audit --csv -o data/Personal/personal_financial_report.pdf
```

- `--audit` writes `<report>_audit.csv`: every parsed row in statement order
  with the bank-printed balance (`Balance`), the report's recomputed running
  balance (`Running`), its position in the calculation order (`Seq`), the
  exact `Source` (file, PDF page, row), and an `InReport` flag that shows
  whether the row falls inside the requested `--year`/`--month`/`--quarter`
  period (statement closing dates are kept in the audit but may be excluded
  from period-specific reports).
- `--csv` writes `<report>_transactions.csv`: the deduplicated ledger in the
  same calculation order the PDF uses (`Seq`), so consecutive-row balance
  checks hold in the export, with a `Source` reference per row. CSV cells
  that could be interpreted as spreadsheet formulas are prefixed so they
  open as text.
- `--allow-mismatch` forces report generation when reconciliation fails, but
  the cover page is marked **UNVALIDATED** and the CLI exits with an error
  status.
- Both CSVs are also embedded in the PDF as file attachments.
- `--budget <path>` enables the Budget vs Actual page (defaults to
  `<directory>/budget.yaml` when that file exists).
- `--checks <path>` annotates cleared checks with payee and purpose (defaults
  to `<directory>/checks.yaml` when that file exists) - see below.

The report opens with an Action Summary (next decisions, what is due in 30
days, missing information), then the dashboard (credit/debit breakdown and an
income-to-cash bridge that reconciles "income less categorized spending" to
the observed change in cash and card debt), charts, a Subscription Review,
a Bills & Debt Calendar (card balances, limits, APR, minimums, due dates,
autopay evidence, 60-day calendar), the check register, items needing review,
recurring payments with a forecast whose allowances come from complete
statement months only, reconciliation, the corrections log, and finally the
account-by-month ledger with per-row running balances.

After rendering, the CLI runs export checks (all table rows drawn, nothing past
the footer, credit decomposition sums to total credits, monthly series sums to
the dashboard totals, every ledger transaction bucketed and given a running
balance) and prints `Export checks: PASSED` or lists each failure on stderr.

#### budget.yaml schema

```yaml
# All amounts are US dollars per calendar month. 0.00 ignores an item;
# delete the file to disable the page entirely.
income_monthly: 4200.00        # expected earned income (Payroll + Deposit categories)

categories:                    # monthly caps per spending category
  Fuel: 600.00
  Groceries: 450.00
  Rent: 1200.00
  Subscriptions: 60.00
  # any category the categorizer produces may be listed:
  # Auto Care, Bank Fees, Checks, Fuel, Government, Groceries, Insurance,
  # Interest, Rent, Restaurants, Shopping, Subscriptions, Utilities

spending_monthly: 0.00         # optional overall cap; 0.00 = sum of the categories above
```

"Actual" on the budget page is the unified spending figure used everywhere in
the report: every debit except internal transfers and loan/card payments
(uncategorized items are included). "Income" is earned income - Payroll,
Deposit and Government credits with refunds excluded.

#### checks.yaml schema

Bank statements record only a check's number and amount. Supplying the payee
and purpose keeps the check number as the payment method while categorizing
the amount by what it paid for:

```yaml
checks:
  5125: {payee: "Landlord", category: "Rent"}
  5056: {payee: "Farm Bureau", category: "Insurance", note: "6-month premium"}
  5127: "Cash"              # payee only; the category stays "Checks"
```

Numbers may be written with or without a leading `#`. Unannotated checks are
listed on the Checks page and in the Action Summary as missing information.

## Configuration

Reports are driven by a TOML config file (`business_report.toml` by default). Generate a template:

```bash
ledgersight --cli --init-config
```

Key config sections:

```toml
[general]
business_name = "Acme Transport LLC"
dba = "Acme Transport"
entity_type = "single-member-llc"
fiscal_year_start = 1     # 1 = January

[cpa]
name = "Jane Smith, CPA"

[projections]
monthly_revenue_growth = 0.03
projection_months = 12

[projections.scenarios.conservative]
monthly_revenue_growth = 0.01

# Categorization rules (evaluated in order; first match wins)
[[rules]]
pattern = "APEX CAPITAL CORP"
category = "Service Revenue"
tax_category = "Gross Receipts"
direction = "credit"
is_income = true

[[rules]]
pattern = "PILOT|FLYING J|LOVE'?S"
category = "Fuel"
tax_category = "Vehicle Fuel"
direction = "debit"

# Merchant name normalization
[merchant_aliases]
"BIG TRAILER RENT*" = "Big Trailer Rent"
```

## Project Structure

```
ledgersight/
├── models.py            # Transaction, Statement, BusinessConfig, etc.
├── parsers.py           # PDF text extraction, amount parsing, statement parsing
├── categorizer.py       # Rule engine, merchant normalization
├── config.py            # TOML loading/saving
├── reconciliation.py    # Statement reconciliation
├── charts.py            # Matplotlib chart generators
├── pdf_renderer.py      # FPDF-based PDF output with tables
├── exports.py           # CSV audit, P&L, and CPA exports
├── constants.py         # Shared constants
├── business/
│   ├── pl.py            # Profit & Loss calculation
│   ├── periods.py       # Financial period helpers
│   ├── kpis.py          # Key performance indicators
│   ├── projections.py   # Financial projection engine
│   ├── report.py        # Report builder and orchestration
│   └── cli.py           # CLI argument parsing and entry point
└── tui/
    ├── app.py           # Textual application, sidebar, navigation
    └── screens/         # TUI screens (welcome, config, statements, etc.)
```

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
python -m pytest tests/ -v

# Lint
python -m ruff check ledgersight/ tests/
python -m mypy ledgersight/

# Auto-format
python -m ruff format ledgersight/ tests/
```

## License

See [LICENSE](LICENSE).
