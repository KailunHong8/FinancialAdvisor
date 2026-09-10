"""BankParser protocol (§10.1)."""
from __future__ import annotations

from typing import Protocol

from ...models import BankStatement


class BankParseError(Exception):
    """Raised when a registered parser cannot parse a PDF it claimed via sniff()."""


class BankParser(Protocol):
    bank_name: str

    def sniff(self, pdf_bytes: bytes) -> bool: ...
    def parse(self, pdf_bytes: bytes, source_file: str, source_sha256: str) -> list[BankStatement]: ...
