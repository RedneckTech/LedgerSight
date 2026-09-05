"""Parsing of First Interstate personal bank statement PDF text."""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal

from ledgersight.parsers import _clean_description, parse_amount
from ledgersight.personal.models import Statement, Transaction


def _is_page_artifact(line: str, stripped: str) -> bool:
    """Check if a line is a page header/footer artifact to skip."""
    if not stripped:
        return True
    if "\x0c" in line:
        return True
    if re.match(r"^[0-9A-F]{32}", stripped):
        return True
    if "Statement Ending" in stripped:
        return True
    if "continued" in stripped:
        return True
    if "Post Date" in stripped:
        return True
    if re.match(r"^Page \d+ of \d+", stripped):
        return True
    if re.match(r"^JACOB PFEIFF\s+X+", stripped):
        return True
    if re.match(r"BASIC CHECKING\s*-\s*X+", stripped):
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
    if re.match(r"^RETURN SERVICE REQUESTED", stripped):
        return True
    if re.match(r"^#\d+\s+", stripped):
        return True
    if re.match(r"^\d{1,2}\s*$", stripped):
        return True
    if re.match(r"[A-Z]{2}\s+\d{5}", stripped) and len(stripped) < 20:
        return True
    return False


def parse_statement(text: str, file_path: str = "") -> Statement:
    """Parse a First Interstate personal statement PDF text into a Statement."""
    lines = text.split("\n")

    statement_date = ""
    account_number = ""
    for line in lines[:15]:
        if not statement_date:
            m = re.search(r"Statement Ending\s+(\d{2}/\d{2}/\d{4})", line)
            if m:
                statement_date = m.group(1)
        if not account_number:
            m = re.search(r"(XXXXXXXXXXX\d{4})", line)
            if m:
                account_number = m.group(1)
        if statement_date and account_number:
            break

    account_type = "Savings" if re.search(r"REGULAR SAVINGS\s*-\s*X{4,}\d+", text) else "Checking"

    beginning_balance = Decimal("0")
    ending_balance = Decimal("0")
    total_credits = Decimal("0")
    total_debits = Decimal("0")
    credit_count = 0
    debit_count = 0
    period_start = ""

    in_summary = False
    for line in lines:
        if "Account Summary" in line:
            in_summary = True
            continue
        if not in_summary:
            continue
        if "Beginning Balance" in line:
            m = re.search(r"\$[\d,]+\.\d{2}", line)
            if m:
                beginning_balance = parse_amount(m.group())
            m_start = re.search(r"(\d{2}/\d{2}/\d{4})\s+Beginn", line)
            if m_start:
                period_start = m_start.group(1)
        elif "Credit" in line and "This Period" in line:
            m = re.search(r"(\d+)\s+Credit", line)
            if m:
                credit_count = int(m.group(1))
            m2 = re.search(r"\$[\d,]+\.\d{2}", line)
            if m2:
                total_credits = parse_amount(m2.group())
        elif "Debit" in line and "This Period" in line:
            m = re.search(r"(\d+)\s+Debit", line)
            if m:
                debit_count = int(m.group(1))
            m2 = re.search(r"\$[\d,]+\.\d{2}", line)
            if m2:
                total_debits = parse_amount(m2.group())
        elif "Ending Balance" in line:
            m = re.search(r"\$[\d,]+\.\d{2}", line)
            if m:
                ending_balance = parse_amount(m.group())
            break

    # ---- Account Activity ----
    transactions: list[Transaction] = []
    in_activity = False
    activity_started = False
    header_positions: dict[str, int] = {}
    desc_buffer: list[str] = []
    drain_to_misc = False

    for line in lines:
        if "Account Activity" in line and not activity_started:
            in_activity = True
            activity_started = True
            continue
        if not in_activity:
            continue
        if "Checks Cleared" in line:
            if desc_buffer and transactions:
                extra = _clean_description(" ".join(desc_buffer))
                if extra:
                    transactions[-1].description = _clean_description(f"{transactions[-1].description} {extra}")
                desc_buffer.clear()
            break
        if not line.strip():
            continue

        # Detect column header to find Debits/Credits/Balance columns
        if not header_positions and "Post Date" in line:
            for col_name in ["Debits", "Credits", "Balance"]:
                pos = line.find(col_name)
                if pos >= 0:
                    header_positions[col_name] = pos
            continue

        date_match = re.match(r"^(\d{2}/\d{2}/\d{4})", line)
        if date_match:
            post_date = date_match.group(1)

            # Skip balance-marker lines inside activity
            if "Beginning Balance" in line or "Ending Balance" in line:
                desc_buffer.clear()
                continue

            amounts = list(re.finditer(r"\$[\d,]+\.\d{2}", line))
            if not amounts:
                desc_part = line[date_match.end() :].strip()
                if desc_part:
                    desc_buffer.append(desc_part)
                continue

            if len(amounts) == 1:
                # e.g. "05/27/2026   Beginning Balance                       $645.07"
                continue

            balance = parse_amount(amounts[-1].group())
            tx_amount = parse_amount(amounts[-2].group())

            # Determine credit vs debit
            is_credit = False
            if header_positions:
                amount_col = amounts[-2].start()
                credit_col = header_positions.get("Credits", 9999)
                debit_col = header_positions.get("Debits", 0)
                # If amount lands in or beyond the Credits column
                if amount_col >= credit_col - 2:
                    is_credit = True
                elif amount_col < debit_col + 10 and amount_col + 6 < credit_col:
                    is_credit = False
                else:
                    # Fallback: use balance direction
                    if transactions:
                        is_credit = balance > transactions[-1].balance
                    elif beginning_balance:
                        is_credit = balance > beginning_balance
            else:
                if transactions:
                    is_credit = balance > transactions[-1].balance
                elif beginning_balance:
                    is_credit = balance > beginning_balance

            # Build full description
            desc_parts = list(desc_buffer)
            current_desc = line[date_match.end() : amounts[-2].start()].strip()
            has_own_desc = bool(current_desc)

            if has_own_desc:
                # desc_buffer lines belong to the PREVIOUS transaction
                if transactions:
                    stray_text = " ".join(desc_parts).strip()
                    if stray_text:
                        tx = transactions[-1]
                        tx.description = _clean_description(f"{tx.description} {stray_text}")
                desc_buffer.clear()
                description = _clean_description(current_desc)
            else:
                # desc_buffer lines are THIS transaction's description
                if current_desc:
                    desc_parts.append(current_desc)
                description = " ".join(desc_parts).strip()
                description = _clean_description(description)
                desc_buffer.clear()

            transactions.append(
                Transaction(
                    post_date=post_date,
                    description=description,
                    amount=tx_amount,
                    is_credit=is_credit,
                    balance=balance,
                )
            )
            # Rows whose description column stayed empty or only holds the
            # placeholder 'MISCELLANEOUS DEBIT' print their real detail on
            # the lines that FOLLOW the dated row, so drain those lines into
            # this transaction instead of leaving them for the next row.
            drain_to_misc = not description or description == "MISCELLANEOUS DEBIT"
        else:
            stripped = line.strip()
            if stripped and not _is_page_artifact(line, stripped):
                if drain_to_misc and transactions:
                    tx = transactions[-1]
                    tx.description = _clean_description(f"{tx.description} {stripped}")
                else:
                    desc_buffer.append(stripped)

    # ---- Checks Cleared ----
    checks: list[dict] = []
    in_checks = False
    check_row = re.compile(r"(\d+)\*?\s+(\d{2}/\d{2}/\d{4})\s+\$([\d,]+\.\d{2})")
    for line in lines:
        if "Checks Cleared" in line:
            in_checks = True
            continue
        if not in_checks:
            continue
        if "Daily Balances" in line:
            break
        for m in check_row.finditer(line):
            checks.append(
                {
                    "number": int(m.group(1)),
                    "date": m.group(2),
                    "amount": parse_amount("$" + m.group(3)),
                }
            )

    # ---- Daily Balances ----
    daily_balances: list[dict] = []
    in_daily = False
    for line in lines:
        if "Daily Balances" in line:
            in_daily = True
            continue
        if not in_daily:
            continue
        if "Overdraft" in line or "Total Overdraft Fees" in line:
            break
        pairs = re.findall(r"(\d{2}/\d{2}/\d{4})\s+(-?\$[\d,]+\.\d{2})", line)
        for date_str, amt_str in pairs:
            daily_balances.append({"date": date_str, "balance": parse_amount(amt_str)})

    # ---- Fees ----
    overdraft_fees = Decimal("0")
    returned_fees = Decimal("0")
    for line in lines:
        if "Total Overdraft Fees" in line:
            m = re.search(r"\$[\d,]+\.\d{2}", line)
            if m:
                overdraft_fees = parse_amount(m.group())
        if "Total Returned Item Fees" in line:
            m = re.search(r"\$[\d,]+\.\d{2}", line)
            if m:
                returned_fees = parse_amount(m.group())

    return Statement(
        statement_date=statement_date,
        period_start=period_start,
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
        overdraft_fees=overdraft_fees,
        returned_item_fees=returned_fees,
        account_type=account_type,
        institution="First Interstate",
        file_path=file_path,
    )


