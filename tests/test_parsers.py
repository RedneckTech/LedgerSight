import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from ledgersight.parsers import (
    _is_page_artifact,
    file_hash,
    find_pdfs,
    fmt_dollar,
    parse_amount,
    safe_div,
    safe_pct,
)


class TestParseAmount(unittest.TestCase):
    def test_positive_dollar(self):
        self.assertEqual(parse_amount("$1,234.56"), Decimal("1234.56"))

    def test_negative_dollar(self):
        self.assertEqual(parse_amount("-$500.00"), Decimal("-500.00"))

    def test_zero(self):
        self.assertEqual(parse_amount("$0.00"), Decimal("0"))

    def test_empty(self):
        self.assertEqual(parse_amount(""), Decimal("0"))

    def test_plain_number(self):
        self.assertEqual(parse_amount("42.75"), Decimal("42.75"))

    def test_negative_plain(self):
        self.assertEqual(parse_amount("-99.99"), Decimal("-99.99"))

    def test_with_nbsp(self):
        self.assertEqual(parse_amount("\xa0$1.00"), Decimal("1.00"))


class TestFmtDollar(unittest.TestCase):
    def test_positive(self):
        self.assertEqual(fmt_dollar(Decimal("1234.56")), "$1,234.56")

    def test_negative(self):
        self.assertEqual(fmt_dollar(Decimal("-500.00")), "-$500.00")

    def test_zero(self):
        self.assertEqual(fmt_dollar(Decimal("0")), "$0.00")

    def test_large(self):
        self.assertEqual(fmt_dollar(Decimal("1000000.00")), "$1,000,000.00")


class TestSafePct(unittest.TestCase):
    def test_normal(self):
        self.assertEqual(safe_pct(Decimal("25"), Decimal("100")), "25.0%")

    def test_zero_denominator(self):
        self.assertEqual(safe_pct(Decimal("50"), Decimal("0")), "N/A")

    def test_zero_numerator(self):
        self.assertEqual(safe_pct(Decimal("0"), Decimal("100")), "0.0%")


class TestSafeDiv(unittest.TestCase):
    def test_normal(self):
        self.assertEqual(safe_div(Decimal("100"), Decimal("4")), Decimal("25"))

    def test_division_by_zero(self):
        self.assertEqual(safe_div(Decimal("100"), Decimal("0")), Decimal("0"))


class TestDecimalArithmetic(unittest.TestCase):
    def test_addition(self):
        self.assertEqual(Decimal("0.10") + Decimal("0.20"), Decimal("0.30"))

    def test_subtraction(self):
        self.assertEqual(Decimal("1.00") - Decimal("0.33"), Decimal("0.67"))

    def test_multiplication(self):
        self.assertEqual(Decimal("100") * Decimal("0.25"), Decimal("25.00"))

    def test_division(self):
        self.assertEqual(Decimal("100") / Decimal("4"), Decimal("25"))


class TestFileHash(unittest.TestCase):
    def test_file_hash(self):
        path = Path(tempfile.mktemp())
        path.write_text("test content")
        h1 = file_hash(path)
        h2 = file_hash(path)
        self.assertEqual(h1, h2)
        self.assertEqual(len(h1), 64)
        path.unlink()


class TestFindPdfs(unittest.TestCase):
    def test_finds_pdfs(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "test.pdf").touch()
            (Path(d) / "test.txt").touch()
            (Path(d) / "personal_financial_report_2023.pdf").touch()
            pdfs = find_pdfs(Path(d))
            self.assertEqual(len(pdfs), 1)
            self.assertIn("test.pdf", [p.name for p in pdfs])


class TestStatementTextParsing(unittest.TestCase):
    def test_parse_amount_with_hash(self):
        self.assertTrue(_is_page_artifact("Page 1 of 6", "Page 1 of 6"))
        self.assertTrue(_is_page_artifact("", ""))
        self.assertFalse(_is_page_artifact("Valid transaction", "Valid transaction"))
