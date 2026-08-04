import unittest

from ledgersight.pdf_renderer import ReportPDF


class TestReportPDF(unittest.TestCase):
    def test_instantiate_and_font_attrs(self):
        pdf = ReportPDF("Test Report")
        self.assertIn(pdf.body_font, ("DJV", "Helvetica"))
        self.assertIn(pdf.mono_font, ("DJVM", "Courier"))
        self.assertIsInstance(pdf._use_dejavu, bool)

    def test_add_page_and_output(self):
        pdf = ReportPDF("Multi Page")
        pdf.add_page()
        pdf.section_title("Section A")
        pdf.body_text("Body paragraph text.")
        pdf.add_page()
        pdf.draw_kv_table([("Key", "Value"), ("Foo", "Bar")])
        buf = pdf.output()
        self.assertIsInstance(buf, (bytes, bytearray))
        self.assertGreater(len(buf), 200)