# --------------------------------------------------------------------------
# Capital One credit card statements
# --------------------------------------------------------------------------

_CARD_ROW_RE = re.compile(r"^([A-Z][a-z]{2} \d{1,2})\s+([A-Z][a-z]{2} \d{1,2})\s+(.+?)\s+(-?\s*\$[\d,]+\.\d{2})\s*$")


def _parse_card_date(date_str: str) -> str:
    """Convert 'Jun 27, 2026' to '06/27/2026'."""
    parsed = datetime.strptime(date_str.strip(), "%b %d, %Y")
    return parsed.strftime("%m/%d/%Y")


def _card_post_date_for(post_date: str, year: int, statement_month: int) -> str:
    """Convert a card row date like 'Jun 22' to '06/22/<year>'.

    A statement closing in January may list transactions from the prior
    December, so when the transaction month falls after the statement
    closing month the transaction belongs to the previous year.
    """
    parsed = datetime.strptime(post_date.strip(), "%b %d")
    if parsed.month > statement_month:
        year -= 1
    return parsed.replace(year=year).strftime("%m/%d/%Y")


def _card_summary_amount(text: str, label: str) -> Decimal:
    """Find a labeled dollar figure in the account summary section.

    'New Balance' is special: its value sits on the row following the
    label (the label line also carries the Cash Advances / Minimum
    Payment columns), so the label line is skipped for it.
    """
    idx = text.find(label)
    if idx < 0:
        return Decimal("0")
    window = text[idx : idx + 1000]
    lines = window.split("\n")
    start = 1 if label == "New Balance" else 0
    for line in lines[start:]:
        m = re.search(r"\$([\d,]+\.\d{2})", line)
        if m:
            return parse_amount(m.group(1))
    return Decimal("0")


