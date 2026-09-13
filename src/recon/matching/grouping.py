"""Póliza grouping (passes 3-4) and bounded subset-sum (pass 5) (§11.2)."""
from __future__ import annotations

from decimal import Decimal
from itertools import combinations

from ..models import LedgerTransaction
from .candidates import WorkItem, days_between


def poliza_groups(ledger: list[WorkItem]) -> dict[tuple, list[WorkItem]]:
    """Group unmatched ledger rows by (txn_date, tipo, poliza, direction). Grounded in CONTPAQi
    assigning one Número to every line of a daily sales cut (§11.2)."""
    groups: dict[tuple, list[WorkItem]] = {}
    for w in ledger:
        t: LedgerTransaction = w.source
        if not t.poliza:
            continue
        key = (t.txn_date, t.tipo, t.poliza, w.direction)
        groups.setdefault(key, []).append(w)
    # only groups of 2+ are interesting for N:1 (a single row is handled by passes 1-2)
    return {k: v for k, v in groups.items() if len(v) >= 2}


def unique_subset(target: Decimal, pool: list[WorkItem], bank_date,
                  window: int, max_pool: int, max_subset: int) -> list[WorkItem] | None:
    """Find the unique subset of `pool` summing exactly to `target` within the date window.

    Bounded (pool<=max_pool, subset<=max_subset). A non-unique solution returns None — a
    coincidental combination is evidence of nothing and must not become a match (§11.2).
    """
    candidates = [w for w in pool if days_between(w.txn_date, bank_date) <= window][:max_pool]
    solutions: list[tuple[WorkItem, ...]] = []
    for size in range(1, min(max_subset, len(candidates)) + 1):
        for combo in combinations(candidates, size):
            if sum((w.amount for w in combo), Decimal("0")) == target:
                solutions.append(combo)
                if len(solutions) > 1:
                    return None
    if len(solutions) == 1:
        return list(solutions[0])
    return None
