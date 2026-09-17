"""Derivation (§12): balance-level Tab-1 figures and the reconciling-item taxonomy.

Everything here is derived by the engine from full populations — nothing is copied from the
bookkeeper's embedded schedule.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from ..config import Account, BankConfig
from ..models import BankStatement, LedgerAccountBlock, StatementLine, item_id
from ..money import q, ZERO
from ..matching.engine import AccountMatchOutcome


@dataclass
class DerivedItem:
    id: str
    ledger_account: str
    bank_account: str | None
    bank_name: str | None
    period: str
    side: str            # ledger_outstanding | bank_unbooked | amount_variance | opening_variance
    direction: str
    category: str
    amount: Decimal
    txn_date: str | None
    description: str | None
    source_ref: str
    match_method: str | None
    match_confidence: str | None
    status: str          # outstanding | flagged | matched | resolved
    evidence: dict
    note: str | None = None


@dataclass
class DerivedAccountPeriod:
    entity: str
    ledger_account: str
    period: str
    ledger_open: Decimal
    ledger_cargos: Decimal
    ledger_abonos: Decimal
    ledger_close: Decimal
    bank_open: Decimal | None
    bank_abonos: Decimal | None
    bank_cargos: Decimal | None
    bank_close: Decimal | None
    opening_variance: Decimal | None
    outstanding_inflow: Decimal
    outstanding_outflow: Decimal
    unbooked_inflow: Decimal
    unbooked_outflow: Decimal
    adjusted_ledger: Decimal
    adjusted_bank: Decimal
    closure_residual: Decimal
    status_note: str


def categorize_bank(line: StatementLine, bank_cfg: BankConfig) -> str:
    """known_codes first, then description_categories, else uncategorized (§12.2)."""
    if line.code and line.code in bank_cfg.known_codes:
        return bank_cfg.known_codes[line.code].get("category", "uncategorized")
    desc = line.description or ""
    for rule in bank_cfg.description_categories:
        if re.search(rule["match"], desc, re.IGNORECASE):
            return rule["category"]
    return "uncategorized"


def _mk_item(entity, acct: Account, period, side, direction, category, amount,
             txn_date, description, source_ref, status, evidence,
             match_method=None, match_confidence=None, note=None) -> DerivedItem:
    iid = item_id(entity, acct.ledger_account, side, direction, amount, txn_date, source_ref)
    return DerivedItem(
        id=iid, ledger_account=acct.ledger_account, bank_account=acct.bank_account,
        bank_name=acct.bank, period=period, side=side, direction=direction, category=category,
        amount=amount, txn_date=txn_date, description=description, source_ref=source_ref,
        match_method=match_method, match_confidence=match_confidence, status=status,
        evidence=evidence, note=note)


def derive_account(entity: str, period: str, acct: Account, block: LedgerAccountBlock,
                   stmt: BankStatement | None, outcome: AccountMatchOutcome,
                   bank_cfg: BankConfig | None) -> tuple[DerivedAccountPeriod, list[DerivedItem]]:
    items: list[DerivedItem] = []
    batch_by_member = {
        w.id: candidate
        for candidate in outcome.pos_batch_candidates
        for w in candidate.ledger + candidate.bank
    }

    def evidence_for(w, default: dict) -> dict:
        candidate = batch_by_member.get(w.id)
        if candidate is None:
            return default
        batch = candidate.evidence["pos_batch"]
        return {**default, "pos_batch_candidate": {
            "id": candidate.id, "poliza": batch["poliza"],
            "ledger_rows": batch["ledger_rows"], "bank_lines": batch["bank_lines"]}}

    # ---- reconciling items from leftovers (pass 6 residue) ----------------
    for w in outcome.leftover_ledger:
        t = w.source
        flagged = w.id in outcome.flagged
        side = "ledger_outstanding"
        category = "deposit_in_transit" if w.direction == "inflow" else "outstanding_payment"
        ev = evidence_for(w, outcome.flagged.get(
            w.id, {"ledger_row": t.row_no, "concepto": (t.concepto or "").strip()}))
        items.append(_mk_item(
            entity, acct, period, side, w.direction, category, w.amount,
            t.txn_date.isoformat(), (t.concepto or "").strip(),
            f"ledger row {t.row_no}", "flagged" if flagged else "outstanding", ev))

    for w in outcome.leftover_bank:
        l = w.source
        flagged = w.id in outcome.flagged
        category = categorize_bank(l, bank_cfg) if bank_cfg else "uncategorized"
        ev = evidence_for(w, outcome.flagged.get(
            w.id, {"line_no": l.line_no, "description": l.description,
                   "code": l.code, "reference": l.reference}))
        items.append(_mk_item(
            entity, acct, period, "bank_unbooked", w.direction, category, w.amount,
            l.oper_date.isoformat(), l.description,
            f"stmt {acct.bank_account} p{l.page_no} l{l.line_no}",
            "flagged" if flagged else "outstanding", ev))

    # ---- amount_variance items from fuzzy matches with non-zero delta -----
    for m in outcome.matches:
        if m.amount_delta != ZERO:
            lw = m.ledger[0]
            items.append(_mk_item(
                entity, acct, period, "amount_variance", lw.direction, "fuzzy_match_delta",
                m.amount_delta, lw.txn_date.isoformat(),
                f"fuzzy match delta ({m.method})", f"ledger row {lw.source.row_no}",
                "outstanding", {"match": m.evidence}, match_method=m.method,
                match_confidence=str(m.confidence) if m.confidence is not None else None))

    # ---- balance-level (§12.1) --------------------------------------------
    outstanding_inflow = sum((w.amount for w in outcome.leftover_ledger if w.direction == "inflow"), ZERO)
    outstanding_outflow = sum((w.amount for w in outcome.leftover_ledger if w.direction == "outflow"), ZERO)
    unbooked_inflow = sum((w.amount for w in outcome.leftover_bank if w.direction == "inflow"), ZERO)
    unbooked_outflow = sum((w.amount for w in outcome.leftover_bank if w.direction == "outflow"), ZERO)

    bank_open = stmt.opening_balance if stmt else None
    bank_close = stmt.closing_balance if stmt else None
    bank_abonos = stmt.total_abonos if stmt else None
    bank_cargos = stmt.total_cargos if stmt else None
    opening_variance = (block.opening - bank_open) if bank_open is not None else None

    adjusted_ledger = q(block.closing - outstanding_inflow + outstanding_outflow)
    if bank_close is not None:
        adjusted_bank = q(bank_close - unbooked_inflow + unbooked_outflow)
        closure_residual = q((adjusted_ledger - adjusted_bank) - (opening_variance or ZERO))
    else:
        adjusted_bank = ZERO
        closure_residual = ZERO

    # opening_variance reconciling item (§12.2)
    if opening_variance not in (None, ZERO):
        items.append(_mk_item(
            entity, acct, period, "opening_variance",
            "inflow" if opening_variance > 0 else "outflow", "carried_forward",
            q(abs(opening_variance)), None, "Opening balance variance (ledger vs bank)",
            f"opening {acct.ledger_account}", "flagged",
            {"ledger_open": str(block.opening), "bank_open": str(bank_open)}))

    status_note = _status_note(stmt, closure_residual)
    ap = DerivedAccountPeriod(
        entity=entity, ledger_account=acct.ledger_account, period=period,
        ledger_open=block.opening, ledger_cargos=block.total_cargos,
        ledger_abonos=block.total_abonos, ledger_close=block.closing,
        bank_open=bank_open, bank_abonos=bank_abonos, bank_cargos=bank_cargos,
        bank_close=bank_close, opening_variance=opening_variance,
        outstanding_inflow=q(outstanding_inflow), outstanding_outflow=q(outstanding_outflow),
        unbooked_inflow=q(unbooked_inflow), unbooked_outflow=q(unbooked_outflow),
        adjusted_ledger=adjusted_ledger, adjusted_bank=adjusted_bank,
        closure_residual=closure_residual, status_note=status_note)
    return ap, items


def _status_note(stmt, residual) -> str:
    if stmt is None:
        return "No statement — not reconciled this period."
    if abs(residual) <= q("0.01"):
        return "Reconciled — residual explained by opening variance."
    return "ENGINE ERROR — non-zero closure residual, see run log."
