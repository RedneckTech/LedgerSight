import unittest

from ledgersight.redaction import DataRedactor


class TestDataRedaction(unittest.TestCase):
    def test_mask_removes_vendor_names_from_descriptions(self):
        redactor = DataRedactor(mask_personal=True, redact_names=["John Doe", "Acme Corp"])
        desc = "Payment to John Doe for services at 123 Main St"
        result = redactor.description(desc)
        self.assertIn("Entity 001", result)
        self.assertIn("[ADDRESS REDACTED]", result)
        self.assertNotIn("John Doe", result)
        self.assertNotIn("123 Main St", result)

    def test_mask_merchant_replaces_name(self):
        redactor = DataRedactor(mask_personal=True, redact_names=["Amazon"])
        result = redactor.merchant("Amazon Web Services")
        self.assertIn("Vendor", result)
        self.assertNotEqual(result, "Amazon Web Services")

    def test_no_mask_when_disabled(self):
        redactor = DataRedactor(mask_personal=False, redact_names=["John Doe"])
        desc = "Payment to John Doe at 123 Main St"
        self.assertEqual(redactor.description(desc), desc)
        self.assertEqual(redactor.merchant("John Doe"), "John Doe")
        self.assertEqual(redactor.source_path("/home/user/docs/file.csv"), "/home/user/docs/file.csv")

    def test_source_path_returns_basename(self):
        redactor = DataRedactor(mask_personal=True)
        result = redactor.source_path("/home/user/documents/statement_2023.pdf")
        self.assertNotIn("/home", result)
        self.assertIn("Statement", result)

    def test_empty_value_returns_empty(self):
        redactor = DataRedactor(mask_personal=True, redact_names=["Test"])
        self.assertEqual(redactor.description(""), "")
        self.assertEqual(redactor.merchant(""), "")
        self.assertEqual(redactor.source_path(""), "")

    def test_case_insensitive_name_matching(self):
        redactor = DataRedactor(mask_personal=True, redact_names=["John Smith"])
        self.assertIn("Entity 001", redactor.description("JOHN SMITH paid"))
        self.assertIn("Entity 001", redactor.description("john smith paid"))

    def test_address_pattern_matches_variations(self):
        redactor = DataRedactor(mask_personal=True)
        self.assertIn("[ADDRESS REDACTED]", redactor.description("456 Elm Road"))
        self.assertIn("[ADDRESS REDACTED]", redactor.description("789 Oak Avenue"))
        self.assertIn("[ADDRESS REDACTED]", redactor.description("123 N Maple Drive"))

    def test_phone_numbers_redacted(self):
        redactor = DataRedactor(mask_personal=True)
        self.assertEqual(redactor.description("Call 555-555-1212"),
                         "Call [PHONE]")
        self.assertEqual(redactor.description("(555) 555-1212 office"),
                         "[PHONE] office")

    def test_emails_redacted(self):
        redactor = DataRedactor(mask_personal=True)
        self.assertEqual(redactor.description("Email john@example.com"),
                         "Email [EMAIL]")

    def test_stable_pseudonyms(self):
        redactor = DataRedactor(mask_personal=True)
        p1 = redactor.merchant("APEX CAPITAL CORP")
        p2 = redactor.merchant("APEX CAPITAL CORP")
        self.assertEqual(p1, p2, "Same merchant should get same pseudonym")
        self.assertIn("Vendor", p1)

    def test_account_numbers_redacted(self):
        redactor = DataRedactor(mask_personal=True)
        self.assertEqual(redactor.description("Acct XXXX2136"),
                         "Acct [ACCOUNT]")

    def test_source_path_pseudonyms(self):
        redactor = DataRedactor(mask_personal=True)
        p1 = redactor.source_path("/home/user/data/business/2023/Jan 31 2023.pdf")
        p2 = redactor.source_path("/home/user/data/business/2023/Jan 31 2023.pdf")
        self.assertEqual(p1, p2)
        self.assertIn("Statement", p1)
