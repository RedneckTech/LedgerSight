"""Welcome screen — profile selection and (business) config file selection."""

from __future__ import annotations

from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, ListItem, ListView, Select, Static

from ledgersight.tui.app import LedgerSightApp

RECENTS_FILE = Path.home() / ".ledgersight" / "recents.txt"

PROFILES = [
    ("Business", "business"),
    ("Personal", "personal"),
]


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
    """Welcome / profile and config selection screen."""

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
    #profile-row {
        height: 3;
        margin: 0 0 1 0;
    }
    #profile-label {
        width: 16;
        content-align: left middle;
        text-style: bold;
    }
    #profile-select {
        width: 30;
    }
    #business-box {
        margin: 1 0 0 0;
    }
    #business-box.-hidden,
    #personal-box.-hidden {
        display: none;
    }
    #personal-box {
        margin: 1 0 0 0;
        height: 3;
        content-align: center middle;
    }
    #recent-list {
        height: 10;
        margin: 1 0;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="welcome-box"):
            yield Static("Welcome to LedgerSight", id="welcome-title")
            with Horizontal(id="profile-row"):
                yield Static("Profile:", id="profile-label")
                yield Select(PROFILES, id="profile", value="business", allow_blank=False)
            with Vertical(id="business-box"):
                yield Static("Select a configuration file to get started:")
                yield ListView(*[], id="recent-list")
                with Horizontal():
                    yield Button("Open Config...", id="btn-open", variant="primary")
                    yield Button("New Config", id="btn-new")
                    yield Button("Continue", id="btn-next", variant="success")
            with Vertical(id="personal-box"):
                yield Button("Continue", id="btn-personal-next", variant="success")

    def on_mount(self) -> None:
        recent_list = self.query_one("#recent-list", ListView)
        recents = _load_recents()
        if recents:
            for p in recents[:8]:
                recent_list.append(ListItem(Static(f"  {p.name}  ({p})")))
        else:
            recent_list.append(ListItem(Static("  No recent configs")))
        app = self.app
        if isinstance(app, LedgerSightApp):
            self.query_one("#profile", Select).value = app.state.profile
            self._apply_profile_visibility(app.state.profile)

    def _apply_profile_visibility(self, profile: str) -> None:
        personal = profile == "personal"
        business_box = self.query_one("#business-box", Vertical)
        personal_box = self.query_one("#personal-box", Vertical)
        if personal:
            business_box.add_class("-hidden")
            personal_box.remove_class("-hidden")
        else:
            business_box.remove_class("-hidden")
            personal_box.add_class("-hidden")

    @on(Select.Changed, "#profile")
    async def _on_profile_changed(self, event: Select.Changed) -> None:
        app = self.app
        if isinstance(app, LedgerSightApp):
            app.state.profile = str(event.value)
            self._apply_profile_visibility(app.state.profile)

    @on(ListView.Selected, "#recent-list")
    async def _on_recent_selected(self, event: ListView.Selected) -> None:
        await self._load_from_recent()

    @on(Button.Pressed, "#btn-open")
    async def _open_config(self) -> None:
        await self._load_from_recent()

    async def _load_from_recent(self) -> None:
        app = self.app
        if not isinstance(app, LedgerSightApp):
            return

        from ledgersight.config import load_config

        recent_list = self.query_one("#recent-list", ListView)
        if recent_list.index is not None and recent_list.index < len(recent_list.children):
            recents = _load_recents()
            if recent_list.index < len(recents):
                config_path = recents[recent_list.index]
                if config_path.exists():
                    config = load_config(config_path)
                    app.state.config = config
                    app.state.config_path = config_path
                    _save_recent(config_path)
                    await app.goto_screen("statements")
                    return

        from ledgersight.constants import _DEFAULT_CONFIG

        default_path = Path(_DEFAULT_CONFIG)
        config = load_config(default_path)
        app.state.config = config
        app.state.config_path = default_path
        await app.goto_screen("statements")

    @on(Button.Pressed, "#btn-new")
    async def _new_config(self) -> None:
        app = self.app
        if isinstance(app, LedgerSightApp):
            await app.goto_screen("config_editor")

    @on(Button.Pressed, "#btn-next")
    async def _next_screen(self) -> None:
        await self.navigate_next()

    @on(Button.Pressed, "#btn-personal-next")
    async def _personal_next_screen(self) -> None:
        await self.navigate_next()

    async def navigate_next(self) -> None:
        app = self.app
        if isinstance(app, LedgerSightApp):
            if app.state.profile == "personal":
                await app.goto_screen("personal_report")
            else:
                await app.goto_screen("statements")
