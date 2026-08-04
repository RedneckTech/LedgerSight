"""Transaction categorization engine."""
from __future__ import annotations

import logging
import re

from ledgersight.models import CategoryRule, Statement, Transaction

logger = logging.getLogger("ledgersight.categorizer")

_INCOME_CATEGORIES: dict[str, list[str]] = {
    "Sales Revenue": [
        r"\bINVOICE\b", r"\bSALE\b.*\bREVENUE\b",
    ],
    "Service Revenue": [
        r"\bSERVICE\s+REVENUE\b",
    ],
    "Freight Revenue": [
        r"\bFREIGHT\b", r"\bTRUCKING\s+REVENUE\b",
        r"Incoming\s+Wire\s+.*APEX\s+CAPITAL\s+CORP",
    ],
    "Contract Revenue": [
        r"\bCONTRACT\s+PAY(?:MENT)?\b",
    ],
    "Other Operating Income": [
        r"\bOPERATING\s+INCOME\b",
    ],
    "Interest Income": [
        r"\bINTEREST\s+(?:PAID|EARNED|INCOME)\b",
    ],
    "Refunds and Reimbursements": [
        r"\bREFUND\b", r"\bREIMBURSEMENT\b", r"\bREBATE\b",
    ],
    "Other Income": [
        r"\bOTHER\s+INCOME\b",
    ],
}

