"""PDF report rendering via FPDF."""
from __future__ import annotations

import hashlib
import io
import logging
import os
from datetime import datetime
from pathlib import Path

from fpdf import FPDF

logger = logging.getLogger("ledgersight.pdf_renderer")

_SCRIPT_HASH = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:8]

_DEJAVU_SEARCH_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/local/share/fonts/dejavu/DejaVuSans.ttf",
    "/opt/homebrew/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu-sans/DejaVuSans.ttf",
]


def _find_font(name: str) -> str:
    """Find a font file by searching common paths. Returns the default if not found."""
    base_name = Path(name).name
    for search_base in _DEJAVU_SEARCH_PATHS:
        search_dir = str(Path(search_base).parent)
        candidate = os.path.join(search_dir, base_name)
        if os.path.exists(candidate):
            logger.debug("Found font: %s", candidate)
            return candidate
    # Fallback to the default path
    for search_base in _DEJAVU_SEARCH_PATHS:
        search_dir = str(Path(search_base).parent)
        candidate = os.path.join(search_dir, "DejaVuSans.ttf")
        if os.path.exists(candidate):
            logger.debug("Fallback font: %s", candidate)
            return candidate
    logger.warning("No DejaVu fonts found; PDF will use built-in Helvetica (no unicode support)")
    return name


