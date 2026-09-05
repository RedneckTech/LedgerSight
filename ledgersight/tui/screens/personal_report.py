"""Personal report screen — configure and generate a personal financial report."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, cast

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import (
    Button,
    Input,
    Label,
    ProgressBar,
    RichLog,
    Select,
    Static,
    Switch,
)

from ledgersight.exceptions import LedgerSightError
from ledgersight.tui.app import LedgerSightApp

if TYPE_CHECKING:
    from ledgersight.personal.models import Statement as PersonalStatement

MONTHS = [("All months", 0)] + [(str(i), i) for i in range(1, 13)]
MODES = [
    ("Auto", None),
    ("Combined (yearly + monthly)", "combined"),
    ("Yearly", "yearly"),
    ("Monthly", "monthly"),
]


class PersonalReportScreen(Screen[None]):
    """Configure personal report options and generate the PDF."""

    DEFAULT_CSS = """
    PersonalReportScreen {
        padding: 1 2;
    }
    .config-row {
        height: 3;
        margin: 0 1;
    }
    .config-label {
        width: 24;
        content-align: left middle;
        text-style: bold;
    }
    #pr-progress {
        width: 60;
        margin: 1 0;
    }
    #pr-log {
        height: 12;
        border: solid $primary-background;
        margin: 1 0;
    }
    #pr-status {
        height: 3;
        text-style: bold;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("Personal Financial Report", classes="section-title")
        with Vertical():
            with Horizontal(classes="config-row"):
                yield Label("Directory:", classes="config-label")
                yield Input(id="directory", value="data/Personal")
            with Horizontal(classes="config-row"):
                yield Label("Year (optional):", classes="config-label")
                yield Input(id="year", placeholder="e.g. 2026")
            with Horizontal(classes="config-row"):
                yield Label("Month:", classes="config-label")
                yield Select(MONTHS, id="month", value=0, allow_blank=False)
            with Horizontal(classes="config-row"):
                yield Label("Mode:", classes="config-label")
                yield Select(MODES, id="mode", value=None, allow_blank=False)
            with Horizontal(classes="config-row"):
                yield Label("Audit CSV:", classes="config-label")
                yield Switch(id="audit", value=False)
            with Horizontal(classes="config-row"):
                yield Label("Mask Personal Data:", classes="config-label")
                yield Switch(id="mask", value=False)
        yield Label("Ready.", id="pr-status")
        yield ProgressBar(total=100, id="pr-progress")
        yield RichLog(id="pr-log", highlight=True, markup=True)
        with Horizontal():
            yield Button("Back", id="btn-back")
            yield Button("Generate Report", id="btn-generate", variant="success")

    def on_mount(self) -> None:
        log = self.query_one("#pr-log", RichLog)
        log.write("[bold]Personal Financial Report[/bold]")
        log.write("Pick a directory of statement PDFs, then click Generate.")
        log.write("")
        app = self.app
        if isinstance(app, LedgerSightApp):
            self.query_one("#directory", Input).value = app.state.data_dir

    @on(Button.Pressed, "#btn-generate")
    def _start_generate(self) -> None:
        self.run_worker(self._generate_flow(), exclusive=True)

    @on(Button.Pressed, "#btn-back")
    async def _back(self) -> None:
        app = self.app
        if isinstance(app, LedgerSightApp):
            await app.goto_screen("welcome")

    async def navigate_prev(self) -> None:
        await self._back()

    async def navigate_next(self) -> None:
        await self._back()

    def _parse_statements(self, pdf_files: list[Path]) -> list[PersonalStatement]:
        """Parse all PDFs into personal statements (run in a worker thread)."""
        from ledgersight.parsers import extract_text
        from ledgersight.personal.parser import parse_personal_statement

        statements: list[PersonalStatement] = []
        log = self.query_one("#pr-log", RichLog)
        for pdf_path in pdf_files:
            try:
                text = extract_text(pdf_path)
                stmt = parse_personal_statement(text, file_path=str(pdf_path))
            except Exception as exc:
                log.write(f"[red]Failed to parse {pdf_path.name}: {exc}[/red]")
                continue
            if stmt.statement_date and stmt.transactions:
                statements.append(stmt)
        return statements

    async def _generate_flow(self) -> None:
        app = self.app
        if not isinstance(app, LedgerSightApp):
            return

        try:
            year_raw = self.query_one("#year", Input).value.strip()
            year = int(year_raw) if year_raw else None
        except ValueError:
            self.notify("Year must be a number", severity="error")
            return

        directory = self.query_one("#directory", Input).value.strip()
        month = cast(int, self.query_one("#month", Select).value)
        mode = cast("str | None", self.query_one("#mode", Select).value)
        audit = self.query_one("#audit", Switch).value
        mask = self.query_one("#mask", Switch).value

        # Mode resolution mirrors the CLI.
        if mode:
            report_mode = mode
        elif month:
            report_mode = "monthly"
        elif year:
            report_mode = "yearly"
        else:
            report_mode = "combined"

        if report_mode == "yearly" and month:
            self.notify("Yearly mode and a specific month are incompatible", severity="error")
            return

        dir_path = Path(directory)
        if not dir_path.exists():
            self.notify(f"No such directory: {directory}", severity="error")
            return

        status_label = self.query_one("#pr-status", Label)
        log = self.query_one("#pr-log", RichLog)
        progress = self.query_one("#pr-progress", ProgressBar)

        try:
            from ledgersight.personal.cli import _find_statement_pdfs
            from ledgersight.personal.report import _reconcile_statements, generate_report

            status_label.update("Parsing statements...")
            pdf_files = _find_statement_pdfs(dir_path)
            if not pdf_files:
                log.write("[red]No PDF files found in that directory.[/red]")
                status_label.update("Error: no PDF files found")
                return

            parsed = await asyncio.to_thread(self._parse_statements, pdf_files)
            app.state.personal_statements = parsed
            statements = [s for s in parsed if s.transactions]
            if not statements:
                log.write("[red]No valid statements found.[/red]")
                status_label.update("Error: no valid statements")
                return
            log.write(f"[green]Parsed {len(statements)} statement(s)[/green]")
            progress.update(progress=15)

            year_infer = year
            if year_infer is None and month:
                matching_years = sorted({s.year for s in statements if s.month == month})
                if not matching_years:
                    log.write(f"[red]No statements found for month {month}.[/red]")
                    status_label.update("Error: month not found")
                    return
                year_infer = matching_years[-1]
                log.write(f"[dim]Inferred year {year_infer} for month {month}[/dim]")
                year = year_infer

            yr = year or statements[0].year
            if month:
                output_name = f"personal_financial_report_{month:02d}_{yr}.pdf"
            elif yr:
                output_name = f"personal_financial_report_{yr}.pdf"
            else:
                output_name = "personal_financial_report.pdf"
            output_path = dir_path / output_name
            audit_path = dir_path / f"{output_path.stem}_audit.csv" if audit else None

            if output_path.exists():
                from ledgersight.tui.screens.generate import OverwriteConfirm

                result = await app.push_screen(OverwriteConfirm(output_path.name), wait_for_dismiss=True)
                if not result:
                    status_label.update("Cancelled.")
                    return

            status_label.update("Reconciling statements...")
            recon_ok = await asyncio.to_thread(_reconcile_statements, statements)
            app.state.all_reconciled = recon_ok
            if recon_ok:
                log.write("[green]Reconciliation passed[/green]")
            else:
                log.write("[yellow]Reconciliation warnings — generating anyway[/yellow]")
            progress.update(progress=30)

            log.write(f"[bold]Generating report: mode={report_mode}, year={yr}[/bold]")
            status_label.update("Generating report...")

            await asyncio.to_thread(
                generate_report,
                statements,
                output_path,
                mode=report_mode,
                target_month=month if month else None,
                target_year=year,
                audit_path=audit_path,
                mask_personal=mask,
                allow_mismatch=not recon_ok,
            )

            progress.update(progress=100)
            log.write("")
            log.write(f"[bold green]Report saved to: {output_path}[/bold green]")
            status_label.update(f"Complete — {output_path.name}")

        except LedgerSightError as exc:
            log.write(f"[red]{exc}[/red]")
            status_label.update(f"Error: {exc}")
        except Exception as exc:
            log.write(f"[red]Unexpected error: {exc}[/red]")
            status_label.update(f"Error: {exc}")
