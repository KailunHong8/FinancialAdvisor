"""Structural anomaly detection (§12.6). Each becomes a Tab 3 row. Every kind here was a
finding a human made by hand in the June pass."""
from __future__ import annotations

import hashlib
import re

from ..config import Account, EntityConfig
from ..models import BankStatement, LedgerAccountBlock
from ..money import q, ZERO
from .derive import DerivedAccountPeriod


def _aid(*parts) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


def detect_account_anomalies(entity: str, period: str, acct: Account,
                             block: LedgerAccountBlock, stmt: BankStatement | None,
                             ap: DerivedAccountPeriod, ent_cfg: EntityConfig) -> list[dict]:
    found: list[dict] = []
    la = acct.ledger_account

    def add(kind, amount, detail, docs=None, conclusion=None):
        found.append({"id": _aid(period, la, kind), "period": period, "ledger_account": la,
                      "kind": kind, "amount": (str(q(amount)) if amount is not None else None),
                      "detail": detail, "docs_needed": docs, "conclusion": conclusion})

    # sign_divergence
    if stmt is not None:
        if (block.closing < 0) != (stmt.closing_balance < 0) and block.closing != ZERO \
                and stmt.closing_balance != ZERO:
            add("sign_divergence", block.closing,
                f"ledger close {block.closing} vs bank close {stmt.closing_balance} differ in sign",
                "Confirm whether the negative ledger balance is an overdraft or an unbooked deposit.")

    # label_mismatch (CLABE tail vs account number)
    if stmt is not None and acct.label:
        label_digits = re.findall(r"\d+", acct.label)
        acct_no = (acct.bank_account or "").lstrip("0")
        if label_digits and not any(acct_no in d or d in acct_no for d in label_digits):
            if acct.clabe and any(d and d in acct.clabe for d in label_digits):
                add("label_mismatch", None,
                    f"ledger label digits {label_digits} match the CLABE tail, not account "
                    f"{acct.bank_account}", "Correct the chart-of-accounts label (no dollar impact).")

    # currency_mismatch
    if stmt is not None and stmt.currency != ent_cfg.currency:
        add("currency_mismatch", ap.opening_variance,
            f"statement currency {stmt.currency} != entity currency {ent_cfg.currency}; "
            f"reported variance may be FX translation, not a reconciling difference (§12.5)",
            "Confirm the FX policy; v1 does not translate.")

    # dormant_with_balance
    activity = (block.total_cargos + block.total_abonos) == ZERO
    if stmt is not None:
        bank_activity = (stmt.total_abonos + stmt.total_cargos) == ZERO
        if activity and bank_activity and ap.opening_variance not in (None, ZERO):
            add("dormant_with_balance", ap.opening_variance,
                f"zero activity both sides but variance {ap.opening_variance}",
                "Confirm the account should carry this balance or be closed.")

    # unmatched_period_flow (a gap in matched outflow > materiality)
    if stmt is not None:
        unexplained = q(ap.unbooked_outflow)
        # a large unbooked outflow with no reconciling explanation
        if ap.bank_cargos is not None and unexplained > ent_cfg.materiality:
            gap = q(ap.bank_cargos - (block.total_abonos))
            if abs(gap) > ent_cfg.materiality:
                add("unmatched_period_flow", gap,
                    f"bank retiros {ap.bank_cargos} vs ledger abonos {block.total_abonos}: "
                    f"gap {gap} exceeds materiality",
                    "Trace the unbooked bank charges/debits.")

    # impossible_date
    if stmt is not None:
        bad = [t.row_no for t in block.transactions
               if t.txn_date < stmt.period_start or t.txn_date > stmt.period_end]
        if bad:
            add("impossible_date", None,
                f"{len(bad)} ledger rows dated outside the statement period (rows {bad[:5]}...)",
                "Verify the export period.")

    # annotation_conflict (col I hand-typed bank balance vs statement, §9.4)
    if stmt is not None and block.col_i_annotation:
        stmt_balances = {stmt.opening_balance, stmt.closing_balance}
        for ann in block.col_i_annotation:
            if ann not in stmt_balances and not any(abs(ann - b) <= q("0.01") for b in stmt_balances):
                add("annotation_conflict", ann,
                    f"ledger column-I annotation {ann} matches neither bank open "
                    f"{stmt.opening_balance} nor close {stmt.closing_balance}",
                    "Bank balance always comes from the statement; annotation is advisory.")
                break
    return found


def detect_cross_account(entity: str, period: str, all_leftovers: list[tuple]) -> list[dict]:
    """cross_account_candidate: an unmatched ledger item whose best amount+description match sits
    in a different account (§11.3). all_leftovers: list of (ledger_account, item)."""
    from ..models import normalize_description
    from rapidfuzz import fuzz
    found = []
    for i, (acct_a, a) in enumerate(all_leftovers):
        for acct_b, b in all_leftovers:
            if acct_a == acct_b or a.side != "ledger_outstanding" or b.side != "bank_unbooked":
                continue
            if a.amount != b.amount or a.direction != b.direction:
                continue
            sim = fuzz.token_sort_ratio(normalize_description(a.description or ""),
                                        normalize_description(b.description or "")) / 100.0
            if sim >= 0.6:
                found.append({"id": _aid(period, acct_a, "cross_account_candidate", a.id, b.id),
                              "period": period, "ledger_account": acct_a,
                              "kind": "cross_account_candidate", "amount": str(a.amount),
                              "detail": f"ledger item {a.amount} in {acct_a} best matches a bank "
                                        f"line in {acct_b} (sim {sim:.2f}) — surfaced, never matched",
                              "docs_needed": "Confirm the correct account.", "conclusion": None})
    return found
