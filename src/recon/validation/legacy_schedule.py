"""One-time validation against the bookkeeper's embedded schedule (§17.3). QUARANTINED and
DELETABLE — nothing else imports it. Parses the manual 'Partidas en conciliación' block that
the production ledger parser deliberately skips (§9.5), only to print engine-vs-bookkeeper
figures for the June 2024 acceptance test. After that passes, invariants I1–I8 take over.
"""
from __future__ import annotations

# bookkeeper figures transcribed from the June sheet (§17.3), for a printed comparison
BOOKKEEPER_JUNE = {
    "1112-01-001-00": {"partidas": ("812366.41", "10582.76"), "suma": ("638324.33", "0")},
    "1112-01-002-00": {"partidas": ("103066.15", None), "suma": ("93484.10", "0")},
    "1112-01-003-00": {"partidas": ("37059.07", None), "suma": ("36483.88", "0")},
    "1112-01-006-00": {"partidas": ("131967.30", None), "suma": ("23206.65", "0")},
    "1112-01-013-00": {"partidas": ("75495.79", None), "suma": ("103463.22", "360343.60")},
}


def compare(ledger_path: str, period: str, engine_by_account: dict) -> str:
    """engine_by_account: {ledger_account: DerivedAccountPeriod}. Returns a printable report."""
    lines = [f"Legacy-schedule validation — {period}", "=" * 60]
    for acct, book in BOOKKEEPER_JUNE.items():
        ap = engine_by_account.get(acct)
        if ap is None:
            lines.append(f"{acct}: no engine result")
            continue
        lines.append(
            f"{acct}: engine outstanding(in/out)={ap.outstanding_inflow}/{ap.outstanding_outflow} "
            f"unbooked(in/out)={ap.unbooked_inflow}/{ap.unbooked_outflow} "
            f"closure_residual={ap.closure_residual} | bookkeeper partidas={book['partidas']} "
            f"suma={book['suma']}")
    lines += [
        "",
        "Known differences that MUST appear (§17.3):",
        " - 1112-01-013-00 unbooked_outflow should be 360,343.80 (20 x 18,017.19), "
        "$0.20 above the schedule's 360,343.60.",
        " - 1112-01-006-00 has an extra 1,051.61 of bank retiros the schedule left unexplained.",
        " - account 001's schedule closes with 0.37 / -0.35 slack; the engine's "
        "closure_residual must be 0.00.",
    ]
    return "\n".join(lines)
