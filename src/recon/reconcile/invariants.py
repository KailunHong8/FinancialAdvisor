"""Automatic invariants (§13). Gate = aborts the run; report = becomes a Tab 3 row.

These replace hand verification. A gate failure before matching (I1/I3) means something was
mis-read; after matching (I4-I7) it means a matcher bug. Either way `recon run` writes no
workbook — a half-correct workpaper is worse than none.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ..money import q, ZERO
from ..models import BankStatement, LedgerAccountBlock
from ..matching.engine import AccountMatchOutcome
from .derive import DerivedAccountPeriod

TOL = q("0.01")


@dataclass
class InvariantResult:
    id: str
    passed: bool
    gate: bool
    detail: str
    ledger_account: str | None = None


def _ok(a: Decimal, b: Decimal) -> bool:
    return abs(a - b) <= TOL


# --- pre-matching (I1, I2) --------------------------------------------------

def check_ledger_block(block: LedgerAccountBlock) -> list[InvariantResult]:
    res = []
    acct = block.ledger_account
    # I1: open + Σcargos − Σabonos = close
    lhs = q(block.opening + block.total_cargos - block.total_abonos)
    res.append(InvariantResult("I1", _ok(lhs, block.closing), True,
        f"open+Σcargos−Σabonos={lhs} vs close={block.closing}", acct))
    # I2: parsed transaction sums == the block's Total: row F/G. This is the tie-out that catches
    # dropped or double-read rows and is the gating form of I2. The design spec also compares the
    # last transaction's printed Saldo to the close; on real historical data that running-balance
    # column occasionally does not chain to the Total (e.g. 04-24!1112-01-007-00 is off by
    # 5,418.28 while the sums tie exactly), so the last-saldo comparison is reported, not gated.
    sum_c = sum((t.cargo for t in block.transactions), ZERO)
    sum_a = sum((t.abono for t in block.transactions), ZERO)
    i2_totals = _ok(sum_c, block.total_cargos) and _ok(sum_a, block.total_abonos)
    detail = f"Σcargos={sum_c}/{block.total_cargos} Σabonos={sum_a}/{block.total_abonos}"
    if block.transactions and block.transactions[-1].saldo is not None:
        saldo_ok = _ok(block.transactions[-1].saldo, block.closing)
        detail += f" last_saldo={block.transactions[-1].saldo}/{block.closing}"
        if not saldo_ok:
            detail += " (last-saldo chain quirk — reported, not gated)"
    res.append(InvariantResult("I2", i2_totals, True, detail, acct))
    return res


# --- pre-matching (I3) ------------------------------------------------------

def check_statement(stmt: BankStatement) -> list[InvariantResult]:
    """I3 = (a) open + Σabonos − Σcargos = close, and (b) parsed line sums equal the printed
    'Depósitos'/'Retiros' totals. Both tie to the cent on real BBVA data (§17.2), so a missed
    line is caught.

    Deviation from design-spec §13: the per-line running-balance chain check is NOT applied.
    Calibration against a real 'MAESTRA PYME BBVA' statement showed balances are printed only
    sparsely and in two columns (Operación, ordered by operation date; Liquidación, by
    liquidation date), so consecutive rows do not satisfy prior±amount. The two total tie-outs
    above are the meaningful, verifiable form of I3 for this layout.
    """
    sum_ab = sum((l.abono for l in stmt.lines), ZERO)
    sum_ca = sum((l.cargo for l in stmt.lines), ZERO)
    flow_ok = _ok(q(stmt.opening_balance + sum_ab - sum_ca), stmt.closing_balance)
    totals_ok = _ok(sum_ab, stmt.total_abonos) and _ok(sum_ca, stmt.total_cargos)
    detail = (f"open+Σab−Σca={q(stmt.opening_balance + sum_ab - sum_ca)}/{stmt.closing_balance}; "
              f"Σab={sum_ab}/{stmt.total_abonos} Σca={sum_ca}/{stmt.total_cargos}")
    return [InvariantResult("I3", flow_ok and totals_ok, True, detail, stmt.bank_account)]


# --- post-matching (I4, I5, I6, I7) + R1 ------------------------------------

def check_matching(acct: str, outcome: AccountMatchOutcome, ap: DerivedAccountPeriod,
                   n_ledger: int, n_bank: int) -> list[InvariantResult]:
    res = []
    # I4: Σ matched ledger = Σ matched bank, per direction (after amount_variance; tol 0 default)
    i4_ok = True
    i4_detail = []
    for direction in ("inflow", "outflow"):
        ml = sum((m.ledger_amount for m in outcome.matches
                  if m.ledger and m.ledger[0].direction == direction), ZERO)
        mb = sum((m.bank_amount for m in outcome.matches
                  if m.bank and m.bank[0].direction == direction), ZERO)
        variance = sum((m.amount_delta for m in outcome.matches
                        if m.ledger and m.ledger[0].direction == direction), ZERO)
        if not _ok(ml - variance, mb):
            i4_ok = False
        i4_detail.append(f"{direction}: L{ml}−var{variance} vs B{mb}")
    res.append(InvariantResult("I4", i4_ok, True, "; ".join(i4_detail), acct))

    # I5: closure_residual = 0
    res.append(InvariantResult("I5", _ok(ap.closure_residual, ZERO), True,
        f"closure_residual={ap.closure_residual}", acct))

    # I6: no ledger/bank id in >1 match
    seen = set()
    i6_ok = True
    for m in outcome.matches:
        for w in m.ledger + m.bank:
            if w.id in seen:
                i6_ok = False
            seen.add(w.id)
    res.append(InvariantResult("I6", i6_ok, True, f"{len(seen)} members exclusive", acct))

    # I7: every ledger row and bank line is in a match or has a reconciling item
    matched_ids = {w.id for m in outcome.matches for w in m.ledger + m.bank}
    leftover_ids = {w.id for w in outcome.leftover_ledger + outcome.leftover_bank}
    covered = len(matched_ids) + len(leftover_ids)
    i7_ok = (covered == n_ledger + n_bank) and matched_ids.isdisjoint(leftover_ids)
    res.append(InvariantResult("I7", i7_ok, True,
        f"matched={len(matched_ids)} leftover={len(leftover_ids)} total={n_ledger + n_bank}", acct))

    # R1: opening_variance = 0 (report, not gate — a genuine business finding)
    if ap.opening_variance is not None:
        res.append(InvariantResult("R1", _ok(ap.opening_variance, ZERO), False,
            f"opening_variance={ap.opening_variance}", acct))
    return res


# --- whole-sheet (R2) -------------------------------------------------------

def check_grand_total(blocks_close_sum: Decimal, cargos_sum: Decimal, abonos_sum: Decimal,
                      expected: dict[str, Decimal] | None) -> list[InvariantResult]:
    if not expected:
        return []
    ok = (_ok(cargos_sum, expected["cargos"]) and _ok(abonos_sum, expected["abonos"])
          and _ok(blocks_close_sum, expected["close"]))
    return [InvariantResult("R2", ok, True,
        f"cargos={cargos_sum}/{expected['cargos']} abonos={abonos_sum}/{expected['abonos']} "
        f"close={blocks_close_sum}/{expected['close']}", None)]
