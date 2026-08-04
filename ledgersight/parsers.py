"""PDF bank statement parsing utilities."""
from __future__ import annotations

import hashlib
import logging
import re
import subprocess
from datetime import date
from decimal import Decimal
from pathlib import Path

from ledgersight.constants import _ACTIVITY_SECTION_ENDINGS, MONEY_RE, RC_TOLERANCE
from ledgersight.exceptions import LedgerSightError
from ledgersight.models import Statement, Transaction

logger = logging.getLogger("ledgersight.parsers")


def parse_amount(text: str) -> Decimal:
    """Parse a dollar amount string into Decimal.

    Handles: $1,234.56, -$500.00, ($99.99), and plain numbers.
    """
    t = text.strip()
    m = MONEY_RE.search(t)
    if m:
        amt = m.group("amount").replace(",", "")
        sign = -1 if (m.group("minus") or m.group("paren")) else 1
        return Decimal(amt) * sign
    t = t.replace("$", "").replace(",", "").replace("\xa0", "")
    if not t or t == "-":
        return Decimal("0")
    return Decimal(t)


def fmt_dollar(val: Decimal) -> str:
    """Format Decimal as a dollar string."""
    sign = "-" if val < 0 else ""
    v = abs(val)
    return f"{sign}${v:,.2f}"


def fmt_dollar_plain(val: Decimal) -> str:
    """Format without currency sign."""
    sign = "-" if val < 0 else ""
    return f"{sign}{abs(val):,.2f}"


def safe_pct(numerator: Decimal, denominator: Decimal) -> str:
    """Safe percentage string, returning N/A on zero denominator."""
    if denominator == 0:
        return "N/A"
    pct = float(numerator / denominator) * 100
    return f"{pct:.1f}%"


def safe_float(val: Decimal) -> float:
    """Convert Decimal to float, useful for charting."""
    return float(val)


def safe_div(numerator: Decimal, denominator: Decimal) -> Decimal:
    """Safe division returning Decimal 0 on zero denominator."""
    if denominator == 0:
        return Decimal("0")
    return numerator / denominator


def find_pdfs(directory: Path) -> list[Path]:
    """Find all PDF files in a directory, excluding the generated report."""
    pdfs = []
    exclude_patterns = [
        "personal_financial_report", "business_financial_report",
    ]
    for p in sorted(directory.iterdir()):
        if not p.suffix.lower() == ".pdf":
            continue
        name_lower = p.name.lower()
        if any(pat in name_lower for pat in exclude_patterns):
            continue
        pdfs.append(p)
    return pdfs


