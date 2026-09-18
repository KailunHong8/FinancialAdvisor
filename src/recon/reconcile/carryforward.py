"""Carry-forward across periods (§12.4) and invariant I8.

`reconciling_items` has PRIMARY KEY (id) and item.id excludes period (§11.1), so each item is a
SINGLE row that migrates forward: a deposit in transit carried from June into July keeps its id,
and its row's `period` advances while `periods_open` increments. That is what lets a re-run
auto-resolve a cleared item instead of re-reporting it as new.
"""
from __future__ import annotations

from datetime import date
import json
import sqlite3

from .invariants import InvariantResult


def prev_period(period: str) -> str:
    year, month = (int(x) for x in period.split("-"))
    month -= 1
    if month == 0:
        month, year = 12, year - 1
    return f"{year:04d}-{month:02d}"


def item_lifecycle(conn: sqlite3.Connection, entity: str, period: str, iid: str,
                   status: str, note: str | None) -> dict:
    """Decide periods_open / first_seen / resolution for an item about to be (re)persisted.

    - brand new id                         -> periods_open=1, first_seen=period
    - same id, same period (a re-run)      -> keep periods_open (idempotent)
    - same id, earlier period, outstanding -> carried: periods_open+1, first_seen preserved
    - a stored user resolution             -> re-attached and preserved (§14)
    """
    old = conn.execute(
        "SELECT period, periods_open, first_seen_period, status, resolution, resolved_by, "
        "resolved_in_period FROM reconciling_items WHERE id=? AND entity=?", (iid, entity)).fetchone()
    if old is None:
        return {"periods_open": 1, "first_seen_period": period, "status": status,
                "resolution": note, "resolved_by": None, "resolved_in_period": None}
    if old["resolved_by"] == "user" and old["resolution"]:
        return {"periods_open": old["periods_open"], "first_seen_period": old["first_seen_period"],
                "status": "resolved", "resolution": old["resolution"], "resolved_by": "user",
                "resolved_in_period": old["resolved_in_period"] or period}
    if old["period"] == period:
        return {"periods_open": old["periods_open"], "first_seen_period": old["first_seen_period"],
                "status": status, "resolution": note, "resolved_by": None, "resolved_in_period": None}
    if old["status"] == "outstanding":
        return {"periods_open": old["periods_open"] + 1,
                "first_seen_period": old["first_seen_period"], "status": status,
                "resolution": note, "resolved_by": None, "resolved_in_period": None}
    return {"periods_open": 1, "first_seen_period": period, "status": status,
            "resolution": note, "resolved_by": None, "resolved_in_period": None}


def finalize_carryforward(conn: sqlite3.Connection, entity: str, period: str,
                          run_id: str) -> tuple[list[dict], InvariantResult]:
    """Run AFTER the current period's items are persisted (their rows already advanced to
    `period`). Resolve only uniquely evidenced opposite-side clearances; migrate every other
    prior item into the current period so it remains visible and ages normally."""
    prev = prev_period(period)
    anomalies: list[dict] = []

    prior_open = conn.execute(
        "SELECT * FROM reconciling_items WHERE entity=? AND period=? "
        "AND (status='outstanding' OR (resolved_by='cross_period_match' "
        "AND resolved_in_period=?)) "
        "AND side IN ('ledger_outstanding','bank_unbooked')",
        (entity, prev, period)).fetchall()
    current_open = conn.execute(
        "SELECT * FROM reconciling_items WHERE entity=? AND period=? AND status='outstanding' "
        "AND side IN ('ledger_outstanding','bank_unbooked')",
        (entity, period)).fetchall()

    candidates = {row["id"]: _clearance_candidates(row, current_open) for row in prior_open}
    claimed_by: dict[str, list[str]] = {}
    for prior_id, rows in candidates.items():
        for row in rows:
            claimed_by.setdefault(row["id"], []).append(prior_id)

    resolved_prior: set[str] = set()
    for prior in prior_open:
        matches = candidates[prior["id"]]
        if len(matches) != 1 or len(claimed_by[matches[0]["id"]]) != 1:
            continue
        current = matches[0]
        _record_cross_period_clearance(conn, prior, current, period)
        resolved_prior.add(prior["id"])

    for row in prior_open:
        if row["id"] in resolved_prior or row["status"] != "outstanding":
            continue
        conn.execute(
            "UPDATE reconciling_items SET run_id=?, period=?, periods_open=periods_open+1 "
            "WHERE id=? AND period=?",
            (run_id, period, row["id"], prev))

    for row in conn.execute(
            "SELECT id, ledger_account, amount, periods_open, first_seen_period "
            "FROM reconciling_items WHERE entity=? AND period=? AND status='outstanding' "
            "AND periods_open>=3", (entity, period)).fetchall():
        anomalies.append({
            "id": f"stale-{row['id']}", "period": period, "ledger_account": row["ledger_account"],
            "kind": "stale_outstanding_item", "amount": row["amount"],
            "detail": f"item open {row['periods_open']} periods (since {row['first_seen_period']})",
            "docs_needed": "Escalate — outstanding 3+ periods.", "conclusion": None})
    conn.commit()

    leftover = conn.execute(
        "SELECT COUNT(*) c FROM reconciling_items WHERE entity=? AND period=? AND status='outstanding'",
        (entity, prev)).fetchone()["c"]
    i8 = InvariantResult("I8", leftover == 0, True,
                         f"prior-outstanding items unaccounted after carry-forward: {leftover}",
                         None)
    return anomalies, i8


def _clearance_candidates(prior, current_rows) -> list:
    opposite = {"ledger_outstanding": "bank_unbooked",
                "bank_unbooked": "ledger_outstanding"}[prior["side"]]
    if not prior["txn_date"]:
        return []
    prior_date = date.fromisoformat(prior["txn_date"])
    found = []
    for current in current_rows:
        if (current["ledger_account"] != prior["ledger_account"]
                or current["side"] != opposite
                or current["direction"] != prior["direction"]
                or current["amount"] != prior["amount"]
                or not current["txn_date"]):
            continue
        current_date = date.fromisoformat(current["txn_date"])
        if 0 <= (current_date - prior_date).days <= 45:
            found.append(current)
    return found


def _record_cross_period_clearance(conn, prior, current, period: str) -> None:
    evidence = {"prior_item_id": prior["id"], "prior_period": prior["period"],
                "current_item_id": current["id"], "current_period": period,
                "amount": current["amount"], "direction": current["direction"],
                "rule": "unique exact amount, opposite side, within 45 days"}

    def merged(row, counterpart):
        try:
            existing = json.loads(row["evidence_json"] or "{}")
        except json.JSONDecodeError:
            existing = {}
        return json.dumps({**existing, "cross_period_clearance": {**evidence,
                          "counterpart_item_id": counterpart["id"]}}, ensure_ascii=False)

    resolution = f"Cross-period clearance against {current['id']} in {period}"
    conn.execute(
        "UPDATE reconciling_items SET status='resolved', resolved_in_period=?, "
        "resolved_by='cross_period_match', resolution=?, evidence_json=? WHERE id=?",
        (period, resolution, merged(prior, current), prior["id"]))
    conn.execute(
        "UPDATE reconciling_items SET status='cleared_prior_period', resolved_in_period=?, "
        "resolved_by='cross_period_match', resolution=?, evidence_json=? WHERE id=?",
        (period, f"Clears prior item {prior['id']} from {prior['period']}",
         merged(current, prior), current["id"]))
