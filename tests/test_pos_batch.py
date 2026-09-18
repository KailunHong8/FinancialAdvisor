"""Pass 4 — corte de caja vs aggregated POS terminal settlement (§11.2).

Figures are the real June 2024 case: account 1112-01-002-00 / terminal 4396017, póliza 133 is
11 sale rows on 27-Jun totalling 37,284.30, settled 28-Jun as V45 9,722.47 + V42 27,561.83.
"""
from datetime import date
from decimal import Decimal

from recon.config import MatchDefaults, PosBatch
from recon.models import LedgerTransaction, StatementLine
from recon.matching.candidates import ledger_item, bank_item
from recon.matching.engine import match_account

Z = Decimal("0.00")
TERMINAL = "4396017"
REF = "144396017"                                  # BBVA prints Ref. 14<terminal>

# the 11 rows of póliza 133, summing to 37,284.30
CORTE_133 = ["1250.00", "3480.50", "2199.99", "5100.00", "4875.25", "1899.00",
             "6320.40", "2750.16", "3999.00", "2410.00", "3000.00"]


def L(row, day, amount, poliza="133", direction="inflow"):
    amt = Decimal(str(amount))
    cargo = amt if direction == "inflow" else Z     # asset: cargo = inflow
    abono = amt if direction == "outflow" else Z
    return ledger_item(LedgerTransaction(
        entity="E", period="2024-06", ledger_account="1112-01-002-00", sheet="06-24", row_no=row,
        txn_date=date(2024, 6, day), tipo="Diario", poliza=poliza, concepto="Venta",
        referencia=None, cargo=cargo, abono=abono, saldo=None, direction=direction, amount=amt))


def B(line, day, amount, code="V42", ref=REF, desc="VENTAS DEBITO TERMINALES PUNTO DE VENTA"):
    amt = Decimal(str(amount))
    return bank_item(StatementLine(
        page_no=1, line_no=line, oper_date=date(2024, 6, day), liq_date=None, code=code,
        description=f"{desc} Ref. {ref}", reference=ref, cargo=Z, abono=amt,
        running_balance=None), f"b{line}")


def corte(day=27, poliza="133"):
    return [L(i, day, a, poliza=poliza) for i, a in enumerate(CORTE_133, start=1)]


def cfg(**pb):
    return MatchDefaults(description_min_similarity=0.20, pos_batch=PosBatch(**pb))


def batches(out):
    return [m for m in out.matches if m.method == "pos_batch"]


def test_corte_matches_two_next_day_settlement_credits():
    out = match_account(corte(), [B(1, 28, "9722.47", code="V45"), B(2, 28, "27561.83")],
                        cfg(disposition="auto_match"), pos_terminal=TERMINAL)
    assert len(batches(out)) == 1
    m = batches(out)[0]
    assert m.pass_no == 4
    assert len(m.ledger) == 11 and len(m.bank) == 2
    assert m.ledger_amount == Decimal("37284.30") and m.amount_delta == Z
    assert not out.leftover_ledger and not out.leftover_bank
    ev = m.evidence["pos_batch"]
    assert ev["poliza"] == "133" and ev["pos_terminal"] == TERMINAL
    assert ev["cut_date"] == "2024-06-27" and ev["settlement_lag_days"] == 1


def test_review_candidate_reserves_both_sides_without_matching():
    out = match_account(corte(), [B(1, 28, "9722.47", code="V45"), B(2, 28, "27561.83")],
                        cfg(), pos_terminal=TERMINAL)
    assert not batches(out)
    assert len(out.pos_batch_candidates) == 1
    candidate = out.pos_batch_candidates[0]
    assert candidate.id == f"POS-{candidate.ledger[0].id}"
    assert len(candidate.ledger) == 11 and len(candidate.bank) == 2
    assert len(out.leftover_ledger) == 11 and len(out.leftover_bank) == 2


def test_requires_this_accounts_terminal():
    """Credits stamped with a different terminal belong to a different account's POS batch."""
    out = match_account(corte(), [B(1, 28, "9722.47", code="V45", ref="144396025"),
                                  B(2, 28, "27561.83", ref="144396025")],
                        cfg(), pos_terminal=TERMINAL)
    assert not batches(out)
    assert len(out.leftover_ledger) == 11


def test_settlement_lag_is_one_sided():
    """Credits dated before the cut cannot be its settlement, however well the total agrees."""
    out = match_account(corte(), [B(1, 26, "9722.47", code="V45"), B(2, 26, "27561.83")],
                        cfg(), pos_terminal=TERMINAL)
    assert not batches(out)


def test_friday_cut_settles_monday_within_lag():
    # póliza 116: 07-Jun (Fri) -> 10-Jun (Mon) is the +3 case that forces the default lag
    out = match_account(corte(day=7, poliza="116"),
                        [B(1, 10, "9722.47", code="V45"), B(2, 10, "27561.83")],
                        cfg(disposition="auto_match"), pos_terminal=TERMINAL)
    assert len(batches(out)) == 1 and batches(out)[0].evidence["pos_batch"]["settlement_lag_days"] == 3


