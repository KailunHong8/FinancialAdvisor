# Reconciliation Engine — Secontrol Automatización SA de CV

A deterministic Python pipeline that reconciles the CONTPAQi *Bancos* ledger (`1112-*`) against
BBVA statement PDFs and produces a five-sheet Excel workpaper. No network calls, no LLM at
runtime, no non-determinism: same inputs ⇒ byte-identical outputs (modulo one run timestamp).
Every match is attributable to a numbered rule; every workpaper is reproducible from recorded
input hashes.

Built to the contract in `reconciliation-engine-impl-spec.md`. See **Deviations** below for the
handful of places where a real BBVA statement (calibrated during the build) required changing the
spec's placeholder assumptions.

## Install

```bash
python3 -m venv .venv
.venv/bin/pip install -e .        # add [dev] for pytest
```

Python 3.11+. Dependencies: `openpyxl`, `pdfplumber`, `PyYAML`, `pydantic` v2, `rapidfuzz`.

## Input layout

Point `--root` (or `RECON_ROOT`) at the synced OneDrive folder:

```
<root>/
├── ledger/<anything>.xlsx        # CONTPAQi 'Movimientos, Auxiliares del Catálogo' export; sheet per period (06-24)
├── statements/2024-06/*.pdf      # BBVA statements, any filename — identified by 'No. de Cuenta' inside
└── output/                       # SECONTROL_Bank_Reconciliation_<YYYY-MM>.xlsx is written here
```

Statements are matched to ledger accounts **by the account number printed inside the PDF**, never
by filename. A PDF whose account is not in `config/entities/secontrol.yml` is a hard error.

## Monthly runbook

```bash
recon init-db                                        # once
recon run --root "$RECON_ROOT" --period 2024-06      # parse → match → derive → invariants → workbook
```

`recon run` exits non-zero and writes **no** workbook on any gate-invariant failure — a
half-correct workpaper is worse than none. Exit codes: `0` ok · `2` parse failure · `3` invariant
gate failure · `4` config/inventory error.

Then the analyst works sheet **3. Investigation Register** in Excel (with Copilot), fills the
`Resolution` / `Resolved?` columns, saves, and:

```bash
recon ingest-review <reviewed.xlsx>                  # reads resolutions back by stable item_id
```

Resolutions survive a later `recon run` because item ids are content hashes, not row numbers.

### Other commands
```bash
recon check --root <r> --period 2024-06              # invariants only, no workbook
recon backfill --root <r> --from 2024-01 --to 2024-06  # ascending, stops at first gate failure
recon diagnose-statement <pdf>                       # dump words/coords for calibrating a layout
recon verify-template bbva                            # rerun bank fixtures + I3, promote a template
recon validate-against-schedule --root <r> --period 2024-06   # one-time §17.3 comparison
```

## What the engine guarantees — the invariants

Run every period. **Gate** aborts the run; **report** becomes an Investigation Register row.

| # | Checks | Action |
|---|---|---|
| I1 | ledger `open + Σcargos − Σabonos = close`, per account | gate |
| I2 | parsed transaction sums == the block's `Total:` row F/G | gate |
| I3 | `bank_open + Σabonos − Σcargos = bank_close`, and parsed line sums == printed Depósitos/Retiros totals | gate |
| I4 | Σ matched ledger = Σ matched bank, per direction | gate |
| I5 | `closure_residual = 0` per account | gate |
| I6 | no ledger row / bank line in more than one match | gate |
| I7 | every ledger row and bank line is matched or has a reconciling item | gate |
| I8 | every prior-period outstanding item is matched, resolved, or carried | gate |
| R1 | `opening_variance = 0` | report |
| R2 | ledger Σ == the sheet's grand total | gate |

`closure_residual = 0` is the single most valuable invariant and the replacement for the pilot's
hand verification. It is algebraically zero when matching is sound (I4), so a non-zero value is an
engine bug, not a business finding.

## Deviations from the design spec (all from calibrating a real statement)

The impl-spec was written before a BBVA PDF was in hand (§10.3). Calibrating against the real
"MAESTRA PYME BBVA" statement changed these placeholders — see `config/banks/bbva.yml` and code
comments:

1. **Header labels.** The opening balance is `Saldo de Liquidación Inicial` (not `Saldo Anterior`);
   the `Depósitos / Abonos` line prints a movement count between the label and the total. Regexes
   updated accordingly. Verified: they capture `98,971.26 / 346,100.50 / 3,394,770.89 /
   3,147,641.65` for account 0114091108, matching the prototype's known figures to the cent.
2. **Two balance columns.** BBVA prints both Operación and Liquidación balances, sparsely (not on
   every line). The per-line running-balance chain in the spec's I3 is therefore **not** asserted;
   I3 is the two total tie-outs (flow identity + printed-total sums), which tie to the cent.
3. **I2 last-saldo.** The Σcargos/Σabonos tie-out to the `Total:` row is the gating form of I2
   (it catches dropped/double-read rows). The spec's additional "last transaction saldo == close"
   comparison is reported, not gated, because a few historical months have a running-balance
   column that doesn't chain to the Total (e.g. 04-24!007 off by 5,418.28 while the sums tie).
4. **Ledger edge cases found in real data:** an unlabelled summary row before a blank `Total:`
   label (02-24!017); a zero-amount transaction in a no-`Total:`-row account (03-24!016); a row
   carrying both Cargos and Abonos (05-24!988); Excel `#REF!` cells. All handled without crashing.

None of these weaken the audit position; the flow tie-outs remain exact.

## Notes for the historical backfill

The 2024 target period (June) reconciles cleanly across **all 14 in-scope accounts** — every gate
invariant green, `closure_residual = 0.00` per account (including the USD account), and the bank
open/close for all 14 match the §17.2 acceptance figures to the cent. The pilot's hand-made
structural findings (USD `currency_mismatch`, `dormant_with_balance` on 016 at 13,000.01,
`sign_divergence` on 012) are all reproduced automatically.

Earlier months carry genuine **source-data** inconsistencies that the gates correctly surface and
that require Kai to confirm before backfilling through them, e.g. 01-24 account 002's opening (0)
does not reconcile with its own close (I1), and a few stale `Total:` rows omit a small IVA line.
These are data issues, not parser bugs (I2's sum tie-out passes), and the gate halting backfill at
them is by design.

## Open items (need Kai)

- Real Dr account codes for `charge_account_map` in `config/entities/secontrol.yml` (currently
  `TBD-*` placeholders; Tab 4 flags them as needing confirmation before posting).
- Whether the OneDrive folder already uses a different layout than `ledger/` + `statements/<YYYY-MM>/`.
- Forward periods: one July statement (account 0116016790) is present; the remaining July PDFs are
  needed before the monthly run can go live from July onward.

## Layout

`src/recon/` — `parsers/` (ledger + bank), `matching/` (passes 1-7), `reconcile/` (derive,
invariants, anomalies, carry-forward), `output/` (workbook), `store.py`, `pipeline.py`, `cli.py`.
`config/` holds the entity inventory, matching thresholds, and the BBVA layout template. `data/`
is gitignored (holds the SQLite db, diagnostics, output). Client figures never enter git.
