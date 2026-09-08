"""Config editor screen — edit TOML config fields."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any, cast

import tomli_w
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Input, Label, Select, Static

from ledgersight.models import BusinessConfig
from ledgersight.tui.app import LedgerSightApp


def _save_config_to_toml(config: BusinessConfig, path: Path) -> None:
    """Write BusinessConfig to a TOML file, preserving existing sections.

    Uses a proper TOML load/update/dump cycle so rules, aliases,
    projections, owners, fixed assets, loans, and any unknown sections
    survive regardless of ordering. The candidate document is validated
    before it atomically replaces the original file.
    """
    data: dict[str, Any] = {}
    if path.exists():
        with open(path, "rb") as f:
            data = tomllib.load(f)

    data["general"] = _build_general_dict(config)
    data["cpa"] = _build_cpa_dict(config)
    data["owner"] = {"owners": config.owners}
    if config.projection_config:
        data["projections"] = config.projection_config
    if config.custom_rules:
        data["rules"] = [_rule_to_dict(rule) for rule in config.custom_rules]
    if config.beginning_balances:
        data["balances"] = {k: float(v) for k, v in config.beginning_balances.items()}
    if config.fixed_assets:
        data["fixed_assets"] = config.fixed_assets
    if config.loans:
        data["loans"] = config.loans
    if config.owner_activities:
        data["owner_activity"] = config.owner_activities
    if config.document_checklist:
        data["document_checklist"] = config.document_checklist
    if config.merchant_aliases:
        data["merchant_aliases"] = config.merchant_aliases

    # Validate by re-parsing the generated TOML before replacing the file.
    tmp = path.with_suffix(".tmp")
    with open(tmp, "wb") as f:
        tomli_w.dump(data, f)
    with open(tmp, "rb") as f:
        tomllib.load(f)
    os.replace(str(tmp), str(path))


def _build_general_dict(config: BusinessConfig) -> dict[str, Any]:
    general: dict[str, Any] = {
        "business_name": config.business_name,
        "tax_year": config.tax_year,
        "fiscal_year_start": config.fiscal_year_start,
        "entity_type": config.entity_type,
        "accounting_method": config.accounting_method,
        "mask_ein": config.mask_ein,
        "mask_account": config.mask_account,
        "currency": config.currency,
    }
    for field in ("dba", "address", "phone", "email", "ein_display", "bank_account_display", "industry"):
        value = getattr(config, field)
        if value:
            general[field] = value
    return general


def _build_cpa_dict(config: BusinessConfig) -> dict[str, Any]:
    cpa: dict[str, Any] = {"name": config.cpa_name}
    for field in ("firm", "email", "phone"):
        value = getattr(config, f"cpa_{field}")
        if value:
            cpa[field] = value
    return cpa


def _rule_to_dict(rule) -> dict[str, Any]:
    return {
        "pattern": rule.pattern,
        "category": rule.category,
        "tax_category": rule.tax_category,
        "deductibility": rule.deductibility,
        "is_income": rule.is_income,
        "include_in_pnl": rule.include_in_pnl,
        "is_transfer": rule.is_transfer,
        "is_owner_related": rule.is_owner_related,
        "is_fixed_asset": rule.is_fixed_asset,
        "is_loan": rule.is_loan,
        "direction": rule.direction,
        "priority": rule.priority,
    }


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
                    [
                        ("Sole Proprietor", "sole-prop"),
                        ("Single-Member LLC", "single-member-llc"),
                        ("Partnership", "partnership"),
                        ("S-Corp", "s-corp"),
                        ("C-Corp", "c-corp"),
                        ("Other", "other"),
                    ],
                    id="entity_type",
                    value="sole-prop",
                    allow_blank=False,
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
                    [
                        ("January", 1),
                        ("February", 2),
                        ("March", 3),
                        ("April", 4),
                        ("May", 5),
                        ("June", 6),
                        ("July", 7),
                        ("August", 8),
                        ("September", 9),
                        ("October", 10),
                        ("November", 11),
                        ("December", 12),
                    ],
                    id="fiscal_year_start",
                    value=1,
                    allow_blank=False,
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
            except ValueError, TypeError:
                self.notify("Tax year must be a number", severity="error")
                return

            entity_type_val = cast(str, self.query_one("#entity_type", Select).value)
            fiscal_start_val = cast(int, self.query_one("#fiscal_year_start", Select).value)

            config = replace(
                config,
                business_name=self.query_one("#business_name", Input).value,
                dba=self.query_one("#dba", Input).value,
                address=self.query_one("#address", Input).value,
                phone=self.query_one("#phone", Input).value,
                email=self.query_one("#email", Input).value,
                industry=self.query_one("#industry", Input).value,
                entity_type=entity_type_val or config.entity_type,
                tax_year=tax_year,
                fiscal_year_start=fiscal_start_val or config.fiscal_year_start,
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
