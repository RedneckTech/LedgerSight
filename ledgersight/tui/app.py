"""LedgerSight TUI — Textual application with hybrid wizard + sidebar navigation.

Keybindings:
    Ctrl+B       Toggle sidebar
    Ctrl+Right   Next screen (wizard)
    Ctrl+Left    Previous screen (wizard)
    Ctrl+G       Jump to Generate
    Ctrl+Q       Quit
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Footer, Header, ListItem, ListView, Static

if TYPE_CHECKING:
    from ledgersight.business.pl import ProfitAndLoss
    from ledgersight.business.projections import ProjectionResult
    from ledgersight.models import BusinessConfig, ReconciliationResult, Statement


@dataclass
class AppState:
    """Shared application state passed between screens."""

    config: BusinessConfig | None = None
    config_path: Path | None = None
    data_dir: str = "data/business"
    statements: list[Statement] = field(default_factory=list)
    recon_results: list[ReconciliationResult] = field(default_factory=list)
    all_reconciled: bool = False
    pl: ProfitAndLoss | None = None
    monthly_pls: dict[str, ProfitAndLoss] = field(default_factory=dict)
    quarterly_pls: dict[tuple[int, int], ProfitAndLoss] = field(default_factory=dict)
    projections: list[ProjectionResult] = field(default_factory=list)
    report_year: int | None = None
    report_month: int | None = None
    report_quarter: int | None = None
    report_mode: str = "combined"
    do_projections: bool = False
    export_audit: bool = False
    export_pl: bool = False
    export_cpa: bool = False
    scenario: str = "base"
    mask_personal: bool = False
    output_path: Path | None = None


WIZARD_ORDER = [
    "welcome",
    "config_editor",
    "statements",
    "categories",
    "report_config",
    "generate",
]

SIDEBAR_LABELS: dict[str, str] = {
    "welcome": "Welcome",
    "config_editor": "Config Editor",
    "statements": "Statements",
    "categories": "Category Rules",
    "report_config": "Report Config",
    "generate": "Generate Report",
    "txn_browser": "Browse Transactions",
    "pl_overview": "P&L Overview",
}


class NavItem(ListItem):
    """Sidebar navigation item."""

    def __init__(self, screen_id: str, label: str) -> None:
        super().__init__(Static(f"  {label}"))
        self.screen_id = screen_id


class Sidebar(VerticalScroll):
    """Left sidebar with screen navigation."""


class ContentArea(VerticalScroll):
    """Main content area where screens are rendered."""


class ConfirmQuitScreen(ModalScreen[bool]):
    """Modal confirmation dialog for quitting."""

    DEFAULT_CSS = """
    ConfirmQuitScreen {
        align: center middle;
    }
    #dialog {
        width: 60;
        height: auto;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("Quit LedgerSight?", id="dialog")


LedgerSightAppCSS = """
Screen {
    layers: sidebar content;
}

Sidebar {
    layer: sidebar;
    width: 30;
    height: 100%;
    dock: left;
    background: $surface;
    border-right: solid $primary;
    display: none;
}

Sidebar.-visible {
    display: block;
}

Sidebar ListView {
    height: 100%;
}

Sidebar NavItem {
    padding: 1 0;
}

ContentArea {
    layer: content;
    height: 100%;
}

#wizard-nav {
    dock: bottom;
    height: 3;
    background: $surface;
    border-top: solid $primary-background;
    padding: 0 1;
}

.wizard-btn {
    width: 20;
}
"""


