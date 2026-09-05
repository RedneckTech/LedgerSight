"""Categorization rules and helpers for personal transactions."""

from __future__ import annotations

import re

from ledgersight.personal.models import Statement

CATEGORY_RULES: dict[str, list[str]] = {
    "Payroll": [
        r"RICHERS TRUCKING PAYROLL",
    ],
    "Deposit": [
        r"\bDEPOSIT\b",
    ],
    "Rent": [
        r"125 S Roosevelt Ave",
    ],
    "Insurance": [
        r"PROGRESSIVE INS",
    ],
    "Transfers": [
        r"WEB XFER",
        r"ZELLE",
        r"PAYPAL INST XFER",
        r"^XXXXXX\d{4}\s+\d{1,2}/\d{1,2}/\d{2}$",
    ],
    "Fuel": [
        r"\bPILOT\b",
        r"FLYING J",
        r"LOVE'?S",
        r"KWIK STAR",
        r"\bMARATHON\b",
        r"\bYESWAY\b",
        r"CIRCLE K",
        r"\bTRAVEL CNT\b",
        r"\bTRAVEL CENTER",
        r"\bTA\b\s+(?:#\d+\s+)?[A-Z]{3,}",  # TA truck stops: TA #153 LAREDO, TA DENTON
        r"\bCASEYS\b",
        r"\bBP#\d",
        r"\bBB OF HOUSTON\b",
        r"\bBREAK TIME\b",
        r"\bSHELL\b.*SERVICE",
        r"\b59 FASTLANE\b",
        r"\bCTLP\b",
        r"\bWOODSHED",  # no trailing \b: pdftotext merges city names (e.g. "WOODSHEDBIG CABIN")
        r"\bPORT AUTO TRUCK\b",
        r"\bTEX BEST\b",
        r"\bTBS\b.*DENISON",
        r"\bPHILLIPS\s*66",
        r"\bEXXON\b",
    ],
    "Restaurants": [
        r"SONIC DRIVE",
        r"\bARBYS?\b",
        r"MCDONALD",
        r"TEXAS BEST (?:SMOKE|BBQ)",
        r"HOSHI JAPANE",
        r"\bARANDAS\b",
        r"DOORDASH",
        r"HUCK'?S FOOD",
        r"\bWENDYS?\b",
        r"BURGER KING",
        r"TACO JOHN",
        r"PANCHEROS[\s-]*MEXICA",
        r"CARL'?S\s*JR",
        r"\bDENNY'?S?\b",
        r"\bHARDEE'?S\b",
        r"DAIRY QUEEN",
        r"\bHAYMAKERS?\b",
        r"STILL SMOKIN",
        r"HONG KONG BUFFET",
        r"\bSUBWAY\b",
    ],
    "Groceries": [
        r"WAL-MART",
        r"WALMART",
        r"WM SUPERCENTER",
        r"HY[ -]?VEE",
        r"\bMEPO\s+FOODS\b",
    ],
    "Subscriptions": [
        r"PAYPAL.*DISCORD",
        r"PAYPAL PURCHASE.*(?:HIDIVE|TWITCH|YOUTUBE|DASHDRAM|STORYMAT|SHRIKELI|CLOCKWOR|NETSHORT)",
        r"APPLE\W?COM.*BILL",
        r"GOOGLE\s+(?!.*(?:DramaBox|My Drama|DashDram|StoryMat))",
        r"NETFLIX",
        r"HULU",
        r"OPENAI",
        r"LEGALSHIELD",
        r"Oracle America",
        r"AMAZON PRIME",
        r"Prime Video",
        r"EXPERIAN\b",
        r"SHORTMAX",
        r"\biDrama\b",
        r"DramaBox",
        r"My Drama",
        r"DashDram",
        r"StoryMat",
        r"ANOMALY",
        r"\bAudible\b",
        r"TV.?C PRO DRIVER",
        r"HIDIVE",
        r"HFinance.*Simplex",
        r"Dramawave",
        r"\bNETSHORT\b",
        r"\bSTORYREEL\b",
    ],
    "Shopping": [
        r"AMAZON\b.*\b(?:MARK|MKTPL)\b",
        r"\bSQ\b(?!.*HOSHI)",
        r"SHIP'?S PLACE",
        r"THE KERR SPOT",
        r"LOWE'?S\b",
        r"\bTRACTOR SUPPLY\b",
        r"\bDOLLAR.GENERAL\b",
        r"WESTLAND THEATRE",
        r"MNRD-WESTBULITN",
        r"\bNINTENDO\b",
        r"SP\s+OLD\s+90\s+MARKET",
        r"NAME\s+DOT\s+STORE",
        r"CLOUD FACTORY",
        r"\bWL STEAM\b",
    ],
    "Auto Care": [
        r"LAZER SPOT",
        r"HOMETOWN CARWASH",
        r"PETRO DODGE",
        r"TRUCKPARKINGCLUB",
        r"HEARTLAND HARLEY",
        r"COL\s+JCT\s+AUTO",
    ],
    "Utilities": [
        r"ALLIANT ENERGY",
        r"ALLPAID",
        r"FARMERS ELEVATOR",
        r"SCHOOL PROCESSIN",
    ],
    "Loan/Credit Payment": [
        r"CAPITAL ONE",
        r"JD BYRIDER",
        r"\bChime\b",
    ],
    "Bank Fees": [
        r"JACOB PFEIFF.*XXXXXXXXXXX",
    ],
    "Checks": [
        r"CHECK #",
    ],
    "Government": [
        r"IOWA JUDICIAL",
        r"\bUSPS\b",
    ],
}

