"""Parsing of First Interstate personal bank statement PDF text."""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal

from ledgersight.constants import MONEY_RE
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
    if re.match(r"^[A-Z]{2}\s+\d{5}(?:-\d{4})?$", stripped):
        return True  # address footer "IA 52601" (not "OK 00865713 151124", a description tail)
    return False


# A debit-card row's full text has two parts - "MERCHANT CITY ST 12345678 123456"
# (POS rows print shorter references such as "600001 031333" or "1 287733")
# and the memo "XX0844 DEBIT CARD mm/dd hh:mm" (or "XX0844 POS PINNED ...",
# "XX0844 DDA WITHDRAWAL ...") - in either order. Transfers end with the
# counterparty account and a short date ("... XXXXXX3608 6/24/26"). These
# patterns tell an incomplete description from a complete one when the page
# layout breaks the usual symmetry.
_MEMO_KINDS = r"(?:DEBIT CARD|POS PINNED|DDA WITHDRAWAL)"
_MEMO_FULL_RE = re.compile(rf"\bXX\d{{4}}\s+{_MEMO_KINDS}\s+\d{{2}}/\d{{2}}(?:\s+\d{{2}}:\d{{2}})?")
_MEMO_FRAGMENT_RE = re.compile(r"\bXX\d{4}\b|\bDEBIT CARD\b|\bPOS PINNED\b|\bPINNED\b|\bDDA WITHDRAWAL\b")
_REF_PAIR_RE = re.compile(r"\b\d{1,8}\s+\d{4,6}\b")
_TRAILING_REF_RE = re.compile(r"\b\d{6,8}$")
_XFER_TAIL_RE = re.compile(r"\b\d{1,2}/\d{1,2}/\d{2}\s*$")
_NON_CARD_PREFIXES = ("MasterCard Cross Border Fee", "FEE FOR")
_CONTINUATION_RES = (
    # memo-only line, optionally led by the second half of a split reference:
    # "PINNED 02/17 10:38", "DEBIT CARD 06/16 17:10", "048941 XX0844 POS PINNED 08/01 19:37"
    re.compile(
        r"^(?:\d{1,8}\s+)?(?:XX\d{4}\s+)?(?:DEBIT CARD|POS PINNED|PINNED|CARD|DDA WITHDRAWAL)"
        r"\s+\d{2}/\d{2}(?:\s+\d{2}:\d{2})?$"
    ),
    # location + references, upper case, at most three words of city/phone:
    # "NEWARK NJ 41860326 812703", "SAN FRANCISCO CA 67616426 553076",
    # "4029357733 CA 05530267 403601", "OK 00865713 151124". A merchant-first
    # line ("GOOGLE DramaBox Mountain View CA 64925886 329831") has more
    # words and lower case.
    re.compile(r"^(?:[A-Z0-9][A-Z0-9.'&-]*\s+){0,3}[A-Z]{2}\s+\d{1,8}\s+\d{4,6}$"),
    # bare reference pair: "40683082 506947"
    re.compile(r"^\d{1,8}\s+\d{4,6}$"),
    # memo date/time left over when the memo wrapped after "DEBIT CARD": "07/06 07:21"
    re.compile(r"^\d{2}/\d{2}\s+\d{2}:\d{2}$"),
    # transfer tail: "6/24/26", "XXXXXX3608 6/24/26"
    re.compile(r"^(?:X{4,}\d{4}\s+)?\d{1,2}/\d{1,2}/\d{2}$"),
)
_START_RES = (
    # memo followed by merchant text: "XX0844 DEBIT CARD 08/03 06:40 GOOGLE My Drama"
    re.compile(rf"^XX\d{{4}}\s+{_MEMO_KINDS}\s+\d{{2}}/\d{{2}}(?:\s+\d{{2}}:\d{{2}})?\s+\S"),
    re.compile(r"^\d{6}\s+WEB XFER\b"),
    re.compile(
        r"^(?:DEPOSIT|SERVICE CHARGE|MISCELLANEOUS DEBIT|CAPITAL ONE|FEE FOR|MasterCard Cross Border"
        r"|PAYPAL (?:PURCHASE|INST XFER)|RICHERS TRUCKING|SALES TAX)\b"
    ),
)


