"""SQLite persistence — DDL (§7) plus thin repository helpers. No ORM by design (§5)."""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

DDL = """
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS runs (
  run_id            TEXT PRIMARY KEY,
  started_at        TEXT NOT NULL,
  entity            TEXT NOT NULL,
  period            TEXT NOT NULL,
  ledger_file       TEXT NOT NULL,
  ledger_sha256     TEXT NOT NULL,
  code_version      TEXT NOT NULL,
  config_sha256     TEXT NOT NULL,
  status            TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ledger_transactions (
  id                TEXT PRIMARY KEY,
  run_id            TEXT NOT NULL REFERENCES runs(run_id),
  entity            TEXT NOT NULL,
  period            TEXT NOT NULL,
  ledger_account    TEXT NOT NULL,
  sheet             TEXT NOT NULL,
  row_no            INTEGER NOT NULL,
  txn_date          TEXT NOT NULL,
  tipo              TEXT,
  poliza            TEXT,
  concepto          TEXT,
  referencia        TEXT,
  cargo             TEXT NOT NULL,
  abono             TEXT NOT NULL,
  saldo             TEXT,
  direction         TEXT NOT NULL,
  amount            TEXT NOT NULL,
  UNIQUE (entity, period, ledger_account, sheet, row_no)
);

CREATE TABLE IF NOT EXISTS bank_transactions (
  id                TEXT PRIMARY KEY,
  run_id            TEXT NOT NULL REFERENCES runs(run_id),
  entity            TEXT NOT NULL,
  period            TEXT NOT NULL,
  bank_name         TEXT NOT NULL,
  bank_account      TEXT NOT NULL,
  statement_file    TEXT NOT NULL,
  statement_sha256  TEXT NOT NULL,
  page_no           INTEGER NOT NULL,
  line_no           INTEGER NOT NULL,
  oper_date         TEXT NOT NULL,
  liq_date          TEXT,
  code              TEXT,
  description       TEXT,
  reference         TEXT,
  cargo             TEXT NOT NULL,
  abono             TEXT NOT NULL,
  running_balance   TEXT,
  direction         TEXT NOT NULL,
  amount            TEXT NOT NULL,
  UNIQUE (entity, period, bank_account, statement_sha256, line_no)
);

CREATE TABLE IF NOT EXISTS matches (
  match_id          TEXT PRIMARY KEY,
  run_id            TEXT NOT NULL REFERENCES runs(run_id),
  entity            TEXT NOT NULL,
  period            TEXT NOT NULL,
  ledger_account    TEXT NOT NULL,
  direction         TEXT NOT NULL,
  pass_no           INTEGER NOT NULL,
  match_method      TEXT NOT NULL,
  match_confidence  TEXT,
  ledger_amount     TEXT NOT NULL,
  bank_amount       TEXT NOT NULL,
  amount_delta      TEXT NOT NULL,
  date_delta_days   INTEGER NOT NULL,
  rule_note         TEXT NOT NULL,
  evidence_json     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS match_members (
  match_id          TEXT NOT NULL REFERENCES matches(match_id),
  side              TEXT NOT NULL,
  txn_id            TEXT NOT NULL,
  PRIMARY KEY (match_id, side, txn_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_member_exclusive ON match_members (side, txn_id);

CREATE TABLE IF NOT EXISTS reconciling_items (
  id                  TEXT PRIMARY KEY,
  run_id              TEXT NOT NULL REFERENCES runs(run_id),
  entity              TEXT NOT NULL,
  ledger_account      TEXT NOT NULL,
  bank_account        TEXT,
  bank_name           TEXT,
  period              TEXT NOT NULL,
  side                TEXT NOT NULL,
  direction           TEXT NOT NULL,
  category            TEXT NOT NULL,
  amount              TEXT NOT NULL,
  txn_date            TEXT,
  description         TEXT,
  source_ref          TEXT NOT NULL,
  match_method        TEXT,
  match_confidence    TEXT,
  status              TEXT NOT NULL,
  first_seen_period   TEXT NOT NULL,
  periods_open        INTEGER NOT NULL DEFAULT 1,
  resolved_in_period  TEXT,
  resolution          TEXT,
  resolved_by         TEXT,
  evidence_json       TEXT NOT NULL,
  note                TEXT
);
CREATE INDEX IF NOT EXISTS ix_items_acct_period ON reconciling_items (ledger_account, period, status);

CREATE TABLE IF NOT EXISTS account_period (
  entity              TEXT NOT NULL,
  ledger_account      TEXT NOT NULL,
  period              TEXT NOT NULL,
  run_id              TEXT NOT NULL REFERENCES runs(run_id),
  ledger_open         TEXT NOT NULL,
  ledger_cargos       TEXT NOT NULL,
  ledger_abonos       TEXT NOT NULL,
  ledger_close        TEXT NOT NULL,
  bank_open           TEXT,
  bank_abonos         TEXT,
  bank_cargos         TEXT,
  bank_close          TEXT,
  opening_variance    TEXT,
  outstanding_inflow  TEXT NOT NULL,
  outstanding_outflow TEXT NOT NULL,
  unbooked_inflow     TEXT NOT NULL,
  unbooked_outflow    TEXT NOT NULL,
  adjusted_ledger     TEXT NOT NULL,
  adjusted_bank       TEXT NOT NULL,
  closure_residual    TEXT NOT NULL,
  status_note         TEXT NOT NULL,
  PRIMARY KEY (entity, ledger_account, period)
);

CREATE TABLE IF NOT EXISTS anomalies (
  id            TEXT PRIMARY KEY,
  run_id        TEXT NOT NULL REFERENCES runs(run_id),
  period        TEXT NOT NULL,
  ledger_account TEXT,
  kind          TEXT NOT NULL,
  amount        TEXT,
  detail        TEXT NOT NULL,
  docs_needed   TEXT,
  conclusion    TEXT
);

CREATE TABLE IF NOT EXISTS bank_format_registry (
  bank_name             TEXT PRIMARY KEY,
  parser_version        TEXT NOT NULL,
  template_sha256       TEXT NOT NULL,
  template_source       TEXT NOT NULL,
  last_verified_period  TEXT NOT NULL
);
"""

