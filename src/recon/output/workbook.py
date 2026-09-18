"""Five-sheet workpaper writer (§15). Sheets, names and column orders transcribed from the
prototype. The prototype's live formulas are reproduced as formulas so a reviewer can trace the
arithmetic in Excel."""
from __future__ import annotations

import json
from decimal import Decimal

from openpyxl import Workbook
from openpyxl.worksheet.datavalidation import DataValidation

from ..config import Config
from . import styles as st

MONEY_COLS_TAB1 = "CDEFGHIJKLMNOPQRS"

# §15.5 rule 4 blocks proposed entries on any account carrying an unresolved anomaly. A
# pos_batch_aggregate row is a request for supporting documents on an item that already ties to
# the cent, not an unresolved discrepancy, so it must not suppress that account's other entries.
NON_BLOCKING_ANOMALIES = ("pos_batch_aggregate",)


def _num(v):
    return Decimal(v) if v not in (None, "") else None


def write_workbook(conn, config: Config, run_result, out_path: str) -> str:
    period = run_result.period
    entity = run_result.entity
    _MATERIALITY[0] = config.entity.materiality
    wb = Workbook()
    _cover(wb.active, config, run_result)
    _summary(wb.create_sheet("1. Reconciliation Summary"), conn, config, run_result)
    _items(wb.create_sheet("2. Item Validation Report"), conn, entity, period)
    _register(wb.create_sheet("3. Investigation Register"), conn, entity, period)
    _entries(wb.create_sheet("4. Proposed Adjusting Entries"), conn, config, period)
    wb.save(out_path)
    return out_path


# --- Cover -----------------------------------------------------------------

def _cover(ws, config: Config, rr) -> None:
    ws.title = "Cover"
    inv = rr.invariants
    gate_ids = ["I1", "I2", "I3", "I4", "I5", "I6", "I7", "I8"]
    inv_summary = []
    for iid in gate_ids:
        results = [r for r in inv if r.id == iid]
        ok = all(r.passed for r in results) if results else None
        inv_summary.append(f"{iid}: {'pass' if ok else ('n/a' if ok is None else 'FAIL')}")
    n_scope = len([a for a in config.entity.accounts if a.in_scope])
    n_oos = len([a for a in config.entity.accounts if not a.in_scope])
    r1 = [r for r in inv if r.id == "R1" and not r.passed]

    lines = [
        (f"Bank Reconciliation — {rr.entity}", st.TITLE_FONT),
        (f"Period: {rr.period}", st.BOLD),
        ("", None),
        (f"Run ID: {rr.run_id}", None),
        (f"Ledger file: {rr.ledger_file}", None),
        (f"Ledger SHA-256: {rr.ledger_sha256}", None),
        (f"Config SHA-256: {rr.config_sha256}", None),
        ("", None),
        (f"Scope: {n_scope} accounts in scope, {n_oos} out of scope (see sheet 1).", None),
        (f"Materiality: {config.entity.materiality} {config.entity.currency}", None),
        ("", None),
        ("Invariant summary (replaces the prototype's 'hand-verified' claim):", st.BOLD),
        ("   " + "   ".join(inv_summary), None),
        ("", None),
        ("What didn't reconcile:", st.BOLD),
    ]
    lead = _lead_paragraph(conn=None, rr=rr, r1=r1)
    lines.append((lead, st.WRAP))
    for i, (text, font) in enumerate(lines, start=1):
        c = ws.cell(i, 1, text)
        if font is st.WRAP:
            c.alignment = st.WRAP
        elif font is not None:
            c.font = font
    ws.column_dimensions["A"].width = 120


def _lead_paragraph(conn, rr, r1) -> str:
    bits = []
    if r1:
        bits.append("Opening variances remain on: "
                    + ", ".join(f"{r.ledger_account} ({r.detail.split('=')[-1]})" for r in r1) + ".")
    # POS batch rows are counted separately: they are evidence requests on matched items, and
    # folding them into the anomaly count would overstate what failed to reconcile.
    pos = [a for a in rr.anomalies if a["kind"] in NON_BLOCKING_ANOMALIES]
    other = [a for a in rr.anomalies if a["kind"] not in NON_BLOCKING_ANOMALIES]
    if other:
        kinds = sorted({a["kind"] for a in other})
        bits.append(f"{len(other)} structural anomalies flagged for investigation "
                    f"({', '.join(kinds)}).")
    if not bits:
        bits.append("All in-scope accounts with a statement reconciled to a zero closure residual.")
    if pos:
        bits.append(f"{len(pos)} account(s) had corte de caja batches matched against aggregated "
                    f"POS terminal settlements; acquirer batch reports are requested in sheet 3.")
    return " ".join(bits)


