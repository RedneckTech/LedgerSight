"""Data redaction / masking for personal information."""
from __future__ import annotations
import re
from pathlib import Path


class DataRedactor:
    """Centralized data redaction for reports and exports.
    
    When mask_personal is True, known names, addresses, account numbers,
    and file paths are replaced with generic placeholders.
    """

    def __init__(self, mask_personal: bool = False,
                 redact_names: list[str] | None = None,
                 redact_addresses: list[str] | None = None,
                 redact_paths: list[str] | None = None):
        self.mask_personal = mask_personal
        self._names = redact_names or []
        self._addresses = redact_addresses or []
        self._paths = redact_paths or []
        # Build regex patterns
        self._name_patterns = [re.compile(re.escape(n), re.IGNORECASE) for n in self._names if n]
        self._addr_patterns = [re.compile(re.escape(a), re.IGNORECASE) for a in self._addresses if a]
        self._path_patterns = [re.compile(re.escape(p), re.IGNORECASE) for p in self._paths if p]

    def description(self, value: str) -> str:
        if not self.mask_personal or not value:
            return value
        # Redact known names/addresses from descriptions
        for pat in self._name_patterns:
            value = pat.sub("[NAME REDACTED]", value)
        for pat in self._addr_patterns:
            value = pat.sub("[ADDRESS REDACTED]", value)
        # Generic address pattern (123 Main St, etc.)
        value = re.sub(
            r"\b\d+\s+(?:[NSEW]\s+)?[A-Z0-9.'-]+(?:\s+[A-Z0-9.'-]+){0,4}\s+(?:RD|ROAD|ST|STREET|AVE|AVENUE|DR|DRIVE|LN|LANE|WAY|BLVD|BOULEVARD)\b",
            "[ADDRESS REDACTED]", value, flags=re.IGNORECASE,
        )
        return value

    def merchant(self, value: str) -> str:
        if not self.mask_personal or not value:
            return value
        for pat in self._name_patterns:
            value = pat.sub("[NAME REDACTED]", value)
        return value

    def person(self, value: str) -> str:
        return self.merchant(value)

    def source_path(self, value: str) -> str:
        if not self.mask_personal or not value:
            return value
        return Path(value).name