def file_hash(path: Path) -> str:
    """SHA-256 hash of a file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parse_daily_date(date_str: str) -> date | None:
    """Parse a daily-balance date string like '01/15/2023' to a date object."""
    if not date_str:
        return None
    try:
        parts = date_str.split("/")
        return date(int(parts[2]), int(parts[0]), int(parts[1]))
    except (ValueError, IndexError):
        return None


def check_pdftotext() -> None:
    """Verify pdftotext is installed."""
    try:
        subprocess.run(
            ["pdftotext", "-v"],
            capture_output=True,
            timeout=5,
        )
    except FileNotFoundError:
        logger.error(
            "pdftotext not found. Install poppler-utils:\n"
            "  Ubuntu/Debian: sudo apt install poppler-utils\n"
            "  macOS: brew install poppler\n"
            "  Windows: https://github.com/oschwartz10612/poppler-windows/releases"
        )
        raise LedgerSightError(
            "pdftotext not found. Install poppler-utils."
        ) from None
    except Exception as exc:
        logger.error(f"Cannot run pdftotext: {exc}")
        raise LedgerSightError(
            f"Cannot run pdftotext: {exc}"
        ) from exc


def extract_text(pdf_path: Path) -> str:
    """Extract text from a PDF using pdftotext."""
    result = subprocess.run(
        ["pdftotext", "-layout", str(pdf_path), "-"],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return result.stdout


def _clean_description(desc: str) -> str:
    """Clean a transaction description."""
    desc = re.sub(r"\d\s+\d{2}/\d{2}", "", desc)
    desc = re.sub(r"\s+\d{1,2}\s*$", "", desc)
    desc = re.sub(r"\b\w{50,}\b", "", desc)
    desc = re.sub(r"\b([A-Za-z]+)\d{1,2}\b", r"\1", desc)
    desc = re.sub(r"\s+", " ", desc).strip()
    return desc


def _is_page_artifact(line: str, stripped: str) -> bool:
    """Check if a line is a page header/footer artifact to skip."""
    if not stripped:
        return True
    if "\x0c" in line:
        return True
    if re.match(r"^[0-9A-F]{32}", stripped):
        return True
    if "Statement Ending" in stripped and "Page" not in stripped:
        return True
    if "continued" in stripped.lower():
        return True
    if "Post Date" in stripped and "Description" in line:
        return True
    if re.match(r"^Page \d+ of \d+", stripped):
        return True
    if re.match(r"^[A-Z\s]+LLC\s+X+", stripped):
        return True
    if "BASIC CHECKING" in stripped and "XXXX" in stripped:
        return True
    if "Checking Account Statements" in stripped:
        return True
    if "Account Activity" in stripped:
        return True
    if "Managing Your Accounts" in stripped:
        return True
    if "Client Contact" in stripped:
        return True
    if re.match(r"^\d{8}\s+Checking Account Statements", stripped):
        return True
    if "RETURN SERVICE REQUESTED" in stripped:
        return True
    if re.match(r"^#\d+\s+", stripped):
        return True
    if re.match(r"^\d{1,2}\s*$", stripped):
        return True
    if re.match(r"[A-Z]{2}\s+\d{5}", stripped) and len(stripped) < 20:
        return True
    if "Summary of Accounts" in stripped:
        return True
    if re.match(r"^(?:CLASSIC|BASIC|BUSINESS)\s+CHECKING", stripped):
        return True
    if "Average Ledger" in stripped:
        return True
    if "Service Charges" in stripped:
        return True
    return False


def _is_section_ending(line: str) -> bool:
    """Return True if *line* is a recognised next-section heading."""
    stripped = line.strip()
    for heading in _ACTIVITY_SECTION_ENDINGS:
        if heading in stripped:
            return True
    return False


def parse_statement(text: str, file_path: str = "") -> Statement:
    """Parse a First Interstate Bank statement PDF text into a Statement.

    Uses a section-aware state machine that terminates transaction
    extraction on recognised next-section headings instead of relying
    on a single "Checks Cleared" sentinel.
    """
    lines = text.split("\n")

    statement_date = ""
    account_number = ""
    for line in lines[:15]:
        if not statement_date:
            m = re.search(r"Statement Ending\s+(\d{2}/\d{2}/\d{4})", line)
            if m:
                statement_date = m.group(1)
        if not account_number:
            m = re.search(r"(XXXXXXXXXXX?\d{4})", line)
            if m:
                account_number = m.group(1)
        if statement_date and account_number:
            break

    beginning_balance = Decimal("0")
    ending_balance = Decimal("0")
    total_credits = Decimal("0")
    total_debits = Decimal("0")
    credit_count = 0
    debit_count = 0

    in_summary = False
    for line in lines:
        if "Account Summary" in line:
            in_summary = True
            continue
        if not in_summary:
            continue
        if _is_section_ending(line):
            break
        if "Beginning Balance" in line:
            m = MONEY_RE.search(line)
            if m:
                beginning_balance = parse_amount(m.group())
        elif "Credit" in line and "This Period" in line:
            m = re.search(r"(\d+)\s+Credit", line)
            if m:
                credit_count = int(m.group(1))
            m2 = MONEY_RE.search(line)
            if m2:
                total_credits = parse_amount(m2.group())
        elif "Debit" in line and "This Period" in line:
            m = re.search(r"(\d+)\s+Debit", line)
            if m:
                debit_count = int(m.group(1))
            m2 = MONEY_RE.search(line)
            if m2:
                total_debits = parse_amount(m2.group())
        elif "Ending Balance" in line:
            m = MONEY_RE.search(line)
            if m:
                ending_balance = parse_amount(m.group())
            break

    # ---- Account Activity (section-aware extraction) ----
    transactions: list[Transaction] = []
    tx_seq = 0
    in_activity = False
    activity_started = False
    header_positions: dict[str, int] = {}
    desc_buffer: list[str] = []

    for line in lines:
        if "Account Activity" in line and not activity_started:
            in_activity = True
            activity_started = True
            continue
        if not in_activity:
            continue
        if not line.strip():
            continue

        # Column-header detection
        if not header_positions and "Post Date" in line:
            for col_name in ["Debits", "Credits", "Balance"]:
                pos = line.find(col_name)
                if pos >= 0:
                    header_positions[col_name] = pos
            continue

        # ---- Check for a dated transaction line FIRST ----
        # A line may contain both a date+transaction AND a later
        # section heading (e.g. "02/28 SALES TAX Daily Balances ...").
        # We must extract the transaction before deciding to stop.
        date_match = re.match(r"^(\d{2}/\d{2}/\d{4})", line)
        if date_match:
            post_date = date_match.group(1)

            if "Beginning Balance" in line or "Ending Balance" in line:
                desc_buffer.clear()
                continue

            amounts = list(MONEY_RE.finditer(line))
            if not amounts:
                desc_part = line[date_match.end():].strip()
                if desc_part:
                    desc_buffer.append(desc_part)
                continue

            # ---- Determine balance and amount columns ----
            balance = parse_amount(amounts[-1].group())

            if len(amounts) == 1:
                # Single-amount line: could be a balance-only row, a
                # wrapped continuation, or a stand-alone amount with
                # balance on another line.  Try column-position
                # heuristics first.
                if header_positions:
                    amt_col = amounts[0].start()
                    bal_col = header_positions.get("Balance", 9999)
                    if amt_col >= bal_col - 3:
                        # Amount is in the Balance column position —
                        # treat as a balance-only line, skip.
                        continue
                # Fallback: treat as a description-line continuation.
                desc_part = line[date_match.end():amounts[0].start()].strip()
                if desc_part:
                    desc_buffer.append(desc_part)
                continue

            tx_amount = parse_amount(amounts[-2].group())

            # ---- Credit/debit detection (arithmetic first) ----
            # Running-balance arithmetic is the most reliable indicator.
            # Column-position heuristics are the fallback.
            is_credit = False
            prev_bal = (transactions[-1].balance if transactions
                        else beginning_balance)

            credited = abs((prev_bal + tx_amount) - balance) <= RC_TOLERANCE
            debited = abs((prev_bal - tx_amount) - balance) <= RC_TOLERANCE

            if credited and not debited:
                is_credit = True
            elif debited and not credited:
                is_credit = False
            elif credited and debited:
                # Ambiguous (amount = 0 or balance didn't change).
                # Fall through to column-position heuristics.
                pass

            # Column-position fallback when arithmetic is inconclusive
            if not (credited or debited) or (credited and debited):
                if header_positions:
                    amount_col = amounts[-2].start()
                    credit_col = header_positions.get("Credits", 9999)
                    debit_col = header_positions.get("Debits", 0)
                    if amount_col >= credit_col - 2:
                        is_credit = True
                    elif amount_col < debit_col + 10 and amount_col + 6 < credit_col:
                        is_credit = False
                    else:
                        if transactions:
                            is_credit = balance > transactions[-1].balance
                        elif beginning_balance:
                            is_credit = balance > beginning_balance
                else:
                    if transactions:
                        is_credit = balance > transactions[-1].balance
                    elif beginning_balance:
                        is_credit = balance > beginning_balance

            desc_parts = list(desc_buffer)
            current_desc = line[date_match.end():amounts[-2].start()].strip()
            has_own_desc = bool(current_desc)

            if has_own_desc:
                if transactions:
                    stray_text = " ".join(desc_parts).strip()
                    if stray_text:
                        tx = transactions[-1]
                        tx.description = _clean_description(
                            f"{tx.description} {stray_text}"
                        )
                        tx.original_description = tx.description
                desc_buffer.clear()
                description = _clean_description(current_desc)
            else:
                if current_desc:
                    desc_parts.append(current_desc)
                description = " ".join(desc_parts).strip()
                description = _clean_description(description)
                desc_buffer.clear()

            tx_seq += 1
            transactions.append(Transaction(
                post_date=post_date,
                description=description,
                original_description=description,
                amount=tx_amount,
                is_credit=is_credit,
                balance=balance,
                source_statement=file_path,
                sequence=tx_seq,
            ))

            # After parsing the transaction line, check whether this
            # line ALSO contains a section-ending heading that tells us
            # to stop reading further lines.
            if _is_section_ending(line):
                break
            continue

        # ---- No date: check for section ending ----
        if _is_section_ending(line):
            if desc_buffer and transactions:
                extra = _clean_description(" ".join(desc_buffer))
                if extra:
                    tx = transactions[-1]
                    tx.description = _clean_description(
                        f"{tx.description} {extra}"
                    )
                    tx.original_description = tx.description
                desc_buffer.clear()
            break

        # ---- Plain description continuation line ----
        stripped = line.strip()
        if stripped and not _is_page_artifact(line, stripped):
            desc_buffer.append(stripped)

    # Checks Cleared
    checks: list[dict] = []
    in_checks = False
    for line in lines:
        if "Checks Cleared" in line:
            in_checks = True
            continue
        if not in_checks:
            continue
        if "Daily Balances" in line:
            break
        m = re.match(r"\s*(\d+)\s+(\d{2}/\d{2}/\d{4})\s+\$([\d,]+\.\d{2})", line)
        if m:
            checks.append({
                "number": int(m.group(1)),
                "date": m.group(2),
                "amount": parse_amount("$" + m.group(3)),
            })

    # Daily Balances
    daily_balances: list[dict] = []
    in_daily = False
    for line in lines:
        if "Daily Balances" in line:
            in_daily = True
            continue
        if not in_daily:
            continue
        # Stop on next section headings
        if "Overdraft" in line or "Total Overdraft Fees" in line or _is_section_ending(line):
            break
        pairs = re.findall(r"(\d{2}/\d{2}/\d{4})\s+(-?\$[\d,]+\.\d{2})", line)
        for date_str, amt_str in pairs:
            daily_balances.append({
                "date": date_str,
                "balance": parse_amount(amt_str),
            })

    return Statement(
        statement_date=statement_date,
        account_number=account_number,
        beginning_balance=beginning_balance,
        ending_balance=ending_balance,
        total_credits=total_credits,
        total_debits=total_debits,
        credit_count=credit_count,
        debit_count=debit_count,
        transactions=transactions,
        checks_cleared=checks,
        daily_balances=daily_balances,
        file_path=file_path,
    )


def validate_statement(stmt: Statement) -> list[str]:
    """Validate that a parsed statement has required metadata. Returns warnings."""
    warnings = []
    if not stmt.statement_date:
        warnings.append("Missing statement date")
    elif stmt.statement_date.strip():
        try:
            parts = stmt.statement_date.split("/")
            if len(parts) != 3:
                warnings.append(f"Unparseable statement date: {stmt.statement_date}")
        except (ValueError, IndexError):
            warnings.append(f"Unparseable statement date: {stmt.statement_date}")
    if not stmt.account_number:
        warnings.append("Missing account number")
    if stmt.beginning_balance == 0 and stmt.ending_balance == 0 and stmt.transactions:
        warnings.append("Summary balances not found (beginning/ending)")
    if stmt.credit_count == 0 and stmt.debit_count == 0 and stmt.transactions:
        warnings.append("Credit/debit counts not found in summary")
    # Validate transaction dates are plausible
    for tx in stmt.transactions[:3]:
        try:
            tx.date_obj
        except (ValueError, IndexError):
            warnings.append(f"Invalid transaction date: {tx.post_date}")
            break
    return warnings
