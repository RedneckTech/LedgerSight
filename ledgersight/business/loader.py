"""Shared statement loading for CLI and TUI."""
from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ledgersight.exceptions import LedgerSightError
from ledgersight.models import Statement
from ledgersight.parsers import (
    check_pdftotext,
    extract_text,
    file_hash,
    parse_statement,
    validate_statement,
)

logger = logging.getLogger("ledgersight.business.loader")


@dataclass
class StatementLoadResult:
    statements: list[Statement] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)


def load_statements(pdf_paths: list[Path]) -> StatementLoadResult:
    """Parse and validate a batch of statement PDFs."""
    result = StatementLoadResult()

    try:
        check_pdftotext()
    except LedgerSightError as exc:
        result.errors.append(str(exc))
        return result

    seen_hashes: set[str] = set()
    for pdf_path in pdf_paths:
        fhash = file_hash(pdf_path)
        if fhash in seen_hashes:
            logger.warning("Skipping duplicate file: %s", pdf_path)
            result.warnings.append(f"Skipping duplicate file: {pdf_path.name}")
            continue
        seen_hashes.add(fhash)

        try:
            text = extract_text(pdf_path)
            stmt = parse_statement(text, str(pdf_path))
            if stmt.transactions:
                stmt_warnings = validate_statement(stmt)
                if stmt_warnings:
                    for w in stmt_warnings:
                        logger.warning("%s: %s", pdf_path.name, w)
                        result.warnings.append(f"{pdf_path.name}: {w}")
                result.statements.append(stmt)
            else:
                msg = f"No transactions parsed from: {pdf_path}"
                logger.warning(msg)
                result.warnings.append(msg)
                result.rejected.append(str(pdf_path))
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError, UnicodeError, ValueError) as exc:
            msg = f"Failed to parse {pdf_path}: {exc}"
            logger.error(msg)
            result.errors.append(msg)
            result.rejected.append(str(pdf_path))
            continue

    return result
