import tempfile
import unittest
from pathlib import Path

from ledgersight.config import (
    generate_example_config,
    load_config,
    validate_config,
)
from ledgersight.models import BusinessConfig, CategoryRule


class TestBusinessConfig(unittest.TestCase):
    def test_defaults(self):
        config = BusinessConfig()
        self.assertEqual(config.business_name, "Business")
        self.assertEqual(config.entity_type, "sole-prop")
        self.assertEqual(config.accounting_method, "cash")

    def test_display_name_without_dba(self):
        config = BusinessConfig(business_name="TestCo")
        self.assertEqual(config.display_name(), "TestCo")

    def test_display_name_with_dba(self):
        config = BusinessConfig(business_name="TestCo LLC", dba="TestCo")
        self.assertIn("TestCo", config.display_name())

    def test_masked_ein(self):
        config = BusinessConfig(ein_display="12-3456789", mask_ein=True)
        masked = config.masked_ein()
        self.assertNotIn("1", masked)
        self.assertTrue(all(c in "X-" for c in masked.replace("X", "").replace("-", "")))

    def test_unmasked_ein(self):
        config = BusinessConfig(ein_display="12-3456789", mask_ein=False)
        self.assertEqual(config.masked_ein(), "12-3456789")

    def test_masked_account(self):
        config = BusinessConfig(
            bank_account_display="CLASSIC BUSINESS CHECKING - XXXX2136",
            mask_account=True,
        )
        masked = config.masked_account()
        self.assertIn("2136", masked)
        self.assertIn("X", masked)


class TestConfigLoading(unittest.TestCase):
    def test_load_nonexistent_config(self):
        config = load_config(Path("/tmp/nonexistent_config.toml"))
        self.assertEqual(config.business_name, "Business")
        self.assertEqual(config.entity_type, "sole-prop")

    def test_init_config(self):
        tmp_path = Path(tempfile.mktemp(suffix=".toml"))
        result = generate_example_config(tmp_path, force=True)
        self.assertTrue(result)
        content = tmp_path.read_text()
        self.assertIn("[general]", content)
        self.assertIn("business_name", content)
        self.assertIn("[cpa]", content)
        self.assertIn("[projections]", content)
        tmp_path.unlink()

    def test_init_config_no_overwrite(self):
        tmp_path = Path(tempfile.mktemp(suffix=".toml"))
        tmp_path.write_text("existing")
        result = generate_example_config(tmp_path, force=False)
        self.assertFalse(result)
        self.assertEqual(tmp_path.read_text(), "existing")
        tmp_path.unlink()


class TestConfigValidation(unittest.TestCase):
    def test_validates_fiscal_year_start_range(self):
        ok_jan = BusinessConfig(fiscal_year_start=1)
        self.assertEqual(validate_config(ok_jan), [])

        ok_dec = BusinessConfig(fiscal_year_start=12)
        self.assertEqual(validate_config(ok_dec), [])

        bad = BusinessConfig(fiscal_year_start=14)
        errors = validate_config(bad)
        self.assertTrue(any("fiscal_year_start" in e for e in errors))

    def test_validates_entity_type(self):
        ok = BusinessConfig(entity_type="s-corp")
        self.assertEqual(validate_config(ok), [])

        bad = BusinessConfig(entity_type="nonprofit")
        errors = validate_config(bad)
        self.assertTrue(any("entity_type" in e for e in errors))

    def test_validates_projection_months_positive(self):
        zero = BusinessConfig(projection_config={"projection_months": 0})
        errors = validate_config(zero)
        self.assertTrue(any("projection_months" in e for e in errors))

        neg = BusinessConfig(projection_config={"projection_months": -12})
        errors = validate_config(neg)
        self.assertTrue(any("projection_months" in e for e in errors))