_EXPENSE_CATEGORIES: dict[str, list[str]] = {
    "Fuel": [
        r"\bPILOT\b", r"FLYING\s*J", r"LOVE'?S",
        r"KWIK\s*STAR", r"\bCASEYS?\b",
        r"\bMARATHON\b", r"\bYESWAY\b", r"CIRCLE\s*K",
        r"\bTRAVEL\s+CNT\b", r"\bTRAVEL\s+CENTER\b",
        r"\bTA\b\s+(?:#\d+\s+)?[A-Z]{3,}",
        r"\bBP#\d", r"\bBB\s+OF\s+HOUSTON\b",
        r"\bBREAK\s+TIME\b", r"\bSHELL\b.*SERVICE",
        r"\b59\s+FASTLANE\b", r"\bCTLP\b",
        r"\bWOODSHED\b", r"\bPORT\s+AUTO\s+TRUCK\b",
        r"\bTEX\s+BEST\b", r"\bTBS\b.*DENISON",
        r"\bDIESEL\b", r"\bGAS\b.*\bSTATION\b",
    ],
    "Freight and Shipping": [
        r"\bFREIGHT\s+(?:CHARGE|FEE|COST)\b",
        r"\bSHIPPING\b",
    ],
    "Subcontractors": [
        r"\bSUBCONTRACTOR\b", r"\bSUBCONTRACT\b",
    ],
    "Direct Labor": [
        r"\bDIRECT\s+LABOR\b",
    ],
    "Materials and Supplies": [
        r"\bMATERIALS\b", r"\bSUPPLIES\b",
    ],
    "Equipment Rental": [
        r"\bEQUIPMENT\s+RENTAL\b", r"\bTRAILER\s+RENTAL\b",
        r"\bRENTAL\s+EQUIPMENT\b",
    ],
    "Tolls and Scale Fees": [
        r"\bTOLL\b", r"\bSCALE\s+FEE\b", r"\bWEIGH\s+STATION\b",
    ],
    "Other Direct Costs": [],
    "Advertising and Marketing": [
        r"\bADVERTISING\b", r"\bMARKETING\b", r"\bDAT\s+SOLUTIONS\b",
        r"\bLOAD\s*BOARD\b", r"\b123LOADBOARD\b",
    ],
    "Bank and Merchant Fees": [
        r"MasterCard Cross Border", r"SALES\s+TAX\b", r"Overdraft",
        r"FEE\s+FOR\s+DDA", r"DDA\s+WITHDRAWAL",
        r"Incoming Wire Transfer Fee",
        r"\bWIRE\s+TRANSFER\s+FEE\b",
        r"^SERVICE\s+CHARGE$",
    ],
    "Business Insurance": [
        r"\bPROGRESSIVE\s+INS\b", r"\bINSURANCE\b",
    ],
    "Commissions": [
        r"\bCOMMISSION\b",
    ],
    "Contract Labor": [
        r"\bCONTRACT\s+LABOR\b",
    ],
    "Depreciation Placeholder": [],
    "Dues and Subscriptions": [
        r"\bDUES\b", r"\bMEMBERSHIP\b",
    ],
    "Employee Benefits": [
        r"\bBENEFITS\b",
    ],
    "Equipment Maintenance": [
        r"\bPARTS\b",
        r"\bREPAIR\s+SHOP", r"\bTRUCK\s+REPAIR",
        r"\bMAINTENANCE\s+SHOP", r"\bTIRE\s+SERVICE",
        r"O'REILLY\s+AUTO", r"\bAUTO\s+ZONE\b",
    ],
    "Legal and Professional Fees": [
        r"\bLEGAL\b", r"\bATTORNEY\b", r"\bLAW\s+OFFICE\b",
        r"\bLEGALSHIELD\b",
    ],
    "Licenses and Permits": [
        r"\bLICENSE\b", r"\bPERMIT\b", r"\bREGISTRATION\b",
    ],
    "Meals": [
        r"SONIC\s+DRIVE", r"\bARBYS?\b", r"MCDONALD",
        r"\bARANDAS\b", r"DOORDASH", r"\bWENDYS?\b",
        r"BURGER\s+KING", r"TACO\s+JOHN",
        r"PANCHEROS[\s-]*MEXICA", r"CARL'?S\s*JR",
        r"\bDENNY'?S?\b", r"\bHARDEE'?S\b",
        r"DAIRY\s+QUEEN", r"\bHAYMAKERS?\b",
        r"STILL\s+SMOKIN", r"HONG\s+KONG\s+BUFFET",
        r"\bRESTAURANT\b",
    ],
    "Office Expense": [
        r"\bOFFICE\b", r"\bSTAPLES\b", r"\bOFFICE\s+DEPOT\b",
    ],
    "Payroll": [
        r"\bPAYROLL\b", r"\bSALARY\b", r"\bWAGES\b",
    ],
    "Payroll Taxes": [
        r"\bPAYROLL\s+TAX\b",
    ],
    "Rent or Lease": [
        r"\bOFFICE\s+RENT\b", r"\bPROPERTY\s+LEASE\b",
        r"\bRENT\s+PAYMENT\b",
    ],
    "Repairs and Maintenance": [
        r"\bREPAIR\b", r"\bMAINTENANCE\b",
        r"\bGENERAL\s+REPAIR\b",
    ],
    "Software and Cloud Services": [
        r"GOOGLE\s+LLC\s+GSUIT", r"\bGSUITE\b",
        r"\bMICROSOFT\s*365\b", r"\bDROPBOX\b",
        r"\bSOFTWARE\b", r"\bCLOUD\b",
    ],
    "Taxes and Fees": [
        r"\bTAX\b", r"\bFEE\b",
    ],
    "Telephone and Internet": [
        r"\bSTRAIGHT\s*TALK\b", r"\bVERIZON\b",
        r"\bAT&T\b", r"\bT-MOBILE\b",
        r"\bINTERNET\b", r"\bPHONE\b",
    ],
    "Travel": [
        r"\bTRAVEL\b", r"\bHOTEL\b", r"\bMOTEL\b",
        r"\bAIRLINE\b", r"\bFLIGHT\b",
    ],
    "Utilities": [
        r"\bALLIANT\s+ENERGY\b", r"\bUTILITY\b",
        r"\bELECTRIC\b", r"\bWATER\b", r"\bGAS\s+COMPANY\b",
    ],
    "Vehicle Expense": [
        r"TRUCK\s*PARKING", r"\bCAR\s*WASH\b",
        r"\bTIRE\b", r"\bOIL\s+CHANGE\b",
    ],
}

