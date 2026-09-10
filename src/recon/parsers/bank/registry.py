"""Bank parser registry (§6 clarification, §10). One plug-in in v1: bbva. A second bank is
additive — register it here."""
from __future__ import annotations

from ...config import Config
from .base import BankParser
from .bbva import BBVAParser


def build_registry(config: Config) -> dict[str, BankParser]:
    return {"bbva": BBVAParser(config.bank("bbva"))}


def sniff_parser(pdf_bytes: bytes, registry: dict[str, BankParser]) -> BankParser | None:
    for parser in registry.values():
        if parser.sniff(pdf_bytes):
            return parser
    return None