# --- Sheet 1: Reconciliation Summary ---------------------------------------

def _summary(ws, conn, config: Config, rr) -> None:
    period, entity = rr.period, rr.entity
    ws["A1"] = "1. Reconciliation Summary"
    ws["A1"].font = st.TITLE_FONT
    headers = ["Ledger Acct", "Account / Bank", "Ledger Open", "Ledger Cargos",
               "Ledger Abonos", "Ledger Close", "Bank Open", "Bank Abonos", "Bank Cargos",
               "Bank Close", "Opening Variance", "Current Outstanding Deposits",
               "Current Outstanding Payments", "Prior Bank Items Booked This Period (net)",
               "Current Bank Items Not Yet Booked (net)",
               "Prior Ledger Items Cleared This Period (net)",
               "Matched Amount Variance (ledger-bank net)",
               "Adjusted Ledger Bal.", "Adjusted Bank Bal.",
               "Residual explained by opening variance?", "Status / Note"]
    hrow = 4
    for c, h in enumerate(headers, start=1):
        ws.cell(hrow, c, h)
    st.style_header(ws, hrow, len(headers))

    rows = conn.execute(
        "SELECT * FROM account_period WHERE entity=? AND period=? ORDER BY ledger_account",
        (entity, period)).fetchall()
    by_acct = {r["ledger_account"]: r for r in rows}
    label = {a.ledger_account: (a.label or a.ledger_account) for a in config.entity.accounts}
    is_mxn = {a.ledger_account: (a.currency == config.entity.currency)
              for a in config.entity.accounts}
    cross_period: dict[tuple[str, str, str], Decimal] = {}
    for row in conn.execute(
            "SELECT ledger_account, side, direction, amount FROM reconciling_items "
            "WHERE entity=? AND period=? AND status='cleared_prior_period'", (entity, period)):
        key = (row["ledger_account"], row["side"], row["direction"])
        cross_period[key] = cross_period.get(key, Decimal(0)) + Decimal(row["amount"])
    amount_variances: dict[str, Decimal] = {}
    for row in conn.execute(
            "SELECT ledger_account, direction, amount FROM reconciling_items "
            "WHERE entity=? AND period=? AND side='amount_variance'", (entity, period)):
        sign = Decimal(1) if row["direction"] == "inflow" else Decimal(-1)
        amount_variances[row["ledger_account"]] = (
            amount_variances.get(row["ledger_account"], Decimal(0))
            + sign * Decimal(row["amount"]))

    r = hrow + 1
    mxn_rows = []
    for acct in config.entity.in_scope_accounts:
        ap = by_acct.get(acct.ledger_account)
        if ap is None:
            continue
        ws.cell(r, 1, acct.ledger_account)
        ws.cell(r, 2, label[acct.ledger_account])
        ws.cell(r, 3, float(_num(ap["ledger_open"])))
        ws.cell(r, 4, float(_num(ap["ledger_cargos"])))
        ws.cell(r, 5, float(_num(ap["ledger_abonos"])))
        ws.cell(r, 6, float(_num(ap["ledger_close"])))
        ws.cell(r, 7, float(_num(ap["bank_open"])) if ap["bank_open"] else None)
        ws.cell(r, 8, float(_num(ap["bank_abonos"])) if ap["bank_abonos"] else None)
        ws.cell(r, 9, float(_num(ap["bank_cargos"])) if ap["bank_cargos"] else None)
        ws.cell(r, 10, float(_num(ap["bank_close"])) if ap["bank_close"] else None)
        ws.cell(r, 11, f"=C{r}-G{r}")
        ledger_cross_in = cross_period.get((acct.ledger_account, "ledger_outstanding", "inflow"), Decimal(0))
        ledger_cross_out = cross_period.get((acct.ledger_account, "ledger_outstanding", "outflow"), Decimal(0))
        bank_cross_in = cross_period.get((acct.ledger_account, "bank_unbooked", "inflow"), Decimal(0))
        bank_cross_out = cross_period.get((acct.ledger_account, "bank_unbooked", "outflow"), Decimal(0))
        ws.cell(r, 12, float(_num(ap["outstanding_inflow"]) - ledger_cross_in))
        ws.cell(r, 13, float(_num(ap["outstanding_outflow"]) - ledger_cross_out))
        ws.cell(r, 14, float(ledger_cross_in - ledger_cross_out))
        net_unbooked = (_num(ap["unbooked_inflow"]) or Decimal(0)) - (_num(ap["unbooked_outflow"]) or Decimal(0))
        bank_cross_net = bank_cross_in - bank_cross_out
        ws.cell(r, 15, float(net_unbooked - bank_cross_net))
        ws.cell(r, 16, float(bank_cross_net))
        ws.cell(r, 17, float(amount_variances.get(acct.ledger_account, Decimal(0))))
        ws.cell(r, 18, f"=F{r}-L{r}+M{r}-N{r}-Q{r}")
        ws.cell(r, 19, f"=J{r}-O{r}-P{r}")
        ws.cell(r, 20, f'=IF(ABS((R{r}-S{r})-K{r})<=0.01,"Yes — residual = opening variance",'
                       f'"No — ENGINE ERROR, see run log")')
        ws.cell(r, 21, ap["status_note"])
        for col in MONEY_COLS_TAB1:
            st.money(ws[f"{col}{r}"])
        if is_mxn.get(acct.ledger_account, True):
            mxn_rows.append(r)
        r += 1

    # TOTAL row (MXN in-scope only; USD footnoted, §17.2 note)
    trow = r
    ws.cell(trow, 1, "TOTAL (MXN in-scope)")
    ws.cell(trow, 1).font = st.BOLD
    if mxn_rows:
        for col in MONEY_COLS_TAB1:
            # sum only MXN rows
            refs = "+".join(f"{col}{rr_}" for rr_ in mxn_rows)
            ws.cell(trow, "ABCDEFGHIJKLMNOPQRSTU".index(col) + 1, f"={refs}")
            st.money(ws.cell(trow, "ABCDEFGHIJKLMNOPQRSTU".index(col) + 1))
    r = trow + 2

    # out-of-scope block
    ws.cell(r, 1, "Out of scope")
    ws.cell(r, 1).font = st.BOLD
    r += 1
    for c, h in enumerate(["Ledger Acct", "Account", "Ledger Close", "n txns", "Reason"], start=1):
        ws.cell(r, c, h)
    st.style_header(ws, r, 5)
    r += 1
    for acct in config.entity.accounts:
        if acct.in_scope:
            continue
        ws.cell(r, 1, acct.ledger_account)
        ws.cell(r, 2, acct.label or "")
        r += 1

    ws.freeze_panes = f"A{hrow + 1}"
    st.autosize(ws, {1: 16, 2: 26, 3: 15, 4: 15, 5: 15, 6: 15, 7: 14, 8: 15, 9: 15,
                     10: 14, 11: 15, 12: 20, 13: 20, 14: 22, 15: 20, 16: 22, 17: 16,
                     18: 16, 19: 16, 20: 34, 21: 34})


