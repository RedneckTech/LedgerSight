"""Categories screen — browse and manage categorization rules."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import Button, DataTable, Label, Static

from ledgersight.tui.app import LedgerSightApp


class CategoriesScreen(Screen[None]):
    """Browse category rules and see match counts."""

    DEFAULT_CSS = """
    CategoriesScreen {
        padding: 1 2;
    }
    #rule-table {
        height: 20;
        margin: 1 0;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("Category Rules", classes="section-title")
        with Horizontal():
            yield Button("Refresh", id="btn-refresh")
            yield Button("Back", id="btn-back")
            yield Button("Continue", id="btn-next", variant="success")
        yield DataTable(id="rule-table")
        yield Label("", id="rule-summary")

    def on_mount(self) -> None:
        table = self.query_one("#rule-table", DataTable)
        table.add_columns("Priority", "Pattern", "Category", "Tax Category", "Direction")
        self._populate_rules()

    def _populate_rules(self) -> None:
        app = self.app
        if not isinstance(app, LedgerSightApp) or app.state.config is None:
            return

        table = self.query_one("#rule-table", DataTable)
        table.clear()

        rules = app.state.config.custom_rules
        for rule in rules:
            table.add_row(
                str(rule.priority),
                rule.pattern[:60],
                rule.category,
                rule.tax_category,
                rule.direction,
            )

        self.query_one("#rule-summary", Label).update(f"{len(rules)} rule(s) loaded")

    @on(Button.Pressed, "#btn-refresh")
    async def _refresh(self) -> None:
        self._populate_rules()

    @on(Button.Pressed, "#btn-back")
    async def _back(self) -> None:
        app = self.app
        if isinstance(app, LedgerSightApp):
            await app.goto_screen("statements")

    @on(Button.Pressed, "#btn-next")
    async def _next(self) -> None:
        app = self.app
        if isinstance(app, LedgerSightApp):
            await app.goto_screen("report_config")

    async def navigate_next(self) -> None:
        await self._next()