def test_beyond_lag_is_not_matched():
    out = match_account(corte(day=7, poliza="116"),
                        [B(1, 11, "9722.47", code="V45"), B(2, 11, "27561.83")],
                        cfg(), pos_terminal=TERMINAL)
    assert not batches(out)


def test_ambiguous_settlement_combination_rejected():
    """Two distinct subsets reach 37,284.30, so neither is evidence — leave it all open (§11.2)."""
    out = match_account(corte(), [B(1, 28, "9722.47", code="V45"), B(2, 28, "27561.83"),
                                  B(3, 28, "10000.00", code="V45"), B(4, 28, "27284.30")],
                        cfg(), pos_terminal=TERMINAL)
    assert not batches(out)
    assert len(out.leftover_ledger) == 11


def test_adjacent_cortes_can_match_with_unique_explicit_exclusion():
    ledger = [L(1, 19, "8400.35", poliza="126"),
              L(2, 19, "3888.79", poliza="126"),
              L(3, 20, "3406.68", poliza="127"),
              L(4, 20, "388.79", poliza="127")]
    for item in ledger:
        item.description = f"corte de ventas del dia {item.txn_date:%d/%m/%y}"
    bank = [B(1, 20, "2325.40"), B(2, 20, "13370.42")]
    out = match_account(ledger, bank, cfg(disposition="auto_match"), pos_terminal=TERMINAL)
    match = next(m for m in out.matches if m.method == "pos_batch")
    assert match.ledger_amount == match.bank_amount == Decimal("15695.82")
    assert match.evidence["pos_batch"]["poliza"] == "126+127"
    assert match.evidence["pos_batch"]["excluded_ledger_rows"] == [4]
    assert [w.amount for w in out.leftover_ledger] == [Decimal("388.79")]


def test_single_corte_can_match_with_unique_explicit_exclusions():
    ledger = [L(1, 25, "1218.00", poliza="131"),
              L(2, 25, "59556.89", poliza="131"),
              L(3, 25, "43667.13", poliza="131"),
              L(4, 25, "12189.86", poliza="131"),
              L(5, 25, "49391.35", poliza="131")]
    for item in ledger:
        item.description = "corte de ventas del dia 25/06/24"
    bank = [B(1, 26, "1218.00", code="I72"), B(2, 26, "12189.86", code="V45"),
            B(3, 26, "49391.35", code="V42")]
    out = match_account(ledger, bank, cfg(disposition="auto_match"), pos_terminal=TERMINAL)
    match = next(m for m in out.matches if m.method == "pos_batch")
    assert match.ledger_amount == match.bank_amount == Decimal("62799.21")
    assert match.evidence["pos_batch"]["excluded_ledger_rows"] == [2, 3]
    assert [w.amount for w in out.leftover_ledger] == [Decimal("59556.89"), Decimal("43667.13")]


def test_single_corte_prefers_one_exclusion_and_reports_small_delta():
    ledger = [L(1, 25, "1218.00", poliza="131"),
              L(2, 25, "1581.14", poliza="131"),
              L(3, 25, "163224.09", poliza="131")]
    for item in ledger:
        item.description = "corte de ventas del dia 25/06/24"
    bank = [B(1, 26, "1218.00", code="I72"), B(2, 27, "163224.02", code="V45")]
    out = match_account(
        ledger, bank,
        cfg(disposition="auto_match", amount_tolerance=Decimal("0.10")),
        pos_terminal=TERMINAL)
    match = next(m for m in out.matches if m.method == "pos_batch")
    assert match.evidence["pos_batch"]["excluded_ledger_rows"] == [2]
    assert match.ledger_amount == Decimal("164442.09")
    assert match.bank_amount == Decimal("164442.02")
    assert match.amount_delta == Decimal("0.07")
    assert [w.amount for w in out.leftover_ledger] == [Decimal("1581.14")]


def test_unresolved_corte_is_not_fragmented_by_later_passes():
    ledger = [L(1, 20, "100.00", poliza="127"), L(2, 20, "200.00", poliza="127")]
    for item in ledger:
        item.description = "corte de ventas del dia 20/06/24"
    bank = [B(1, 20, "100.00")]
    out = match_account(ledger, bank, cfg(disposition="auto_match"), pos_terminal=TERMINAL)
    assert not out.matches
    assert len(out.leftover_ledger) == 2 and len(out.leftover_bank) == 1


def test_disabled_and_non_pos_accounts_are_untouched():
    lines = [B(1, 28, "9722.47", code="V45"), B(2, 28, "27561.83")]
    assert not batches(match_account(corte(), lines, cfg(enabled=False), pos_terminal=TERMINAL))
    assert not batches(match_account(corte(), lines, cfg(), pos_terminal=None))


def test_matched_amounts_agree_per_direction():
    """The I4 property the engine gates on: a batch contributes equally to both sides."""
    out = match_account(corte(), [B(1, 28, "9722.47", code="V45"), B(2, 28, "27561.83")],
                        cfg(disposition="auto_match"), pos_terminal=TERMINAL)
    ledger = sum((m.ledger_amount for m in out.matches), Z)
    bank = sum((m.bank_amount for m in out.matches), Z)
    assert ledger == bank == Decimal("37284.30")
