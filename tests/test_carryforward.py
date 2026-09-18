"""Carry-forward (§12.4) and I8 (§13). reconciling_items is one migrating row per item id."""
from recon.reconcile.carryforward import item_lifecycle, finalize_carryforward, prev_period


def _seed_run(conn, period):
    conn.execute("INSERT OR IGNORE INTO runs (run_id,started_at,entity,period,ledger_file,"
                 "ledger_sha256,code_version,config_sha256,status) VALUES (?,?,?,?,?,?,?,?,?)",
                 ("R" + period, "t", "E", period, "l", "s", "1.0", "c", "ok"))


def _put(conn, period, iid, status="outstanding", periods_open=1, first="2024-06",
         resolution=None, resolved_by=None, side="ledger_outstanding",
         amount="500.00", txn_date=None):
    _seed_run(conn, period)
    conn.execute(
        "INSERT OR REPLACE INTO reconciling_items (id,run_id,entity,ledger_account,period,side,"
        "direction,category,amount,txn_date,source_ref,status,first_seen_period,periods_open,"
        "resolution,resolved_by,evidence_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (iid, "R" + period, "E", "A", period, side, "inflow",
         "deposit_in_transit", amount, txn_date, f"source {iid}", status, first, periods_open,
         resolution, resolved_by, "{}"))
    conn.commit()


def test_prev_period():
    assert prev_period("2024-07") == "2024-06"
    assert prev_period("2024-01") == "2023-12"


def test_lifecycle_new_item(memdb):
    lc = item_lifecycle(memdb, "E", "2024-06", "NEW", "outstanding", None)
    assert lc["periods_open"] == 1 and lc["first_seen_period"] == "2024-06"


def test_lifecycle_carried_increments(memdb):
    _put(memdb, "2024-06", "ITEM2", periods_open=1, first="2024-06")
    lc = item_lifecycle(memdb, "E", "2024-07", "ITEM2", "outstanding", None)
    assert lc["periods_open"] == 2 and lc["first_seen_period"] == "2024-06"


def test_lifecycle_preserves_user_resolution(memdb):
    _put(memdb, "2024-06", "ITEMU", status="resolved", resolution="Cleared", resolved_by="user")
    lc = item_lifecycle(memdb, "E", "2024-07", "ITEMU", "outstanding", None)
    assert lc["status"] == "resolved" and lc["resolved_by"] == "user" and lc["resolution"] == "Cleared"


def test_unmatched_item_migrates_forward_instead_of_resolving_by_absence(memdb):
    _put(memdb, "2024-06", "ITEM1", status="outstanding")
    _seed_run(memdb, "2024-07")
    anoms, i8 = finalize_carryforward(memdb, "E", "2024-07", "R2024-07")
    row = memdb.execute(
        "SELECT status, period, periods_open FROM reconciling_items WHERE id='ITEM1'").fetchone()
    assert row["status"] == "outstanding" and row["period"] == "2024-07"
    assert row["periods_open"] == 2
    assert i8.passed and not anoms


def test_unique_opposite_side_item_records_cross_period_clearance(memdb):
    _put(memdb, "2024-06", "PRIOR", side="bank_unbooked", txn_date="2024-06-30")
    _put(memdb, "2024-07", "CURRENT", side="ledger_outstanding",
         txn_date="2024-07-03", first="2024-07")
    _, i8 = finalize_carryforward(memdb, "E", "2024-07", "R2024-07")
    prior = memdb.execute("SELECT * FROM reconciling_items WHERE id='PRIOR'").fetchone()
    current = memdb.execute("SELECT * FROM reconciling_items WHERE id='CURRENT'").fetchone()
    assert prior["status"] == "resolved" and prior["resolved_by"] == "cross_period_match"
    assert prior["resolved_in_period"] == "2024-07"
    assert current["status"] == "cleared_prior_period"
    assert '"counterpart_item_id": "CURRENT"' in prior["evidence_json"]
    assert '"counterpart_item_id": "PRIOR"' in current["evidence_json"]
    assert i8.passed


def test_ambiguous_cross_period_amount_stays_open(memdb):
    _put(memdb, "2024-06", "PRIOR", side="bank_unbooked", txn_date="2024-06-30")
    _put(memdb, "2024-07", "CURRENT1", side="ledger_outstanding",
         txn_date="2024-07-03", first="2024-07")
    _put(memdb, "2024-07", "CURRENT2", side="ledger_outstanding",
         txn_date="2024-07-04", first="2024-07")
    finalize_carryforward(memdb, "E", "2024-07", "R2024-07")
    prior = memdb.execute("SELECT status, period FROM reconciling_items WHERE id='PRIOR'").fetchone()
    assert prior["status"] == "outstanding" and prior["period"] == "2024-07"


    def test_cross_period_clearance_is_restored_on_rerun(memdb):
        _put(memdb, "2024-06", "PRIOR", side="bank_unbooked", txn_date="2024-06-30")
        _put(memdb, "2024-07", "CURRENT", side="ledger_outstanding",
            txn_date="2024-07-03", first="2024-07")
        finalize_carryforward(memdb, "E", "2024-07", "R2024-07")
        memdb.execute("DELETE FROM reconciling_items WHERE id='CURRENT'")
        _put(memdb, "2024-07", "CURRENT", side="ledger_outstanding",
            txn_date="2024-07-03", first="2024-07")
        finalize_carryforward(memdb, "E", "2024-07", "R2024-07")
        current = memdb.execute("SELECT status FROM reconciling_items WHERE id='CURRENT'").fetchone()
        assert current["status"] == "cleared_prior_period"


def test_stale_after_three_periods(memdb):
    # simulate the current period's row already advanced to periods_open=3
    _put(memdb, "2024-08", "ITEM3", status="outstanding", periods_open=3, first="2024-05")
    anoms, i8 = finalize_carryforward(memdb, "E", "2024-08", "R2024-08")
    assert any(a["kind"] == "stale_outstanding_item" for a in anoms)
    assert i8.passed