_NON_PNL_CATEGORIES: dict[str, list[str]] = {
    "Account Transfer": [
        r"WEB\s+XFER", r"ZELLE", r"PAYPAL\s+INST\s+XFER",
    ],
    "Credit Card Payment": [
        r"\bCAPITAL\s+ONE\b", r"\bCHIME\b",
        r"\bCREDIT\s+CARD\s+PAYMENT\b",
    ],
    "Loan Proceeds": [
        r"\bLOAN\s+PROCEEDS?\b", r"\bLOAN\s+DISBURSEMENT\b",
    ],
    "Payment Reversal": [
        r"RETURNED\s+ITEM.*INSUFFICIENT\s+FUNDS",
    ],
    "Loan Principal Payment": [
        r"APEX\s+CAPITAL\s+FUNDING",
    ],
    "Loan Interest": [],
    "Owner Contribution": [
        r"\bOWNER\s+CONTRIBUTION\b",
    ],
    "Owner Draw or Distribution": [
        r"\bOWNER\s+DRAW\b", r"\bDISTRIBUTION\b",
    ],
    "Fixed Asset Purchase": [
        r"\bTRUCK\s+PURCHASE\b", r"\bEQUIPMENT\s+PURCHASE\b",
    ],
    "Tax Payment": [
        r"\bTAX\s+PAYMENT\b", r"IOWA\s+JUDICIAL\b",
    ],
    "Refund": [
        r"\bREFUND\b",
    ],
    "Reimbursement": [
        r"\bREIMBURSEMENT\b",
    ],
    "Opening Balance": [],
    "Uncategorized": [],
    "CPA Review Required": [],
}


def build_default_rules() -> list[CategoryRule]:
    """Build the complete ordered list of default category rules."""
    rules: list[CategoryRule] = []
    priority_counter = 0

    for cat, patterns in _NON_PNL_CATEGORIES.items():
        for pat in patterns:
            priority_counter += 1
            rule = CategoryRule(
                pattern=pat,
                category=cat,
                tax_category="non-pl",
                deductibility="not-applicable",
                priority=priority_counter,
            )
            if cat == "Account Transfer":
                rule.is_transfer = True
                rule.include_in_pnl = False
            elif cat == "Credit Card Payment":
                rule.is_transfer = True
                rule.include_in_pnl = False
                rule.direction = "debit"
            elif cat == "Loan Proceeds":
                rule.is_loan = True
                rule.include_in_pnl = False
                rule.direction = "credit"
            elif cat == "Loan Principal Payment":
                rule.is_loan = True
                rule.include_in_pnl = False
                rule.direction = "debit"
            elif cat == "Payment Reversal":
                rule.include_in_pnl = False
                rule.direction = "debit"
            elif cat == "Loan Interest":
                rule.is_loan = True
                rule.include_in_pnl = True
            elif cat == "Owner Contribution":
                rule.is_owner_related = True
                rule.include_in_pnl = False
                rule.direction = "credit"
            elif cat == "Owner Draw or Distribution":
                rule.is_owner_related = True
                rule.include_in_pnl = False
                rule.direction = "debit"
            elif cat == "Fixed Asset Purchase":
                rule.is_fixed_asset = True
                rule.include_in_pnl = False
                rule.direction = "debit"
            elif cat == "Tax Payment":
                rule.include_in_pnl = False
                rule.direction = "debit"
            elif cat in ("Refund", "Reimbursement"):
                rule.include_in_pnl = True
                rule.direction = "debit"
            elif cat in ("Opening Balance", "Uncategorized", "CPA Review Required"):
                rule.include_in_pnl = False
            rules.append(rule)

    for cat, patterns in _EXPENSE_CATEGORIES.items():
        for pat in patterns:
            priority_counter += 1
            rules.append(CategoryRule(
                pattern=pat,
                category=cat,
                tax_category=_get_default_tax_category(cat),
                deductibility="likely-deductible" if cat != "Meals" else "possibly-deductible",
                include_in_pnl=True,
                direction="debit",
                priority=priority_counter,
            ))

    for cat, patterns in _INCOME_CATEGORIES.items():
        for pat in patterns:
            priority_counter += 1
            rules.append(CategoryRule(
                pattern=pat,
                category=cat,
                tax_category="Gross Receipts",
                deductibility="not-applicable",
                is_income=True,
                include_in_pnl=True,
                direction="credit",
                priority=priority_counter,
            ))

    return rules


