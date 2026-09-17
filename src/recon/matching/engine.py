"""Matching engine — passes 1-7 (§11.2).

Run per (ledger_account, direction), in order. Each pass consumes only still-unmatched items;
a consumed item is never reconsidered. Iteration order is (txn_date, amount, sort_key)
throughout, so the result is deterministic.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from ..config import MatchDefaults
from ..money import ZERO
from .candidates import WorkItem, order, days_between, score
from .grouping import poliza_groups, unique_subset
from .pos_batch import carries_terminal, eligible_settlements, settlement_subset


@dataclass
class MatchResult:
    pass_no: int
    method: str
    ledger: list[WorkItem]
    bank: list[WorkItem]
    rule_note: str
    confidence: float | None = None
    evidence: dict = field(default_factory=dict)

    @property
    def ledger_amount(self) -> Decimal:
        return sum((w.amount for w in self.ledger), ZERO)

    @property
    def bank_amount(self) -> Decimal:
        return sum((w.amount for w in self.bank), ZERO)

    @property
    def amount_delta(self) -> Decimal:
        return self.ledger_amount - self.bank_amount

    @property
    def date_delta_days(self) -> int:
        if not self.ledger or not self.bank:
            return 0
        return days_between(self.ledger[0].txn_date, self.bank[0].txn_date)


@dataclass
class PosBatchCandidate:
    id: str
    ledger: list[WorkItem]
    bank: list[WorkItem]
    evidence: dict


@dataclass
class AccountMatchOutcome:
    matches: list[MatchResult] = field(default_factory=list)
    pos_batch_candidates: list[PosBatchCandidate] = field(default_factory=list)
    leftover_ledger: list[WorkItem] = field(default_factory=list)
    leftover_bank: list[WorkItem] = field(default_factory=list)
    flagged: dict[str, dict] = field(default_factory=dict)   # work_item_id -> evidence (§11.3)


def _evidence(ledger: list[WorkItem], bank: list[WorkItem]) -> dict:
    def led(w):
        t = w.source
        return {"id": w.id, "row_no": t.row_no, "date": w.txn_date.isoformat(),
                "amount": str(w.amount), "concepto": (t.concepto or "").strip()}

    def bnk(w):
        l = w.source
        return {"id": w.id, "line_no": l.line_no, "date": w.txn_date.isoformat(),
                "amount": str(w.amount), "description": l.description}

    return {"ledger": [led(w) for w in ledger], "bank": [bnk(w) for w in bank]}


def _unmatched(items: list[WorkItem]) -> list[WorkItem]:
    return [w for w in items if not w.matched]


def _available(items: list[WorkItem]) -> list[WorkItem]:
    return [w for w in items if not w.matched and not w.reserved]


def match_account(ledger: list[WorkItem], bank: list[WorkItem], mcfg: MatchDefaults,
                  allow_fuzzy: bool = True,
                  pos_terminal: str | None = None) -> AccountMatchOutcome:
    out = AccountMatchOutcome()
    for direction in ("inflow", "outflow"):
        L = order([w for w in ledger if w.direction == direction])
        B = order([w for w in bank if w.direction == direction])
        _match_direction(L, B, direction, mcfg, allow_fuzzy, pos_terminal, out)
    out.leftover_ledger = _unmatched(ledger)
    out.leftover_bank = _unmatched(bank)
    return out


def _match_direction(L, B, direction: str, mcfg: MatchDefaults, allow_fuzzy: bool,
                     pos_terminal: str | None, out: AccountMatchOutcome):
    window = mcfg.date_window_days
    tol = Decimal(str(mcfg.amount_tolerance))
    min_sim = mcfg.description_min_similarity
    margin = mcfg.min_score_margin
    ss = mcfg.subset_sum
    pb = mcfg.pos_batch

    # Review candidates reserve both sides so later passes cannot turn only part of a proven
    # aggregate into ordinary matches. They remain unmatched and therefore appear in Tab 2.
    if pos_terminal and pb.enabled and direction == "inflow":
        pos_lines = order([w for w in B if carries_terminal(w, pos_terminal)])
        groups = poliza_groups(_available(L))
        for key in sorted(groups, key=lambda k: (k[0], str(k[2]))):
            group = groups[key]
            cut, poliza = key[0], key[2]
            target = sum((w.amount for w in group), ZERO)
            subset = settlement_subset(
                target, eligible_settlements(pos_lines, cut, pb.settlement_lag_days),
                pb.max_bank_lines)
            if not subset:
                continue
            settled = min(w.txn_date for w in subset)
            extra = {"pos_terminal": pos_terminal, "poliza": poliza,
                     "cut_date": cut.isoformat(), "settled_date": settled.isoformat(),
                     "settlement_lag_days": (settled - cut).days,
                     "ledger_rows": len(group), "bank_lines": len(subset)}
            if pb.disposition == "auto_match":
                _commit(out, 4, "pos_batch", list(group), subset,
                        f"POS batch: póliza {poliza} corte of {len(group)} rows on {cut:%d/%b/%Y} "
                        f"= {len(subset)} terminal-{pos_terminal} credit(s)",
                        extra_evidence=extra, evidence_key="pos_batch")
                continue
            candidate_id = f"POS-{group[0].id}"
            evidence = _evidence(list(group), subset)
            evidence["pos_batch"] = {"candidate_id": candidate_id, **extra}
            for w in group + subset:
                w.reserved = True
            out.pos_batch_candidates.append(PosBatchCandidate(candidate_id, list(group), subset, evidence))

    # ---- Pass 1: exact (same date, same amount, unique both sides) --------
    by_key_l: dict[tuple, list[WorkItem]] = {}
    by_key_b: dict[tuple, list[WorkItem]] = {}
    for w in _available(L):
        by_key_l.setdefault((w.txn_date, w.amount), []).append(w)
    for w in _available(B):
        by_key_b.setdefault((w.txn_date, w.amount), []).append(w)
    for key in sorted(by_key_l, key=lambda k: (k[0], k[1])):
        ls, bs = by_key_l[key], by_key_b.get(key, [])
        if len(ls) == 1 and len(bs) == 1:
            _commit(out, 1, "exact", [ls[0]], [bs[0]],
                    f"exact: same date {key[0]:%d/%b/%Y}, amount {key[1]}, unique both sides")

    # ---- Pass 2: exact amount + date window -------------------------------
    for lw in order(_available(L)):
        cands = [bw for bw in _available(B)
                 if bw.amount == lw.amount and days_between(lw.txn_date, bw.txn_date) <= window]
        if not cands:
            continue
        cands.sort(key=lambda bw: (days_between(lw.txn_date, bw.txn_date), bw.sort_key))
        if len(cands) == 1:
            best = cands[0]
        else:
            d0 = days_between(lw.txn_date, cands[0].txn_date)
            d1 = days_between(lw.txn_date, cands[1].txn_date)
            if d0 == d1:
                _flag(out, lw, cands, 2, window, margin=None)   # still tied -> defer to pass 7
                continue
            best = cands[0]
        # confirm the chosen bank is not equally claimed (unique on both sides)
        rival = [ow for ow in _available(L)
                 if ow is not lw and ow.amount == best.amount
                 and days_between(ow.txn_date, best.txn_date) < days_between(lw.txn_date, best.txn_date)]
        if rival:
            continue
        dd = days_between(lw.txn_date, best.txn_date)
        _commit(out, 2, "exact_dated", [lw], [best],
                f"exact amount {lw.amount}, |Δ|={dd}d within {window}d window")

    # ---- Pass 3: póliza group N:1 -----------------------------------------
    for key, group in poliza_groups(_available(L)).items():
        gsum = sum((w.amount for w in group), ZERO)
        gdate = group[0].txn_date
        for bw in order(_available(B)):
            if bw.amount == gsum and days_between(gdate, bw.txn_date) <= window \
                    and all(not w.matched for w in group):
                _commit(out, 3, "group", list(group), [bw],
                        f"póliza {key[2]} group of {len(group)} sums to bank {bw.amount}")
                break

    # ---- Pass 5: subset-sum N:1 -------------------------------------------
    for bw in order(_available(B)):
        pool = order(_available(L))
        subset = unique_subset(bw.amount, pool, bw.txn_date, window,
                               ss.max_pool, ss.max_subset_size)
        if subset:
            _commit(out, 5, "subset", subset, [bw],
                    f"unique subset of {len(subset)} ledger rows sums to bank {bw.amount}")

    # ---- Pass 6: fuzzy ----------------------------------------------------
    if allow_fuzzy:
        for lw in order(_available(L)):
            scored = []
            for bw in _available(B):
                if abs(lw.amount - bw.amount) > tol:
                    continue
                if days_between(lw.txn_date, bw.txn_date) > window:
                    continue
                sc = score(lw, bw, window)
                if sc["similarity"] >= min_sim:
                    scored.append((sc["score"], bw, sc))
            if not scored:
                continue
            scored.sort(key=lambda t: (-t[0], t[1].sort_key))
            best_score, best_bw, best_sc = scored[0]
            runner = scored[1][0] if len(scored) > 1 else 0.0
            if best_score - runner >= margin:
                _commit(out, 6, "fuzzy", [lw], [best_bw],
                        f"fuzzy score {best_score:.3f} (Δamt {best_sc['amount_delta']}, "
                        f"{best_sc['date_delta_days']}d, sim {best_sc['similarity']})",
                        confidence=best_score, extra_evidence=best_sc)
            else:
                _flag(out, lw, [t[1] for t in scored], 6, window, margin=margin,
                      scores=[t[2] for t in scored])


def _commit(out, pass_no, method, ledger, bank, note, confidence=None, extra_evidence=None,
            evidence_key="score"):
    for w in ledger + bank:
        w.matched = True
    ev = _evidence(ledger, bank)
    if extra_evidence:
        ev[evidence_key] = extra_evidence
    out.matches.append(MatchResult(pass_no, method, list(ledger), list(bank), note,
                                    confidence=confidence, evidence=ev))


def _flag(out, ledger_item, bank_cands, pass_no, window, margin, scores=None):
    """Record an ambiguity so Tab 3 says *why* an item is open (§11.3). The item stays
    unmatched; pass 7 (derive.py) turns it into a flagged reconciling item."""
    cand_records = []
    for i, bw in enumerate(bank_cands):
        sc = scores[i] if scores else score(ledger_item, bw, window)
        rec = {"bank_txn_id": bw.id, "line_no": bw.source.line_no,
               "date": bw.txn_date.isoformat(), "amount": str(bw.amount),
               "description": bw.source.description, **{k: sc[k] for k in
               ("amount_score", "date_score", "similarity", "score")}}
        cand_records.append(rec)
    top = sorted((c["score"] for c in cand_records), reverse=True)
    out.flagged[ledger_item.id] = {
        "reason": "multiple_candidates", "pass": pass_no,
        "ledger": {"id": ledger_item.id, "row_no": ledger_item.source.row_no,
                   "date": ledger_item.txn_date.isoformat(), "amount": str(ledger_item.amount),
                   "concepto": (ledger_item.source.concepto or "").strip()},
        "candidates": cand_records,
        "margin": round(top[0] - top[1], 3) if len(top) > 1 else None,
        "min_score_margin": margin,
    }