class LedgerSightApp(App[None]):
    """LedgerSight Textual application."""

    CSS = LedgerSightAppCSS
    BINDINGS = [
        Binding("ctrl+b", "toggle_sidebar", "Sidebar", show=True),
        Binding("ctrl+right", "next_screen", "Next", show=True),
        Binding("ctrl+left", "prev_screen", "Back", show=True),
        Binding("ctrl+g", "goto_generate", "Generate", show=True),
        Binding("ctrl+q", "quit", "Quit", show=True),
    ]

    TITLE = "LedgerSight"
    SUB_TITLE = "Financial Report Generator"

    def __init__(self) -> None:
        super().__init__()
        self.state = AppState()
        self._current_wizard = 0
        self._sidebar_visible = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        yield Sidebar(ListView())
        yield ContentArea()
        yield Footer()

    async def on_mount(self) -> None:
        self.sidebar = self.query_one(Sidebar)
        self.sidebar_list = self.query_one(ListView)
        self.content = self.query_one(ContentArea)

        for screen_id, label in SIDEBAR_LABELS.items():
            self.sidebar_list.append(NavItem(screen_id, label))

        await self.push_screen("welcome")

    # ------------------------------------------------------------------
    # Screen management
    # ------------------------------------------------------------------

    async def push_screen(self, screen_id: str) -> None:
        """Push a named screen onto the content area."""
        self._update_sidebar_highlight(screen_id)
        if screen_id == "welcome":
            from ledgersight.tui.screens.welcome import WelcomeScreen
            await super().push_screen(WelcomeScreen())
        elif screen_id == "config_editor":
            from ledgersight.tui.screens.config_editor import ConfigEditorScreen
            await super().push_screen(ConfigEditorScreen())
        elif screen_id == "statements":
            from ledgersight.tui.screens.statements import StatementsScreen
            await super().push_screen(StatementsScreen())
        elif screen_id == "categories":
            from ledgersight.tui.screens.categories import CategoriesScreen
            await super().push_screen(CategoriesScreen())
        elif screen_id == "report_config":
            from ledgersight.tui.screens.report_config import ReportConfigScreen
            await super().push_screen(ReportConfigScreen())
        elif screen_id == "generate":
            from ledgersight.tui.screens.generate import GenerateScreen
            await super().push_screen(GenerateScreen())
        elif screen_id == "txn_browser":
            from ledgersight.tui.screens.txn_browser import TxnBrowserScreen
            await super().push_screen(TxnBrowserScreen())
        elif screen_id == "pl_overview":
            from ledgersight.tui.screens.pl_overview import PLOverviewScreen
            await super().push_screen(PLOverviewScreen())

    def _update_sidebar_highlight(self, screen_id: str) -> None:
        for i, current_id in enumerate(SIDEBAR_LABELS):
            if current_id == screen_id:
                if hasattr(self.sidebar_list, "index"):
                    self.sidebar_list.index = i
                break

    async def goto_screen(self, screen_id: str) -> None:
        await self.push_screen(screen_id)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_toggle_sidebar(self) -> None:
        self._sidebar_visible = not self._sidebar_visible
        sidebar = self.query_one(Sidebar)
        if self._sidebar_visible:
            sidebar.add_class("-visible")
        else:
            sidebar.remove_class("-visible")

    async def action_next_screen(self) -> None:
        screen = self.screen
        if hasattr(screen, "navigate_next"):
            await screen.navigate_next()

    async def action_prev_screen(self) -> None:
        screen = self.screen
        if hasattr(screen, "navigate_prev"):
            await screen.navigate_prev()

    async def action_goto_generate(self) -> None:
        await self.push_screen("generate")

    # ------------------------------------------------------------------
    # Sidebar click handling
    # ------------------------------------------------------------------

    @on(ListView.Selected)
    async def _on_sidebar_select(self, event: ListView.Selected) -> None:
        if isinstance(event.item, NavItem):
            await self.push_screen(event.item.screen_id)
            event.stop()

    # ------------------------------------------------------------------
    # Wizard navigation helpers (called by screens)
    # ------------------------------------------------------------------

    def wizard_next(self, current: str) -> str | None:
        """Return the next screen in wizard order, or None if this is the last."""
        try:
            idx = WIZARD_ORDER.index(current)
            return WIZARD_ORDER[idx + 1] if idx + 1 < len(WIZARD_ORDER) else None
        except ValueError:
            return None

    def wizard_prev(self, current: str) -> str | None:
        """Return the previous screen in wizard order, or None if this is the first."""
        try:
            idx = WIZARD_ORDER.index(current)
            return WIZARD_ORDER[idx - 1] if idx > 0 else None
        except ValueError:
            return None


def run() -> None:
    app = LedgerSightApp()
    app.run()