def _get_default_tax_category(business_cat: str) -> str:
    """Map business category to a default tax category."""
    mapping = {
        "Fuel": "Vehicle Fuel",
        "Freight and Shipping": "Freight & Shipping",
        "Subcontractors": "Contract Labor",
        "Direct Labor": "Direct Labor",
        "Materials and Supplies": "Materials & Supplies",
        "Equipment Rental": "Equipment Rental",
        "Tolls and Scale Fees": "Tolls & Fees",
        "Other Direct Costs": "Other Direct Costs",
        "Advertising and Marketing": "Advertising",
        "Bank and Merchant Fees": "Bank Charges",
        "Business Insurance": "Insurance",
        "Commissions": "Commissions",
        "Contract Labor": "Contract Labor",
        "Depreciation Placeholder": "Depreciation",
        "Dues and Subscriptions": "Dues & Subscriptions",
        "Employee Benefits": "Employee Benefits",
        "Equipment Maintenance": "Repairs & Maintenance",
        "Legal and Professional Fees": "Legal & Professional",
        "Licenses and Permits": "Licenses & Permits",
        "Meals": "Meals (50%)",
        "Office Expense": "Office Expenses",
        "Payroll": "Wages & Salaries",
        "Payroll Taxes": "Payroll Taxes",
        "Rent or Lease": "Rent Expense",
        "Repairs and Maintenance": "Repairs & Maintenance",
        "Software and Cloud Services": "Software & Cloud",
        "Taxes and Fees": "Taxes & Licenses",
        "Telephone and Internet": "Telephone & Internet",
        "Travel": "Travel",
        "Utilities": "Utilities",
        "Vehicle Expense": "Vehicle Expenses",
    }
    return mapping.get(business_cat, "Other Expense")