TABLE_NAMES = [
    "runs", "ledger_transactions", "bank_transactions", "matches", "match_members",
    "reconciling_items", "account_period", "anomalies", "bank_format_registry",
]


def connect(db_path: str | Path) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(DDL)
    conn.commit()


# --- ULID-ish run id (monotonic, sortable, no dependency) --------------------
_B32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_ulid() -> str:
    ms = int(time.time() * 1000)
    import os
    rand = int.from_bytes(os.urandom(10), "big")
    n = (ms << 80) | rand
    out = []
    for _ in range(26):
        out.append(_B32[n & 31])
        n >>= 5
    return "".join(reversed(out))


def _insert(conn: sqlite3.Connection, table: str, row: dict) -> None:
    cols = ",".join(row)
    ph = ",".join("?" for _ in row)
    conn.execute(f"INSERT OR REPLACE INTO {table} ({cols}) VALUES ({ph})", tuple(row.values()))


def insert_run(conn, row: dict) -> None:
    _insert(conn, "runs", row)


def insert_ledger_txn(conn, row: dict) -> None:
    _insert(conn, "ledger_transactions", row)


def insert_bank_txn(conn, row: dict) -> None:
    _insert(conn, "bank_transactions", row)


def insert_match(conn, match_row: dict, members: list[dict]) -> None:
    _insert(conn, "matches", match_row)
    for m in members:
        _insert(conn, "match_members", m)


def insert_item(conn, row: dict) -> None:
    _insert(conn, "reconciling_items", row)


def insert_account_period(conn, row: dict) -> None:
    _insert(conn, "account_period", row)


def insert_anomaly(conn, row: dict) -> None:
    _insert(conn, "anomalies", row)


def upsert_registry(conn, row: dict) -> None:
    _insert(conn, "bank_format_registry", row)


def clear_run(conn: sqlite3.Connection, entity: str, period: str) -> None:
    """Remove derived rows for a period so a re-run is idempotent (§14). Resolutions on
    reconciling_items are re-attached by item_id afterwards, not wiped blindly."""
    conn.execute(
        "DELETE FROM match_members WHERE match_id IN "
        "(SELECT match_id FROM matches WHERE entity=? AND period=?)", (entity, period))
    conn.execute("DELETE FROM matches WHERE entity=? AND period=?", (entity, period))
    conn.execute("DELETE FROM ledger_transactions WHERE entity=? AND period=?", (entity, period))
    conn.execute("DELETE FROM bank_transactions WHERE entity=? AND period=?", (entity, period))
    conn.execute("DELETE FROM account_period WHERE entity=? AND period=?", (entity, period))
    conn.execute("DELETE FROM anomalies WHERE period=?", (period,))
    # Current-period residue is regenerated below. Leaving it here would let an item that is
    # matched by a later rerun continue to appear in Tab 2.
    conn.execute("DELETE FROM reconciling_items WHERE entity=? AND period=? AND status!='resolved'",
           (entity, period))
    conn.commit()