# --- Sheet 2: Item Validation Report ---------------------------------------

_ITEM_TYPE = {"ledger_outstanding": "Ledger-side outstanding",
              "bank_unbooked": "Bank-side unbooked",
              "amount_variance": "Amount variance",
              "opening_variance": "Opening variance"}


def _items(ws, conn, entity, period) -> None:
    ws["A1"] = "2. Item Validation Report"
    ws["A1"].font = st.TITLE_FONT
    headers = ["item_id", "Ledger Acct", "Item Type", "Direction", "Category", "Date", "Amount",
               "Description / Supporting Evidence", "Side", "Clearance", "Bank verification",
               "Verification detail", "Material?", "First seen period", "Periods open",
               "Cleared in period"]
    hrow = 3
    for c, h in enumerate(headers, start=1):
        ws.cell(hrow, c, h)
    st.style_header(ws, hrow, len(headers))
    rows = conn.execute(
        "SELECT * FROM reconciling_items WHERE entity=? "
        "AND (period=? OR resolved_in_period=?) "
        "ORDER BY ledger_account, CAST(amount AS REAL) DESC", (entity, period, period)).fetchall()
    r = hrow + 1
    for it in rows:
        ws.cell(r, 1, it["id"])
        ws.cell(r, 2, it["ledger_account"])
        ws.cell(r, 3, _ITEM_TYPE.get(it["side"], it["side"]))
        ws.cell(r, 4, it["direction"].title())
        ws.cell(r, 5, it["category"])
        if it["txn_date"]:
            from datetime import date
            try:
                y, m, d = (int(x) for x in it["txn_date"].split("-"))
                c = ws.cell(r, 6, date(y, m, d)); st.date_cell(c)
            except Exception:
                ws.cell(r, 6, it["txn_date"])
        ws.cell(r, 7, float(Decimal(it["amount"]))); st.money(ws.cell(r, 7))
        ws.cell(r, 8, it["description"])
        ws.cell(r, 9, "Ledger" if it["side"] == "ledger_outstanding" else "Bank")
        clearance = ("Outstanding" if it["status"] == "outstanding"
                 else it["status"].replace("_", " ").title())
        ws.cell(r, 10, clearance)
        ws.cell(r, 11, _bank_verification(it))
        ws.cell(r, 12, it["source_ref"])
        ws.cell(r, 13, "Material" if abs(Decimal(it["amount"])) >= _materiality(conn) else "")
        ws.cell(r, 14, it["first_seen_period"])
        ws.cell(r, 15, it["periods_open"])
        ws.cell(r, 16, it["resolved_in_period"])
        ws.cell(r, 8).alignment = st.WRAP
        r += 1
    ws.freeze_panes = f"A{hrow + 1}"
    ws.column_dimensions["A"].hidden = True
    if r > hrow + 1:
        ws.auto_filter.ref = f"A{hrow}:P{r - 1}"
    st.autosize(ws, {1: 18, 2: 16, 3: 22, 4: 11, 5: 22, 6: 13, 7: 14, 8: 50, 9: 8,
                     10: 12, 11: 30, 12: 22, 13: 10, 14: 15, 15: 12, 16: 15})


