"""Welcome screen — config file selection and recent files."""

from __future__ import annotations

from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, ListItem, ListView, Static

from ledgersight.tui.app import LedgerSightApp

RECENTS_FILE = Path.home() / ".ledgersight" / "recents.txt"


def _load_recents() -> list[Path]:
    """Load recently used config paths."""
    try:
        if RECENTS_FILE.exists():
            lines = RECENTS_FILE.read_text().strip().split("\n")
            return [Path(p) for p in lines if Path(p).exists()]
    except OSError:
        pass
    return []


def _save_recent(path: Path) -> None:
    """Save a config path to recents."""
    recents = _load_recents()
    recents.insert(0, path.resolve())
    seen: set[str] = set()
    unique: list[Path] = []
    for p in recents:
        sp = str(p)
        if sp not in seen:
            seen.add(sp)
            unique.append(p)
    RECENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    RECENTS_FILE.write_text("\n".join(str(p) for p in unique[:10]))


class WelcomeScreen(Screen[None]):
    """Welcome / config selection screen."""

    DEFAULT_CSS = """
    WelcomeScreen {
        align: center middle;
    }
    #welcome-box {
        width: 60;
        height: auto;
        border: solid $primary;
        padding: 1 2;
    }
    #welcome-title {
        text-style: bold;
        content-align: center middle;
        padding: 1;
    }
    #recent-list {
        height: 10;
        margin: 1 0;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="welcome-box"):
            yield Static("Welcome to LedgerSight", id="welcome-title")
            yield Static("Select a configuration file to get started:")
            yield ListView(*[], id="recent-list")
            with Horizontal():
                yield Button("Open Config...", id="btn-open", variant="primary")
                yield Button("New Config", id="btn-new")
                yield Button("Continue", id="btn-next", variant="success")

    def on_mount(self) -> None:
        recent_list = self.query_one("#recent-list", ListView)
        recents = _load_recents()
        if recents:
            for p in recents[:8]:
                recent_list.append(ListItem(Static(f"  {p.name}  ({p})")))
        else:
            recent_list.append(ListItem(Static("  No recent configs")))

    @on(Button.Pressed, "#btn-open")
    async def _open_config(self) -> None:
        app = self.app
        if isinstance(app, LedgerSightApp):
            from ledgersight.config import _DEFAULT_CONFIG, load_config

            config = load_config(Path(_DEFAULT_CONFIG))
            app.state.config = config
            app.state.config_path = Path(_DEFAULT_CONFIG)
            await app.goto_screen("statements")

    @on(Button.Pressed, "#btn-new")
    async def _new_config(self) -> None:
        app = self.app
        if isinstance(app, LedgerSightApp):
            await app.goto_screen("config_editor")

    @on(Button.Pressed, "#btn-next")
    async def _next_screen(self) -> None:
        await self.navigate_next()

    async def navigate_next(self) -> None:
        app = self.app
        if isinstance(app, LedgerSightApp):
            await app.goto_screen("statements")
