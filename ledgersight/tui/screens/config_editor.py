"""Config editor screen — edit TOML config fields."""

from __future__ import annotations

import os
import re
from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Input, Label, Select, Static

from ledgersight.models import BusinessConfig
from ledgersight.tui.app import LedgerSightApp


def _save_config_to_toml(config: BusinessConfig, path: Path) -> None:
    """Write BusinessConfig to a TOML file, preserving existing sections."""
    general = _build_general_section(config)
    cpa = _build_cpa_section(config)
    tail = _extract_tail(path)

    tmp = path.with_suffix(".tmp")
    tmp.write_text(general + "\n" + cpa + tail + "\n")
    os.replace(str(tmp), str(path))


def _build_general_section(config: BusinessConfig) -> str:
    parts = [
        "[general]",
        f'business_name = "{config.business_name}"',
    ]
    for field, key in (
        ("dba", config.dba),
        ("address", config.address),
        ("phone", config.phone),
        ("email", config.email),
    ):
        if key:
            parts.append(f'{field} = "{key}"')
    parts.append(f"tax_year = {config.tax_year}")
    parts.append(f"fiscal_year_start = {config.fiscal_year_start}")
    parts.append(f'entity_type = "{config.entity_type}"')
    parts.append(f'accounting_method = "{config.accounting_method}"')
    if config.ein_display:
        parts.append(f'ein_display = "{config.ein_display}"')
    parts.append(f"mask_ein = {str(config.mask_ein).lower()}")
    if config.bank_account_display:
        parts.append(f'bank_account_display = "{config.bank_account_display}"')
    parts.append(f"mask_account = {str(config.mask_account).lower()}")
    if config.industry:
        parts.append(f'industry = "{config.industry}"')
    parts.append(f'currency = "{config.currency}"')
    return "\n".join(parts)


def _build_cpa_section(config: BusinessConfig) -> str:
    parts = [
        "[cpa]",
        f'name = "{config.cpa_name}"',
    ]
    for field, key in (
        ("firm", config.cpa_firm),
        ("email", config.cpa_email),
        ("phone", config.cpa_phone),
    ):
        if key:
            parts.append(f'{field} = "{key}"')
    return "\n".join(parts)


def _extract_tail(path: Path) -> str:
    """Extract everything after [cpa] section from existing TOML."""
    if not path.exists():
        return ""
    raw = path.read_text()
    match = re.search(r"\n\[cpa\].*?(?=\n\[)", raw, re.DOTALL)
    if not match:
        return ""
    return "\n" + raw[match.end():]