def _bank_verification(it) -> str:
    if it["resolved_by"] == "cross_period_match":
        return ("CLEARS PRIOR-PERIOD ITEM" if it["status"] == "cleared_prior_period"
                else "CLEARED IN SUBSEQUENT PERIOD")
    if it["status"] == "flagged":
        try:
            ev = json.loads(it["evidence_json"])
            n = len(ev.get("candidates", []))
            if n:
                return f"AMBIGUOUS — {n} candidates"
        except Exception:
            pass
        return "FLAGGED — see Investigation Register"
    if it["match_method"]:
        return f"MATCHED (rule: {it['match_method']})"
    if it["side"] == "ledger_outstanding":
        return "CONFIRMED ABSENT FROM STATEMENT"
    if it["side"] == "bank_unbooked":
        return "CONFIRMED ABSENT FROM LEDGER"
    return "DERIVED"


def _materiality(conn) -> Decimal:
    # materiality is a config value; the run doesn't store it, so callers pass via module state
    return _MATERIALITY[0]


_MATERIALITY = [Decimal("5000.00")]


# --- Sheet 3: Investigation Register ---------------------------------------

def _register(ws, conn, entity, period) -> None:
    ws["A1"] = "3. Investigation Register"
    ws["A1"].font = st.TITLE_FONT
    headers = ["item_id", "Item", "Ledger Acct", "Amount", "Found in bank?", "Found in ledger?",
               "Supporting docs needed", "Auditor conclusion", "Resolution (fill in)", "Resolved?"]
    hrow = 3
    for c, h in enumerate(headers, start=1):
        ws.cell(hrow, c, h)
    st.style_header(ws, hrow, len(headers))

    r = hrow + 1
    flagged = conn.execute(
        "SELECT * FROM reconciling_items WHERE entity=? AND period=? AND status='flagged' "
        "ORDER BY side, CAST(amount AS REAL) DESC", (entity, period)).fetchall()
    for it in flagged:
        ws.cell(r, 1, it["id"])
        ws.cell(r, 2, _ITEM_TYPE.get(it["side"], it["side"]))
        ws.cell(r, 3, it["ledger_account"])
        ws.cell(r, 4, float(Decimal(it["amount"]))); st.money(ws.cell(r, 4))
        ws.cell(r, 5, "No" if it["side"] == "ledger_outstanding" else "Yes")
        ws.cell(r, 6, "Yes" if it["side"] == "ledger_outstanding" else "No")
        ws.cell(r, 7, "")
        ws.cell(r, 8, it["note"] or it["description"])
        ws.cell(r, 8).alignment = st.WRAP
        r += 1

    anoms = conn.execute(
        "SELECT * FROM anomalies WHERE period=? ORDER BY kind, CAST(COALESCE(amount,'0') AS REAL) DESC",
        (period,)).fetchall()
    for a in anoms:
        ws.cell(r, 1, a["id"])
        ws.cell(r, 2, a["kind"])
        ws.cell(r, 3, a["ledger_account"] or "")
        ws.cell(r, 4, float(Decimal(a["amount"])) if a["amount"] else None)
        if a["amount"]:
            st.money(ws.cell(r, 4))
        ws.cell(r, 5, "")
        ws.cell(r, 6, "")
        ws.cell(r, 7, a["docs_needed"] or "")
        ws.cell(r, 8, a["detail"]); ws.cell(r, 8).alignment = st.WRAP
        r += 1

    last = r - 1
    ws.freeze_panes = f"A{hrow + 1}"
    ws.column_dimensions["A"].hidden = True
    if last >= hrow + 1:
        dv = DataValidation(type="list", formula1='"No,Yes,Not an item"', allow_blank=True)
        ws.add_data_validation(dv)
        dv.add(f"J{hrow + 1}:J{last}")
        ws.auto_filter.ref = f"A{hrow}:J{last}"
    st.autosize(ws, {1: 18, 2: 24, 3: 16, 4: 15, 5: 12, 6: 13, 7: 26, 8: 55, 9: 30, 10: 12})