CATEGORY_COLORS: dict[str, str] = {
    "Payroll": "#27ae60",
    "Deposit": "#2ecc71",
    "Rent": "#c0392b",
    "Insurance": "#d35400",
    "Transfers": "#2980b9",
    "Fuel": "#e74c3c",
    "Restaurants": "#e67e22",
    "Groceries": "#f1c40f",
    "Subscriptions": "#9b59b6",
    "Shopping": "#1abc9c",
    "Utilities": "#16a085",
    "Auto Care": "#1abc9c",
    "Loan/Credit Payment": "#8e44ad",
    "Interest": "#e67e22",
    "Bank Fees": "#95a5a6",
    "Checks": "#d35400",
    "Government": "#7f8c8d",
    "Other": "#bdc3c7",
}

BANK_FEE_PRECEDENCE_PATTERNS = [
    r"MasterCard Cross Border",
    r"SALES TAX",
    r"Overdraft",
    r"FEE FOR DDA",
    r"DDA WITHDRAWAL",
    r"SERVICE CHARGE",
]

SUBSCRIPTION_PRECEDENCE_PATTERNS = [
    r"PAYPAL (?:INST XFER|PURCHASE).*HIDIVE",
    r"PAYPAL (?:INST XFER|PURCHASE).*DISCORD",
    r"PAYPAL (?:INST XFER|PURCHASE).*TWITCH",
    r"PAYPAL.*CLOCKWOR",
    r"PAYPAL.*SHRIKELI",
    r"DASHPASS",
]


def clean_memo_prefix(description: str) -> str:
    """Strip a leading First Interstate masked-account memo fragment.

    First Interstate prepends the literal masked account number and post date
    (``XXXXXX6781 03/24/26``) to merchant memos. The fragment alone must not
    decide the category, so remove it when real content follows.
    """
    return re.sub(r"^XXXXXX\d{4}\s+(?:\d{1,2}/\d{1,2}/\d{2}\s+)+(?=\S)", "", description, flags=re.IGNORECASE)


def categorize(description: str) -> str:
    """Return the spending category for a description."""
    for pat in BANK_FEE_PRECEDENCE_PATTERNS:
        if re.search(pat, description, re.IGNORECASE):
            return "Bank Fees"
    for pat in SUBSCRIPTION_PRECEDENCE_PATTERNS:
        if re.search(pat, description, re.IGNORECASE):
            return "Subscriptions"
    for cat, patterns in CATEGORY_RULES.items():
        if cat in ("Bank Fees", "Subscriptions"):
            continue
        for pat in patterns:
            if re.search(pat, description, re.IGNORECASE):
                return cat
    for pat in CATEGORY_RULES.get("Subscriptions", []):
        if re.search(pat, description, re.IGNORECASE):
            return "Subscriptions"
    return "Other"


def categorize_transactions(statements: list[Statement]) -> None:
    """Categorize every transaction in place.

    Pre-assigned categories (e.g. parser-generated Interest/Fees entries)
    are preserved. A leading ``XXXXXXdddd MM/DD/YY`` memo fragment (First
    Interstate) is stripped before rules run so it never implies a transfer.
    """
    for stmt in statements:
        for tx in stmt.transactions:
            tx.description = clean_memo_prefix(tx.description)
            if not tx.category:
                tx.category = categorize(tx.description)
