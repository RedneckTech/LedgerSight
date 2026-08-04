import shutil
import tempfile
import unittest
from pathlib import Path

from ledgersight.business.pl import build_pl
from ledgersight.exports import CSVExporter
from ledgersight.models import BusinessConfig
from tests.conftest import make_stmt, make_tx


class TestCSVExports(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        tx = make_tx(description="FUEL PURCHASE", amount="200.00", is_credit=False,
                     business_category="Fuel", tax_category="Vehicle Fuel",
                     include_in_pnl=True, deductibility="likely-deductible")
        stmt = make_stmt(transactions=[tx])
        self.statements = [stmt]
        config = BusinessConfig(business_name="Test")
        pl = build_pl([tx])
        self.exporter = CSVExporter(self.statements, config, pl)

    def test_export_audit(self):
        path = Path(self.tmpdir) / "audit.csv"
        self.exporter.export_audit(path)
        self.assertTrue(path.exists())
        content = path.read_text()
        self.assertIn("Statement", content)
        self.assertIn("BusinessCategory", content)
        self.assertIn("Fuel", content)

    def test_export_pl(self):
        path = Path(self.tmpdir) / "pl.csv"
        self.exporter.export_pl(path)
        self.assertTrue(path.exists())
        content = path.read_text()
        self.assertIn("Category", content)
        self.assertIn("Revenue", content)

    def test_export_cpa(self):
        outdir = Path(self.tmpdir) / "cpa"
        self.exporter.export_cpa(outdir)
        self.assertTrue((outdir / "cpa_expense_detail.csv").exists())
        self.assertTrue((outdir / "cpa_revenue_detail.csv").exists())
        self.assertTrue((outdir / "cpa_uncategorized.csv").exists())

    def test_export_category_template(self):
        path = Path(self.tmpdir) / "cat_template.csv"
        self.exporter.export_category_template(path)
        self.assertTrue(path.exists())
        content = path.read_text()
        self.assertIn("Merchant", content)
        self.assertIn("CurrentBusinessCategory", content)

    def test_csv_formula_prefix_is_escaped(self):
        tx = make_tx(description="=SUM(A1:A10)", amount="100.00", is_credit=False,
                     business_category="=DANGEROUS", tax_category="+BAD",
                     deductibility="@INJECT", review_reason="-EXPLOIT",
                     include_in_pnl=True)
        stmt = make_stmt(transactions=[tx])
        config = BusinessConfig(business_name="Test")
        pl = build_pl([tx])
        exporter = CSVExporter([stmt], config, pl)
        path = Path(self.tmpdir) / "formula_audit.csv"
        exporter.export_audit(path)
        content = path.read_text()
        self.assertIn("'=SUM", content)
        self.assertIn("'=DANGEROUS", content)
        self.assertIn("'+BAD", content)
        self.assertIn("'@INJECT", content)
        self.assertIn("'-EXPLOIT", content)

    def test_csv_encoding_is_utf8(self):
        tx = make_tx(description="caf\u00e9 \u20ac100", amount="50.00", is_credit=True,
                     business_category="M\u00fcnchen Services",
                     include_in_pnl=True)
        stmt = make_stmt(transactions=[tx])
        config = BusinessConfig(business_name="Test")
        pl = build_pl([tx])
        exporter = CSVExporter([stmt], config, pl)
        path = Path(self.tmpdir) / "utf8_audit.csv"
        exporter.export_audit(path)
        content = path.read_text()
        self.assertIn("caf\u00e9 \u20ac100", content)
        outdir = Path(self.tmpdir) / "utf8_cpa"
        exporter.export_cpa(outdir)
        rev = (outdir / "cpa_revenue_detail.csv").read_text()
        self.assertIn("caf\u00e9 \u20ac100", rev)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)
