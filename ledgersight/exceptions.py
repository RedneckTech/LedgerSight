"""LedgerSight-specific exceptions."""

class LedgerSightError(RuntimeError):
    """Base exception for LedgerSight errors."""


class ConfigurationError(LedgerSightError):
    """Invalid or missing configuration."""


class ReconciliationError(LedgerSightError):
    """Statement reconciliation failed."""


class ReportGenerationError(LedgerSightError):
    """Report generation could not complete."""


class NoStatementsError(LedgerSightError):
    """No valid statements were found."""
