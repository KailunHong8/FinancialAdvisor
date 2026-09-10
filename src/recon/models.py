"""Domain models and deterministic identity (§10.1, §11.1)."""
from __future__ import annotations

import hashlib
import unicodedata
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

# ---------------------------------------------------------------------------
# Identity (§11.1). IDs are stable across reruns so Tab 3 resolutions survive.
# ---------------------------------------------------------------------------


def _hash(*parts: object) -> str:
    joined = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def ledger_txn_id(entity: str, period: str, ledger_account: str, sheet: str, row_no: int) -> str:
    return _hash(entity, period, ledger_account, sheet, row_no)


def bank_txn_id(entity: str, bank_account: str, statement_sha256: str, line_no: int) -> str:
    return _hash(entity, bank_account, statement_sha256, line_no)


def item_id(entity: str, ledger_account: str, side: str, direction: str,
            amount: Decimal, txn_date: str | None, source_ref: str) -> str:
    # deliberately excludes period and run_id so a carried item keeps its id (§11.1, §12.4)
    return _hash(entity, ledger_account, side, direction, amount, txn_date or "", source_ref)


# ---------------------------------------------------------------------------
# Description normalization for similarity (§11.1)
# ---------------------------------------------------------------------------

_STOP_WORDS = {"TRF", "SPEI", "DEPOSITO", "PAGO"}
_LONG_DIGITS = re.compile(r"\b\w*\d{4,}\w*\b")   # long digit runs: SPEI/Trf refs, F/ invoice refs
_INVOICE = re.compile(r"\bF/\S+\b", re.IGNORECASE)
_PUNCT = re.compile(r"[^\w\s]")
_WS = re.compile(r"\s+")


def normalize_description(text: str) -> str:
    """Uppercase, strip accents, drop punctuation, drop high-cardinality tokens (§11.1).

    Trf9359103819 Ame de Quintana Roo SA F/202147 -> AME DE QUINTANA ROO SA
    """
    if not text:
        return ""
    s = unicodedata.normalize("NFKD", text)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.upper()
    s = _INVOICE.sub(" ", s)
    s = _LONG_DIGITS.sub(" ", s)
    s = _PUNCT.sub(" ", s)
    tokens = [t for t in _WS.split(s) if t and t not in _STOP_WORDS]
    return " ".join(tokens)


# ---------------------------------------------------------------------------
# Parser outputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LedgerTransaction:
    entity: str
    period: str
    ledger_account: str
    sheet: str
    row_no: int
    txn_date: date
    tipo: str | None
    poliza: str | None
    concepto: str | None
    referencia: str | None
    cargo: Decimal
    abono: Decimal
    saldo: Decimal | None
    direction: str          # inflow | outflow
    amount: Decimal

    @property
    def id(self) -> str:
        return ledger_txn_id(self.entity, self.period, self.ledger_account, self.sheet, self.row_no)


@dataclass
class LedgerAccountBlock:
    ledger_account: str
    label: str
    opening: Decimal
    closing: Decimal
    total_cargos: Decimal
    total_abonos: Decimal
    transactions: list[LedgerTransaction] = field(default_factory=list)
    col_i_annotation: list[Decimal] = field(default_factory=list)   # §9.4
    had_total_row: bool = True


@dataclass(frozen=True)
class StatementLine:
    page_no: int
    line_no: int
    oper_date: date
    liq_date: date | None
    code: str | None
    description: str
    reference: str | None
    cargo: Decimal          # money OUT of the account
    abono: Decimal          # money IN
    running_balance: Decimal | None

    @property
    def direction(self) -> str:
        return "outflow" if self.cargo > 0 else "inflow"

    @property
    def amount(self) -> Decimal:
        return self.cargo if self.cargo > 0 else self.abono


@dataclass
class BankStatement:
    bank_name: str
    bank_account: str
    clabe: str | None
    currency: str
    period_start: date
    period_end: date
    opening_balance: Decimal
    closing_balance: Decimal
    total_abonos: Decimal
    total_cargos: Decimal
    lines: list[StatementLine]
    source_file: str
    source_sha256: str
    parser_version: str
