"""Generate screen — report generation with progress and results."""

from __future__ import annotations

import asyncio
from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Label, ProgressBar, RichLog, Static
from textual.worker import Worker, WorkerState, get_current_worker

from ledgersight.tui.app import LedgerSightApp


class GenerateScreen(Screen[None]):
    """Run report generation and show progress."""

    DEFAULT_CSS = """
    GenerateScreen {
        padding: 1 2;
    }
    #gen-progress {
        width: 60;
        margin: 1 0;
    }
    #gen-log {
        height: 15;
        border: solid $primary-background;
        margin: 1 0;
    }
    #gen-status {
        height: 3;
        text-style: bold;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("Report Generation", classes="section-title")
        yield Label("Ready to generate.", id="gen-status")
        yield ProgressBar(total=100, id="gen-progress")
        yield RichLog(id="gen-log", highlight=True, markup=True)
        with Horizontal():
            yield Button("Back", id="btn-back")
            yield Button("Generate", id="btn-generate", variant="success")
            yield Button("Browse Transactions", id="btn-browse")

    def on_mount(self) -> None:
        log = self.query_one("#gen-log", RichLog)
        log.write("[bold]LedgerSight Report Generator[/bold]")
        log.write("Configure options, then click Generate.")
        log.write("")

    @on(Button.Pressed, "#btn-generate")
    async def _start_generate(self) -> None:
        app = self.app
        if not isinstance(app, LedgerSightApp):
            return

        self.query_one("#btn-generate", Button).disabled = True
        self.query_one("#gen-status", Label).update("Generating report...")
        progress = self.query_one("#gen-progress", ProgressBar)
        log = self.query_one("#gen-log", RichLog)

        self.run_worker(self._do_generate(), exclusive=True)

    async def _do_generate(self) -> None:
        app = self.app
        if not isinstance(app, LedgerSightApp):
            return

        log = self.query_one("#gen-log", RichLog)
        progress = self.query_one("#gen-progress", ProgressBar)
        status_label = self.query_one("#gen-status", Label)

        try:
            from ledgersight.business.report import build_report
            from ledgersight.categorizer import TransactionCategorizer
            from ledgersight.config import load_config
            from ledgersight.constants import _DEFAULT_CONFIG
            from ledgersight.reconciliation import reconcile_all

            config = app.state.config
            if config is None:
                config = load_config(Path(_DEFAULT_CONFIG))
                app.state.config = config

            statements = app.state.statements
            if not statements:
                log.write("[red]No statements loaded. Go back and load statements first.[/red]")
                status_label.update("Error: No statements loaded")
                self.query_one("#btn-generate", Button).disabled = False
                return

            log.write(f"[green]Loaded {len(statements)} statement(s)[/green]")
            progress.update(progress=10)

            if config.custom_rules:
                categorizer = TransactionCategorizer(config.custom_rules, config.merchant_aliases)
                categorizer.categorize_all(statements)
                categorizer.mark_credit_deposits(statements)
            log.write("[green]Transactions categorized[/green]")
            progress.update(progress=20)

            recon_results, all_ok, forced = reconcile_all(statements)
            app.state.recon_results = recon_results
            app.state.all_reconciled = all_ok
            if not all_ok and not forced:
                log.write("[yellow]Reconciliation warnings — some statements may not balance[/yellow]")
            else:
                log.write("[green]Reconciliation complete[/green]")
            progress.update(progress=30)

            output_path = Path(f"business_financial_report_{app.state.report_year or config.tax_year}.pdf")
            app.state.output_path = output_path

            await asyncio.to_thread(
                build_report,
                statements=statements,
                config=config,
                output_path=output_path,
                mode=app.state.report_mode,
                target_month=app.state.report_month,
                target_year=app.state.report_year,
                target_quarter=app.state.report_quarter,
                audit_path=Path(f"{output_path.stem}_audit.csv") if app.state.export_audit else None,
                pl_csv_path=Path(f"{output_path.stem}_pl.csv") if app.state.export_pl else None,
                cpa_export_dir=Path(f"{output_path.stem}_cpa") if app.state.export_cpa else None,
                do_projections=app.state.do_projections,
                scenario=app.state.scenario,
                mask_personal=False,
                allow_mismatch=False,
                strict=False,
                full_detail=True,
            )

            progress.update(progress=100)
            log.write(f"")
            log.write(f"[bold green]Report saved to: {output_path}[/bold green]")
            status_label.update(f"Complete — {output_path}")

        except Exception as exc:
            log.write(f"[red]Error: {exc}[/red]")
            status_label.update(f"Error: {exc}")
        finally:
            self.query_one("#btn-generate", Button).disabled = False

    @on(Button.Pressed, "#btn-back")
    async def _back(self) -> None:
        app = self.app
        if isinstance(app, LedgerSightApp):
            await app.goto_screen("report_config")

    @on(Button.Pressed, "#btn-browse")
    async def _browse(self) -> None:
        app = self.app
        if isinstance(app, LedgerSightApp):
            await app.goto_screen("txn_browser")

    async def navigate_next(self) -> None:
        app = self.app
        if isinstance(app, LedgerSightApp):
            await app.goto_screen("txn_browser")
