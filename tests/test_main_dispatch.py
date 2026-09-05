"""Unit tests for ledgersight.__main__ dispatch."""

from __future__ import annotations

import unittest

from ledgersight.__main__ import _parse_profile


class TestParseProfile(unittest.TestCase):
    def test_default_is_business(self) -> None:
        argv = ["--cli", "-d", "data/business"]
        self.assertEqual(_parse_profile(argv), "business")
        self.assertEqual(argv, ["--cli", "-d", "data/business"])

    def test_explicit_personal_extracted(self) -> None:
        argv = ["--cli", "--profile", "personal", "-d", "data"]
        self.assertEqual(_parse_profile(argv), "personal")
        self.assertEqual(argv, ["--cli", "-d", "data"])

    def test_explicit_business_extracted(self) -> None:
        argv = ["--cli", "--profile", "business"]
        self.assertEqual(_parse_profile(argv), "business")
        self.assertEqual(argv, ["--cli"])


if __name__ == "__main__":
    unittest.main()
