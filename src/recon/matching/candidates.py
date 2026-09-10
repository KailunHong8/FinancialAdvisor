"""Shared matching item and scoring (§11)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from ..models import LedgerTransaction, StatementLine
from .similarity import similarity


@dataclass
class WorkItem:
    side: str            # 'ledger' | 'bank'
    id: str
    txn_date: date
    amount: Decimal
    direction: str
    description: str
    sort_key: int        # ledger row_no or bank line_no — for deterministic ordering
    source: object       # LedgerTransaction | StatementLine, for evidence
    matched: bool = False


def ledger_item(t: LedgerTransaction) -> WorkItem:
    return WorkItem("ledger", t.id, t.txn_date, t.amount, t.direction,
                    (t.concepto or ""), t.row_no, t)


def bank_item(l: StatementLine, txn_id: str) -> WorkItem:
    return WorkItem("bank", txn_id, l.oper_date, l.amount, l.direction,
                    l.description, l.line_no, l)


def order(items: list[WorkItem]) -> list[WorkItem]:
    """Deterministic iteration order: (txn_date, amount, sort_key) (§11.2)."""
    return sorted(items, key=lambda w: (w.txn_date, w.amount, w.sort_key))


def days_between(a: date, b: date) -> int:
    return abs((a - b).days)


def score(ledger: WorkItem, bank: WorkItem, window: int) -> dict:
    """Fuzzy score components (§11.2 pass 5)."""
    amt_delta = abs(ledger.amount - bank.amount)
    max_amt = max(ledger.amount, bank.amount, Decimal("1"))
    amount_score = float(max(Decimal("0"), Decimal("1") - amt_delta / max_amt))
    dd = days_between(ledger.txn_date, bank.txn_date)
    date_score = max(0.0, 1.0 - dd / window) if window else (1.0 if dd == 0 else 0.0)
    sim = similarity(ledger.description, bank.description)
    total = 0.5 * amount_score + 0.2 * date_score + 0.3 * sim
    return {"amount_score": round(amount_score, 3), "date_score": round(date_score, 3),
            "similarity": round(sim, 3), "score": round(total, 3),
            "amount_delta": str(ledger.amount - bank.amount), "date_delta_days": dd}
