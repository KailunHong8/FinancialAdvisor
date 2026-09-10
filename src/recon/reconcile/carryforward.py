"""Carry-forward across periods (§12.4) and invariant I8.

`reconciling_items` has PRIMARY KEY (id) and item.id excludes period (§11.1), so each item is a
SINGLE row that migrates forward: a deposit in transit carried from June into July keeps its id,
and its row's `period` advances while `periods_open` increments. That is what lets a re-run
auto-resolve a cleared item instead of re-reporting it as new.
"""
from __future__ import annotations

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
                          current_ids: set[str]) -> tuple[list[dict], InvariantResult]:
    """Run AFTER the current period's items are persisted (their rows already advanced to
    `period`). Any prior-outstanding row still sitting at prev_period was not re-derived this
    period, so it cleared -> mark resolved auto_carryforward. Then detect stale items and I8."""
    prev = prev_period(period)
    anomalies: list[dict] = []

    prior_open = conn.execute(
        "SELECT id FROM reconciling_items WHERE entity=? AND period=? AND status='outstanding'",
        (entity, prev)).fetchall()
    for row in prior_open:
        if row["id"] not in current_ids:
            conn.execute(
                "UPDATE reconciling_items SET status='resolved', resolved_in_period=?, "
                "resolved_by='auto_carryforward' WHERE id=? AND period=?",
                (period, row["id"], prev))

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
