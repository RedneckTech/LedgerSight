import unittest
from ledgersight.redaction import DataRedactor


class TestDataRedaction(unittest.TestCase):
    def test_mask_removes_vendor_names_from_descriptions(self):
        redactor = DataRedactor(mask_personal=True, redact_names=["John Doe", "Acme Corp"])
        desc = "Payment to John Doe for services at 123 Main St"
        result = redactor.description(desc)
        self.assertIn("[NAME REDACTED]", result)
        self.assertIn("[ADDRESS REDACTED]", result)
        self.assertNotIn("John Doe", result)
        self.assertNotIn("123 Main St", result)

    def test_mask_merchant_replaces_name(self):
        redactor = DataRedactor(mask_personal=True, redact_names=["Amazon"])
        result = redactor.merchant("Amazon Web Services")
        self.assertIn("[NAME REDACTED]", result)
        self.assertNotIn("Amazon", result)

    def test_no_mask_when_disabled(self):
        redactor = DataRedactor(mask_personal=False, redact_names=["John Doe"])
        desc = "Payment to John Doe at 123 Main St"
        self.assertEqual(redactor.description(desc), desc)
        self.assertEqual(redactor.merchant("John Doe"), "John Doe")
        self.assertEqual(redactor.source_path("/home/user/docs/file.csv"), "/home/user/docs/file.csv")

    def test_source_path_returns_basename(self):
        redactor = DataRedactor(mask_personal=True)
        result = redactor.source_path("/home/user/documents/statement_2023.pdf")
        self.assertEqual(result, "statement_2023.pdf")
        self.assertNotIn("home", result)

    def test_empty_value_returns_empty(self):
        redactor = DataRedactor(mask_personal=True, redact_names=["Test"])
        self.assertEqual(redactor.description(""), "")
        self.assertEqual(redactor.merchant(""), "")
        self.assertEqual(redactor.source_path(""), "")

    def test_case_insensitive_name_matching(self):
        redactor = DataRedactor(mask_personal=True, redact_names=["John Smith"])
        self.assertIn("[NAME REDACTED]", redactor.description("JOHN SMITH paid"))
        self.assertIn("[NAME REDACTED]", redactor.description("john smith paid"))

    def test_address_pattern_matches_variations(self):
        redactor = DataRedactor(mask_personal=True)
        self.assertIn("[ADDRESS REDACTED]", redactor.description("456 Elm Road"))
        self.assertIn("[ADDRESS REDACTED]", redactor.description("789 Oak Avenue"))
        self.assertIn("[ADDRESS REDACTED]", redactor.description("123 N Maple Drive"))
