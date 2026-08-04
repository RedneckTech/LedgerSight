"""P&L Overview screen — summary of profit and loss."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import Button, DataTable, Label, Static

from ledgersight.tui.app import LedgerSightApp


class PLOverviewScreen(Screen[None]):
    """View profit and loss summary."""

    DEFAULT_CSS = """
    PLOverviewScreen {
        padding: 1 2;
    }
    #pl-table {
        height: 20;
        margin: 1 0;
    }
    .pl-positive {
        color: $success;
    }
    .pl-negative {
        color: $error;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("Profit & Loss Overview", classes="section-title")
        yield DataTable(id="pl-table")
        yield Label("", id="pl-summary")
        with Horizontal():
            yield Button("Back", id="btn-back")
            yield Button("Done", id="btn-done", variant="success")

    def on_mount(self) -> None:
        table = self.query_one("#pl-table", DataTable)
        table.add_columns("Line Item", "Amount")

        self._populate_pl()

    def _populate_pl(self) -> None:
        app = self.app
        if not isinstance(app, LedgerSightApp):
            self.query_one("#pl-summary", Label).update("Generate a report first to see P&L data.")
            return

        if app.state.pl is None:
            self.query_one("#pl-summary", Label).update("Generate a report first to see P&L data.")
            return

        pl = app.state.pl
        table = self.query_one("#pl-table", DataTable)
        table.clear()

        table.add_row("[bold]Total Revenue[/bold]", f"${pl.total_revenue:,.2f}")
        table.add_row("[bold]Total COGS/Direct Costs[/bold]", f"(${pl.total_direct_costs:,.2f})")
        table.add_row("[bold green]Gross Profit[/bold green]", f"${pl.gross_profit:,.2f}")
        table.add_row("  Gross Margin", pl.gross_margin)
        table.add_row("[bold]Total Operating Expenses[/bold]", f"(${pl.total_operating_expenses:,.2f})")
        table.add_row("[bold green]Operating Profit[/bold green]", f"${pl.operating_profit:,.2f}")
        table.add_row("  Operating Margin", pl.operating_margin)
        table.add_row("[bold green]Net Profit[/bold green]", f"${pl.net_profit:,.2f}")
        table.add_row("  Net Margin", pl.net_margin)

        # Non-P&L buckets
        if pl.non_pnl_credits or pl.non_pnl_debits:
            table.add_row("", "")
            table.add_row("Non-P&L Credits", f"${pl.non_pnl_credits:,.2f}")
            table.add_row("Non-P&L Debits", f"(${pl.non_pnl_debits:,.2f})")

        self.query_one("#pl-summary", Label).update(f"Period: {pl.label}")

    @on(Button.Pressed, "#btn-back")
    async def _back(self) -> None:
        app = self.app
        if isinstance(app, LedgerSightApp):
            await app.goto_screen("generate")

    @on(Button.Pressed, "#btn-done")
    async def _done(self) -> None:
        app = self.app
        if isinstance(app, LedgerSightApp):
            await app.goto_screen("welcome")
