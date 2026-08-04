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