def _description_incomplete(desc: str) -> bool:
    """True when a First Interstate description is visibly missing a part."""
    has_fragment = bool(_MEMO_FRAGMENT_RE.search(desc))
    memo_first = _MEMO_FULL_RE.match(desc)
    has_full_memo = bool(_MEMO_FULL_RE.search(desc))
    has_refs = bool(_REF_PAIR_RE.search(desc))
    non_card = desc.startswith(_NON_CARD_PREFIXES)
    if has_fragment and not has_full_memo:
        return True  # "... XX0844 POS", "... XX0844 DEBIT", "... XX0844"
    if memo_first and "DDA" not in memo_first.group(0) and not _REF_PAIR_RE.search(desc[memo_first.end() :]):
        return True  # "XX0844 DEBIT CARD 06/22 21:31 OPENAI CHATGPT SAN" (merchant tail missing)
    if has_refs and not has_fragment and not non_card:
        return True  # "GOOGLE DramaBox Mountain View CA 64925886 225666" (memo missing)
    if not has_fragment and not non_card and _TRAILING_REF_RE.search(desc):
        return True  # "WESTLAND THEATRE WEST BURLINGT IA 08102091" (reference split across lines)
    if "WEB XFER" in desc and not _XFER_TAIL_RE.search(desc):
        return True  # "649104 WEB XFER FROM REGULAR SAVINGS" (account/date tail missing)
    if "DDA WITHDRAWAL" in desc and not has_refs:
        return True  # "FEE FOR DDA WITHDRAWAL 07/31 20:39 715 HIGHWAY" (location tail missing)
    return False


def _looks_like_continuation(line: str) -> bool:
    """True when a line can only be the tail of a description, never its start."""
    return any(rx.match(line) for rx in _CONTINUATION_RES)


