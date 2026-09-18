"""Pipeline orchestration (§4). Ties parse -> match -> derive -> invariants -> persist.

A gate-invariant failure aborts with a non-zero exit and writes no workbook (§13). Exit codes
(§16): 0 ok, 2 parse failure, 3 invariant gate failure, 4 config/inventory error.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import __version__
from .config import Config
from .money import q, ZERO
from .models import bank_txn_id
from .parsers.ledger_contpaq import parse_ledger_file
from .parsers.bank.registry import build_registry, sniff_parser
from .parsers.bank.base import BankParseError
from .parsers.bank.diagnostics import write_bundle
from .matching.candidates import ledger_item, bank_item
from .matching.engine import match_account, AccountMatchOutcome
from .reconcile.derive import derive_account, DerivedAccountPeriod, DerivedItem
from .reconcile import invariants as inv
from .reconcile.anomalies import (detect_account_anomalies, detect_cross_account,
                                 detect_pos_batch_evidence)
from .reconcile.carryforward import item_lifecycle, finalize_carryforward
from . import store
from .sources.local import LocalFolderSource

log = logging.getLogger("recon")

# grand-total expectations from §17.1 (validation aid for the June sheet)
GRAND_TOTALS = {"2024-06": {"cargos": q("9981179.96"), "abonos": q("8824633.83"),
                            "close": q("12320085.30")}}


class RunError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class AccountView:
    account: object
    block: object
    stmt: object
    outcome: AccountMatchOutcome
    ap: DerivedAccountPeriod
    items: list[DerivedItem]
    anomalies: list[dict] = field(default_factory=list)


@dataclass
class RunResult:
    run_id: str
    entity: str
    period: str
    views: list[AccountView]
    out_of_scope: list[object]
    invariants: list[inv.InvariantResult]
    anomalies: list[dict]
    ledger_file: str
    ledger_sha256: str
    config_sha256: str
    status: str


def _parse_statements(config: Config, source: LocalFolderSource, period: str,
                      registry, diag_dir: Path):
    """Return {bank_account: BankStatement}. Statements identified by content (§8.2)."""
    rel = f"statements/{period.replace('-', '-')}"  # statements/2024-06
    files = source.list(rel, "*.pdf")
    by_account = {}
    for f in files:
        data = source.read_bytes(f)
        parser = sniff_parser(data, registry)
        if parser is None:
            bundle = write_bundle(diag_dir / f.name, data, config.bank("bbva"),
                                  f"No registered parser recognised {f.name}.")
            raise RunError(2, f"unrecognized statement layout: {f.name}; bundle at {bundle}")
        try:
            stmts = parser.parse(data, f.rel_path, f.sha256)
        except BankParseError as e:
            bundle = write_bundle(diag_dir / f.name, data, config.bank(parser.bank_name),
                                  f"{parser.bank_name} parser failed on {f.name}: {e}")
            raise RunError(2, f"statement parse failed: {f.name}: {e}; bundle at {bundle}") from e
        for s in stmts:
            acct = config.entity.by_bank_account(s.bank_account)
            if acct is None:
                raise RunError(2, f"statement account {s.bank_account} not in inventory ({f.name})")
            by_account[s.bank_account] = s
    return by_account


def run_period(conn, config: Config, root: str, period: str,
               diag_root: Path, *, write_registry: bool = True,
               read_back_path: str | None = None) -> RunResult:
    entity = config.entity.entity
    source = LocalFolderSource(root)
    registry = build_registry(config)

    # Read hand-entered resolutions out of the existing workbook BEFORE we clear/re-derive, so
    # item_lifecycle preserves them and carry-forward/I8 see them (§14). Without this a scheduled
    # run would regenerate the workbook and silently drop resolutions typed since the last ingest.
    if read_back_path and Path(read_back_path).exists():
        from .exceptions_io import ingest_review
        summary = ingest_review(conn, read_back_path)
        if summary["updated"]:
            log.info("read back %d hand-entered resolution(s) from %s",
                     summary["updated"], read_back_path)

    ledger_files = source.list("ledger", "*.xlsx")
    if not ledger_files:
        raise RunError(2, f"no ledger .xlsx found under {root}/ledger")
    ledger_f = ledger_files[0]
    ledger_path = str(Path(root) / ledger_f.rel_path)

    run_id = store.new_ulid()
    store.clear_run(conn, entity, period)

    # ---- parse ledger -----------------------------------------------------
    blocks = parse_ledger_file(ledger_path, entity, period, config.entity)
    invariants: list[inv.InvariantResult] = []
    anomalies: list[dict] = []

    inventory = {a.ledger_account for a in config.entity.accounts}
    rollup = set(config.entity.ledger_rollup_accounts)
    for b in blocks:
        if b.ledger_account in rollup:
            continue
        if b.ledger_account not in inventory:
            raise RunError(4, f"unknown account {b.ledger_account} — add to inventory (§6.1)")

    for b in blocks:
        if b.ledger_account in rollup:
            continue
        invariants += inv.check_ledger_block(b)

    # R2 grand total
    close_sum = sum((b.closing for b in blocks if b.ledger_account not in rollup), ZERO)
    cargos_sum = sum((b.total_cargos for b in blocks if b.ledger_account not in rollup), ZERO)
    abonos_sum = sum((b.total_abonos for b in blocks if b.ledger_account not in rollup), ZERO)
    invariants += inv.check_grand_total(close_sum, cargos_sum, abonos_sum, GRAND_TOTALS.get(period))

    # ---- parse statements -------------------------------------------------
    stmts = _parse_statements(config, source, period, registry, diag_root / period)
    for s in stmts.values():
        invariants += inv.check_statement(s)

    # gate on pre-matching invariants before touching the matcher (§13)
    _gate(invariants)

    block_by_acct = {b.ledger_account: b for b in blocks}
    views: list[AccountView] = []
    all_leftover_items: list[tuple] = []

    for acct in config.entity.in_scope_accounts:
        block = block_by_acct.get(acct.ledger_account)
        if block is None:
            continue
        stmt = stmts.get(acct.bank_account)
        li = [ledger_item(t) for t in block.transactions]
        bi = []
        if stmt is not None:
            for l in stmt.lines:
                bid = bank_txn_id(entity, stmt.bank_account, stmt.source_sha256, l.line_no)
                bi.append(bank_item(l, bid))
        mcfg = config.matching.for_account(acct.ledger_account)
        allow_fuzzy = acct.currency == config.entity.currency  # USD: passes 1-5 only (§12.5)
        bank_cfg = config.bank(acct.bank) if acct.bank else None
        if stmt is not None:
            outcome = match_account(li, bi, mcfg, allow_fuzzy=allow_fuzzy,
                                    pos_terminal=acct.pos_terminal, bank_cfg=bank_cfg)
        else:
            # No statement for this in-scope account: report balances and a note, but do not
            # dump every ledger row as an "outstanding" item — that is not a reconciling finding.
            outcome = AccountMatchOutcome()
        ap, items = derive_account(entity, period, acct, block, stmt, outcome, bank_cfg)
        if stmt is not None:
            invariants += inv.check_matching(acct.ledger_account, outcome, ap, len(li), len(bi))
        acct_anoms = detect_account_anomalies(entity, period, acct, block, stmt, ap, config.entity)
        acct_anoms += detect_pos_batch_evidence(period, acct, outcome)
        anomalies += acct_anoms
        for it in items:
            all_leftover_items.append((acct.ledger_account, it))
        views.append(AccountView(acct, block, stmt, outcome, ap, items, acct_anoms))

    anomalies += detect_cross_account(entity, period, all_leftover_items)

    # gate on post-matching invariants
    _gate(invariants)

    # ---- persist ----------------------------------------------------------
    status = "ok"
    _persist(conn, run_id, entity, period, ledger_f, config, ledger_path, status,
             views, anomalies, stmts)

    # carry-forward + I8 (runs after items are persisted with their advanced periods_open)
    cf_anoms, i8 = finalize_carryforward(conn, entity, period, run_id)
    invariants.append(i8)
    for a in cf_anoms:
        store.insert_anomaly(conn, {
            "id": a["id"], "run_id": run_id, "period": a["period"],
            "ledger_account": a.get("ledger_account"), "kind": a["kind"],
            "amount": a.get("amount"), "detail": a["detail"],
            "docs_needed": a.get("docs_needed"), "conclusion": a.get("conclusion")})
        anomalies.append(a)
    conn.commit()

    if not i8.passed:
        conn.execute("UPDATE runs SET status='failed_invariant' WHERE run_id=?", (run_id,))
        conn.commit()
        raise RunError(3, "I8 carry-forward invariant failed")

    return RunResult(run_id, entity, period, views, list(config.entity.accounts),
                     invariants, anomalies, ledger_f.rel_path, ledger_f.sha256,
                     config.config_sha256, status)


def _gate(invariants):
    failed = [r for r in invariants if r.gate and not r.passed]
    if failed:
        msg = "; ".join(f"{r.id}@{r.ledger_account or '-'}: {r.detail}" for r in failed)
        log.error("GATE FAILURE: %s", msg)
        raise RunError(3, f"invariant gate failure: {msg}")


def _persist(conn, run_id, entity, period, ledger_f, config, ledger_path, status,
             views, anomalies, stmts):
    store.insert_run(conn, {
        "run_id": run_id, "started_at": datetime.now().isoformat(timespec="seconds"),
        "entity": entity, "period": period, "ledger_file": ledger_f.rel_path,
        "ledger_sha256": ledger_f.sha256, "code_version": __version__,
        "config_sha256": config.config_sha256, "status": status})

    for v in views:
        block = v.block
        for t in block.transactions:
            store.insert_ledger_txn(conn, {
                "id": t.id, "run_id": run_id, "entity": entity, "period": period,
                "ledger_account": t.ledger_account, "sheet": t.sheet, "row_no": t.row_no,
                "txn_date": t.txn_date.isoformat(), "tipo": t.tipo, "poliza": t.poliza,
                "concepto": t.concepto, "referencia": t.referencia,
                "cargo": str(t.cargo), "abono": str(t.abono),
                "saldo": (str(t.saldo) if t.saldo is not None else None),
                "direction": t.direction, "amount": str(t.amount)})
        if v.stmt is not None:
            s = v.stmt
            for l in s.lines:
                bid = bank_txn_id(entity, s.bank_account, s.source_sha256, l.line_no)
                store.insert_bank_txn(conn, {
                    "id": bid, "run_id": run_id, "entity": entity, "period": period,
                    "bank_name": s.bank_name, "bank_account": s.bank_account,
                    "statement_file": s.source_file, "statement_sha256": s.source_sha256,
                    "page_no": l.page_no, "line_no": l.line_no,
                    "oper_date": l.oper_date.isoformat(),
                    "liq_date": (l.liq_date.isoformat() if l.liq_date else None),
                    "code": l.code, "description": l.description, "reference": l.reference,
                    "cargo": str(l.cargo), "abono": str(l.abono),
                    "running_balance": (str(l.running_balance) if l.running_balance is not None else None),
                    "direction": l.direction, "amount": str(l.amount)})
        for m in v.outcome.matches:
            mid = store.new_ulid()
            store.insert_match(conn, {
                "match_id": mid, "run_id": run_id, "entity": entity, "period": period,
                "ledger_account": v.account.ledger_account,
                "direction": (m.ledger[0].direction if m.ledger else m.bank[0].direction),
                "pass_no": m.pass_no, "match_method": m.method,
                "match_confidence": (str(m.confidence) if m.confidence is not None else None),
                "ledger_amount": str(m.ledger_amount), "bank_amount": str(m.bank_amount),
                "amount_delta": str(m.amount_delta), "date_delta_days": m.date_delta_days,
                "rule_note": m.rule_note, "evidence_json": json.dumps(m.evidence, ensure_ascii=False)},
                [{"match_id": mid, "side": w.side, "txn_id": w.id} for w in m.ledger + m.bank])
        for it in v.items:
            lc = item_lifecycle(conn, entity, period, it.id, it.status, it.note)
            store.insert_item(conn, {
                "id": it.id, "run_id": run_id, "entity": entity,
                "ledger_account": it.ledger_account, "bank_account": it.bank_account,
                "bank_name": it.bank_name, "period": period, "side": it.side,
                "direction": it.direction, "category": it.category, "amount": str(it.amount),
                "txn_date": it.txn_date, "description": it.description,
                "source_ref": it.source_ref, "match_method": it.match_method,
                "match_confidence": it.match_confidence, "status": lc["status"],
                "first_seen_period": lc["first_seen_period"], "periods_open": lc["periods_open"],
                "resolution": lc["resolution"], "resolved_by": lc["resolved_by"],
                "resolved_in_period": lc["resolved_in_period"],
                "evidence_json": json.dumps(it.evidence, ensure_ascii=False), "note": it.note})
        ap = v.ap
        store.insert_account_period(conn, {
            "entity": entity, "ledger_account": ap.ledger_account, "period": period,
            "run_id": run_id, "ledger_open": str(ap.ledger_open),
            "ledger_cargos": str(ap.ledger_cargos), "ledger_abonos": str(ap.ledger_abonos),
            "ledger_close": str(ap.ledger_close),
            "bank_open": (str(ap.bank_open) if ap.bank_open is not None else None),
            "bank_abonos": (str(ap.bank_abonos) if ap.bank_abonos is not None else None),
            "bank_cargos": (str(ap.bank_cargos) if ap.bank_cargos is not None else None),
            "bank_close": (str(ap.bank_close) if ap.bank_close is not None else None),
            "opening_variance": (str(ap.opening_variance) if ap.opening_variance is not None else None),
            "outstanding_inflow": str(ap.outstanding_inflow),
            "outstanding_outflow": str(ap.outstanding_outflow),
            "unbooked_inflow": str(ap.unbooked_inflow),
            "unbooked_outflow": str(ap.unbooked_outflow),
            "adjusted_ledger": str(ap.adjusted_ledger), "adjusted_bank": str(ap.adjusted_bank),
            "closure_residual": str(ap.closure_residual), "status_note": ap.status_note})

    for a in anomalies:
        store.insert_anomaly(conn, {
            "id": a["id"], "run_id": run_id, "period": a["period"],
            "ledger_account": a.get("ledger_account"), "kind": a["kind"],
            "amount": a.get("amount"), "detail": a["detail"],
            "docs_needed": a.get("docs_needed"), "conclusion": a.get("conclusion")})
    conn.commit()
