"""Statements screen — PDF loading, parsing, reconciliation status."""

from __future__ import annotations

from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import Button, DataTable, Label, Static

from ledgersight.tui.app import LedgerSightApp


class StatementsScreen(Screen[None]):
    """Load and parse bank statement PDFs."""

    DEFAULT_CSS = """
    StatementsScreen {
        padding: 1 2;
    }
    #stmt-table {
        height: 20;
        margin: 1 0;
    }
    .status-pass {
        color: $success;
    }
    .status-fail {
        color: $error;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("Statement Loading", classes="section-title")
        with Horizontal():
            yield Label("Data directory: ")
            yield Static("data/business/", id="data-dir")
        with Horizontal():
            yield Button("Load Statements", id="btn-load", variant="primary")
            yield Button("Back", id="btn-back")
        yield DataTable(id="stmt-table")
        yield Label("", id="stmt-summary")

    def on_mount(self) -> None:
        table = self.query_one("#stmt-table", DataTable)
        table.add_columns("File", "Month", "Transactions", "Credits", "Debits", "Reconciled")

    @on(Button.Pressed, "#btn-load")
    async def _load_statements(self) -> None:
        self.query_one("#stmt-summary", Label).update("Loading statements...")
        loader = self._load_statements_worker()
        self.run_worker(loader, exclusive=True)

    async def _load_statements_worker(self) -> None:
        app = self.app
        if not isinstance(app, LedgerSightApp):
            return

        from ledgersight.categorizer import TransactionCategorizer
        from ledgersight.config import load_config
        from ledgersight.constants import _DEFAULT_CONFIG
        from ledgersight.parsers import check_pdftotext, extract_text, find_pdfs, parse_statement
        from ledgersight.reconciliation import reconcile_all

        try:
            check_pdftotext()
        except SystemExit:
            self.query_one("#stmt-summary", Label).update(
                "Error: pdftotext not installed. Install poppler-utils."
            )
            return

        data_dir = Path("data/business")
        if not data_dir.exists():
            self.query_one("#stmt-summary", Label).update(
                "No data/business/ directory found."
            )
            return

        pdfs = find_pdfs(data_dir)
        if not pdfs:
            self.query_one("#stmt-summary", Label).update("No PDF files found.")
            return

        config = app.state.config
        if config is None:
            config = load_config(Path(_DEFAULT_CONFIG))

        import logging
        import subprocess
        logger = logging.getLogger("ledgersight.tui")

        statements = []
        for pdf_path in pdfs:
            self.query_one("#stmt-summary", Label).update(f"Parsing: {pdf_path.name}...")
            try:
                text = extract_text(pdf_path)
                stmt = parse_statement(text, str(pdf_path))
                if stmt.transactions:
                    statements.append(stmt)
            except subprocess.CalledProcessError as exc:
                logger.error("Failed to parse %s: %s", pdf_path, exc)
                continue

        if config and config.custom_rules:
            categorizer = TransactionCategorizer(config.custom_rules, config.merchant_aliases)
            categorizer.categorize_all(statements)
            categorizer.mark_credit_deposits(statements)

        recon_results, all_ok, _forced = reconcile_all(statements)

        app.state.statements = statements
        app.state.recon_results = recon_results
        app.state.all_reconciled = all_ok
        app.state.config = config

        table = self.query_one("#stmt-table", DataTable)
        table.clear()
        for stmt in statements:
            rr = next((r for r in recon_results if r.statement_label == stmt.month_label), None)
            status = "[green]PASS[/]" if (rr and rr.passed) else "[red]FAIL[/]" if rr else "N/A"
            table.add_row(
                Path(stmt.file_path).name if stmt.file_path else "?",
                stmt.month_label,
                str(len(stmt.transactions)),
                f"${stmt.total_credits:,.2f}",
                f"${stmt.total_debits:,.2f}",
                status,
            )

        self.query_one("#stmt-summary", Label).update(
            f"Loaded {len(statements)} statement(s) | "
            f"{sum(1 for r in recon_results if r.passed)}/{len(recon_results)} reconciled"
        )

    @on(Button.Pressed, "#btn-back")
    async def _back(self) -> None:
        app = self.app
        if isinstance(app, LedgerSightApp):
            await app.goto_screen("welcome")

    async def navigate_next(self) -> None:
        app = self.app
        if isinstance(app, LedgerSightApp):
            await app.goto_screen("categories")
