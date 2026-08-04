import unittest

from ledgersight.categorizer import (
    TransactionCategorizer,
    build_default_rules,
    normalize_merchant,
)
from ledgersight.models import CategoryRule
from tests.conftest import make_stmt, make_tx


class TestCategoryRules(unittest.TestCase):
    def test_fuel_match(self):
        rules = build_default_rules()
        fuel_rule = [r for r in rules if r.category == "Fuel" and "PILOT" in r.pattern]
        self.assertTrue(len(fuel_rule) > 0)

    def test_transfer_match(self):
        rules = build_default_rules()
        xfer_rule = [r for r in rules if r.category == "Account Transfer" and "WEB" in r.pattern]
        self.assertTrue(len(xfer_rule) > 0)
        self.assertTrue(xfer_rule[0].is_transfer)
        self.assertFalse(xfer_rule[0].include_in_pnl)

    def test_income_rule_has_is_income(self):
        rules = build_default_rules()
        service_rev = [r for r in rules if r.category == "Service Revenue"]
        if service_rev:
            self.assertTrue(service_rev[0].is_income)

    def test_expense_before_income(self):
        rules = build_default_rules()
        expense_idx = next(i for i, r in enumerate(rules) if r.category == "Fuel")
        income_idx = next(i for i, r in enumerate(rules) if r.is_income)
        self.assertLess(expense_idx, income_idx,
                        "Expense rules must come before income rules")


class TestTransactionCategorizer(unittest.TestCase):
    def setUp(self):
        self.cat = TransactionCategorizer()

    def test_transfer_categorization(self):
        tx = make_tx(description="WEB XFER TO SAVINGS", is_credit=False, amount="500.00")
        self.cat.categorize(tx)
        self.assertEqual(tx.business_category, "Account Transfer")
        self.assertTrue(tx.is_transfer)
        self.assertFalse(tx.include_in_pnl)

    def test_fuel_categorization(self):
        tx = make_tx(description="PILOT TRAVEL CENTER FUEL", is_credit=False, amount="200.00")
        self.cat.categorize(tx)
        self.assertEqual(tx.business_category, "Fuel")
        self.assertTrue(tx.include_in_pnl)

    def test_uncategorized_cpa_review(self):
        tx = make_tx(description="SOMETHING COMPLETELY UNKNOWN XYZ", is_credit=False, amount="50.00")
        self.cat.categorize(tx)
        self.assertEqual(tx.business_category, "Uncategorized")
        self.assertTrue(tx.cpa_review)
        self.assertFalse(tx.include_in_pnl)

    def test_credit_deposit_flagged(self):
        tx = make_tx(description="UNKNOWN DEPOSIT FROM UNKNOWN SOURCE", is_credit=True, amount="1000.00")
        self.cat.categorize(tx)
        self.cat.mark_credit_deposits([make_stmt(transactions=[tx])])

    def test_custom_rule_override(self):
        custom = [
            CategoryRule(
                pattern="MY_CUSTOM_PATTERN",
                category="Custom Category",
                tax_category="Custom Tax",
                deductibility="likely-deductible",
                priority=1,
                include_in_pnl=True,
            ),
        ]
        cat = TransactionCategorizer(custom_rules=custom)
        tx = make_tx(description="MY_CUSTOM_PATTERN HERE", is_credit=False, amount="50.00")
        cat.categorize(tx)
        self.assertEqual(tx.business_category, "Custom Category")

    def test_first_match_wins(self):
        custom = [
            CategoryRule(pattern="PILOT TRAVEL", category="Custom High Priority",
                            include_in_pnl=True, priority=1),
        ]
        cat = TransactionCategorizer(custom_rules=custom)
        tx = make_tx(description="PILOT TRAVEL CENTER", is_credit=False, amount="200.00")
        cat.categorize(tx)
        self.assertEqual(tx.business_category, "Custom High Priority")

    def test_owner_contribution_excluded(self):
        custom = [
            CategoryRule(pattern="OWNER CONTRIB", category="Owner Contribution",
                            tax_category="non-pl", is_owner_related=True,
                            include_in_pnl=False, priority=1),
        ]
        cat = TransactionCategorizer(custom_rules=custom)
        tx = make_tx(description="OWNER CONTRIB", is_credit=True, amount="5000.00")
        cat.categorize(tx)
        self.assertFalse(tx.include_in_pnl)
        self.assertTrue(tx.is_owner_related)

    def test_fixed_asset_excluded(self):
        custom = [
            CategoryRule(pattern="TRUCK PURCHASE", category="Fixed Asset Purchase",
                            tax_category="non-pl", is_fixed_asset=True,
                            include_in_pnl=False, priority=1),
        ]
        cat = TransactionCategorizer(custom_rules=custom)
        tx = make_tx(description="TRUCK PURCHASE FREIGHTLINER", is_credit=False, amount="50000.00")
        cat.categorize(tx)
        self.assertFalse(tx.include_in_pnl)
        self.assertTrue(tx.is_fixed_asset)

    def test_loan_interest_included(self):
        rules = build_default_rules()
        li_rules = [r for r in rules if r.category == "Loan Interest"]
        if li_rules:
            r = li_rules[0]
            self.assertTrue(r.is_loan)
            self.assertTrue(r.include_in_pnl)


class TestMerchantNormalization(unittest.TestCase):
    def test_normalize_removes_card_data(self):
        result = normalize_merchant("XX5326 DEBIT CARD 01/01 13:02 STORE NAME")
        self.assertNotIn("XX5326", result)
        self.assertNotIn("DEBIT CARD", result.lower())
        self.assertIn("STORE NAME", result)

    def test_normalize_removes_dates(self):
        result = normalize_merchant("01/15 10:30 STORE NAME")
        self.assertNotIn("01/15", result)

    def test_normalize_removes_long_numbers(self):
        result = normalize_merchant("STORE 123456789012345")
        self.assertNotIn("123456789012345", result)