def _looks_like_start(line: str) -> bool:
    """True when a line can only begin a description, never continue one."""
    return any(rx.match(line) for rx in _START_RES)


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
            m = MONEY_RE.search(line)
            if m:
                beginning_balance = parse_amount(m.group())
            m_start = re.search(r"(\d{2}/\d{2}/\d{4})\s+Beginn", line)
            if m_start:
                period_start = m_start.group(1)
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

    # ---- Account Activity ----
    #
    # Layout (pdftotext -layout): each dated row is vertically centred against
    # its wrapped description cell. A one-line description prints ON the dated
    # row; a two-line description prints its first line ABOVE the dated row
    # and its second line BELOW it (three lines: above / inline / below). So a
    # transaction owes exactly as many continuation lines after its dated row
    # as it had description lines before it. Tracking that count is what keeps
    # "NEWARK NJ 41860326 812703" attached to the Audible charge instead of
    # being glued onto the front of the next row's description.
    transactions: list[Transaction] = []
    in_activity = False
    activity_started = False
    header_positions: dict[str, int] = {}
    desc_buffer: list[str] = []  # description lines printed above the next dated row
    pending_below = 0  # continuation lines still owed to transactions[-1]
    page = 1  # pdftotext separates pages with a form feed

    for line in lines:
        if "\x0c" in line:
            page += line.count("\x0c")
        if "Account Activity" in line and not activity_started:
            in_activity = True
            activity_started = True
            continue
        if not in_activity:
            continue
        if "Checks Cleared" in line or "Daily Balances" in line:
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
                pending_below = 0
                continue

            amounts = list(MONEY_RE.finditer(line))
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

            # Build the description: lines printed above this dated row, then
            # any text on the row itself. The same number of lines will follow.
            above = list(desc_buffer)
            desc_buffer.clear()
            current_desc = line[date_match.end() : amounts[-2].start()].strip()
            parts = above + ([current_desc] if current_desc else [])
            description = _clean_description(" ".join(parts))
            pending_below = len(above)

            transactions.append(
                Transaction(
                    post_date=post_date,
                    description=description,
                    amount=tx_amount,
                    is_credit=is_credit,
                    balance=balance,
                    source_page=page,
                    source_row=len(transactions) + 1,
                )
            )
        else:
            stripped = line.strip()
            if stripped and not _is_page_artifact(line, stripped):
                last = transactions[-1] if transactions else None
                if last is not None and pending_below > 0:
                    if not _description_incomplete(last.description) and _looks_like_start(stripped):
                        # Symmetry over-counted (the row was already complete
                        # and this line can only begin a record): start the
                        # next record instead.
                        pending_below = 0
                        desc_buffer.append(stripped)
                    else:
                        last.description = _clean_description(f"{last.description} {stripped}")
                        pending_below -= 1
                elif (
                    last is not None
                    and not desc_buffer
                    and _description_incomplete(last.description)
                    and _looks_like_continuation(stripped)
                ):
                    # The dated row was merged with its first description line
                    # (page bottom), so symmetry saw no lines above it; the
                    # tail still follows - possibly on the next page.
                    last.description = _clean_description(f"{last.description} {stripped}")
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
        pairs = re.findall(r"(\d{2}/\d{2}/\d{4})\s+(-?\s*\$[\d,]+\.\d{2})", line)
        for date_str, amt_str in pairs:
            daily_balances.append({"date": date_str, "balance": parse_amount(amt_str)})

    # ---- Fees ----
    overdraft_fees = Decimal("0")
    returned_fees = Decimal("0")
    for line in lines:
        if "Total Overdraft Fees" in line:
            m = MONEY_RE.search(line)
            if m:
                overdraft_fees = parse_amount(m.group())
        if "Total Returned Item Fees" in line:
            m = MONEY_RE.search(line)
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

    The year is applied before parsing the day so that leap-day rows on
    February statements validate against the statement year instead of
    the 1900 default used by ``strptime`` without a year.
    """
    month_str, day_str = post_date.strip().split()
    month = datetime.strptime(month_str, "%b").month
    effective_year = year - 1 if month > statement_month else year
    parsed = datetime.strptime(f"{month_str} {day_str} {effective_year}", "%b %d %Y")
    return parsed.strftime("%m/%d/%Y")


def _card_summary_amount(text: str, label: str, absolute: bool = False, window_chars: int = 3000) -> Decimal | None:
    """Find a labeled dollar figure in the account summary section.

       Returns None when the label is not present so callers can distinguish
       a missing summary field from a present zero.

       'New Balance' is special: its value sits on the row following the
       label (the label line also carries the Cash Advances / Minimum
       Payment columns), so the label line is skipped for it.

       Payment/credit labels are often printed as "Payments - $100.00" even
       though the amount is a positive credit; set *absolute* to True for
       those labels to discard the decorative minus sign.

       The search is restricted to the first *window_chars* characters and
    skips section headers such as "#0142: Payments, Credits and Adjustments"
       that introduce transaction tables rather than summary totals.
    """
    search_text = text[:window_chars]
    start_pos = 0
    while True:
        idx = search_text.find(label, start_pos)
        if idx < 0:
            return None
        # Locate the line containing this label.
        line_start = search_text.rfind("\n", 0, idx) + 1
        line_end = search_text.find("\n", idx)
        if line_end < 0:
            line_end = len(search_text)
        label_line = search_text[line_start:line_end]
        # Section headers like "JACOB C PFEIFF #0142: Payments, Credits and
        # Adjustments" introduce tables, not summary totals.
        if re.search(r"#\d{4}:\s*", label_line):
            start_pos = idx + len(label)
            continue
        window = search_text[idx : idx + 1000]
        lines = window.split("\n")
        # Summary values live on the label line itself (or the next line for
        # New Balance). Table headers have column headings before any money.
        start = 1 if label == "New Balance" else 0
        for line in lines[start : start + 2]:
            m = MONEY_RE.search(line)
            if m:
                val = parse_amount(m.group())
                return abs(val) if absolute else val
        start_pos = idx + len(label)
    return Decimal("0")


def _card_summary_amount_first(text: str, labels: tuple[str, ...], absolute: bool = False) -> Decimal | None:
    """Return the first non-None summary amount for any of *labels*."""
    for label in labels:
        val = _card_summary_amount(text, label, absolute=absolute)
        if val is not None:
            return val
    return None


def _card_summary_amount_sum(text: str, labels: tuple[str, ...], absolute: bool = False) -> Decimal | None:
    """Sum every present summary amount for *labels*.

    Some statements split payment/credit totals across multiple lines
    (e.g. "Payments" and "Other Credits"), while others use a single
    combined line. Summing handles both cases without double-counting
    missing labels.
    """
    total: Decimal | None = None
    for label in labels:
        val = _card_summary_amount(text, label, absolute=absolute)
        if val is not None:
            total = (total or Decimal("0")) + val
    return total


def load_card_transactions(text: str) -> tuple[list[dict], list[dict]]:
    """Split Capital One transaction-table rows into credits and purchases."""
    credits: list[dict] = []
    purchases: list[dict] = []
    section: str | None = None
    page = 1
    for line in text.split("\n"):
        if "\x0c" in line:
            page += line.count("\x0c")
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
        # Capital One may prefix payment/credit amounts with a minus sign in
        # the column even though they reduce the balance owed. Store all credit
        # and debit amounts as positive values; the section determines sign.
        row = {
            "trans_date": m.group(1),
            "post_date": m.group(2),
            "description": " ".join(m.group(3).split()),
            "amount": abs(parse_amount(m.group(4))),
            "page": page,
        }
        if section == "credits":
            credits.append(row)
        elif section == "purchases":
            purchases.append(row)
    return credits, purchases


def _card_terms(text: str) -> tuple[str, Decimal, Decimal, Decimal]:
    """(payment due date, minimum payment, credit limit, purchase APR %) from a card statement."""
    due = ""
    m = re.search(r"Payment Due Date:\s*([A-Z][a-z]{2} \d{1,2}, \d{4})", text)
    if m:
        due = _parse_card_date(m.group(1))
    minimum = Decimal("0")
    # The remittance stub prints "New Balance   Minimum Payment Due   Amount Enclosed"
    # with the amounts on the following non-blank line.
    lines = text.split("\n")
    for idx, line in enumerate(lines):
        if "Minimum Payment Due" in line and "New Balance" in line:
            for nxt in lines[idx + 1 : idx + 4]:
                amounts = re.findall(r"\$[\d,]+\.\d{2}", nxt)
                if len(amounts) >= 2:
                    minimum = parse_amount(amounts[1])
                    break
            if minimum:
                break
    limit = Decimal("0")
    m = re.search(r"Credit Limit\s+\$([\d,]+\.\d{2})", text)
    if m:
        limit = parse_amount("$" + m.group(1))
    apr = Decimal("0")
    m = re.search(r"Purchases\s+(\d{1,2}\.\d{2})%", text)
    if m:
        apr = Decimal(m.group(1))
    return due, minimum, limit, apr


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

    previous_balance = _card_summary_amount(text, "Previous Balance") or Decimal("0")
    new_balance = _card_summary_amount(text, "New Balance") or Decimal("0")
    cash_advances = _card_summary_amount(text, "Cash Advances") or Decimal("0")
    fees = _card_summary_amount(text, "Fees Charged") or Decimal("0")
    interest = _card_summary_amount(text, "Interest Charged") or Decimal("0")
    # Payments/credits may be labeled several ways across statement variants.
    # Their labels often carry a decorative minus ("Payments - $100.00") even
    # though the value is a positive credit, so request the absolute value.
    # Some statements split the total across "Payments" and "Other Credits",
    # so sum every present label rather than stopping at the first match.
    payments_credits = _card_summary_amount_sum(
        text,
        (
            "Payments, Credits and Adjustments",
            "Payments and Other Credits",
            "Payments",
            "Other Credits",
        ),
        absolute=True,
    )
    transactions_total = _card_summary_amount(text, "Transactions")
    due_date, minimum_payment, credit_limit, apr = _card_terms(text)

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
                source_page=row.get("page", 0),
                source_row=len(transactions) + 1,
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
                source_page=row.get("page", 0),
                source_row=len(transactions) + 1,
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

    parsed_credits = sum((tx.amount for tx in transactions if tx.is_credit), Decimal("0"))
    parsed_debits = sum((tx.amount for tx in transactions if not tx.is_credit), Decimal("0"))
    parsed_credit_count = sum(1 for tx in transactions if tx.is_credit)
    parsed_debit_count = sum(1 for tx in transactions if not tx.is_credit)

    # Prefer independently parsed summary totals; fall back to computed totals
    # only when the statement does not print the summary line.
    total_credits = payments_credits if payments_credits is not None else parsed_credits
    total_debits = (
        (transactions_total or Decimal("0")) + cash_advances + fees + interest
        if transactions_total is not None
        else parsed_debits
    )
    credit_count = parsed_credit_count
    debit_count = parsed_debit_count

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
        payment_due_date=due_date,
        minimum_payment=minimum_payment,
        credit_limit=credit_limit,
        apr_purchases=apr,
    )


def parse_personal_statement(text: str, file_path: str = "") -> Statement:
    """Parse a personal bank statement PDF text, auto-detecting the source."""
    head = text[:5000]
    card_markers = any(m in head for m in ("Mastercard", "Visa", "Platinum"))
    if "ending in " in head and (card_markers or "capitalone" in text.lower()):
        return parse_capone_statement(text, file_path)
    return parse_statement(text, file_path)
