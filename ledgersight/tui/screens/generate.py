"""Generate screen — report generation with progress and results."""

from __future__ import annotations

import asyncio
from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, Label, ProgressBar, RichLog, Static

from ledgersight.exceptions import LedgerSightError
from ledgersight.tui.app import LedgerSightApp


class OverwriteConfirm(ModalScreen[bool]):
    """Modal asking user to confirm overwrite."""

    DEFAULT_CSS = """
    OverwriteConfirm {
        align: center middle;
    }
    #dialog {
        width: 50;
        border: thick $warning;
        background: $surface;
        padding: 1 2;
    }
    """

    def __init__(self, filename: str) -> None:
        super().__init__()
        self._filename = filename

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static("Report already exists. Overwrite?")
            yield Static(f"[bold]{self._filename}[/bold]")
            with Horizontal():
                yield Button("Cancel", id="btn-cancel", variant="primary")
                yield Button("Replace", id="btn-replace", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-replace":
            self.dismiss(True)
        else:
            self.dismiss(False)


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

        from ledgersight.config import load_config
        from ledgersight.constants import _DEFAULT_CONFIG as _DEF_CFG

        config = app.state.config
        if config is None:
            config = load_config(Path(_DEF_CFG))
            app.state.config = config

        base = f"business_financial_report_{app.state.report_year or config.tax_year}"
        if app.state.report_month:
            output_name = f"{base}-{app.state.report_month:02d}.pdf"
        elif app.state.report_quarter:
            output_name = f"{base}-FY{app.state.report_year}FQ{app.state.report_quarter}.pdf"
        else:
            output_name = f"{base}.pdf"
        output_path = Path(output_name)
        app.state.output_path = output_path

        overwrite = False
        if output_path.exists():
            result = await self.app.push_screen(OverwriteConfirm(str(output_path.name)))
            if not result:
                self.query_one("#gen-status", Label).update("Cancelled.")
                return
            overwrite = True

        self.query_one("#btn-generate", Button).disabled = True
        self.query_one("#gen-status", Label).update("Generating report...")

        self.run_worker(self._do_generate(overwrite=overwrite), exclusive=True)

    async def _do_generate(self, overwrite: bool = False) -> None:
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

            output_path = app.state.output_path

            period_info = (
                f"year={app.state.report_year or config.tax_year}, "
                f"mode={app.state.report_mode}"
            )
            if app.state.report_month:
                period_info += f", month={app.state.report_month}"
            elif app.state.report_quarter:
                period_info += f", quarter=FQ{app.state.report_quarter}"
            log.write(f"[bold]Generating report: {period_info}[/bold]")

            result = await asyncio.to_thread(
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
                mask_personal=app.state.mask_personal,
                allow_mismatch=False,
                strict=False,
                full_detail=True,
                overwrite=overwrite,
            )

            if result:
                app.state.pl = result.pl
                app.state.monthly_pls = result.monthly_pls
                app.state.quarterly_pls = result.quarterly_pls
                app.state.projections = result.projections or []

            progress.update(progress=100)
            log.write("")
            log.write(f"[bold green]Report saved to: {output_path}[/bold green]")
            status_label.update(f"Complete — {output_path}")

        except LedgerSightError as exc:
            log.write(f"[red]{exc}[/red]")
            status_label.update(f"Error: {exc}")
        except Exception as exc:
            log.write(f"[red]Unexpected error: {exc}[/red]")
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
