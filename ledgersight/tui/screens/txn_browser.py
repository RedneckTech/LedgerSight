"""Transaction browser — searchable, sortable transaction table."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import Button, DataTable, Input, Label, Static

from ledgersight.tui.app import LedgerSightApp


class TxnBrowserScreen(Screen[None]):
    """Browse all transactions with search and filtering."""

    DEFAULT_CSS = """
    TxnBrowserScreen {
        padding: 1 2;
    }
    #search-input {
        width: 40;
    }
    #txn-table {
        height: 1fr;
        margin: 1 0;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("Transaction Browser", classes="section-title")
        with Horizontal():
            yield Input(placeholder="Search description...", id="search-input")
            yield Label("", id="search-count")
        yield DataTable(id="txn-table", cursor_type="row")
        with Horizontal():
            yield Button("Back", id="btn-back")
            yield Button("P&L Overview", id="btn-pl")

    def on_mount(self) -> None:
        table = self.query_one("#txn-table", DataTable)
        table.add_columns("Date", "Description", "Category", "Amount", "Type", "In P&L?")
        self._populate_transactions()

    def _populate_transactions(self, filter_text: str = "") -> None:
        app = self.app
        if not isinstance(app, LedgerSightApp):
            return

        table = self.query_one("#txn-table", DataTable)
        table.clear()

        count = 0
        search = filter_text.lower()
        for stmt in app.state.statements:
            for tx in stmt.transactions:
                description = tx.description if hasattr(tx, 'description') else tx.original_description
                if search and search not in description.lower():
                    continue
                count += 1
                if count > 500:
                    continue
                table.add_row(
                    tx.post_date,
                    description[:60],
                    (tx.business_category if hasattr(tx, 'business_category')
                     else tx.category if hasattr(tx, 'category')
                     else "?"),
                    f"${tx.amount:,.2f}",
                    "Credit" if tx.is_credit else "Debit",
                    "Yes" if (hasattr(tx, 'include_in_pnl') and tx.include_in_pnl) else "No",
                )

        self.query_one("#search-count", Label).update(
            f"Showing {min(count, 500)} of {sum(len(s.transactions) for s in app.state.statements)} transactions"
        )

    @on(Input.Changed, "#search-input")
    def _on_search(self, event: Input.Changed) -> None:
        self._populate_transactions(event.value)

    @on(Button.Pressed, "#btn-back")
    async def _back(self) -> None:
        app = self.app
        if isinstance(app, LedgerSightApp):
            await app.goto_screen("generate")

    @on(Button.Pressed, "#btn-pl")
    async def _pl_overview(self) -> None:
        app = self.app
        if isinstance(app, LedgerSightApp):
            await app.goto_screen("pl_overview")

    async def navigate_next(self) -> None:
        await self._pl_overview()
