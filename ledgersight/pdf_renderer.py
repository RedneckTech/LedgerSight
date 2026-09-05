"""PDF report rendering via FPDF."""

from __future__ import annotations

import hashlib
import io
import logging
import os
import struct
from collections.abc import Sequence
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


def _png_size(buf: io.BytesIO) -> tuple[int | None, int | None]:
    """Read PNG width/height (in pixels) from an image buffer."""
    try:
        data = buf.getvalue()
        if data[:8] != b"\x89PNG\r\n\x1a\n":
            return None, None
        w, h = struct.unpack(">II", data[16:24])
        return w, h
    except struct.error, IndexError, ValueError:
        return None, None


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
        self.header_extra: str = ""
        self.chart_note: str = ""
        # Render accounting: every table row handed to draw_table must be
        # drawn, and every drawn row must end above the footer line. The
        # report's export checks read these after rendering.
        self.table_rows_requested: int = 0
        self.table_rows_drawn: int = 0
        self.render_problems: list[str] = []
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
        if getattr(self, "header_extra", ""):
            self.set_font(self.body_font, "I", 7)
            self.cell(0, 4, self.header_extra[:80], new_x="LMARGIN", new_y="NEXT")
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(3)

    def footer(self):
        self.set_y(-15)
        self.set_font(self.body_font, "I", 6)
        self.set_text_color(150, 150, 150)
        self.cell(
            0,
            8,
            f"Generated {self.generated_at}  |  LedgerSight  |  {_SCRIPT_HASH}",
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

    def body_text(self, text: str, size: int | float = 9):
        self.set_font(self.body_font, "", size)
        self.set_text_color(50, 50, 50)
        self.multi_cell(0, 4.5, text)
        self.ln(1)

    def body_text_small(self, text: str, size: int | float = 7):
        self.set_font(self.body_font, "", size)
        self.set_text_color(80, 80, 80)
        self.multi_cell(0, 3.5, text)
        self.ln(1)

    def truncate_text(self, text: str, width_mm: float, font_size: float = 7) -> str:
        self.set_font(self.body_font, "", font_size)
        if self.get_string_width(text) <= width_mm:
            return text
        ellipsis = "..."
        while len(text) > 3 and self.get_string_width(text + ellipsis) > width_mm:
            text = text[:-1]
        return text + ellipsis

    def _wrap_cell(self, text: str, width_mm: float, font_size: float) -> list[str]:
        """Break one cell into display lines that fit a column width.

        Explicit newlines are honoured, then long lines are word-wrapped by
        measured string width. This is the single source of truth for how tall
        a row is, so pagination (``_draw_table_paginated``) and drawing
        (``_draw_table_section``) can never disagree about how many rows fit.
        """
        self.set_font(self.body_font, "", font_size)
        lines: list[str] = []
        for hard_line in text.split("\n"):
            words = hard_line.split(" ")
            if not words or all(not w for w in words):
                lines.append("")
                continue
            current = ""
            for word in words:
                if not word:
                    continue
                trial = f"{current} {word}".strip()
                if current and self.get_string_width(trial) <= width_mm:
                    current = trial
                    continue
                if not current:
                    remaining = word
                    while remaining and self.get_string_width(remaining) > width_mm:
                        # Estimate how many characters fit, then shrink until the
                        # chunk really does fit so nothing has to be truncated.
                        cut = max(1, int(len(remaining) / max(self.get_string_width(remaining) / width_mm, 1.0)))
                        while cut > 1 and self.get_string_width(remaining[:cut]) > width_mm:
                            cut -= 1
                        lines.append(remaining[:cut])
                        remaining = remaining[cut:]
                    current = remaining
                else:
                    lines.append(current)
                    current = word
            lines.append(current)
        return lines or [""]

    def draw_table(
        self,
        headers: list[str],
        rows: list[list[str]],
        col_widths: Sequence[float] | None = None,
        col_aligns: Sequence[str] | None = None,
        header_color: tuple = (44, 62, 80),
        section_label: str = "",
        header_font_size: float = 7,
        row_font_size: float = 7,
        row_height: float = 4.5,
    ):
        if col_widths is None:
            usable = self.w - self.l_margin - self.r_margin
            col_widths = [usable / len(headers)] * len(headers)
        if col_aligns is None:
            col_aligns = ["L"] * len(headers)
        bad = [len(r) for r in rows if len(r) != len(headers)]
        if bad:
            label = section_label or headers[0]
            raise ValueError(f"draw_table '{label}': rows have {bad[0]} cells but there are {len(headers)} headers")

        auto_break = self.auto_page_break
        self.set_auto_page_break(auto=False)
        try:
            self._draw_table_paginated(
                headers,
                rows,
                list(col_widths),
                list(col_aligns),
                header_color,
                section_label,
                header_font_size,
                row_font_size,
                row_height,
            )
        finally:
            self.set_auto_page_break(auto=auto_break, margin=18)

    def _draw_table_paginated(
        self,
        headers: list[str],
        rows: list[list[str]],
        col_widths: Sequence[float],
        col_aligns: Sequence[str],
        header_color: tuple,
        section_label: str,
        header_font_size: float,
        row_font_size: float,
        row_height: float,
    ):
        header_h = 6
        cont_h = 5 if section_label else 0
        # Content must stop above the auto page-break line so rows can never
        # collide with the footer or vanish past the bottom of the page.
        content_bottom = self.h - 18
        if not rows:
            return
        self.table_rows_requested += len(rows)
        heights = []
        for row in rows:
            line_counts = [len(self._wrap_cell(str(c), col_widths[i], row_font_size)) for i, c in enumerate(row)]
            heights.append(max(line_counts, default=1) * row_height)

        i = 0
        first = True
        while i < len(rows):
            if not first:
                self.add_page()
            if first:
                if self.get_y() + header_h + heights[i] > content_bottom:
                    self.add_page()
            elif section_label and self.get_y() + cont_h + header_h + heights[i] > content_bottom:
                self.add_page()
            extra = header_h if first else (header_h + (cont_h if section_label else 0))
            take: list[int] = []
            cur = i
            used = 0.0
            while cur < len(rows) and self.get_y() + extra + used + heights[cur] <= content_bottom:
                take.append(cur)
                used += heights[cur]
                cur += 1
            if not take:
                take = [i]
                cur = i + 1
            if not first and section_label:
                self.set_font(self.body_font, "I", 7)
                self.set_text_color(100, 100, 100)
                self.cell(0, 4, f"{section_label} (continued)", new_x="LMARGIN", new_y="NEXT")
                self.ln(1)
            self._draw_table_section(
                headers,
                [rows[j] for j in take],
                col_widths,
                col_aligns,
                header_color,
                header_font_size,
                row_font_size,
                row_height,
            )
            self.table_rows_drawn += len(take)
            # _draw_table_section ends with ln(3); measure the last row itself.
            last_row_bottom = self.get_y() - 3
            if last_row_bottom > content_bottom + 0.5:
                label = section_label or headers[0]
                self.render_problems.append(
                    f"table '{label}' ran {last_row_bottom - content_bottom:.1f}mm past the content area "
                    f"on page {self.page_no()}"
                )
            i = cur
            first = False

    def _draw_table_section(
        self,
        headers,
        rows,
        col_widths,
        col_aligns,
        header_color,
        header_font_size,
        row_font_size,
        row_height,
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
            wrapped = [self._wrap_cell(str(cell_text), col_widths[i], row_font_size) for i, cell_text in enumerate(row)]
            max_lines = max((len(lines) for lines in wrapped), default=1)
            for lines in wrapped:
                lines.extend([""] * (max_lines - len(lines)))
            row_start = self.get_y()
            for line_idx in range(max_lines):
                y = row_start + line_idx * row_height
                for i, lines in enumerate(wrapped):
                    text = self.truncate_text(lines[line_idx], col_widths[i], row_font_size)
                    self.set_xy(self.l_margin + sum(col_widths[:i]), y)
                    self.cell(
                        col_widths[i],
                        row_height,
                        text,
                        border=0,
                        fill=True,
                        align=col_aligns[i],
                    )
            self.set_xy(self.l_margin, row_start + max_lines * row_height)
        self.ln(3)

    def embed_chart(self, buf: io.BytesIO, w: float | None = None, caption: str = ""):
        if w is None:
            w = self.w - self.l_margin - self.r_margin
        if not caption:
            if self.get_y() + w * 0.5 > self.h - 25:
                self.add_page()
            self.image(buf, x=self.l_margin, w=w)
            self.ln(3)
            return
        pw, ph = _png_size(buf)
        img_h = (w * ph / pw) if (pw and ph) else w * 0.5
        available = (self.h - self.b_margin) - self.get_y() - 16
        if img_h > available and available > 12:
            w = max(w * available / img_h, 20)
            img_h = available
        self.image(buf, x=self.l_margin, w=w)
        self.ln(2)
        self.set_font(self.body_font, "I", 7)
        self.set_text_color(120, 120, 120)
        self.multi_cell(0, 3.5, caption)
        self.ln(2)

    def draw_kv_table(
        self,
        pairs: list[tuple[str, str]],
        col_widths: Sequence[float] | None = None,
        font_size: float = 9,
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
