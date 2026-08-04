"""Data redaction / masking for personal information."""
from __future__ import annotations

import re


class DataRedactor:
    """Centralized data redaction for reports and exports.

    When mask_personal is True, known names, addresses, account numbers,
    and file paths are replaced with generic placeholders.
    """

    _PHONE_RE = re.compile(r'\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}')
    _EMAIL_RE = re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+')
    _ACCOUNT_RE = re.compile(r'X{4,}\d{0,4}')

    def __init__(self, mask_personal: bool = False,
                 redact_names: list[str] | None = None,
                 redact_addresses: list[str] | None = None,
                 redact_paths: list[str] | None = None,
                 redact_phone_numbers: bool = True,
                 redact_email: bool = True):
        self.mask_personal = mask_personal
        self.redact_phone_numbers = redact_phone_numbers
        self.redact_email = redact_email
        self._names = redact_names or []
        self._addresses = redact_addresses or []
        self._paths = redact_paths or []
        self._pseudonym_counter: dict[str, int] = {}
        self._pseudonym_map: dict[str, str] = {}
        # Build regex patterns
        self._name_patterns = [re.compile(re.escape(n), re.IGNORECASE) for n in self._names if n]
        self._addr_patterns = [re.compile(re.escape(a), re.IGNORECASE) for a in self._addresses if a]
        self._path_patterns = [re.compile(re.escape(p), re.IGNORECASE) for p in self._paths if p]

    def _get_pseudonym(self, key: str, prefix: str) -> str:
        lookup = key.lower()
        if lookup not in self._pseudonym_map:
            idx = self._pseudonym_counter.get(prefix, 0) + 1
            self._pseudonym_counter[prefix] = idx
            self._pseudonym_map[lookup] = f"{prefix} {idx:03d}"
        return self._pseudonym_map[lookup]

    def description(self, value: str) -> str:
        if not self.mask_personal or not value:
            return value
        # Replace known addresses
        for pat in self._addr_patterns:
            value = pat.sub("[ADDRESS REDACTED]", value)
        # Generic address pattern (123 Main St, etc.)
        value = re.sub(
            r"\b\d+\s+(?:[NSEW]\s+)?[A-Z0-9.'-]+(?:\s+[A-Z0-9.'-]+){0,4}\s+(?:RD|ROAD|ST|STREET|AVE|AVENUE|DR|DRIVE|LN|LANE|WAY|BLVD|BOULEVARD)\b",
            "[ADDRESS REDACTED]", value, flags=re.IGNORECASE,
        )
        # Replace known names with pseudonyms
        for i, pat in enumerate(self._name_patterns):
            name = self._names[i]
            pseudonym = self._get_pseudonym(name, "Entity")
            value = pat.sub(pseudonym, value)
        if self.redact_phone_numbers:
            value = self._PHONE_RE.sub("[PHONE]", value)
        if self.redact_email:
            value = self._EMAIL_RE.sub("[EMAIL]", value)
        value = self._ACCOUNT_RE.sub("[ACCOUNT]", value)
        return value

    def merchant(self, value: str) -> str:
        if not self.mask_personal or not value:
            return value
        return self._get_pseudonym(value, "Vendor")

    def person(self, value: str) -> str:
        if not self.mask_personal or not value:
            return value
        return self._get_pseudonym(value, "Person")

    def source_path(self, value: str) -> str:
        if not self.mask_personal or not value:
            return value
        return self._get_pseudonym(value, "Statement")
