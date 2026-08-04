"""Business configuration loading from TOML."""
from __future__ import annotations

import logging
import re
import tomllib
from decimal import Decimal
from pathlib import Path
from typing import Any

from ledgersight.constants import (
    VALID_DEDUCTIBILITY,
    VALID_ENTITY_TYPES,
)
from ledgersight.exceptions import ConfigurationError
from ledgersight.models import BusinessConfig, CategoryRule

logger = logging.getLogger("ledgersight.config")

VALID_DIRECTIONS = {"credit", "debit", "either"}


def safe_float_or_none(value: Any) -> float | None:
    """Try to convert *value* to float; return None on failure."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def validate_config(config: BusinessConfig) -> tuple[list[str], list[str]]:
    """Return (errors, warnings) — errors are fatal, warnings reduce confidence."""
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(config.fiscal_year_start, int) or isinstance(config.fiscal_year_start, bool):
        errors.append(
            f"fiscal_year_start must be an integer, got {type(config.fiscal_year_start).__name__}"
        )
    elif not 1 <= config.fiscal_year_start <= 12:
        errors.append(f"fiscal_year_start must be 1-12, got {config.fiscal_year_start}")

    if config.entity_type not in VALID_ENTITY_TYPES:
        errors.append(
            f"entity_type '{config.entity_type}' is not valid; "
            f"must be one of {sorted(VALID_ENTITY_TYPES)}"
        )

    if config.accounting_method not in ("cash", "accrual"):
        errors.append(
            f"accounting_method must be 'cash' or 'accrual', got '{config.accounting_method}'"
        )

    if not isinstance(config.tax_year, int) or isinstance(config.tax_year, bool):
        errors.append(
            f"tax_year must be an integer, got {type(config.tax_year).__name__}"
        )
    elif not 2000 <= config.tax_year <= 2099:
        errors.append(f"tax_year {config.tax_year} is outside the reasonable range 2000-2099")

    for bool_field in ("mask_ein", "mask_account"):
        val = getattr(config, bool_field)
        if not isinstance(val, bool):
            errors.append(f"{bool_field} must be a boolean, got {type(val).__name__}")

    if config.projection_config:
        pc = config.projection_config

        mg = pc.get("monthly_revenue_growth")
        if mg is not None:
            if safe_float_or_none(mg) is None:
                errors.append(
                    f"projection_config.monthly_revenue_growth must be a number, got '{mg}'"
                )

        for field_name in ("projection_months", "lookback_months"):
            val = pc.get(field_name)
            if val is not None:
                if not isinstance(val, (int, float)) or isinstance(val, bool) or val <= 0:
                    errors.append(
                        f"projection_config.{field_name} must be > 0, got {val}"
                    )

        tr = pc.get("tax_reserve_pct")
        if tr is not None:
            f_tr = safe_float_or_none(tr)
            if f_tr is None or not (0 <= f_tr <= 1):
                errors.append(
                    f"projection_config.tax_reserve_pct must be between 0 and 1, got {tr}"
                )

    for i, rule in enumerate(config.custom_rules):
        if rule.direction not in VALID_DIRECTIONS:
            errors.append(
                f"custom_rules[{i}] direction must be one of {sorted(VALID_DIRECTIONS)}, "
                f"got '{rule.direction}' (category='{rule.category}')"
            )

    for i, rule in enumerate(config.custom_rules):
        if rule.deductibility not in VALID_DEDUCTIBILITY:
            warnings.append(
                f"custom_rules[{i}] deductibility '{rule.deductibility}' is not recognized; "
                f"must be one of {sorted(VALID_DEDUCTIBILITY)} "
                f"(category='{rule.category}')"
            )

    for key, value in config.merchant_aliases.items():
        if not key or not key.strip():
            warnings.append("merchant_aliases contains an empty key")
        if not value or not value.strip():
            warnings.append(f"merchant_aliases['{key}'] has an empty value")

    return errors, warnings


def validate_config_strict(config: BusinessConfig) -> None:
    """Raise ValueError if *config* has fatal errors."""
    errors, _warnings = validate_config(config)
    if errors:
        raise ValueError("Configuration errors:\n" + "\n".join(f"  - {e}" for e in errors))

# =============================================================================
# Example TOML Configuration
# =============================================================================

EXAMPLE_TOML = '''# Business Financial Report Configuration
# Placeholder values — replace with your own business data.

[general]
business_name = "Acme Transport LLC"
dba = "Acme Transport"
address = "123 Main St, Anytown ST 00000"
phone = "555-555-1212"
email = "contact@example.com"
tax_year = 2025
fiscal_year_start = 1  # 1 = January
entity_type = "single-member-llc"  # sole-prop, single-member-llc, partnership, s-corp, c-corp, other
accounting_method = "cash"  # cash or accrual
ein_display = "XX-XXXXXXX"
mask_ein = true
bank_account_display = "CLASSIC BUSINESS CHECKING - XXXX0000"
mask_account = true
industry = "trucking"
currency = "USD"

[cpa]
name = "Jane Smith, CPA"
firm = "Smith & Associates, CPAs"
email = "jane@smithcpa.example.com"
phone = "555-555-3000"

[owner]
owners = ["John Doe"]

[projections]
monthly_revenue_growth = 0.03  # 3%
monthly_expense_inflation = 0.02  # 2%
cogs_percentage = 0.35  # 35% of revenue
payroll_growth = 0.02  # 2% (reserved for future use)
lookback_months = 6
projection_months = 12
min_cash_balance = 5000.0
tax_reserve_pct = 0.25  # 25% of net income
one_time_revenue = []  # reserved for future use
one_time_expenses = []  # reserved for future use
planned_equipment = []  # reserved for future use

# Seasonal multipliers (1.0 = no adjustment)
[projections.seasonal]
1 = 0.85
2 = 0.90
3 = 0.95
4 = 1.05
5 = 1.10
6 = 1.15
7 = 1.10
8 = 1.05
9 = 1.00
10 = 0.95
11 = 0.90
12 = 0.85

# Three scenarios (growth rates override monthly_revenue_growth)
[projections.scenarios.conservative]
monthly_revenue_growth = 0.01

[projections.scenarios.base]
monthly_revenue_growth = 0.03

[projections.scenarios.growth]
monthly_revenue_growth = 0.06

# Beginning balance sheet (reserved for future use — not yet integrated into reports)
[balances]
beginning_cash = 0
beginning_accounts_receivable = 0
beginning_inventory = 0
beginning_fixed_assets = 0
beginning_accumulated_depreciation = 0
beginning_accounts_payable = 0
beginning_loan_balances = 0
beginning_owner_equity = 0

# Known fixed assets (reserved for future integration)
[[fixed_assets]]
name = "2020 Freightliner Cascadia"
purchase_date = "2020-06-15"
cost = 85000
asset_class = "5-year"

# Known loans (reserved for future integration)
[[loans]]
lender = "Equipment Finance Co"
original_amount = 70000
current_balance = 45000
monthly_payment = 1850
interest_rate = 0.065
description_keywords = ["EQUIPMENT FINANCE"]

# Owner activity (reserved for future integration)
[[owner_activity]]
type = "contribution"
date = "2025-01-15"
amount = 10000
description = "Initial capital contribution"

# Merchant name normalization — collapses variants of the same vendor
# before category rules are applied.  Glob patterns with * are supported.
[merchant_aliases]
"BIG TRAILER RENT*" = "Big Trailer Rent"
"DAT SOLUTIONS*" = "DAT Solutions"

# Custom transaction category rules
# Each rule has: pattern (regex), category, tax_category, deductibility, direction
# Rules are evaluated in order; first match wins.
# direction: "credit", "debit", or "either" — rules skip mismatched-direction transactions
[[rules]]
pattern = "APEX CAPITAL CORP"
category = "Service Revenue"
tax_category = "Gross Receipts"
deductibility = "not-applicable"
direction = "credit"
is_income = true

[[rules]]
pattern = "PILOT|FLYING J|LOVE'?S|KWIK STAR|CASEYS|MARATHON|TA\\\\s+#?\\\\d*\\\\s+[A-Z]{3}"
category = "Fuel"
tax_category = "Vehicle Fuel"
deductibility = "likely-deductible"
direction = "debit"

[[rules]]
pattern = "ALLIANT ENERGY|ALLPAID"
category = "Utilities"
tax_category = "Utilities"
deductibility = "likely-deductible"
direction = "debit"

[[rules]]
pattern = "WEB XFER|ZELLE|PAYPAL INST XFER"
category = "Account Transfer"
tax_category = "non-pl"
deductibility = "not-applicable"
direction = "either"

[[rules]]
pattern = "CHECK #"
category = "Uncategorized"
tax_category = "CPA Review"
deductibility = "unknown"
direction = "debit"

# Document checklist for CPA package
[document_checklist]
bank_statements = "provided"
credit_card_statements = "not-provided"
loan_statements = "not-provided"
payroll_reports = "not-provided"
quarterly_payroll_filings = "not-provided"
forms_w2 = "not-provided"
forms_1099 = "not-provided"
contractor_w9 = "not-provided"
sales_tax_filings = "not-provided"
prior_year_tax_return = "not-provided"
fixed_asset_docs = "not-provided"
vehicle_docs = "not-provided"
mileage_logs = "not-provided"
insurance_statements = "not-provided"
merchant_processor_reports = "not-provided"
ar_records = "not-provided"
ap_records = "not-provided"
inventory_records = "not-provided"
owner_contribution_records = "not-provided"
owner_distribution_records = "not-provided"
estimated_tax_confirmations = "not-provided"
business_license = "not-provided"
home_office_records = "not-provided"
health_insurance_docs = "not-provided"
'''


# =============================================================================
# Configuration Loading
# =============================================================================


def load_config(config_path: Path, was_explicit: bool = False, strict: bool = True) -> BusinessConfig:
    """Load business configuration from a TOML file.

    If *was_explicit* is True (user supplied --config), a missing file is fatal.
    When *strict* is True (default), fatal validation errors raise ConfigurationError.
    Otherwise silently returns defaults.
    """
    config = BusinessConfig()

    if config_path.exists():
        logger.info("Configuration loaded: %s", config_path.resolve())
        with open(config_path, "rb") as f:
            data = tomllib.load(f)

        general = data.get("general", {})
        config.business_name = general.get("business_name", config.business_name)
        config.dba = general.get("dba", config.dba)
        config.address = general.get("address", config.address)
        config.phone = general.get("phone", config.phone)
        config.email = general.get("email", config.email)
        config.tax_year = general.get("tax_year", config.tax_year)
        config.fiscal_year_start = general.get("fiscal_year_start", config.fiscal_year_start)
        config.entity_type = general.get("entity_type", config.entity_type)
        if config.entity_type not in VALID_ENTITY_TYPES:
            logger.warning("Unknown entity_type: %s", config.entity_type)
        config.accounting_method = general.get("accounting_method", config.accounting_method)
        config.ein_display = general.get("ein_display", config.ein_display)
        config.mask_ein = general.get("mask_ein", config.mask_ein)
        config.bank_account_display = general.get("bank_account_display", config.bank_account_display)
        config.mask_account = general.get("mask_account", config.mask_account)
        config.industry = general.get("industry", config.industry)
        config.currency = general.get("currency", config.currency)

        cpa = data.get("cpa", {})
        config.cpa_name = cpa.get("name", config.cpa_name)
        config.cpa_firm = cpa.get("firm", config.cpa_firm)
        config.cpa_email = cpa.get("email", config.cpa_email)
        config.cpa_phone = cpa.get("phone", config.cpa_phone)

        owner = data.get("owner", {})
        config.owners = owner.get("owners", config.owners)

        config.projection_config = data.get("projections", {})

        # Load custom rules
        for rule_data in data.get("rules", []):
            cat = rule_data.get("category", "Uncategorized")
            tax_cat = rule_data.get("tax_category", "")
            # Derive safe defaults from category/tax names
            is_non_pl = tax_cat == "non-pl" or cat in (
                "Account Transfer", "Credit Card Payment",
                "Loan Proceeds", "Loan Principal Payment",
                "Owner Contribution", "Owner Draw or Distribution",
                "Fixed Asset Purchase", "Tax Payment",
                "Refund", "Reimbursement", "Opening Balance",
                "Uncategorized", "CPA Review Required",
            )
            include_pl = rule_data.get("include_in_pnl", not is_non_pl)
            try:
                re.compile(rule_data.get("pattern", ""))
            except re.error as exc:
                logger.error(
                    "Invalid regex pattern in custom rule '%s': %s",
                    rule_data.get("category", "?"), exc,
                )
                raise ConfigurationError(
                    f"Invalid regex pattern in custom rule '{rule_data.get('category', '?')}': {exc}",
                ) from exc

            config.custom_rules.append(CategoryRule(
                pattern=rule_data.get("pattern", ""),
                category=cat,
                tax_category=tax_cat or "CPA Review",
                deductibility=rule_data.get("deductibility", "unknown"),
                is_income=rule_data.get("is_income", False),
                include_in_pnl=include_pl,
                is_transfer=rule_data.get("is_transfer", cat in ("Account Transfer", "Credit Card Payment")),
                is_owner_related=rule_data.get(
                    "is_owner_related",
                    cat in ("Owner Contribution", "Owner Draw or Distribution"),
                ),
                is_fixed_asset=rule_data.get("is_fixed_asset", cat == "Fixed Asset Purchase"),
                is_loan=rule_data.get("is_loan", cat in ("Loan Proceeds", "Loan Principal Payment", "Loan Interest")),
                direction=rule_data.get("direction", "either"),
                priority=rule_data.get("priority", 0),  # custom rules default higher priority than defaults
            ))

        # Balances
        balances = data.get("balances", {})
        for key in balances:
            config.beginning_balances[key] = Decimal(str(balances[key]))

        config.fixed_assets = data.get("fixed_assets", [])
        config.loans = data.get("loans", [])
        config.owner_activities = data.get("owner_activity", [])
        config.document_checklist = data.get("document_checklist", {})
        config.merchant_aliases = data.get("merchant_aliases", {})

        logger.info("Business: %s", config.display_name())
        logger.info("Entity type: %s", config.entity_type)
    elif was_explicit:
        logger.error(
            "Configuration file not found: %s (use --init-config to create one)",
            config_path.resolve(),
        )
        raise ConfigurationError(
            f"Configuration file not found: {config_path.resolve()}",
        )

    errors, warnings = validate_config(config)
    for err in errors:
        logger.error("Config validation error: %s", err)
    for warn in warnings:
        logger.warning("Config validation warning: %s", warn)
    if errors and strict:
        raise ConfigurationError(
            "Configuration has {} fatal error(s):\n{}".format(
                len(errors),
                "\n".join(f"  - {e}" for e in errors),
            ),
        )
    elif errors:
        logger.error(
            "Configuration has %d fatal error(s). Report generation may be unreliable.",
            len(errors),
        )

    return config


def generate_example_config(path: Path, force: bool = False) -> bool:
    """Generate example TOML configuration file."""
    if path.exists() and not force:
        logger.error("Config file already exists: %s (use --force to overwrite)", path)
        return False
    path.write_text(EXAMPLE_TOML)
    logger.info("Example config written to: %s", path)
    return True