class ReportPDF(FPDF):
    """Extended FPDF for business financial reports."""

    DEJAVU_SANS = _find_font("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    DEJAVU_SANS_BOLD = _find_font("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
    DEJAVU_MONO = _find_font("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf")
    DEJAVU_MONO_BOLD = _find_font("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf")

    def __init__(self, title: str, orientation: str = "P"):
        super().__init__(orientation=orientation, unit="mm", format="A4")
        self._report_title = title
        self.generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
        self.set_auto_page_break(auto=True, margin=18)
        self.set_margins(12, 12, 12)
        self._use_dejavu = False
        self.body_font = "Helvetica"
        self.mono_font = "Courier"
        self._setup_fonts()
        self.set_title(title)
        self.set_author("LedgerSight")
        self.set_subject("Business Financial Report")

    def _setup_fonts(self):
        if os.path.exists(self.DEJAVU_SANS):
            self.add_font("DJV", "", self.DEJAVU_SANS)
            self.add_font("DJV", "B", self.DEJAVU_SANS_BOLD)
            self.add_font("DJV", "I", self.DEJAVU_SANS)
            self.add_font("DJVM", "", self.DEJAVU_MONO)
            self.add_font("DJVM", "B", self.DEJAVU_MONO_BOLD)
            self._use_dejavu = True
            self.body_font = "DJV"
            self.mono_font = "DJVM"
        else:
            self._use_dejavu = False
            self.body_font = "Helvetica"
            self.mono_font = "Courier"

    def header(self):
        if self.page_no() <= 1:
            return
        self.set_font(self.body_font, "I", 7)
        self.set_text_color(120, 120, 120)
        title_short = self._report_title[:80]
        self.cell(0, 4, title_short, align="L")
        self.cell(0, 4, f"Page {self.page_no()}", align="R", new_x="LMARGIN", new_y="NEXT")
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(3)

    def footer(self):
        self.set_y(-15)
        self.set_font(self.body_font, "I", 6)
        self.set_text_color(150, 150, 150)
        self.cell(
            0, 8,
            f"Generated {self.generated_at}  |  business_financial_report.py  |  {_SCRIPT_HASH}",
            align="C",
        )

    def section_title(self, text: str):
        self.set_font(self.body_font, "B", 14)
        self.set_text_color(44, 62, 80)
        self.cell(0, 8, text, new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(44, 62, 80)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(4)
        self.start_section(text)

    def sub_title(self, text: str):
        self.set_font(self.body_font, "B", 11)
        self.set_text_color(52, 73, 94)
        self.cell(0, 6, text, new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def body_text(self, text: str, size: int = 9):
        self.set_font(self.body_font, "", size)
        self.set_text_color(50, 50, 50)
        self.multi_cell(0, 4.5, text)
        self.ln(1)

    def body_text_small(self, text: str, size: int = 7):
        self.set_font(self.body_font, "", size)
        self.set_text_color(80, 80, 80)
        self.multi_cell(0, 3.5, text)
        self.ln(1)

    def truncate_text(self, text: str, width_mm: float, font_size: int = 7) -> str:
        self.set_font(self.body_font, "", font_size)
        if self.get_string_width(text) <= width_mm:
            return text
        ellipsis = "..."
        while len(text) > 3 and self.get_string_width(text + ellipsis) > width_mm:
            text = text[:-1]
        return text + ellipsis

    def draw_table(
        self,
        headers: list[str],
        rows: list[list[str]],
        col_widths: list[float] | None = None,
        col_aligns: list[str] | None = None,
        header_color: tuple = (44, 62, 80),
        section_label: str = "",
        header_font_size: int = 7,
        row_font_size: int = 7,
        row_height: float = 4.5,
    ):
        if col_widths is None:
            usable = self.w - self.l_margin - self.r_margin
            col_widths = [usable / len(headers)] * len(headers)
        if col_aligns is None:
            col_aligns = ["L"] * len(headers)

        header_h = 6
        min_body_rows = 2
        header_space = header_h + min_body_rows * row_height + 4

        total_space = header_h + len(rows) * row_height + 4
        if self.get_y() + total_space <= self.h - self.b_margin:
            self._draw_table_section(
                headers, rows, col_widths, col_aligns,
                header_color, header_font_size, row_font_size, row_height,
            )
        else:
            if self.get_y() + header_space > self.h - self.b_margin:
                self.add_page()

            remaining = list(rows)
            first_page = True
            while remaining:
                available = int(
                    (self.h - self.b_margin - self.get_y() - header_h) / row_height
                )
                if available < min_body_rows:
                    self.add_page()
                    available = int(
                        (self.h - self.b_margin - self.get_y() - header_h) / row_height
                    )

                chunk = remaining[:available]
                remaining = remaining[available:]

                if not first_page and section_label:
                    self.set_font(self.body_font, "I", 7)
                    self.set_text_color(100, 100, 100)
                    self.cell(0, 4, f"{section_label} (continued)", new_x="LMARGIN", new_y="NEXT")
                    self.ln(1)

                self._draw_table_section(
                    headers, chunk, col_widths, col_aligns,
                    header_color, header_font_size, row_font_size, row_height,
                )
                first_page = False

    def _draw_table_section(
        self, headers, rows, col_widths, col_aligns,
        header_color, header_font_size, row_font_size, row_height,
    ):
        self.set_fill_color(*header_color)
        self.set_text_color(255, 255, 255)
        self.set_font(self.body_font, "B", header_font_size)
        for i, h in enumerate(headers):
            self.cell(col_widths[i], 6, h, border=0, fill=True, align="C")
        self.ln()

        for idx, row in enumerate(rows):
            if idx % 2 == 0:
                self.set_fill_color(245, 245, 245)
            else:
                self.set_fill_color(255, 255, 255)
            self.set_text_color(50, 50, 50)
            self.set_font(self.body_font, "", row_font_size)
            for i, cell_text in enumerate(row):
                truncated = self.truncate_text(str(cell_text), col_widths[i], row_font_size)
                self.cell(
                    col_widths[i], row_height, truncated,
                    border=0, fill=True, align=col_aligns[i],
                )
            self.ln()
        self.ln(3)

    def embed_chart(self, buf: io.BytesIO, w: float | None = None):
        if w is None:
            w = self.w - self.l_margin - self.r_margin
        if self.get_y() + w * 0.5 > self.h - 25:
            self.add_page()
        self.image(buf, x=self.l_margin, w=w)
        self.ln(3)

    def draw_kv_table(
        self,
        pairs: list[tuple[str, str]],
        col_widths: list[float] | None = None,
        font_size: int = 9,
    ):
        """Draw a simple key-value table."""
        if col_widths is None:
            usable = self.w - self.l_margin - self.r_margin
            col_widths = [usable * 0.55, usable * 0.45]
        for label, val in pairs:
            if self.get_y() > self.h - 20:
                self.add_page()
            self.set_fill_color(245, 245, 245)
            self.set_font(self.body_font, "B", font_size)
            self.set_text_color(50, 50, 50)
            self.cell(col_widths[0], 7, f"  {label}", fill=True)
            self.set_font(self.body_font, "", font_size)
            self.cell(col_widths[1], 7, val, fill=True, align="R", new_x="LMARGIN", new_y="NEXT")
        self.ln(3)
