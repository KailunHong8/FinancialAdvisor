from datetime import date
from decimal import Decimal

from recon.models import LedgerAccountBlock, LedgerTransaction, BankStatement, StatementLine
from recon.reconcile import invariants as inv

Z = Decimal("0.00")


def _block(open_, cargos, abonos, close, txns=()):
    return LedgerAccountBlock("A", "lbl", Decimal(open_), Decimal(close),
                              Decimal(cargos), Decimal(abonos), list(txns))


def test_i1_gate_fails_on_broken_balance():
    b = _block("100.00", "50.00", "10.00", "999.00")   # 100+50-10 != 999
    r = [x for x in inv.check_ledger_block(b) if x.id == "I1"][0]
    assert r.gate and not r.passed


def test_i2_gate_fails_when_txn_sums_disagree_with_total():
    t = LedgerTransaction("E", "2024-06", "A", "06-24", 10, date(2024, 6, 1), "Diario", "1",
                          "x", None, Decimal("5.00"), Z, Decimal("105.00"), "inflow", Decimal("5.00"))
    b = _block("100.00", "50.00", "0.00", "150.00", txns=[t])  # Total F=50 but Σtxn cargo=5
    r = [x for x in inv.check_ledger_block(b) if x.id == "I2"][0]
    assert not r.passed


def _stmt(open_, close, ta, tc, lines):
    return BankStatement("bbva", "0114091108", None, "MXN", date(2024, 6, 1), date(2024, 6, 30),
                         Decimal(open_), Decimal(close), Decimal(ta), Decimal(tc), lines,
                         "f.pdf", "sha", "1.0.0")


def _line(n, amount, direction):
    amt = Decimal(amount)
    return StatementLine(1, n, date(2024, 6, 1), None, None, "d", None,
                         amt if direction == "outflow" else Z,
                         amt if direction == "inflow" else Z, None)


def test_i3_gate_fails_on_missing_line():
    # printed totals say abonos=100 but only one 40 line parsed => sum != printed total
    s = _stmt("0.00", "100.00", "100.00", "0.00", [_line(1, "40.00", "inflow")])
    r = inv.check_statement(s)[0]
    assert r.gate and not r.passed


def test_i3_passes_when_consistent():
    s = _stmt("0.00", "100.00", "100.00", "0.00", [_line(1, "60.00", "inflow"),
                                                   _line(2, "40.00", "inflow")])
    assert inv.check_statement(s)[0].passed
