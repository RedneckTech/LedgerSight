"""Report configuration screen — year, period, mode, and export options."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Input, Label, Select, Static, Switch

from ledgersight.tui.app import LedgerSightApp

MODES = [
    ("Combined (full report)", "combined"),
    ("Monthly", "monthly"),
    ("Quarterly", "quarterly"),
    ("Yearly", "yearly"),
    ("CPA Package", "cpa"),
]

SCENARIOS = [
    ("Base", "base"),
    ("Conservative", "conservative"),
    ("Growth", "growth"),
    ("All", "all"),
]

QUARTERS = [
    ("All (yearly)", 0),
    ("Q1", 1), ("Q2", 2), ("Q3", 3), ("Q4", 4),
]


class ReportConfigScreen(Screen[None]):
    """Set report generation options."""

    DEFAULT_CSS = """
    ReportConfigScreen {
        padding: 1 2;
    }
    .config-row {
        height: 3;
        margin: 0 1;
    }
    .config-label {
        width: 22;
        content-align: left middle;
        text-style: bold;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Report Configuration", classes="section-title")
            with Horizontal(classes="config-row"):
                yield Label("Year:", classes="config-label")
                yield Input(id="year", value="2025")
            with Horizontal(classes="config-row"):
                yield Label("Month (1-12, 0=all):", classes="config-label")
                yield Input(id="month", value="0")
            with Horizontal(classes="config-row"):
                yield Label("Quarter:", classes="config-label")
                yield Select(*QUARTERS, id="quarter")
            with Horizontal(classes="config-row"):
                yield Label("Mode:", classes="config-label")
                yield Select(*MODES, id="mode", value="combined")
            with Horizontal(classes="config-row"):
                yield Label("Projections:", classes="config-label")
                yield Switch(id="projections", value=False)
            with Horizontal(classes="config-row"):
                yield Label("Audit CSV:", classes="config-label")
                yield Switch(id="export_audit", value=False)
            with Horizontal(classes="config-row"):
                yield Label("Export P&L CSV:", classes="config-label")
                yield Switch(id="export_pl", value=False)
            with Horizontal(classes="config-row"):
                yield Label("Export CPA CSV:", classes="config-label")
                yield Switch(id="export_cpa", value=False)
            with Horizontal(classes="config-row"):
                yield Label("Mask Personal Data:", classes="config-label")
                yield Switch(id="mask_personal", value=False)
            with Horizontal(classes="config-row"):
                yield Label("Scenario:", classes="config-label")
                yield Select(*SCENARIOS, id="scenario", value="base")
            with Horizontal():
                yield Button("Back", id="btn-back")
                yield Button("Generate Report", id="btn-next", variant="success")

    def on_mount(self) -> None:
        app = self.app
        if isinstance(app, LedgerSightApp):
            if app.state.report_year:
                self.query_one("#year", Input).value = str(app.state.report_year)
            elif app.state.config:
                self.query_one("#year", Input).value = str(app.state.config.tax_year)

    @on(Button.Pressed, "#btn-next")
    async def _generate(self) -> None:
        app = self.app
        if not isinstance(app, LedgerSightApp):
            return

        try:
            year = int(self.query_one("#year", Input).value or "0")
            month_val = int(self.query_one("#month", Input).value or "0")
        except (ValueError, TypeError):
            self.notify("Year and month must be numbers", severity="error")
            return

        if month_val > 0 and int(self.query_one("#quarter", Select).value or 0) > 0:
            self.notify("Cannot select both month and quarter", severity="error")
            return

        app.state.report_year = year
        app.state.report_month = month_val if month_val > 0 else None
        quarter_val = self.query_one("#quarter", Select).value
        app.state.report_quarter = int(quarter_val) if quarter_val and int(quarter_val) > 0 else None
        app.state.report_mode = self.query_one("#mode", Select).value or "combined"
        app.state.do_projections = self.query_one("#projections", Switch).value
        app.state.export_audit = self.query_one("#export_audit", Switch).value
        app.state.export_pl = self.query_one("#export_pl", Switch).value
        app.state.export_cpa = self.query_one("#export_cpa", Switch).value
        app.state.mask_personal = self.query_one("#mask_personal", Switch).value
        app.state.scenario = self.query_one("#scenario", Select).value or "base"
        await app.goto_screen("generate")

    @on(Button.Pressed, "#btn-back")
    async def _back(self) -> None:
        app = self.app
        if isinstance(app, LedgerSightApp):
            await app.goto_screen("categories")

    async def navigate_next(self) -> None:
        await self._generate()