class ConfigEditorScreen(Screen[None]):
    """Edit business configuration fields."""

    DEFAULT_CSS = """
    ConfigEditorScreen {
        padding: 1 2;
    }
    #config-form {
        width: 70;
        height: auto;
    }
    .form-row {
        height: 3;
        margin: 0 1;
    }
    .form-label {
        width: 22;
        content-align: left middle;
        text-style: bold;
    }
    .form-input {
        width: 44;
    }
    """

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="config-form"):
            yield Static("Business Configuration", classes="section-title")
            with Horizontal(classes="form-row"):
                yield Label("Business Name:", classes="form-label")
                yield Input(id="business_name", value="Business", classes="form-input")
            with Horizontal(classes="form-row"):
                yield Label("DBA:", classes="form-label")
                yield Input(id="dba", value="", classes="form-input")
            with Horizontal(classes="form-row"):
                yield Label("Address:", classes="form-label")
                yield Input(id="address", value="", classes="form-input")
            with Horizontal(classes="form-row"):
                yield Label("Phone:", classes="form-label")
                yield Input(id="phone", value="", classes="form-input")
            with Horizontal(classes="form-row"):
                yield Label("Email:", classes="form-label")
                yield Input(id="email", value="", classes="form-input")
            with Horizontal(classes="form-row"):
                yield Label("Entity Type:", classes="form-label")
                yield Select(
                    [("Sole Proprietor", "sole-prop"),
                     ("Single-Member LLC", "single-member-llc"),
                     ("Partnership", "partnership"),
                     ("S-Corp", "s-corp"),
                     ("C-Corp", "c-corp"),
                     ("Other", "other")],
                    id="entity_type",
                    value="sole-prop",
                    classes="form-input",
                )
            with Horizontal(classes="form-row"):
                yield Label("Industry:", classes="form-label")
                yield Input(id="industry", value="", classes="form-input")
            with Horizontal(classes="form-row"):
                yield Label("Tax Year:", classes="form-label")
                yield Input(id="tax_year", value="2025", classes="form-input")
            with Horizontal(classes="form-row"):
                yield Label("Fiscal Year Start:", classes="form-label")
                yield Select(
                    [("January", 1), ("February", 2), ("March", 3),
                     ("April", 4), ("May", 5), ("June", 6),
                     ("July", 7), ("August", 8), ("September", 9),
                     ("October", 10), ("November", 11), ("December", 12)],
                    id="fiscal_year_start",
                )
            yield Static("")
            yield Static("CPA Information", classes="section-title")
            with Horizontal(classes="form-row"):
                yield Label("CPA Name:", classes="form-label")
                yield Input(id="cpa_name", value="CPA", classes="form-input")
            with Horizontal(classes="form-row"):
                yield Label("CPA Firm:", classes="form-label")
                yield Input(id="cpa_firm", value="", classes="form-input")
            with Horizontal(classes="form-row"):
                yield Label("CPA Email:", classes="form-label")
                yield Input(id="cpa_email", value="", classes="form-input")
            with Horizontal(classes="form-row"):
                yield Label("CPA Phone:", classes="form-label")
                yield Input(id="cpa_phone", value="", classes="form-input")
            yield Static("")
            with Horizontal():
                yield Button("Back", id="btn-back")
                yield Button("Save & Continue", id="btn-next", variant="success")

    def on_mount(self) -> None:
        app = self.app
        if isinstance(app, LedgerSightApp) and app.state.config is not None:
            config = app.state.config
            self.query_one("#business_name", Input).value = config.business_name
            self.query_one("#dba", Input).value = config.dba
            self.query_one("#address", Input).value = config.address
            self.query_one("#phone", Input).value = config.phone
            self.query_one("#email", Input).value = config.email
            self.query_one("#industry", Input).value = config.industry
            self.query_one("#entity_type", Select).value = config.entity_type
            self.query_one("#tax_year", Input).value = str(config.tax_year)
            self.query_one("#fiscal_year_start", Select).value = config.fiscal_year_start
            self.query_one("#cpa_name", Input).value = config.cpa_name
            self.query_one("#cpa_firm", Input).value = config.cpa_firm
            self.query_one("#cpa_email", Input).value = config.cpa_email
            self.query_one("#cpa_phone", Input).value = config.cpa_phone

    @on(Button.Pressed, "#btn-next")
    async def _save_continue(self) -> None:
        app = self.app
        if isinstance(app, LedgerSightApp):
            from dataclasses import replace
            from pathlib import Path

            from ledgersight.config import validate_config
            from ledgersight.constants import _DEFAULT_CONFIG
            from ledgersight.models import BusinessConfig

            config = app.state.config or BusinessConfig()

            try:
                tax_year = int(self.query_one("#tax_year", Input).value or config.tax_year)
            except (ValueError, TypeError):
                self.notify("Tax year must be a number", severity="error")
                return

            config = replace(
                config,
                business_name=self.query_one("#business_name", Input).value,
                dba=self.query_one("#dba", Input).value,
                address=self.query_one("#address", Input).value,
                phone=self.query_one("#phone", Input).value,
                email=self.query_one("#email", Input).value,
                industry=self.query_one("#industry", Input).value,
                entity_type=self.query_one("#entity_type", Select).value or config.entity_type,
                tax_year=tax_year,
                fiscal_year_start=int(
                    self.query_one("#fiscal_year_start", Select).value
                    or config.fiscal_year_start
                ),
                cpa_name=self.query_one("#cpa_name", Input).value,
                cpa_firm=self.query_one("#cpa_firm", Input).value,
                cpa_email=self.query_one("#cpa_email", Input).value,
                cpa_phone=self.query_one("#cpa_phone", Input).value,
            )

            errors, _warnings = validate_config(config)
            if errors:
                self.notify(f"Config error: {errors[0]}", severity="error")
                return

            from ledgersight.tui.screens.welcome import _save_recent

            app.state.config = config
            if not app.state.config_path:
                app.state.config_path = Path(_DEFAULT_CONFIG)

            try:
                _save_config_to_toml(config, app.state.config_path)
            except OSError as exc:
                self.notify(f"Could not save config: {exc}", severity="error")

            _save_recent(app.state.config_path)
            await app.goto_screen("statements")

    @on(Button.Pressed, "#btn-back")
    async def _back(self) -> None:
        app = self.app
        if isinstance(app, LedgerSightApp):
            await app.goto_screen("welcome")

    async def navigate_next(self) -> None:
        await self._save_continue()
