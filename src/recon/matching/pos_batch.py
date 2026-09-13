"""POS batch settlement matching (pass 4) (§11.2).

A day's *corte de caja* is booked in the ledger as one póliza of individual sale rows, but the
acquirer settles it into the bank account on a later banking day as a handful of aggregate
credits — one per card product. The ledger row count and the bank line count therefore never
agree, and no 1:1 or N:1 pass can close it.

Calibrated on account 0118892253 / terminal 4396017, June 2024: póliza 133 (11 rows, 27-Jun,
37,284.30) arrives 28-Jun as `V45 VENTAS CREDITO` 9,722.47 + `V42 VENTAS DEBITO` 27,561.83, both
stamped `Ref. 144396017`.

Two constraints make this safe to automate where the blind subset-sum of pass 5 would not be:
the bank side is restricted to credits carrying this account's own terminal id, and the lag is
one-sided — settlement follows the cut, it never precedes it.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from itertools import combinations

from ..money import ZERO
from .candidates import WorkItem


def carries_terminal(item: WorkItem, terminal: str) -> bool:
    """True if this bank line is stamped with `terminal`.

    The acquirer stamps every settlement credit with the terminal id (BBVA prints it as
    `Ref. 14<terminal>`), and that reference — not the operation code — is the dependable POS
    signal: on the calibration account 37 of 38 credits carry it, spread over four codes, of
    which only V42/V45 are in `known_codes` while I72/K54 are not. Categorising by code would
    have silently skipped those batches.
    """
    line = item.source
    return any(f and terminal in f
               for f in (getattr(line, "reference", None), getattr(line, "description", None)))


def eligible_settlements(pos_lines: list[WorkItem], cut: date, lag_days: int) -> list[WorkItem]:
    """Still-unmatched terminal credits landing on the cut date or up to `lag_days` after it."""
    return [w for w in pos_lines if not w.matched and 0 <= (w.txn_date - cut).days <= lag_days]


def settlement_subset(target: Decimal, eligible: list[WorkItem],
                      max_lines: int) -> list[WorkItem] | None:
    """The unique subset of `eligible` summing exactly to `target`, else None.

    Two qualifying subsets means None: a coincidental combination is evidence of nothing, the
    same rule pass 5 applies (§11.2). Bounded by `max_lines` — on the calibration month raising
    it past 4 resolved no additional batch, so the bound costs nothing.
    """
    found: list[WorkItem] | None = None
    for size in range(1, min(max_lines, len(eligible)) + 1):
        for combo in combinations(eligible, size):
            if sum((w.amount for w in combo), ZERO) == target:
                if found is not None:
                    return None
                found = list(combo)
    return found
