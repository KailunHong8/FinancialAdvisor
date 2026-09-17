"""`recon` CLI (§16).

Exit codes: 0 ok · 2 parse failure · 3 invariant gate failure · 4 config/inventory error.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from .config import Config
from . import store
from .pipeline import run_period, RunError


def _log(level: str):
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO),
                        format="%(levelname)s %(name)s: %(message)s", stream=sys.stderr)


def _config(args) -> Config:
    return Config(args.config_dir)


def _root(args) -> str:
    root = args.root or os.environ.get("RECON_ROOT")
    if not root:
        raise RunError(4, "no --root and no RECON_ROOT env var")
    return root


def _out_path(args, config, period) -> str:
    if getattr(args, "out", None):
        return args.out
    out_dir = Path(_root(args)) / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    return str(out_dir / f"SECONTROL_Bank_Reconciliation_{period}.xlsx")


def cmd_init_db(args):
    conn = store.connect(args.db)
    store.init_db(conn)
    print(f"initialized {len(store.TABLE_NAMES)} tables in {args.db}")
    return 0


def cmd_run(args):
    config = _config(args)
    conn = store.connect(args.db)
    store.init_db(conn)
    out = None if args.dry_run else _out_path(args, config, args.period)
    rr = run_period(conn, config, _root(args), args.period, Path(args.db).parent / "diagnostics",
                    read_back_path=out)
    if not args.dry_run:
        from .output.workbook import write_workbook
        write_workbook(conn, config, rr, out)
        print(f"wrote {out}")
    _print_invariants(rr)
    return 0


def cmd_check(args):
    """Invariants only, no workbook."""
    config = _config(args)
    conn = store.connect(args.db)
    store.init_db(conn)
    rr = run_period(conn, config, _root(args), args.period, Path(args.db).parent / "diagnostics")
    _print_invariants(rr)
    return 0


def cmd_backfill(args):
    config = _config(args)
    conn = store.connect(args.db)
    store.init_db(conn)
    periods = _period_range(args.from_period, args.to_period)
    for p in periods:  # strictly ascending so carry-forward is meaningful (§16)
        print(f"=== {p} ===")
        out = None if args.dry_run else _out_path(args, config, p)
        rr = run_period(conn, config, _root(args), p, Path(args.db).parent / "diagnostics",
                        read_back_path=out)
        if not args.dry_run:
            from .output.workbook import write_workbook
            write_workbook(conn, config, rr, out)
        _print_invariants(rr)
    return 0


def cmd_ingest_review(args):
    from .exceptions_io import ingest_review
    conn = store.connect(args.db)
    summary = ingest_review(conn, args.reviewed)
    print(summary)
    return 0


def cmd_diagnose_statement(args):
    from .parsers.bank.diagnostics import write_bundle
    config = _config(args)
    data = Path(args.pdf).read_bytes()
    out = Path(args.db).parent / "diagnostics" / "manual" / Path(args.pdf).stem
    write_bundle(out, data, config.bank(args.bank), f"Manual diagnose of {args.pdf}.")
    print(f"diagnostic bundle at {out}")
    return 0


def cmd_verify_template(args):
    """Rerun bank fixtures + I3 for a bank (§10.4). Fixtures live under tests/fixtures/<bank>/."""
    from .parsers.bank.registry import build_registry
    from .reconcile.invariants import check_statement
    config = _config(args)
    registry = build_registry(config)
    parser = registry.get(args.bank)
    if parser is None:
        print(f"no parser for {args.bank}", file=sys.stderr)
        return 4
    fixdir = Path("tests/fixtures") / args.bank
    pdfs = sorted(fixdir.glob("*.pdf")) if fixdir.exists() else []
    if not pdfs:
        print(f"no fixtures under {fixdir}", file=sys.stderr)
        return 2
    import hashlib
    all_ok = True
    for pdf in pdfs:
        data = pdf.read_bytes()
        stmts = parser.parse(data, pdf.name, hashlib.sha256(data).hexdigest())
        for s in stmts:
            for res in check_statement(s):
                status = "PASS" if res.passed else "FAIL"
                all_ok &= res.passed
                print(f"{pdf.name} {s.bank_account}: I3 {status} — {res.detail}")
    if all_ok:
        conn = store.connect(args.db)
        store.init_db(conn)
        from datetime import date
        store.upsert_registry(conn, {
            "bank_name": args.bank, "parser_version": config.bank(args.bank).parser_version,
            "template_sha256": config.bank_template_sha256(args.bank),
            "template_source": "copilot_assisted_confirmed",
            "last_verified_period": date.today().isoformat()})
        print(f"{args.bank}: all fixtures pass; registry updated")
    return 0 if all_ok else 3


def cmd_validate_schedule(args):
    from .validation.legacy_schedule import compare
    config = _config(args)
    conn = store.connect(args.db)
    store.init_db(conn)
    rr = run_period(conn, config, _root(args), args.period, Path(args.db).parent / "diagnostics")
    engine_by_account = {v.account.ledger_account: v.ap for v in rr.views}
    print(compare("", args.period, engine_by_account))
    return 0


def _print_invariants(rr):
    from collections import Counter
    c = Counter()
    for r in rr.invariants:
        c[(r.id, r.passed)] += 1
    print("Invariants:", {f"{k[0]}{'✓' if k[1] else '✗'}": v for k, v in sorted(c.items())},
          file=sys.stderr)
    for r in rr.invariants:
        if not r.passed and r.gate:
            print(f"  GATE FAIL {r.id}@{r.ledger_account}: {r.detail}", file=sys.stderr)


def _period_range(a: str, b: str) -> list[str]:
    ya, ma = (int(x) for x in a.split("-"))
    yb, mb = (int(x) for x in b.split("-"))
    out = []
    y, m = ya, ma
    while (y, m) <= (yb, mb):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            m, y = 1, y + 1
    return out


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="recon", description="Deterministic bank reconciliation")
    p.add_argument("--db", default="data/recon.sqlite")
    p.add_argument("--config-dir", default="config")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--log-level", default="INFO")
    p.add_argument("--fail-on-warning", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init-db").set_defaults(func=cmd_init_db)

    for name in ("run", "check"):
        sp = sub.add_parser(name)
        sp.add_argument("--root")
        sp.add_argument("--period", required=True)
        sp.add_argument("--out")
        sp.set_defaults(func=cmd_run if name == "run" else cmd_check)

    sp = sub.add_parser("backfill")
    sp.add_argument("--root")
    sp.add_argument("--from", dest="from_period", required=True)
    sp.add_argument("--to", dest="to_period", required=True)
    sp.set_defaults(func=cmd_backfill)

    sp = sub.add_parser("ingest-review")
    sp.add_argument("reviewed")
    sp.set_defaults(func=cmd_ingest_review)

    sp = sub.add_parser("diagnose-statement")
    sp.add_argument("pdf")
    sp.add_argument("--bank", default="bbva")
    sp.set_defaults(func=cmd_diagnose_statement)

    sp = sub.add_parser("verify-template")
    sp.add_argument("bank")
    sp.set_defaults(func=cmd_verify_template)

    sp = sub.add_parser("validate-against-schedule")
    sp.add_argument("--root")
    sp.add_argument("--period", required=True)
    sp.set_defaults(func=cmd_validate_schedule)
    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _log(args.log_level)
    try:
        return args.func(args)
    except RunError as e:
        print(f"ERROR ({e.code}): {e}", file=sys.stderr)
        return e.code


if __name__ == "__main__":
    sys.exit(main())
