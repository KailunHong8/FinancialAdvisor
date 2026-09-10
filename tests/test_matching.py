from datetime import date
from decimal import Decimal

from recon.config import MatchDefaults
from recon.models import LedgerTransaction, StatementLine
from recon.matching.candidates import ledger_item, bank_item
from recon.matching.engine import match_account

Z = Decimal("0.00")


def L(row, day, amount, direction="inflow", poliza=None, tipo="Diario", concepto=""):
    amt = Decimal(str(amount))
    cargo = amt if direction == "inflow" else Z       # asset: cargo=inflow
    abono = amt if direction == "outflow" else Z
    return ledger_item(LedgerTransaction(
        entity="E", period="2024-06", ledger_account="A", sheet="06-24", row_no=row,
        txn_date=date(2024, 6, day), tipo=tipo, poliza=poliza, concepto=concepto,
        referencia=None, cargo=cargo, abono=abono, saldo=None, direction=direction, amount=amt))


def B(line, day, amount, direction="inflow", desc=""):
    amt = Decimal(str(amount))
    cargo = amt if direction == "outflow" else Z
    abono = amt if direction == "inflow" else Z
    return bank_item(StatementLine(
        page_no=1, line_no=line, oper_date=date(2024, 6, day), liq_date=None, code=None,
        description=desc, reference=None, cargo=cargo, abono=abono, running_balance=None), f"b{line}")


def cfg(**kw):
    base = dict(date_window_days=3, amount_tolerance=Decimal("0.00"),
                description_min_similarity=0.55, min_score_margin=0.15)
    base.update(kw)
    return MatchDefaults(**base)


def test_pass1_exact():
    out = match_account([L(1, 10, 500)], [B(1, 10, 500)], cfg())
    assert len(out.matches) == 1 and out.matches[0].pass_no == 1
    assert not out.leftover_ledger and not out.leftover_bank


def test_pass2_exact_dated():
    out = match_account([L(1, 10, 500)], [B(1, 12, 500)], cfg())
    assert len(out.matches) == 1 and out.matches[0].pass_no == 2


def test_pass3_poliza_group():
    ledgers = [L(1, 10, 300, poliza="135"), L(2, 10, 200, poliza="135")]
    out = match_account(ledgers, [B(1, 10, 500)], cfg())
    m = [m for m in out.matches if m.pass_no == 3]
    assert len(m) == 1 and len(m[0].ledger) == 2


def test_pass4_subset_unique():
    ledgers = [L(1, 10, 300, poliza="1"), L(2, 10, 250, poliza="2")]
    out = match_account(ledgers, [B(1, 10, 550)], cfg())
    assert any(m.pass_no == 4 for m in out.matches)


def test_pass4_subset_rejects_non_unique():
    # two distinct subsets sum to 500 ({100,400},{200,300}); no single item is 500, so passes 1-3
    # cannot consume them => subset-sum must reject the ambiguity, all 4 stay leftover (§11.2)
    ledgers = [L(1, 10, 100, poliza="1"), L(2, 10, 400, poliza="2"),
               L(3, 10, 200, poliza="3"), L(4, 10, 300, poliza="4")]
    out = match_account(ledgers, [B(1, 10, 500)], cfg())
    assert not any(m.pass_no == 4 for m in out.matches)
    assert len(out.leftover_ledger) == 4


def test_pass5_fuzzy_unique():
    # amounts differ within tolerance so passes 1-4 (exact) cannot fire; fuzzy resolves on
    # description similarity + date proximity
    c = cfg(amount_tolerance=Decimal("3.00"))
    out = match_account([L(1, 10, 500, concepto="ACME CORP SA")],
                        [B(1, 11, 498, desc="SPEI ACME CORP SA DE CV")], c)
    assert any(m.pass_no == 5 for m in out.matches)


def test_coincidental_amount_collision_flagged():
    # same date+amount, two bank candidates => not an exact match; ambiguous, flagged (§11.2)
    out = match_account([L(1, 10, 468750, direction="outflow", concepto="ARTECK")],
                        [B(1, 10, 468750, direction="outflow", desc="CONSTRUBASCO"),
                         B(2, 10, 468750, direction="outflow", desc="OTHER PARTY")], cfg())
    assert out.flagged
    assert not any(m.pass_no in (1, 2) for m in out.matches)


def test_determinism():
    def fresh():
        return match_account([L(1, 10, 500), L(2, 11, 500)],
                             [B(1, 10, 500), B(2, 12, 500)], cfg())
    key = lambda o: sorted((m.pass_no, str(m.ledger_amount), tuple(w.id for w in m.ledger))
                           for m in o.matches)
    assert key(fresh()) == key(fresh())
