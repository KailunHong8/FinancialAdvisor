from decimal import Decimal

from recon.parsers.bank.bbva import BBVAParser
from recon.parsers.bank.base import BankParseError
from recon.reconcile.invariants import check_statement
from recon.money import ZERO


def _parse(config, data):
    return BBVAParser(config.bank("bbva")).parse(data, "stmt.pdf", "sha")[0]


def test_sniff_accepts_bbva(config, statement_bytes):
    assert BBVAParser(config.bank("bbva")).sniff(statement_bytes) is True


def test_sniff_rejects_non_bbva(config, statement_bytes):
    # same PDF, but with markers that never appear => sniff must decline
    p = BBVAParser(config.bank("bbva"))
    p.cfg.sniff_any = ["HSBC", "SANTANDER ESTADO"]
    assert p.sniff(statement_bytes) is False


def test_header_and_totals(config, statement_bytes):
    s = _parse(config, statement_bytes)
    assert s.bank_account == "0114091108"
    assert s.currency == "MXN"
    assert s.opening_balance == Decimal("98971.26")
    assert s.closing_balance == Decimal("346100.50")
    assert s.total_abonos == Decimal("3394770.89")
    assert s.total_cargos == Decimal("3147641.65")


def test_line_count_and_tie_out(config, statement_bytes):
    s = _parse(config, statement_bytes)
    assert len(s.lines) == 213
    assert sum(1 for l in s.lines if l.abono > 0) == 146
    assert sum(1 for l in s.lines if l.cargo > 0) == 67
    assert sum((l.abono for l in s.lines), ZERO) == s.total_abonos
    assert sum((l.cargo for l in s.lines), ZERO) == s.total_cargos


def test_i3_passes(config, statement_bytes):
    s = _parse(config, statement_bytes)
    for res in check_statement(s):
        assert res.passed, res.detail


def test_no_line_has_both_cargo_and_abono(config, statement_bytes):
    s = _parse(config, statement_bytes)
    assert not [l for l in s.lines if l.cargo > 0 and l.abono > 0]


def test_wrapped_description_captures_counterparty(config, statement_bytes):
    s = _parse(config, statement_bytes)
    # a SPEI line should carry the counterparty name from continuation rows
    spei = [l for l in s.lines if "SPEI" in l.description and l.reference]
    assert spei