# --- Sheet 4: Proposed Adjusting Entries -----------------------------------

def _entries(ws, conn, config: Config, period) -> None:
    ws["A1"] = "4. Proposed Adjusting Entries"
    ws["A1"].font = st.TITLE_FONT
    headers = ["#", "Account (Dr/Cr)", "Description", "Debit", "Credit", "Ledger Acct",
               "Basis / Confirmation needed before posting"]
    hrow = 3
    for c, h in enumerate(headers, start=1):
        ws.cell(hrow, c, h)
    st.style_header(ws, hrow, len(headers))
    entity = config.entity.entity
    cmap = config.entity.charge_account_map
    eligible_cats = {"bank_commission", "bank_commission_iva", "loan_installment"}

    # accounts with a flagged item or unresolved anomaly are blocked (§15.5 rule 4)
    blocked = {r["ledger_account"] for r in conn.execute(
        "SELECT DISTINCT ledger_account FROM reconciling_items WHERE entity=? AND period=? "
        "AND status='flagged'", (entity, period)).fetchall()}
    blocked |= {r["ledger_account"] for r in conn.execute(
        "SELECT DISTINCT ledger_account FROM anomalies WHERE period=? AND ledger_account IS NOT NULL "
        f"AND kind NOT IN ({','.join('?' * len(NON_BLOCKING_ANOMALIES))})",
        (period, *NON_BLOCKING_ANOMALIES)).fetchall()}

    items = conn.execute(
        "SELECT * FROM reconciling_items WHERE entity=? AND period=? AND side='bank_unbooked' "
        "AND status!='resolved' ORDER BY ledger_account, CAST(amount AS REAL) DESC",
        (entity, period)).fetchall()
    r = hrow + 1
    n = 0
    for it in items:
        if it["category"] not in eligible_cats:
            continue
        if it["ledger_account"] in blocked:
            continue
        dr = cmap.get(it["category"], f"TBD-{it['category']}")
        n += 1
        ws.cell(r, 1, n)
        ws.cell(r, 2, f"Dr {dr}")
        ws.cell(r, 3, it["description"])
        ws.cell(r, 4, float(Decimal(it["amount"]))); st.money(ws.cell(r, 4))
        ws.cell(r, 5, None); st.money(ws.cell(r, 5))
        ws.cell(r, 6, it["ledger_account"])
        basis = "From parsed statement line. "
        if dr.startswith("TBD"):
            basis += "Dr account is a placeholder — confirm the real CONTPAQi code before posting (§19)."
        ws.cell(r, 7, basis); ws.cell(r, 7).alignment = st.WRAP
        r += 1
    if n == 0:
        ws.cell(r, 2, "No entries proposed — every quantified bank-side item is either already "
                      "booked, flagged, or lacks a mapped charge account (see sheet 3).")
        ws.cell(r, 2).alignment = st.WRAP
    ws.freeze_panes = f"A{hrow + 1}"
    st.autosize(ws, {1: 5, 2: 26, 3: 50, 4: 14, 5: 14, 6: 16, 7: 55})