def load_card_transactions(text: str) -> tuple[list[dict], list[dict]]:
    """Split Capital One transaction-table rows into credits and purchases."""
    credits: list[dict] = []
    purchases: list[dict] = []
    section: str | None = None
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        marker = re.match(r"^[A-Z][A-Z ]*#\d{4}:\s*([A-Za-z ,]+)", stripped)
        if marker:
            title = marker.group(1)
            if "Payments, Credits and Adjustments" in title:
                section = "credits"
            elif title == "Transactions" or title.startswith("Transactions"):
                section = "purchases"
            else:
                section = None
            continue
        if "Total Transactions for This Period" in stripped:
            continue
        m = _CARD_ROW_RE.match(stripped)
        if not m:
            continue
        row = {
            "trans_date": m.group(1),
            "post_date": m.group(2),
            "description": " ".join(m.group(3).split()),
            "amount": parse_amount(m.group(4)),
        }
        if section == "credits":
            credits.append(row)
        elif section == "purchases":
            purchases.append(row)
    return credits, purchases


def parse_capone_statement(text: str, file_path: str = "") -> Statement:
    """Parse a Capital One credit card statement PDF text into a Statement."""
    lines = text.split("\n")

    last4 = ""
    statement_date = ""
    for line in lines[:40]:
        m = re.search(r"ending in (\d{4})", line)
        if m and not last4:
            last4 = m.group(1)
        m2 = re.search(r"-\s*([A-Z][a-z]{2} \d{1,2}, \d{4})\s*\|", line)
        if m2 and not statement_date:
            statement_date = _parse_card_date(m2.group(1))
        if last4 and statement_date:
            break

    account_number = f"XXXXXXXXXXXX{last4}" if last4 else ""

    previous_balance = _card_summary_amount(text, "Previous Balance")
    new_balance = _card_summary_amount(text, "New Balance")
    cash_advances = _card_summary_amount(text, "Cash Advances")
    fees = _card_summary_amount(text, "Fees Charged")
    interest = _card_summary_amount(text, "Interest Charged")

    creds, purch = load_card_transactions(text)
    stmt_year = int(statement_date.split("/")[2])
    stmt_month = int(statement_date.split("/")[0])

    # Build the full chronological transaction list (with running balance).
    transactions: list[Transaction] = []
    for row in creds:
        transactions.append(
            Transaction(
                post_date=_card_post_date_for(row["post_date"], stmt_year, stmt_month),
                description=row["description"],
                amount=row["amount"],
                is_credit=True,
                balance=Decimal("0"),
            )
        )
    for row in purch:
        transactions.append(
            Transaction(
                post_date=_card_post_date_for(row["post_date"], stmt_year, stmt_month),
                description=row["description"],
                amount=row["amount"],
                is_credit=False,
                balance=Decimal("0"),
            )
        )
    if cash_advances > 0:
        transactions.append(
            Transaction(
                post_date=statement_date,
                description="CASH ADVANCE",
                amount=cash_advances,
                is_credit=False,
                balance=Decimal("0"),
            )
        )
    if fees > 0:
        transactions.append(
            Transaction(
                post_date=statement_date,
                description="FEES CHARGED",
                amount=fees,
                is_credit=False,
                balance=Decimal("0"),
                category="Bank Fees",
            )
        )
    if interest > 0:
        transactions.append(
            Transaction(
                post_date=statement_date,
                description="INTEREST CHARGED",
                amount=interest,
                is_credit=False,
                balance=Decimal("0"),
                category="Interest",
            )
        )

    transactions.sort(key=lambda tx: (tx.post_date, tx.amount))
    balance = previous_balance
    for tx in transactions:
        if tx.is_credit:
            balance -= tx.amount
        else:
            balance += tx.amount
        tx.balance = balance

    total_credits = sum((tx.amount for tx in transactions if tx.is_credit), Decimal("0"))
    total_debits = sum((tx.amount for tx in transactions if not tx.is_credit), Decimal("0"))
    credit_count = sum(1 for tx in transactions if tx.is_credit)
    debit_count = sum(1 for tx in transactions if not tx.is_credit)

    return Statement(
        statement_date=statement_date,
        account_number=account_number,
        beginning_balance=previous_balance,
        ending_balance=new_balance,
        total_credits=total_credits,
        total_debits=total_debits,
        credit_count=credit_count,
        debit_count=debit_count,
        transactions=transactions,
        fees_charged=fees,
        interest_charged=interest,
        account_type="Credit Card",
        institution="Capital One",
        file_path=file_path,
        period_start="",
    )


def parse_personal_statement(text: str, file_path: str = "") -> Statement:
    """Parse a personal bank statement PDF text, auto-detecting the source."""
    head = text[:5000]
    card_markers = any(m in head for m in ("Mastercard", "Visa", "Platinum"))
    if "ending in " in head and (card_markers or "capitalone" in text.lower()):
        return parse_capone_statement(text, file_path)
    return parse_statement(text, file_path)
