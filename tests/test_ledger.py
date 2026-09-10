from decimal import Decimal

from recon.parsers.ledger_contpaq import parse_ledger_file, period_to_sheet
from recon.reconcile.invariants import check_ledger_block

EXPECT_JUNE = {
    "1112-01-001-00": ("2380012.58", "3568812.60", "3158224.06", "2790601.12", 215),
    "1112-01-004-00": ("-60457.48", "157974.59", "303150.08", "-205632.97", 20),
    "1112-01-008-00": ("1064.82", "0.00", "0.00", "1064.82", 0),
    "1112-01-013-00": ("1965428.77", "392891.34", "36228.01", "2322092.10", 118),
    "1112-02-001-00": ("23697.18", "0.00", "5367.20", "18329.98", 2),
}


def test_period_to_sheet():
    assert period_to_sheet("2024-06") == "06-24"
    assert period_to_sheet("2024-01") == "01-24"


def test_june_table_exact(ledger_path, config):
    blocks = parse_ledger_file(ledger_path, config.entity.entity, "2024-06", config.entity)
    by = {b.ledger_account: b for b in blocks}
    for acct, (o, c, a, cl, n) in EXPECT_JUNE.items():
        b = by[acct]
        assert b.opening == Decimal(o), acct
        assert b.total_cargos == Decimal(c), acct
        assert b.total_abonos == Decimal(a), acct
        assert b.closing == Decimal(cl), acct
        assert len(b.transactions) == n, acct


def test_i1_holds_all_accounts(ledger_path, config):
    blocks = parse_ledger_file(ledger_path, config.entity.entity, "2024-06", config.entity)
    for b in blocks:
        for res in check_ledger_block(b):
            assert res.passed, f"{res.id}@{b.ledger_account}: {res.detail}"


def test_grand_total(ledger_path, config):
    blocks = parse_ledger_file(ledger_path, config.entity.entity, "2024-06", config.entity)
    from recon.money import ZERO
    assert sum((b.total_cargos for b in blocks), ZERO) == Decimal("9981179.96")
    assert sum((b.total_abonos for b in blocks), ZERO) == Decimal("8824633.83")
    assert sum((b.closing for b in blocks), ZERO) == Decimal("12320085.30")


def test_spot_rows(ledger_path, config):
    # §17.1 spot assertions pinning row_no semantics
    blocks = parse_ledger_file(ledger_path, config.entity.entity, "2024-06", config.entity)
    txns = {t.row_no: t for b in blocks for t in b.transactions}
    r218 = txns[218]
    assert r218.poliza == "135" and r218.cargo == Decimal("100000.00")
    r215 = txns[215]
    assert r215.abono == Decimal("10582.76") and "La Ferre" in (r215.concepto or "")


def test_layout_variations_jan_to_may(ledger_path, config):
    # Exercises the layout variations (empty A1 in 01-24; missing Total: row in 03-24!016;
    # both-Cargos-and-Abonos row in 05-24; unlabelled summary row in 02-24!017; 16-22 accounts
    # per sheet). The parser must handle every one without crashing, find all blocks, and tie
    # Σcargos to each block's Total: row F. (Historical months also carry small SOURCE-data
    # quirks — a stale Total that omits a 7.24 IVA row, opening breaks — which the I1/I2 gates
    # correctly surface at run time; those are data issues, not parser bugs, so they are not
    # asserted-clean here.)
    from decimal import Decimal
    expect_blocks = {"2024-01": 19, "2024-02": 22, "2024-03": 16, "2024-04": 17, "2024-05": 21}
    for period, n in expect_blocks.items():
        blocks = parse_ledger_file(ledger_path, config.entity.entity, period, config.entity)
        assert len(blocks) == n, f"{period}: {len(blocks)} blocks (expected {n})"
        for b in blocks:
            sum_c = sum((t.cargo for t in b.transactions), Decimal("0"))
            assert abs(sum_c - b.total_cargos) <= Decimal("0.01"), \
                f"{period} Σcargos@{b.ledger_account}: {sum_c} vs {b.total_cargos}"
            # any Σabonos gap must be a tiny source quirk, never a dropped material row
            sum_a = sum((t.abono for t in b.transactions), Decimal("0"))
            assert abs(sum_a - b.total_abonos) < Decimal("10.00"), \
                f"{period} Σabonos@{b.ledger_account}: {sum_a} vs {b.total_abonos}"


def test_i1_surfaces_historical_opening_break(ledger_path, config):
    # I1 (open + Σcargos − Σabonos == close) is a gate. In January the source file's opening for
    # 1112-01-002-00 is 0 but its own Total close (145,482.30) does not reconcile with the period
    # flows — a genuine opening-balance inconsistency the gate is designed to catch (not a parser
    # bug: I2 passes for this account). This asserts the gate fires on real messy history.
    blocks = parse_ledger_file(ledger_path, config.entity.entity, "2024-01", config.entity)
    b002 = next(b for b in blocks if b.ledger_account == "1112-01-002-00")
    results = {r.id: r for r in check_ledger_block(b002)}
    assert results["I2"].passed          # parser read the block correctly
    assert not results["I1"].passed      # but opening vs close does not reconcile