class TransactionCategorizer:
    """Categorizes transactions using ordered category rules."""

    def __init__(self, custom_rules: list[CategoryRule] | None = None,
                 merchant_aliases: dict[str, str] | None = None):
        self.default_rules = build_default_rules()
        self.custom_rules = custom_rules or []
        self.merchant_aliases = merchant_aliases or {}
        self._build_combined_rules()

    def _build_combined_rules(self):
        self._rules = [
            *sorted(self.custom_rules, key=lambda r: r.priority),
            *sorted(self.default_rules, key=lambda r: r.priority),
        ]

    def categorize(self, transaction: Transaction) -> Transaction:
        """Apply rules to categorize a single transaction. First match wins.

        A rule with direction="credit" is skipped for debit transactions;
        a rule with direction="debit" is skipped for credit transactions.
        Mismatched-direction rules never clear the CPA-review flag.
        """
        desc = transaction.description
        if self.merchant_aliases:
            desc_upper = desc.upper()
            for pattern, replacement in self.merchant_aliases.items():
                if _alias_glob_match(desc_upper, pattern.upper()):
                    desc = replacement
                    break
        desc_upper = desc.upper()
        for rule in self._rules:
            try:
                rule_dir = rule.direction
                tx_is_credit = transaction.is_credit
                if rule_dir == "credit" and not tx_is_credit:
                    continue
                if rule_dir == "debit" and tx_is_credit:
                    continue

                if re.search(rule.pattern, desc_upper, re.IGNORECASE):
                    transaction.business_category = rule.category
                    transaction.tax_category = rule.tax_category
                    transaction.deductibility = rule.deductibility
                    transaction.include_in_pnl = rule.include_in_pnl
                    transaction.is_transfer = rule.is_transfer
                    transaction.is_owner_related = rule.is_owner_related
                    transaction.is_fixed_asset = rule.is_fixed_asset
                    transaction.is_loan = rule.is_loan
                    if rule.category not in (
                        "Uncategorized", "CPA Review Required",
                    ) and rule.tax_category != "non-pl":
                        transaction.cpa_review = False
                    else:
                        transaction.cpa_review = True
                        transaction.review_reason = (
                            f"Matched '{rule.category}' rule — requires CPA verification"
                        )
                    return transaction
            except re.error as exc:
                logger.warning(
                    "Invalid regex pattern skipped: %s — %s",
                    rule.pattern, exc,
                )
                continue

        transaction.business_category = "Uncategorized"
        transaction.tax_category = "CPA Review"
        transaction.deductibility = "unknown"
        transaction.include_in_pnl = False
        transaction.cpa_review = True
        transaction.review_reason = "No category rule matched"
        return transaction

    def categorize_all(self, statements: list[Statement]) -> None:
        """Categorize all transactions across all statements."""
        for stmt in statements:
            for tx in stmt.transactions:
                self.categorize(tx)

    def mark_credit_deposits(self, statements: list[Statement]) -> None:
        """Mark uncategorized credits (deposits) for CPA review."""
        for stmt in statements:
            for tx in stmt.transactions:
                if tx.is_credit and tx.business_category == "Uncategorized":
                    tx.cpa_review = True
                    tx.review_reason = "Uncategorized deposit - verify revenue vs transfer vs loan"
                    tx.business_category = "CPA Review Required"


def normalize_merchant(description: str) -> str:
    """Normalize a transaction description into a merchant/customer name."""
    desc = description.upper()
    desc = re.sub(r"XX\d{4}\s+(POS\s+)?PINNED\s+\d{2}/\d{2}\s+\d{2}:\d{2}\s*", "", desc)
    desc = re.sub(r"XX\d{4}\s+DEBIT\s+CARD\s+\d{2}/\d{2}\s+\d{2}:\d{2}\s*", "", desc)
    desc = re.sub(r"DEBIT\s+CARD\s+\d{2}/\d{2}\s+\d{2}:\d{2}\s*", "", desc)
    desc = re.sub(r"CARD\s+\d{2}/\d{2}\s+\d{2}:\d{2}\s*", "", desc)
    desc = re.sub(r"\b\d{2}/\d{2}\s+\d{2}:\d{2}\b", "", desc)
    desc = re.sub(r"\b[0-9A-F]{12,}\b", "", desc)
    desc = re.sub(r"\b\d{8,}\b", "", desc)
    desc = re.sub(r"\s+[A-Z]{2}\s+\d{5}(?:-\d{4})?", "", desc)
    desc = re.sub(r"\b([A-Za-z]{4,})\d{1,2}\b", r"\1", desc)
    desc = re.sub(r"\s+", " ", desc).strip()
    if not desc:
        desc = description[:80]
    return desc


def _alias_glob_match(text: str, pattern: str) -> bool:
    """Simple glob-style match: * matches any sequence.  Case-sensitive."""
    if pattern == "*":
        return True
    parts = pattern.split("*")
    if len(parts) == 1:
        return text == parts[0]
    if not text.startswith(parts[0]):
        return False
    idx = len(parts[0])
    for part in parts[1:-1]:
        pos = text.find(part, idx)
        if pos == -1:
            return False
        idx = pos + len(part)
    return text.endswith(parts[-1]) if parts[-1] else True
