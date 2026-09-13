# Reconciliation Engine — Implementation Spec

**Status:** v1.0 — buildable. Derived from `reconciliation-engine-spec.md` (design v0.2) plus Kai's
six clarifications of 2026-09-08.
**Entity:** Secontrol Automatización SA de CV
**Target:** a deterministic Python pipeline that reproduces `SECONTROL_Bank_Reconciliation_June2024_v2.xlsx`
from raw inputs alone, then runs monthly.

Every structural claim in §7–§10 and every figure in §17 was read directly out of
`01-12-2024-PatyMaiki2022-2-Kai-test.xlsx` and `SECONTROL_Bank_Reconciliation_June2024_v2.xlsx`
while writing this spec. Where a figure is asserted as a test expectation, it is a
verified fact from those files, not an estimate.

---

## 1. What the clarifications change

| # | Kai's clarification | Design-spec section affected | v1 decision |
|---|---|---|---|
| 1 | OneDrive credentials come later, on the deployment machine where the account is signed in | §3.1 Storage connector | **Read files from the local OneDrive sync folder as a plain filesystem path.** No Graph API, no app registration, no OAuth in v1. A `FileSource` interface keeps a Graph adapter possible later. See §8 — this is a simplification worth taking, argued there. |
| 2 | `SECONTROL_Bank_Reconciliation_June2024_v2.xlsx` is the prototype of the first working version | §1, §5 Phase 4 | That workbook is the **binding output contract**. §15 specifies its five sheets and exact columns. |
| 3 | Fallback for an unrecognized statement layout = Copilot helps update the primary path; not Azure Document Intelligence | §3.2 tier 2, §6 Q2 | **No document-AI service, no vision call.** Unrecognized layout ⇒ the run fails loudly and emits a *diagnostic bundle* (§10.4) that the user takes to Copilot to produce a new/updated layout template. Layout templates are **YAML**, not Python, so the update is usually a config edit (§10.2). |
| 4 | The exception queue is a manual step: the user works flagged items in Excel with Copilot | §2, §3.3, §6 Q1 | **No LLM is called anywhere at runtime.** v1 is 100% deterministic. Tab 3 becomes a two-way handoff: the engine emits flagged items with stable `item_id`s; the user fills in resolutions in Excel; `recon ingest-review` reads them back (§14). |
| 5 | Email delivery is not important yet | §3.5, §6 Q3 | Deferred. v1 writes the workbook to the output folder and stops. No mail scope, no SMTP. |
| 6 | First version is BBVA only | §3.2, §5 Phase 6, §6 Q4 | One bank plug-in (`bbva`). The registry and interface exist so a second bank is additive, but nothing non-BBVA is built. |

**Net effect:** both LLM boxes in the design spec's architecture diagram leave the runtime.
Design-spec open questions 1, 2 and 3 are answered (no runtime LLM vendor needed; no
document-AI fallback; delivery deferred); question 4 is moot for v1.

The consequence is worth stating plainly, because it is the main quality argument for v1:
**the pipeline is fully reproducible.** Same inputs ⇒ byte-identical outputs (modulo one run
timestamp). Every match is attributable to a numbered rule. That is a stronger audit position
than the design spec's v0.2 shape, not a weaker one.

---

## 2. Scope

### In scope (v1)
- CONTPAQi *Movimientos, Auxiliares del Catálogo* Excel export — ledger side, all `1112-*` Bancos sub-accounts.
- BBVA MXN and USD statement PDFs — bank side.
- Deterministic two-way matching, per-account thresholds, N:1 grouping and bounded subset-sum.
- SQLite persistence of every ledger row, bank line, match and reconciling item.
- Automatic invariants replacing hand verification (§13).
- Five-sheet Excel workpaper identical in shape to the prototype (§15).
- One-time validation against June 2024's bookkeeper-built schedule, in a quarantined module (§17.3).
- Backfill Jan–Jun 2024 and forward monthly runs, with carry-forward (§12.4).
- Manual exception handoff and read-back (§14).

### Out of scope (v1)
Non-BBVA banks · any LLM/document-AI call · email or share-link delivery · Graph API auth ·
scheduling (run it by hand or add a cron entry; no scheduler code) · FX revaluation of the USD
account (the variance is reported, not translated — see §12.5) · writing back into CONTPAQi ·
a web UI.

### Non-goals, permanently
- Reading cell fill colour as a matching signal. The parser ignores fills entirely (design spec §0).
- Requiring a bookkeeper-built `Partidas en conciliación` block to exist. The engine derives
  everything itself; the schedule parser is validation-only and deletable.

---

## 3. Output contract

The deliverable is one `.xlsx` per entity per period, with exactly these sheets and names
(from the prototype):

| Sheet | Rows | Source query |
|---|---|---|
| `Cover` | narrative | run metadata + materiality + scope counts |
| `1. Reconciliation Summary` | one per in-scope account + total + out-of-scope block | `account_period` aggregate |
| `2. Item Validation Report` | one per reconciling item | `reconciling_items WHERE status != 'resolved'` |
| `3. Investigation Register` | one per flagged item + one per opening variance + structural anomalies | `reconciling_items WHERE status='flagged'` ∪ `account_period` variances ∪ `anomalies` |
| `4. Proposed Adjusting Entries` | Dr/Cr pairs | quantified, unblocked bank-side items (§15.5) |

Column layouts are pinned in §15. The prototype's live formulas are reproduced as formulas,
not baked values, so a reviewer can trace arithmetic in Excel.

---

## 4. Architecture (v1)

```
 OneDrive sync folder        Parse                     Reconcile                     Output
 ┌──────────────────┐   ┌───────────────────┐   ┌───────────────────────┐   ┌────────────────┐
 │ ledger/*.xlsx     │──▶│ ledger_contpaq.py │──▶│ matching/engine.py     │──▶│ output/         │
 │ statements/<per>/ │   │ (openpyxl)        │   │  P1 exact               │   │ workbook.py     │
 │   *.pdf           │──▶│ bank/bbva.py      │──▶│  P2 exact+window        │   │ (openpyxl)      │
 └──────────────────┘   │ (pdfplumber +     │   │  P3 póliza group N:1    │   └────────────────┘
          ▲              │  banks/bbva.yml)  │   │  P4 subset-sum N:1      │            │
          │              └─────────┬─────────┘   │  P5 fuzzy               │            ▼
          │                        │ layout       │  P6 classify residue    │   ┌────────────────┐
          │                        │ unknown      └───────────┬───────────┘   │ OneDrive        │
          │                        ▼                          ▼                │ output folder   │
          │              ┌───────────────────┐    ┌───────────────────────┐   └────────────────┘
          │              │ diagnostics.py    │    │ store.py (SQLite)      │
          │              │ → bundle for      │    │ ledger_transactions    │
          │              │   Copilot         │    │ bank_transactions      │
          │              └───────────────────┘    │ matches                │
          │                                        │ reconciling_items      │
          │              ┌───────────────────┐    │ account_period         │
          └──────────────│ exceptions_io.py  │◀───┴───────────────────────┘
            reviewed     │ ingest-review     │  flagged items round-trip
            workbook     └───────────────────┘  through Excel + Copilot (manual)
```

No network calls. No non-determinism. Two human-in-the-loop points, both explicit and offline:
updating a layout template (§10.4) and resolving flagged items (§14).

---

## 5. Stack and repo layout

Python 3.11+. Dependencies kept deliberately small:

| Package | Use | Why not something else |
|---|---|---|
| `openpyxl` | read CONTPAQi export, write workbook | already the prototype's tool; formulas supported |
| `pdfplumber` | BBVA text + word coordinates | pure-Python, no Java/Ghostscript unlike `camelot`/`tabula` |
| `PyYAML` | config | — |
| `pydantic` v2 | validate config at load | catches a bad threshold at startup, not mid-run |
| `rapidfuzz` | description similarity | C-speed, no `python-Levenshtein` build pain |
| `pytest` | tests | — |

Stdlib `sqlite3`, `argparse`, `decimal`, `datetime`. **No ORM** — the schema is 6 tables and the
queries are hand-written; SQLAlchemy would be pure overhead here.

**All money is `decimal.Decimal`**, quantized to 2 places, parsed from the source string where
one exists. Never `float`. Floats are how you get `75495.79000000001` (which is literally what
cell `F1646` of the June ledger contains) and a residual that fails an exact-zero invariant.

```
FinancialAdvisor/
├── pyproject.toml
├── README.md
├── reconciliation-engine-spec.md            # design v0.2 (input, do not edit)
├── reconciliation-engine-impl-spec.md       # this file
├── config/
│   ├── entities/secontrol.yml               # §6.1 account inventory
│   ├── matching.yml                         # §6.2 thresholds
│   └── banks/bbva.yml                       # §10.2 layout template
├── src/recon/
│   ├── cli.py                               # §16
│   ├── config.py                            # pydantic models for the three files above
│   ├── models.py                            # LedgerTransaction, StatementLine, ReconcilingItem…
│   ├── store.py                             # §7 DDL + repository functions
│   ├── sources/{base,local,graph}.py        # §8  (graph.py is a stub in v1)
│   ├── parsers/
│   │   ├── ledger_contpaq.py                # §9
│   │   └── bank/{base,registry,bbva,diagnostics}.py   # §10
│   ├── matching/{engine,candidates,grouping,similarity}.py  # §11
│   ├── reconcile/{derive,invariants,carryforward,anomalies}.py  # §12–13
│   ├── output/{workbook,styles}.py          # §15
│   ├── exceptions_io.py                     # §14
│   └── validation/legacy_schedule.py        # §17.3 — quarantined, deletable
├── tests/
│   ├── fixtures/                            # trimmed xlsx + pdf, and expected JSON
│   └── test_*.py
└── data/                                    # gitignored
    ├── recon.sqlite
    ├── diagnostics/
    └── output/
```

`data/` and anything containing client figures is gitignored. The repo holds code and config only.

---

## 6. Configuration

### 6.1 `config/entities/secontrol.yml` — the Phase 0 inventory

This file is the answer to design-spec Phase 0 and removes the June pilot's "9 accounts with no
statement" ambiguity. **The ledger→bank mapping must be explicit here and must not be inferred by
regex from the account name.** Three counterexamples from the real file prove why:

- `1112-01-007-00` is named `Bancomer Cta 1160167315` — that is the tail of the 18-digit CLABE,
  not the 10-digit account number, which is `0116016731`.
- `1112-01-002-00` is named `Bancomer Cta 118892253 Ter 4396017` — two numbers, one of which is
  a POS terminal ID.
- `1112-01-010-00` is `Bancomer Cta. 3001239060 T.C.` — a credit-card sub-ledger, not a bank account.

Pre-populated below from the prototype's Reconciliation Summary and out-of-scope block:

```yaml
entity: "Secontrol Automatización SA de CV"
currency: MXN
materiality: 5000.00            # MXN, per the prototype Cover sheet
ledger_root: "1112"             # Bancos
ledger_rollup_accounts:         # parent rows to skip, never treated as accounts
  - "1112-00-000-00"

accounts:
  - ledger_account: "1112-01-001-00"
    label: "Bancomer 114091108"
    bank: bbva
    bank_account: "0114091108"
    currency: MXN
    in_scope: true
  - ledger_account: "1112-01-002-00"
    label: "Bancomer Cta 118892253 Ter 4396017"
    bank: bbva
    bank_account: "0118892253"
    pos_terminal: "4396017"
    currency: MXN
    in_scope: true
  - { ledger_account: "1112-01-003-00", bank: bbva, bank_account: "0118892717", pos_terminal: "4396025", currency: MXN, in_scope: true }
  - { ledger_account: "1112-01-004-00", bank: bbva, bank_account: "0118893314", currency: MXN, in_scope: true }
  - { ledger_account: "1112-01-005-00", bank: bbva, bank_account: "0192251736", currency: MXN, in_scope: true }
  - { ledger_account: "1112-01-006-00", bank: bbva, bank_account: "0114232658", currency: MXN, in_scope: true }
  - ledger_account: "1112-01-007-00"
    bank: bbva
    bank_account: "0116016731"
    clabe: "012691001160167315"
    currency: MXN
    in_scope: true
    note: >
      Ledger label carries the CLABE tail (1160167315), not the account number.
      Chart-of-accounts label correction pending — no dollar impact.
  - { ledger_account: "1112-01-009-00", bank: bbva, bank_account: "0118893187", pos_terminal: "4396035", currency: MXN, in_scope: true }
  - { ledger_account: "1112-01-011-00", bank: bbva, bank_account: "0118892830", pos_terminal: "4396028", currency: MXN, in_scope: true }
  - { ledger_account: "1112-01-012-00", bank: bbva, bank_account: "0116016790", currency: MXN, in_scope: true }
  - { ledger_account: "1112-01-013-00", bank: bbva, bank_account: "0118893039", pos_terminal: "4396047", currency: MXN, in_scope: true }
  - { ledger_account: "1112-01-016-00", bank: bbva, bank_account: "0122211882", pos_terminal: "4726658", currency: MXN, in_scope: true, dormant: true }
  - { ledger_account: "1112-01-017-00", bank: bbva, bank_account: "0120444995", pos_terminal: "4524119", currency: MXN, in_scope: true }
  - { ledger_account: "1112-02-001-00", bank: bbva, bank_account: "0107961006", currency: USD, in_scope: true }

  # out of scope — carried explicitly so the engine reports them rather than silently ignoring
  - { ledger_account: "1112-01-008-00", in_scope: false, reason: "No statement provided (last-4 3595)." }
  - { ledger_account: "1112-01-010-00", in_scope: false, reason: "Credit-card sub-ledger (T.C.), not a checking account." }
  - { ledger_account: "1112-01-014-00", in_scope: false, reason: "Unnamed/unused sub-account." }
  - { ledger_account: "1112-01-015-00", in_scope: false, reason: "Bancomer 12360601404 Tulum — no statement provided." }
  - { ledger_account: "1112-01-018-00", in_scope: false, reason: "Unnamed/unused sub-account." }
  - { ledger_account: "1112-01-019-00", in_scope: false, reason: "Banco Afirme — non-BBVA, out of scope in v1." }
  - { ledger_account: "1112-02-096-00", in_scope: false, reason: "Suspense/'Complementaria' memo account, not a bank account." }

# Dr accounts used by §15.5 when proposing entries. Placeholders — Kai to confirm (§19).
charge_account_map:
  bank_commission:      "TBD-comisiones-bancarias"
  bank_commission_iva:  "TBD-iva-acreditable"
  loan_installment:     "TBD-prestamos-por-pagar"
```

Startup validation: every `in_scope: true` account must have `bank` + `bank_account`; every
`1112-*` account found in a parsed ledger must appear in this file or the run fails with
"unknown account — add to inventory". That failure is the point: a new sub-account must not
silently drop out of the reconciliation.

### 6.2 `config/matching.yml`

```yaml
defaults:
  date_window_days: 3
  amount_tolerance: 0.00
  description_min_similarity: 0.55
  min_score_margin: 0.15          # best candidate must beat runner-up by this, else ambiguous
  subset_sum:
    max_pool: 24                  # candidate ledger items considered
    max_subset_size: 4
    require_unique_solution: true
  pos_batch:                      # corte de caja -> aggregated terminal settlement (pass 4)
    enabled: true
    settlement_lag_days: 3        # one-sided: settlement follows the cut. 3 covers Fri -> Mon.
    max_bank_lines: 4             # one credit per card product; 4 sufficed for all of June 2024

accounts:
  # POS-terminal-heavy accounts: bank prints only a generic V42/V45 code, so description
  # similarity is near-useless; compensate with a tight date window and zero amount tolerance.
  "1112-01-013-00":
    date_window_days: 1
    amount_tolerance: 0.00
    description_min_similarity: 0.20
  "1112-01-002-00": { description_min_similarity: 0.20 }
  "1112-01-003-00": { description_min_similarity: 0.20 }
  "1112-01-009-00": { description_min_similarity: 0.20 }
  # June 2024 showed a 9-day date-tag lag on this account's deposits.
  "1112-01-006-00":
    date_window_days: 10
    description_min_similarity: 0.60
```

Per-account values override defaults key-by-key. Thresholds are **inputs to be tuned during
Phase 2 against June 2024**, not final values; the file is the tuning surface.

---

## 7. Data model (SQLite)

```sql
PRAGMA journal_mode = WAL;

-- one row per pipeline execution; every derived row is attributable to a run
CREATE TABLE runs (
  run_id            TEXT PRIMARY KEY,        -- ULID
  started_at        TEXT NOT NULL,
  entity            TEXT NOT NULL,
  period            TEXT NOT NULL,           -- 'YYYY-MM'
  ledger_file       TEXT NOT NULL,
  ledger_sha256     TEXT NOT NULL,
  code_version      TEXT NOT NULL,           -- git describe
  config_sha256     TEXT NOT NULL,
  status            TEXT NOT NULL            -- ok | failed_invariant | failed_parse
);

CREATE TABLE ledger_transactions (
  id                TEXT PRIMARY KEY,        -- §11.1 deterministic hash
  run_id            TEXT NOT NULL REFERENCES runs(run_id),
  entity            TEXT NOT NULL,
  period            TEXT NOT NULL,
  ledger_account    TEXT NOT NULL,
  sheet             TEXT NOT NULL,           -- '06-24'
  row_no            INTEGER NOT NULL,        -- worksheet row; the prototype's "Ledger row N"
  txn_date          TEXT NOT NULL,           -- ISO
  tipo              TEXT,                    -- Diario | Egresos | Ingresos | …
  poliza            TEXT,                    -- 'Número' column
  concepto          TEXT,
  referencia        TEXT,
  cargo             TEXT NOT NULL,           -- Decimal as string, '0.00' if blank
  abono             TEXT NOT NULL,
  saldo             TEXT,                    -- running balance as printed
  direction         TEXT NOT NULL,           -- inflow | outflow   (§11.1)
  amount            TEXT NOT NULL,           -- abs(cargo) or abs(abono)
  UNIQUE (entity, period, ledger_account, sheet, row_no)
);

CREATE TABLE bank_transactions (
  id                TEXT PRIMARY KEY,
  run_id            TEXT NOT NULL REFERENCES runs(run_id),
  entity            TEXT NOT NULL,
  period            TEXT NOT NULL,
  bank_name         TEXT NOT NULL,
  bank_account      TEXT NOT NULL,
  statement_file    TEXT NOT NULL,
  statement_sha256  TEXT NOT NULL,
  page_no           INTEGER NOT NULL,
  line_no           INTEGER NOT NULL,        -- 1-based within the statement
  oper_date         TEXT NOT NULL,           -- 'FECHA OPER'
  liq_date          TEXT,                    -- 'FECHA LIQ' where printed
  code              TEXT,                    -- 'V42', 'H09', 'T20', …
  description       TEXT,
  reference         TEXT,
  cargo             TEXT NOT NULL,           -- bank CARGO = money OUT of the account
  abono             TEXT NOT NULL,           -- bank ABONO = money IN
  running_balance   TEXT,
  direction         TEXT NOT NULL,           -- inflow (abono) | outflow (cargo)  (§11.1)
  amount            TEXT NOT NULL,
  UNIQUE (entity, period, bank_account, statement_sha256, line_no)
);

-- a match is N ledger rows ↔ M bank lines (M is 1 in every v1 pass, N may be >1)
CREATE TABLE matches (
  match_id          TEXT PRIMARY KEY,
  run_id            TEXT NOT NULL REFERENCES runs(run_id),
  entity            TEXT NOT NULL,
  period            TEXT NOT NULL,
  ledger_account    TEXT NOT NULL,
  direction         TEXT NOT NULL,
  pass_no           INTEGER NOT NULL,        -- 1..6, the rule that made the match
  match_method      TEXT NOT NULL,           -- exact | exact_dated | group | pos_batch | subset | fuzzy | manual
  match_confidence  TEXT,                    -- NULL for passes 1–5
  ledger_amount     TEXT NOT NULL,
  bank_amount       TEXT NOT NULL,
  amount_delta      TEXT NOT NULL,           -- ledger − bank; non-zero spawns an amount_variance item
  date_delta_days   INTEGER NOT NULL,
  rule_note         TEXT NOT NULL,           -- one-line human-readable rule, for the workpaper
  evidence_json     TEXT NOT NULL            -- verbatim ledger rows + bank lines matched
);

CREATE TABLE match_members (
  match_id          TEXT NOT NULL REFERENCES matches(match_id),
  side              TEXT NOT NULL,           -- ledger | bank
  txn_id            TEXT NOT NULL,           -- ledger_transactions.id or bank_transactions.id
  PRIMARY KEY (match_id, side, txn_id)
);
CREATE UNIQUE INDEX ux_member_exclusive ON match_members (side, txn_id);  -- enforces I6

CREATE TABLE reconciling_items (
  id                  TEXT PRIMARY KEY,      -- STABLE across reruns; §11.1
  run_id              TEXT NOT NULL REFERENCES runs(run_id),
  entity              TEXT NOT NULL,
  ledger_account      TEXT NOT NULL,
  bank_account        TEXT,
  bank_name           TEXT,
  period              TEXT NOT NULL,
  side                TEXT NOT NULL,         -- ledger_outstanding | bank_unbooked | amount_variance | opening_variance
  direction           TEXT NOT NULL,         -- inflow | outflow
  category            TEXT NOT NULL,         -- §12.2 taxonomy
  amount              TEXT NOT NULL,
  txn_date            TEXT,
  description         TEXT,
  source_ref          TEXT NOT NULL,         -- 'ledger row 218' | 'stmt 0114091108 p2 l14'
  match_method        TEXT,
  match_confidence    TEXT,
  status              TEXT NOT NULL,         -- outstanding | flagged | resolved | matched
  first_seen_period   TEXT NOT NULL,
  periods_open        INTEGER NOT NULL DEFAULT 1,
  resolved_in_period  TEXT,
  resolution          TEXT,                  -- written by ingest-review (§14)
  resolved_by         TEXT,                  -- 'user' | 'auto_carryforward'
  evidence_json       TEXT NOT NULL,
  note                TEXT
);
CREATE INDEX ix_items_acct_period ON reconciling_items (ledger_account, period, status);

CREATE TABLE account_period (               -- Tab 1, one row per account per period
  entity              TEXT NOT NULL,
  ledger_account      TEXT NOT NULL,
  period              TEXT NOT NULL,
  run_id              TEXT NOT NULL REFERENCES runs(run_id),
  ledger_open         TEXT NOT NULL,
  ledger_cargos       TEXT NOT NULL,
  ledger_abonos       TEXT NOT NULL,
  ledger_close        TEXT NOT NULL,
  bank_open           TEXT,
  bank_abonos         TEXT,                  -- depósitos
  bank_cargos         TEXT,                  -- retiros
  bank_close          TEXT,
  opening_variance    TEXT,                  -- ledger_open − bank_open
  outstanding_inflow  TEXT NOT NULL,         -- deposits in transit
  outstanding_outflow TEXT NOT NULL,         -- outstanding payments
  unbooked_inflow     TEXT NOT NULL,
  unbooked_outflow    TEXT NOT NULL,
  adjusted_ledger     TEXT NOT NULL,
  adjusted_bank       TEXT NOT NULL,
  closure_residual    TEXT NOT NULL,         -- must be 0.00 — §13 I5
  status_note         TEXT NOT NULL,
  PRIMARY KEY (entity, ledger_account, period)
);

CREATE TABLE anomalies (                     -- structural findings for Tab 3
  id            TEXT PRIMARY KEY,
  run_id        TEXT NOT NULL REFERENCES runs(run_id),
  period        TEXT NOT NULL,
  ledger_account TEXT,
  kind          TEXT NOT NULL,              -- §12.6
  amount        TEXT,
  detail        TEXT NOT NULL,
  docs_needed   TEXT,
  conclusion    TEXT
);

CREATE TABLE bank_format_registry (
  bank_name             TEXT PRIMARY KEY,
  parser_version        TEXT NOT NULL,
  template_sha256       TEXT NOT NULL,       -- of banks/<bank>.yml
  template_source       TEXT NOT NULL,       -- 'deterministic' | 'copilot_assisted_confirmed'
  last_verified_period  TEXT NOT NULL
);
```

Deviations from design-spec §4, and why: `ledger_transactions` / `bank_transactions` /
`matches` are added because the design spec's single `reconciling_items` table stores only the
*residue*, and the invariants in §13 need the full populations on both sides to assert
completeness and exclusivity. `runs` is added so a workpaper is reproducible from recorded
input hashes. `account_period` is added because Tab 1 is a balance-level view that is not
derivable from `reconciling_items` alone (it needs opening balances and period flows).

---

## 8. Source access

### 8.1 Recommendation: use the local OneDrive sync folder, not the Graph API

The design spec (§3.1) assumed MS Graph + MSAL device-code flow. Given clarification 1 — the
deployment machine has the OneDrive account **signed in** — Graph is the wrong tool:

| | Local sync folder | Graph API |
|---|---|---|
| Setup | none | Entra app registration, client ID, delegated scopes, consent |
| Auth code | none | MSAL, device-code flow, token cache, refresh handling, expiry failures |
| Failure modes | file missing | + token expired, consent revoked, throttling, network |
| Offline | works | fails |
| Testing | a temp directory | needs mocks or a live tenant |
| Files it can reach | anything OneDrive has synced | anything in the drive |

The only real advantage of Graph is reaching files that are *not* synced locally (e.g. an
"online-only" folder). If that turns out to matter, the fix is to mark the folder
"Always keep on this device" in OneDrive — a two-click setting — rather than several hundred
lines of auth code and a credential to rotate.

So: `LocalFolderSource` is the v1 implementation. `sources/graph.py` exists as a documented stub
implementing the same interface, to be filled in only if a real need appears.

```python
class FileSource(Protocol):
    def list(self, rel_dir: str, pattern: str) -> list[SourceFile]: ...
    def open(self, f: SourceFile) -> BinaryIO: ...
    def put(self, rel_path: str, data: bytes) -> SourceFile: ...

@dataclass(frozen=True)
class SourceFile:
    rel_path: str
    name: str
    size: int
    modified: datetime
    sha256: str          # computed on read; recorded in runs/bank_transactions
```

### 8.2 Expected folder layout

Configured by one root path (CLI `--root`, or `RECON_ROOT` env var), pointing at the synced
OneDrive folder:

```
<root>/
├── ledger/
│   └── <anything>.xlsx            # CONTPAQi exports; sheet per period, e.g. '06-24'
├── statements/
│   └── 2024-06/
│       └── *.pdf                  # BBVA statements, any filename
└── output/
    └── SECONTROL_Bank_Reconciliation_<YYYY-MM>.xlsx
```

**Statement PDFs are identified by content, never by filename.** The parser reads the
`No. de Cuenta` field from each PDF and maps it to a ledger account via §6.1. This directly
implements the accounting skill's own pitfall note ("don't assume statement pages belong to the
account you expect") and means the client can name files however they like. A PDF whose account
number is not in the inventory is a hard error listing the unmatched number.

Multiple accounts in one PDF, and one account split across PDFs, are both handled: lines are
grouped by the account number in scope at the page where they appear.

---

## 9. Ledger parser — `parsers/ledger_contpaq.py`

The grammar below was read out of all six 2024 sheets of the real export
(`01-24`…`06-24`) and is stable across all of them.

### 9.1 Sheet-level

| Location | Content | Use |
|---|---|---|
| `D1` | `SECONTROL AUTOMATIZACION` | entity cross-check (warn on mismatch) |
| `D2` | `Movimientos, Auxiliares del Catálogo` | **format assertion** — abort if absent |
| `D3` | `del 01/Jun/2024 al 30/Jun/2024` | authoritative period start/end |
| `D4` | `Moneda: Peso Mexicano` | ledger currency |
| `H2` | `Fecha: 13/Ago/2026` | export date (metadata only) |
| row 7 | `Fecha │ Tipo │ Número │ Concepto │ Referencia │ Cargos │ Abonos │ Saldo` | column map |

`A1`/`A3` are populated in some sheets and empty in others (`06-24` has `A1='CONTPAQ i'`,
`01-24` does not) — so **locate the header row by scanning for the row whose cells match the
eight header labels**, do not hardcode row 7. Column letters are then read from that row, not
assumed, per the accounting skill's instruction that CONTPAQi layouts vary by client config.

Period is taken from `D3` and must agree with the sheet name (`06-24` ⇒ `2024-06`); disagreement
is a hard error.

### 9.2 Row grammar

Scanning downward from the header row, classify each row:

| Row kind | Test | Handling |
|---|---|---|
| **account header** | col A matches `^\d{4}-\d{2}-\d{3}-\d{2}$` | opens a new account block. col B = label, col G = `Saldo inicial :`, col H = **ledger opening balance**. Col I, if present, is a *manual annotation* — see §9.4. |
| **transaction** | col A matches `^\d{2}/(Ene|Feb|Mar|Abr|May|Jun|Jul|Ago|Sep|Oct|Nov|Dic)/\d{4}$` | emit a `LedgerTransaction`. Record the worksheet row number as `row_no`. |
| **account total** | col E starts with `Total` (`'Total:'`) | closes the block. col F = Σ cargos, col G = Σ abonos, col H = **ledger closing balance**. |
| **grand total** | col E is `Total Bancos :` or `T o t a l:` | end of data; used for a whole-sheet cross-check. |
| **blank / filler** | all empty, or col A is a single space | skip |
| **anything after the block's `Total:` row and before the next account header** | — | the bookkeeper's embedded schedule; **skipped by the production parser** (§9.5) |

Notes that matter for correctness:

- `1112-00-000-00 Bancos` is a **roll-up parent**, not an account. Its `Saldo inicial` is the sum
  of all children (`11163539.17` in June). Skip it via `ledger_rollup_accounts` in §6.1.
- **An account block may have no `Total:` row at all.** In `03-24`, `1112-01-016-00` has none.
  Fall back to: closing balance = opening balance, Σ cargos = Σ abonos = 0 — and assert that the
  block contained zero transaction rows. If it had transactions but no `Total:` row, that is a
  hard parse error, not a silent fallback.
- A zero-activity account may instead have a `Total:` row with `0 / 0` (June's `1112-01-008-00`).
  Both shapes are valid.
- Dates use **Spanish month abbreviations**; build an explicit `Ene…Dic` map. Do not rely on
  locale.
- `Concepto` values carry a leading space (`' Trf3747417022 Surtidora…'`) — strip for matching,
  keep verbatim in `evidence_json`.
- Amounts arrive as floats from openpyxl and are lossy (`75495.79000000001`). Convert via
  `Decimal(str(round(v, 2)))` and quantize to `0.01`. Where openpyxl exposes the cached string,
  prefer it.
- `direction`: the ledger's Bancos accounts are **assets**, so `Cargos` increase the balance
  (a deposit ⇒ `inflow`) and `Abonos` decrease it (a payment ⇒ `outflow`). Verified against the
  running balance: row 12 of `06-24` has `Abonos 2,746.49` and the balance falls from
  2,380,012.58 to 2,377,266.09.
- Both `Cargos` and `Abonos` populated on one row: not observed; treat as a hard parse error.

### 9.3 Cell fill colours

Ignored. `openpyxl` is loaded with default settings and fills are never read in the production
path. Only `validation/legacy_schedule.py` may read them, for the one-time June comparison.

### 9.4 Column I is a manual annotation and must not be trusted

`06-24!I11 = 98971.26` is account 001's **bank** opening balance, and `06-24!I227 = 346100.50`
its bank closing balance — hand-typed by the bookkeeper next to the CONTPAQi output. Counts of
these annotations across 2024: `01-24` 0, `02-24` 3, `03-24` 3, `04-24` 12, `05-24` 10,
`06-24` 1.

They are sporadic, unlabelled, and outside the CONTPAQi column range. **The engine never
sources a bank balance from the ledger workbook.** Bank open/close come from the statement PDF,
always. Column I is captured into `evidence_json` as `manual_annotation_col_i` for
cross-checking only, and a mismatch against the statement raises an anomaly rather than
changing any figure.

### 9.5 The embedded reconciliation schedule

Rows between a block's `Total:` row and the next account header are the bookkeeper's manual
schedule. In June 2024 five accounts have one (001, 002, 003, 006, 013); in January 2024
seventeen do. Its shape (account 001, rows 228–249):

```
228  D='Partidas en conciliación contabilidad'   F=812366.41  G=10582.76
229  D='Saldo en contabilidad conciliado'        F=2756446.19 G=3147641.30
231  D='Saldo en bancos'                         F=3394770.89 G=3147641.65
232  D='Movim de periodos anteriores (…)'        F=11044
234  A=2024-06-27  D='Dep erroneo'               F=468750
…    (one row per bank-side unbooked item; dates here are real datetimes, not CONTPAQi strings)
247  D='Suma Partidas en Conciliación'           F=638324.33  G=0
248  D='Saldo en Bancos conciliado'              F=2756446.56 G=3147641.65
249                                               F=0.37       G=-0.35   ← the bookkeeper's own tie-out slack
```

The production parser **skips this region entirely**. It is parsed only by
`validation/legacy_schedule.py`, only when `--validate-against-schedule` is passed, only for
the June 2024 acceptance test (§17.3). That module is designed to be deleted; nothing else
imports it.

---

## 10. Bank statement parser — `parsers/bank/`

### 10.1 Interface

```python
@dataclass(frozen=True)
class StatementLine:
    page_no: int
    line_no: int
    oper_date: date
    liq_date: date | None
    code: str | None          # BBVA operation code, e.g. 'V42'
    description: str
    reference: str | None
    cargo: Decimal            # money OUT of the account
    abono: Decimal            # money IN
    running_balance: Decimal | None

@dataclass(frozen=True)
class BankStatement:
    bank_name: str
    bank_account: str         # from the PDF's 'No. de Cuenta'
    clabe: str | None
    currency: str             # MXN | USD
    period_start: date
    period_end: date
    opening_balance: Decimal  # 'Saldo Anterior'
    closing_balance: Decimal  # 'Saldo al …'
    total_abonos: Decimal     # 'Depósitos / Abonos' header total
    total_cargos: Decimal     # 'Retiros / Cargos' header total
    lines: list[StatementLine]
    source_file: str
    source_sha256: str
    parser_version: str

class BankParser(Protocol):
    bank_name: str
    def sniff(self, pdf_bytes: bytes) -> bool: ...          # is this my bank's layout?
    def parse(self, pdf_bytes: bytes) -> list[BankStatement]: ...
```

Critically, `opening_balance`, `closing_balance`, `total_abonos` and `total_cargos` are read
from the statement's own printed summary — they are **independent facts to tie out against**,
not sums of the parsed lines. That is what makes invariant I3 (§13) meaningful: if the printed
total and the line sum disagree, lines were missed.

**Polarity.** The bank statement is written from the bank's point of view, so its `CARGO`/`ABONO`
are the *opposite* of the ledger's for the same event. Mapping, stated once and encoded in one
place:

| Event | Ledger | Bank statement | canonical `direction` |
|---|---|---|---|
| money into the account | `Cargos` | `ABONO` / Depósito | `inflow` |
| money out of the account | `Abonos` | `CARGO` / Retiro | `outflow` |

Matching compares canonical `direction` only. Nothing downstream of the parsers touches the
raw cargo/abono columns for matching purposes.

### 10.2 BBVA layout as YAML, not code

So that a layout change can be fixed by editing config (clarification 3), `banks/bbva.yml`
carries the markers and column geometry, and `bbva.py` carries only generic logic driven by it:

```yaml
bank_name: bbva
parser_version: "1.0.0"

sniff_any:                         # this layout is BBVA if any of these appear on page 1
  - "BBVA"
  - "BBVA BANCOMER"

header_fields:                     # label → regex capturing the value
  bank_account:  'No\.?\s*de\s*Cuenta\s*[:\s]*([0-9]{10,})'
  clabe:         'CLABE\s*[:\s]*([0-9]{18})'
  period:        'Per[ií]odo\s+DEL\s+(\d{2}/\d{2}/\d{4})\s+AL\s+(\d{2}/\d{2}/\d{4})'
  opening:       'Saldo\s+Anterior\s*\$?\s*([\d,]+\.\d{2})'
  closing:       'Saldo\s+(?:Final|al)\s*\S*\s*\$?\s*([\d,]+\.\d{2})'
  total_abonos:  'Dep[oó]sitos\s*/?\s*Abonos\s*\$?\s*([\d,]+\.\d{2})'
  total_cargos:  'Retiros\s*/?\s*Cargos\s*\$?\s*([\d,]+\.\d{2})'
  currency_usd:  'MONEDA\s+DOLARES'          # presence ⇒ USD

table:
  start_marker: "Detalle de Movimientos Realizados"
  end_markers:
    - "Total de Movimientos"
    - "Glosario"
    - "Estimado Cliente"
  # x-ranges in pdfplumber points, calibrated per §10.3
  columns:
    oper_date:       [ 30,  75]
    liq_date:        [ 75, 115]
    code:            [115, 145]
    description:     [145, 400]
    reference:       [400, 455]
    cargo:           [455, 515]
    abono:           [515, 575]
    running_balance: [575, 640]
  row_anchor: 'oper_date'          # a new line begins where a date appears in this column
  date_format: "%d/%b"             # BBVA prints DD/MMM; year comes from the period
  month_names_es: [ENE, FEB, MAR, ABR, MAY, JUN, JUL, AGO, SEP, OCT, NOV, DIC]

wrapped_description: true          # continuation lines with no date append to the prior line

known_codes:                       # observed in the June 2024 pass; used by §12.2 categorisation
  V42: { label: "VENTAS DEBITO",                    category: pos_settlement }
  V45: { label: "VENTAS CREDITO",                   category: pos_settlement }
  H09: { label: "COBRO AUTOMATICO RECIBO",          category: loan_installment }
  H86: { label: "REEMBOLSO PAGO DE CREDITO",        category: loan_reimbursement }
  T20: { label: "SPEI ENVIADO",                     category: transfer_out }
  T17: { label: "SPEI RECIBIDO",                    category: transfer_in }
description_categories:            # fallback when the code is absent/unknown
  - { match: 'COMISION',            category: bank_commission }
  - { match: 'I\.?V\.?A\.?',        category: bank_commission_iva }
  - { match: 'TERMINALES PUNTO DE VENTA', category: pos_settlement }
  - { match: 'SPEI\s+RECIBIDO',     category: transfer_in }
  - { match: 'SPEI\s+ENVIADO',      category: transfer_out }
```

The codes, labels and marker strings above are all lifted from evidence text in the prototype
workbook (`V42 VENTAS DEBITO`, `H09 COBRO AUTOMATICO RECIBO / PREST. 9818644907`,
`H86 REEMBOLSO PAGO DE CREDITO`, `SPEI RECIBIDOSTP`, `SPEI ENVIADO STP`,
`TERMINALES PUNTO DE VENTA Ref. 144396047`, `Información Financiera MONEDA DOLARES`,
`No. de Cuenta 0116016731 / CLABE 012691001160167315`) plus the start/end markers named in
design spec §3.2.

### 10.3 Calibration — the one thing that needs a real PDF

**No BBVA PDF was available while writing this spec** (none exists in the repo or anywhere under
`~/Documents/Personal`). The `columns` x-ranges above are therefore placeholders. The first
build task in Phase 1 is:

1. `recon diagnose-statement <pdf>` dumps, per page, every `pdfplumber` word with its `x0/x1/top`.
2. Cluster word `x0` values to recover true column boundaries; write them into `banks/bbva.yml`.
3. Verify with §17.2 — the parsed statement must reproduce the printed `Saldo Anterior`,
   `Saldo Final`, `Depósitos` and `Retiros` totals **and** the per-account figures already known
   from the prototype (§17.2 table). Those figures are known now, before the PDFs are in hand,
   which means the calibration has an objective pass/fail test from day one.

Everything else in the pipeline can be built and tested against synthetic `BankStatement`
objects before a single PDF is available. Nothing is blocked on this.

### 10.4 Unrecognized layout ⇒ diagnostic bundle, never a guess

Per clarification 3, there is no automated fallback. If `sniff()` fails for every registered
parser, or a registered parser's post-parse invariants fail (I3), the run **stops** for that
statement and writes `data/diagnostics/<period>/<pdf-stem>/`:

```
raw_text_page01.txt …           full text per page
words_page01.csv                text, x0, x1, top, bottom — the calibration input
tables_page01.txt               pdfplumber extract_table() attempts
markers_found.json              which header_fields regexes hit, which missed
column_histogram.txt            x0 clusters, i.e. suggested column boundaries
draft_template.yml              banks/<bank>.yml skeleton pre-filled with what was detected
COPILOT_PROMPT.md               ready-to-paste instructions + what to fill in and how to verify
failure_report.md               which assertion failed, with the offending page and line
```

`COPILOT_PROMPT.md` is a fixed template that asks Copilot to complete `draft_template.yml` from
`words_page01.csv` and `raw_text_page01.txt`, and states the acceptance test the result must
pass. Promotion of the edited template requires:

1. copy the completed YAML to `config/banks/<bank>.yml`, bump `parser_version`;
2. add the PDF (redacted if needed) as a regression fixture with its expected `BankStatement`;
3. `recon verify-template <bank>` — reruns every fixture for that bank plus I3;
4. on success, `bank_format_registry` is updated with `template_source='copilot_assisted_confirmed'`
   and `last_verified_period`.

This is the design spec's "verify once, then trust" gate, with a human confirming before a new
layout can produce a match — just without the document-AI call.

---

## 11. Matching engine — `matching/`

### 11.1 Normalization and identity

Canonical `direction` per §10.1. `amount = abs(cargo or abono)` as `Decimal`.

Normalized description for similarity: uppercase, strip accents, collapse whitespace, drop
punctuation, and **strip high-cardinality tokens that defeat similarity** — long digit runs
(SPEI/`Trf` reference numbers), `F/…` invoice refs, and the literal words `TRF`, `SPEI`,
`DEPOSITO`, `PAGO`. What is left is mostly counterparty name, which is the part with signal.
(Cf. `Trf9359103819 Ame de Quintana Roo SA F/202147` → `AME DE QUINTANA ROO SA`.)

IDs must be **stable across reruns** so Tab 3 resolutions survive a re-run:

```
ledger_txn.id  = sha256(entity|period|ledger_account|sheet|row_no)[:16]
bank_txn.id    = sha256(entity|bank_account|statement_sha256|line_no)[:16]
item.id        = sha256(entity|ledger_account|side|direction|amount|txn_date|source_ref)[:16]
```

Note `item.id` deliberately excludes `period` and `run_id`: a deposit in transit carried from
June into July keeps the same id, which is what makes carry-forward (§12.4) and review
read-back (§14) work.

### 11.2 Passes

Run per `(ledger_account, direction)`, in order. Each pass consumes only items still unmatched,
and a consumed item is never reconsidered. Iteration order is `(txn_date, amount, row_no/line_no)`
throughout, so the result is deterministic.

| # | Pass | Rule | `match_method` |
|---|---|---|---|
| 1 | **exact** | same date, same amount to the cent, exactly one candidate on each side | `exact` |
| 2 | **exact + window** | same amount to the cent, `|Δdays| ≤ date_window_days`, unique on both sides. Tie-break on smallest `|Δdays|`; still tied ⇒ ambiguous, defer to pass 7 | `exact_dated` |
| 3 | **póliza group N:1** | group unmatched ledger rows by `(txn_date, tipo, poliza, direction)`; if a group's sum equals an unmatched bank line to the cent within the window ⇒ match the whole group | `group` |
| 4 | **POS batch N:M** | POS accounts only (`pos_terminal` set), inflow only. Group unmatched ledger rows by póliza as in pass 3; match the group total against the **unique** subset (≤ `max_bank_lines`) of unmatched bank credits that carry this account's terminal id and fall `0..settlement_lag_days` **after** the cut date | `pos_batch` |
| 5 | **subset-sum N:1** | for each remaining bank line, search unmatched ledger items in the window for a subset summing exactly to it. Bounded: pool ≤ `max_pool`, subset ≤ `max_subset_size`, and the solution must be **unique** — two distinct qualifying subsets ⇒ no match, ambiguity recorded | `subset` |
| 6 | **fuzzy** | `|Δamount| ≤ amount_tolerance` **and** `|Δdays| ≤ date_window_days` **and** `similarity ≥ description_min_similarity`. Score `= 0.5·amount_score + 0.2·date_score + 0.3·similarity`. Accept only if `best − runner_up ≥ min_score_margin` | `fuzzy` |
| 7 | **classify residue** | every still-unmatched item becomes a `reconciling_item`; items that had ≥2 plausible candidates get `status='flagged'` with the candidate list in `evidence_json` | — |

Design notes:

- **Pass 3 is grounded in the data, not invented.** CONTPAQi assigns one `Número` (póliza) to
  every line of a daily sales cut: `06-24` rows 216–225 are ten separate `Cargos` all with
  `Número = 135`, all dated 29/Jun, which the bank settles as a smaller number of POS batch
  lines. Grouping by póliza is the cheap, exact, explainable version of that.
- **Pass 4 is the only N:M pass, and the terminal id is what makes it safe.** A day's *corte de
  caja* is booked as one póliza of individual card sales but settles into the bank on a later
  banking day as one aggregate credit per card product, so neither side's row count matches the
  other's and passes 1–3 cannot close it. Calibrated on `1112-01-002-00` / terminal `4396017`:
  póliza 133 (11 rows, 27-Jun, 37,284.30) arrives 28-Jun as `V45` 9,722.47 + `V42` 27,561.83.
  Two constraints do the work. First, the bank side is filtered to credits stamped with this
  account's terminal — BBVA prints `Ref. 14<terminal>` — and that reference, *not* the operation
  code, is the dependable signal: on the calibration account 37 of 38 credits carry it across
  four codes (`V42`, `V45`, `I72`, `K54`), of which only the first two are in `known_codes`, so
  categorising by code would have skipped batches silently. Second, the lag is one-sided:
  settlement follows the cut and never precedes it, which kills the coincidences a symmetric
  window would admit. `settlement_lag_days: 3` because a Friday corte settles the following
  Monday (póliza 116, 07-Jun → 10-Jun). Raising `max_bank_lines` past 4 resolved no additional
  batch in June 2024, so the bound is free. Uniqueness is required exactly as in pass 5.
  The match is committed — it ties to the cent — but every account with a batch also gets a
  `pos_batch_aggregate` anomaly requesting the acquirer's settlement report, because arithmetic
  agreement is not the same as documentary proof.
- **Pass 4 runs after pass 3, not before pass 1.** Running it first would let it claim whole
  pólizas before any 1:1 pass fragments them (≈50% more batches on the June data), but pass 1
  builds its date/amount index from `L`/`B` rather than from the unmatched residue — it assumes
  it runs first — so hoisting pass 4 above it double-books items and trips I5/I6. Placing pass 4
  between 3 and 5 gets the protection that matters (a spurious ≤4-row subset-sum can no longer
  break up a terminal-confirmed batch) without that precondition. Fixing pass 1 to filter on
  `_unmatched` would unlock the reorder.
- **Pass 5 is the automation of what is already being done by hand.** The ledger workbook
  contains three scratch sheets — `Busqueda 37879.27`, `Busqueda 4069.87`, `Busqueda 26117.44` —
  each a manual subset-sum search for a target bank amount ("Búsqueda de importe objetivo",
  "Coincidencias exactas en Cargos/Abonos: 0", "Combinaciones mostradas: 8"). The bounds and the
  uniqueness requirement exist because unbounded subset-sum over a 250-row account will find
  spurious combinations; a non-unique solution is evidence of nothing and must not become a match.
- **Pass 6 must not silently absorb an amount difference.** If an accepted fuzzy match has
  `amount_delta ≠ 0`, the engine additionally emits a `side='amount_variance'` reconciling item
  for exactly that delta. Without this, invariant I5 stops being exactly zero and the closure
  test degrades into a tolerance check — which is precisely the weakness of the hand-built
  schedule (its own slack rows read `F249=0.37`, `G249=−0.35`). With `amount_tolerance: 0.00`
  as the default, this case does not arise at all; the rule exists so that raising the tolerance
  later stays safe.
- **Coincidental amount collisions are what pass 7 is for.** June 2024 has three ledger amounts
  ($6,780.93, $14,964.00, $7,212.18) that appear in account 001's bank statement attached to
  unrelated counterparties, and the Construbasco/Arteck case where $468,750.00 in one account
  and $364,583.33 in another share both counterparties. Under this design, an amount-only
  collision with a failing description check never becomes an `exact` match; it lands in Tab 3
  with both candidates recorded. That is the behaviour the pilot arrived at by hand.

### 11.3 Ambiguity record

A flagged item's `evidence_json` carries every considered candidate with its scores, so Tab 3
tells the user *why* it is open, not merely that it is:

```json
{"reason": "multiple_candidates",
 "pass": 2,
 "ledger": {"id": "…", "row_no": 203, "date": "2024-06-27", "amount": "468750.00",
            "concepto": "Trf9112862908 Proyects Arteck SA F/5434"},
 "candidates": [
   {"bank_txn_id": "…", "bank_account": "0114091108", "line_no": 141, "date": "2024-06-20",
    "amount": "468750.00", "description": "SPEI ENVIADO … CONSTRUBASCO SA DE CV",
    "amount_score": 1.0, "date_score": 0.0, "similarity": 0.11, "score": 0.533},
   {"bank_txn_id": "…", "bank_account": "0118892253", "line_no": 88, "date": "2024-06-27",
    "amount": "364583.33", "description": "SPEI ENVIADO STP … PROYECTS ARTECK SA DE CV",
    "amount_score": 0.0, "date_score": 1.0, "similarity": 0.86, "score": 0.458}],
 "margin": 0.075,
 "min_score_margin": 0.15}
```

Cross-account candidates are surfaced (as above) but **never matched** — a match is always
within one `(ledger_account, bank_account)` pair. A cross-account near-match raises an
`anomalies` row of kind `cross_account_candidate`.

---

## 12. Derivation

### 12.1 Balance-level (Tab 1), per account per period

```
ledger_open, ledger_cargos, ledger_abonos, ledger_close     ← ledger parser
bank_open,   bank_abonos,   bank_cargos,   bank_close        ← statement parser
opening_variance    = ledger_open − bank_open

outstanding_inflow  = Σ unmatched ledger inflow      (deposits in transit)
outstanding_outflow = Σ unmatched ledger outflow     (outstanding payments)
unbooked_inflow     = Σ unmatched bank  inflow       (bank deposits not booked)
unbooked_outflow    = Σ unmatched bank  outflow      (bank charges/debits not booked)

adjusted_ledger = ledger_close − outstanding_inflow + outstanding_outflow
adjusted_bank   = bank_close   − unbooked_inflow    + unbooked_outflow
closure_residual = (adjusted_ledger − adjusted_bank) − opening_variance
```

These are exactly the prototype's Tab 1 formulas (`K = D − H + I`, `L = F − J` where
`J = unbooked_inflow − unbooked_outflow`, and `M` testing `K − L ≈ G`), restated per-item so the
engine derives `H`, `I` and `J` itself instead of copying them from the bookkeeper's schedule.

**`closure_residual` is algebraically zero when matching is sound.** Substituting the flow
tie-outs `ledger_close = ledger_open + Σcargos − Σabonos` and
`bank_close = bank_open + Σabonos_bank − Σcargos_bank` gives

```
closure_residual = (matched_ledger_inflow − matched_bank_inflow)
                 − (matched_ledger_outflow − matched_bank_outflow)
```

which is 0 iff matched amounts agree side-by-side. So the prototype's `<100` tolerance is an
artifact of hand-built inputs; the engine asserts `|closure_residual| ≤ 0.01` and treats a
failure as an **engine bug**, not a business finding. This is the single most valuable invariant
in the system and the concrete replacement for hand verification.

### 12.2 Reconciling-item taxonomy

`side` × `category`, assigned deterministically:

| `side` | `category` | Assigned when |
|---|---|---|
| `ledger_outstanding` | `deposit_in_transit` | unmatched ledger inflow |
| `ledger_outstanding` | `outstanding_payment` | unmatched ledger outflow |
| `bank_unbooked` | `bank_commission` / `bank_commission_iva` / `loan_installment` / `pos_settlement` / `transfer_in` / `transfer_out` / `uncategorized` | unmatched bank line, from `known_codes` then `description_categories` (§10.2) |
| `amount_variance` | `fuzzy_match_delta` | accepted fuzzy match with non-zero delta (§11.2) |
| `opening_variance` | `carried_forward` | `opening_variance ≠ 0` at account level |

`uncategorized` is deliberately a real outcome. Per the accounting skill: an uncategorized
bucket is more honest than a wrong guess.

### 12.3 Materiality

`materiality: 5000.00` from §6.1, matching the prototype's Cover sheet. It **never suppresses**
an item — every item appears in Tab 2. It only drives a `Material?` column and Tab 3 ordering.

### 12.4 Carry-forward

At the start of a period-N run, load period N−1's `reconciling_items` with
`status='outstanding'`. Because `item.id` excludes period (§11.1):

- an item matched in period N ⇒ `status='resolved'`, `resolved_in_period=N`, `resolved_by='auto_carryforward'`;
- an item still unmatched ⇒ carried forward, `periods_open += 1`;
- `periods_open ≥ 3` ⇒ an `anomalies` row of kind `stale_outstanding_item` and a Tab 3 entry.

This directly implements design spec §3.4's carry-forward check, and is the Phase 3 exit test.

### 12.5 USD account (`1112-02-001-00` / bank `0107961006`)

The statement is USD (`Información Financiera MONEDA DOLARES`); the ledger carries the account
in the same peso column as every other. v1 does **not** translate. It reconciles the account in
its own units, reports the variance, and raises an `anomalies` row of kind `currency_mismatch`
stating that the variance may be FX translation rather than a reconciling difference — which is
exactly the prototype's conclusion. Matching for this account is restricted to passes 1–5
(exact amounts only); fuzzy matching across a currency boundary would be meaningless.

### 12.6 Structural anomalies (`anomalies.kind`)

Detected automatically, each becoming a Tab 3 row:

| kind | Test |
|---|---|
| `sign_divergence` | ledger close < 0 while bank close > 0 (or vice versa) for the whole period — caught `1112-01-012-00` in June |
| `label_mismatch` | ledger label's digits ≠ statement `No. de Cuenta`, but match the CLABE — caught `1112-01-007-00` |
| `currency_mismatch` | statement currency ≠ entity currency |
| `dormant_with_balance` | zero activity both sides, non-zero variance — caught `1112-01-016-00` at $13,000.01 |
| `unmatched_period_flow` | account's Σ matched outflow < bank Σ retiros by > materiality with no item explaining it — the $1,051.61 gap on `1112-01-006-00` |
| `impossible_date` | a parsed date outside the statement/ledger period |
| `cross_account_candidate` | best candidate for an item sits in a different account (§11.3) |
| `stale_outstanding_item` | `periods_open ≥ 3` |
| `pos_batch_aggregate` | one or more corte de caja pólizas were closed against aggregated terminal settlements (§11.2 pass 4). Requests the acquirer's batch report. Unlike the other kinds this is an evidence request on items that already tie to the cent, not an unresolved difference, so it is exempt from the §15.5 rule-4 block on proposed entries |
| `annotation_conflict` | ledger column I annotation ≠ statement balance (§9.4) |
| `unknown_ledger_account` | `1112-*` account absent from the inventory (fatal) |

Every one of these was a finding a human made by hand in the June pass. Encoding them is what
makes the engine's Tab 3 comparable to the prototype's.

---

## 13. Invariants — `reconcile/invariants.py`

Run every period. **Gate** = aborts the run; **report** = becomes a Tab 3 row.

| # | Invariant | Tolerance | Failure means | Action |
|---|---|---|---|---|
| I1 | `ledger_open + Σcargos − Σabonos = ledger_close`, per account | 0.01 | ledger parser bug | **gate** |
| I2 | parsed Σcargos/Σabonos = the block's `Total:` row F/G; last txn's `Saldo` = `ledger_close` | 0.01 | rows dropped or double-read | **gate** |
| I3 | `bank_open + Σabonos − Σcargos = bank_close`, **and** parsed line sums = the statement's printed `Depósitos`/`Retiros` totals, **and** each line's `running_balance` equals the prior balance ± the line amount | 0.01 | statement parser bug / missed lines | **gate**, plus diagnostic bundle (§10.4) |
| I4 | `Σ matched ledger inflow = Σ matched bank inflow`, same for outflow, after `amount_variance` items | 0.01 | matcher bug | **gate** |
| I5 | `closure_residual = 0` per account (§12.1) | 0.01 | matcher bug (implied by I4+I6+I7) | **gate** |
| I6 | no ledger row or bank line belongs to >1 match | exact | matcher bug | **gate** (also enforced by `ux_member_exclusive`) |
| I7 | every ledger row and bank line is either in a match or has a reconciling item | exact | items silently dropped | **gate** |
| I8 | every item `outstanding` in N−1 is `matched`, `resolved`, or carried with a reason in N | exact | carry-forward bug | **gate** |
| R1 | `opening_variance = 0` | 0.01 | genuine business finding | **report** |
| R2 | ledger Σ = the sheet's `Total Bancos :` grand total (June: 9,981,179.96 / 8,824,633.83 / 12,320,085.30) | 0.01 | an account block was skipped | **gate** |

I1 and I3 run **before** matching — a failure there means something was mis-read, not that the
business has a reconciling item (design spec §3.4). I4–I7 run after.

`recon run` exits non-zero on any gate failure and writes no workbook. A half-correct workpaper
is worse than none.

---

## 14. Exception handoff — `exceptions_io.py`

Per clarification 4, resolution is a manual step the user performs in Excel with Copilot. The
engine's job is to make that step cheap and to read the result back.

**Out:** Tab 3 is written with everything needed to work an item without opening the source
files — the ledger row verbatim, the bank line verbatim, every rejected candidate with its
scores, and the reason it is open. Two extra columns are added versus the prototype:

| Column | Content |
|---|---|
| `item_id` (hidden, col A) | the stable id from §11.1 — the read-back key |
| `Resolution (fill in)` | empty, for the user/Copilot |
| `Resolved?` | data-validated dropdown: `No` / `Yes` / `Not an item` |

**Back:** the user saves the edited file anywhere and runs

```
recon ingest-review data/output/SECONTROL_Bank_Reconciliation_2024-06_reviewed.xlsx
```

which reads `(item_id, Resolution, Resolved?)` and updates `reconciling_items.resolution`,
`.status`, `.resolved_by='user'`, `.resolved_in_period`. Rules:

- unknown `item_id` ⇒ warn and skip (never invent an item from a workbook);
- `Resolved? = Yes` requires a non-empty `Resolution` ⇒ else reject that row with a message;
- ingestion is idempotent — re-running it changes nothing;
- resolutions are never overwritten by a later `recon run`; a re-run re-derives items but
  re-attaches stored resolutions by `item_id`.

That last property is why `item.id` is a content hash and not a row number. It is also the whole
reason Tab 3 can be a working document rather than a read-only report.

---

## 15. Workbook writer — `output/workbook.py`

Sheets, names and column orders below are transcribed from the prototype. Formulas are written
as formulas.

### 15.1 `Cover`
Single column of narrative blocks: entity; period; `run_id`, code version and input SHA-256s;
scope (`N` accounts reconciled, `M` out of scope, with reasons); materiality; **an invariant
summary block** (`I1…I8: pass`) which is the replacement for the prototype's "hand-verified"
claim; and a plain-language "what didn't reconcile" lead paragraph.

### 15.2 `1. Reconciliation Summary`
Row 4 headers, data from row 5, exactly as the prototype:

`Ledger Acct │ Account / Bank │ Ledger Open │ Ledger Close │ Bank Open │ Bank Close │
Opening Variance │ Outstanding Deposits (ledger, not yet in bank) │ Outstanding Payments (ledger,
not yet in bank) │ Bank items not yet booked (net) │ Adjusted Ledger Bal. │ Adjusted Bank Bal. │
Residual explained by opening variance? │ Status / Note`

Live formulas per row `n`: `G n = Cn − En`, `K n = Dn − Hn + In`, `L n = Fn − Jn`,
`M n = IF(ABS((Kn−Ln)−Gn) <= 0.01, "Yes — residual = opening variance", "No — ENGINE ERROR, see run log")`.
Note the tolerance tightens from the prototype's `100` to `0.01` for the reason given in §12.1.
`TOTAL` row `= SUM(...)` per column. Then the out-of-scope block:
`Ledger Acct │ Account │ Ledger Close │ n txns │ Reason`.

### 15.3 `2. Item Validation Report`
`item_id (hidden) │ Ledger Acct │ Item Type │ Date │ Amount │ Description / Supporting Evidence │
Side │ Clearance │ Bank verification │ Verification detail │ Material? │ Periods open`

`Item Type` ∈ {`Ledger-side outstanding`, `Bank-side unbooked`, `Amount variance`}.
`Bank verification` is engine-generated and factual — `CONFIRMED ABSENT FROM STATEMENT`,
`MATCHED (rule: exact, Δ0d)`, `AMBIGUOUS — 2 candidates`. Unlike the prototype, there is no
`Not independently re-traced this pass` value: the engine traces every item on every account,
every period. That absence is the main functional gain over the pilot.

### 15.4 `3. Investigation Register`
`item_id (hidden) │ Item │ Ledger Acct │ Amount │ Found in bank? │ Found in ledger? │
Supporting docs needed │ Auditor conclusion │ Resolution (fill in) │ Resolved?`

Populated from: `status='flagged'` items; one row per account with `opening_variance ≠ 0`; every
`anomalies` row. Ordered by amount descending within kind, so the largest open question is at
the top.

### 15.5 `4. Proposed Adjusting Entries`
`# │ Account (Dr/Cr) │ Description │ Debit │ Credit │ Ledger Acct │ Basis / Confirmation needed before posting`

An entry is proposed only when **all** hold:
1. `side='bank_unbooked'` (a ledger-side deposit in transit is a timing difference, not an entry);
2. `category` maps to a Dr account in `charge_account_map` — i.e. `bank_commission`,
   `bank_commission_iva`, `loan_installment`;
3. exactly quantified — the item came from a parsed statement line, not an estimate;
4. no `status='flagged'` item on the same account, and no unresolved `anomalies` row for it.

Everything else goes to Tab 3 with a stated reason. This reproduces the prototype's discipline
(it proposed two entries, both the loan debit, and explained in `A11` why nothing else
qualified) without hardcoding that outcome. Where a Dr account is still `TBD-*` in §6.1, the
entry is written with the placeholder and `Basis` flags that the account must be confirmed
before posting.

### 15.6 Formatting
One `styles.py`: header fill and bold, `#,##0.00` for money, `dd/mmm/yyyy` for dates, frozen
panes below the header row, autofilter on tabs 2 and 3, column widths from content, wrap on
narrative columns. Negative amounts in red. No conditional formatting that encodes meaning —
meaning lives in columns, so it survives CSV export and a diff.

---

## 16. CLI — `recon`

```
recon init-db
recon parse-ledger  --root <path> --period 2024-06 [--file <xlsx>] [--json-out <f>]
recon parse-statements --root <path> --period 2024-06
recon match         --period 2024-06
recon report        --period 2024-06 [--out <xlsx>]
recon run           --root <path> --period 2024-06          # the four above, in order
recon backfill      --root <path> --from 2024-01 --to 2024-06
recon ingest-review <reviewed.xlsx>
recon diagnose-statement <pdf> [--bank bbva]
recon verify-template <bank>
recon validate-against-schedule --period 2024-06            # §17.3, one-time
recon check         --period 2024-06                        # invariants only, no write
```

Global flags: `--db`, `--config-dir`, `--dry-run`, `--log-level`, `--fail-on-warning`.
Exit codes: `0` ok · `2` parse failure · `3` invariant gate failure · `4` config/inventory error.
Logs are structured JSON to `data/logs/<run_id>.jsonl`, with a human summary on stderr.

`recon backfill` runs periods strictly in ascending order so carry-forward is meaningful, and
stops at the first gate failure rather than continuing with a corrupt chain.

---

## 17. Testing and acceptance

### 17.1 Ledger parser — exact expectations (verified)

`recon parse-ledger --period 2024-06` must reproduce this table exactly. Every figure was read
from `01-12-2024-PatyMaiki2022-2-Kai-test.xlsx!06-24` and satisfies
`open + cargos − abonos = close`:

| ledger_account | open | cargos | abonos | close | n txns |
|---|---|---|---|---|---|
| 1112-01-001-00 | 2,380,012.58 | 3,568,812.60 | 3,158,224.06 | 2,790,601.12 | 215 |
| 1112-01-002-00 | 95,673.49 | 1,179,399.16 | 1,169,576.28 | 105,496.37 | 259 |
| 1112-01-003-00 | 17,811.11 | 615,208.90 | 631,881.32 | 1,138.69 | 227 |
| 1112-01-004-00 | −60,457.48 | 157,974.59 | 303,150.08 | −205,632.97 | 20 |
| 1112-01-005-00 | 5,467,238.81 | 658,957.47 | 679,152.49 | 5,447,043.79 | 69 |
| 1112-01-006-00 | 279,805.20 | 1,057,245.88 | 1,023,369.90 | 313,681.18 | 115 |
| 1112-01-007-00 | 465,410.63 | 1,336,685.54 | 877,603.42 | 924,492.75 | 113 |
| 1112-01-008-00 | 1,064.82 | 0.00 | 0.00 | 1,064.82 | 0 |
| 1112-01-009-00 | 449,787.04 | 484,817.00 | 370,585.77 | 564,018.27 | 216 |
| 1112-01-010-00 | 55,496.33 | 1,348.73 | 0.00 | 56,845.06 | 1 |
| 1112-01-011-00 | 38,943.24 | 305,889.69 | 342,644.11 | 2,188.82 | 62 |
| 1112-01-012-00 | −140,125.50 | 62,024.19 | 41,859.11 | −119,960.42 | 31 |
| 1112-01-013-00 | 1,965,428.77 | 392,891.34 | 36,228.01 | 2,322,092.10 | 118 |
| 1112-01-014-00 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| 1112-01-015-00 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| 1112-01-016-00 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| 1112-01-017-00 | 121,742.36 | 159,924.87 | 91,428.80 | 190,238.43 | 48 |
| 1112-01-018-00 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| 1112-01-019-00 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| 1112-02-001-00 | 23,697.18 | 0.00 | 5,367.20 | 18,329.98 | 2 |
| 1112-02-096-00 | 2,010.59 | 0.00 | 93,563.28 | −91,552.69 | 2 |
| **grand total** | | **9,981,179.96** | **8,824,633.83** | **12,320,085.30** | |

Plus spot assertions on individual rows the prototype cites, which pin `row_no` semantics
(`source_ref` = worksheet row, as in the prototype's "Ledger row 218"):
`row 218` = 29/Jun/2024, Diario, póliza 135, cargo 100,000.00 · `row 215` = 29/Jun/2024,
Egresos, póliza 63, abono 10,582.76, `Trf1481017 La Ferre Comercializadora F/106901` ·
`row 226` = 30/Jun/2024, `Iva y Comisiones Bancarias`, abono 1,305.81.

The same test runs over `01-24`…`05-24` asserting only I1/I2/R2, which exercises the layout
variations (missing `Total:` row, `A1` empty, 17–23 accounts per sheet).

### 17.2 Bank parser — exact expectations (verified, available before the PDFs are)

Per-account opening/closing balances, from the prototype's Tab 1 columns E/F:

| bank_account | ledger_account | bank_open | bank_close |
|---|---|---|---|
| 0114091108 | 1112-01-001-00 | 98,971.26 | 346,100.50 |
| 0118892253 | 1112-01-002-00 | 18,826.19 | 19,066.95 |
| 0118892717 | 1112-01-003-00 | 30,870.48 | 13,622.18 |
| 0118893314 | 1112-01-004-00 | 152,626.97 | 13,708.18 |
| 0192251736 | 1112-01-005-00 | 149,572.20 | 63,032.11 |
| 0114232658 | 1112-01-006-00 | 120,289.31 | 44,353.04 |
| 0116016731 | 1112-01-007-00 | 40,254.91 | 24,963.67 |
| 0118893187 | 1112-01-009-00 | 52,061.41 | 152,132.36 |
| 0118892830 | 1112-01-011-00 | 163,411.52 | 69,214.00 |
| 0116016790 | 1112-01-012-00 | 91,601.00 | 130,439.60 |
| 0118893039 | 1112-01-013-00 | 242,023.88 | 266,310.29 |
| 0122211882 | 1112-01-016-00 | 13,000.01 | 13,000.01 |
| 0120444995 | 1112-01-017-00 | 183,587.20 | 191,453.34 |
| 0107961006 | 1112-02-001-00 | 2,167.08 | 2,299.05 (USD) |
| **all 14** | | **1,359,263.42** | **1,349,695.28** |

(The USD account is included in that total in the prototype, mixing currencies — an
acknowledged presentational flaw. The engine's Tab 1 total row excludes non-MXN accounts and
footnotes them separately; §12.5.)

Period deposit/withdrawal totals for the five accounts where the bookkeeper's schedule recorded
them (`Saldo en bancos` row) — these are the strongest available test of the line-level parse:

| bank_account | Σ depósitos (abonos) | Σ retiros (cargos) | tie-out |
|---|---|---|---|
| 0114091108 | 3,394,770.89 | 3,147,641.65 | 98,971.26 + 3,394,770.89 − 3,147,641.65 = **346,100.50** ✓ |
| 0118892253 | 1,169,817.04 | 1,169,576.28 | 18,826.19 + … = **19,066.95** ✓ |
| 0118892717 | 614,633.65 | 631,881.95 | 30,870.48 + … = **13,622.18** ✓ |
| 0114232658 | 948,485.24 | 1,024,421.51 | 120,289.31 + … = **44,353.04** ✓ |
| 0118893039 | 420,858.17 | 396,571.76 | 242,023.88 + … = **266,310.29** ✓ |

All five tie to the cent, which confirms invariant I3 holds on real BBVA data before a line of
parser code is written.

Line-level assertions, from prototype evidence text:
- `0118893039` has **20** `H09 COBRO AUTOMATICO RECIBO / PREST. 9818644907` debits of
  **18,017.19** each on 03,04,05,06,07,10,11,12,13,14,17,18,19,20,21,24,25,26,27,28-Jun-2024,
  totalling **360,343.80** (not the schedule's assumed 360,343.60).
- `0118893039`, 26-Jun-2024: `V42 VENTAS DEBITO` **22,648.19**, ref `144396047`.
- `0114091108`, 20-Jun-2024: SPEI enviado **468,750.00** to `CONSTRUBASCO SA DE CV`, ref `0042285216`.
- `0118892253`, 27-Jun-2024: SPEI recibido **364,583.33** `DEVOLUCION DEPOSITO ERRONEO …
  CONSTRUBASCO`, and same-day SPEI enviado **364,583.33** to `PROYECTS ARTECK SA DE CV`.
- `0118892830`, 18-Jun-2024: `H86 REEMBOLSO PAGO DE CREDITO` **18,017.18**.

### 17.3 One-time validation against the bookkeeper's schedule

`recon validate-against-schedule --period 2024-06` parses the embedded schedules for the five
accounts that have one and prints engine-derived versus bookkeeper figures:

| ledger_account | bookkeeper `Partidas en conciliación contabilidad` (F/G) | bookkeeper `Suma Partidas en Conciliación` (F/G) |
|---|---|---|
| 1112-01-001-00 | 812,366.41 / 10,582.76 | 638,324.33 / 0 |
| 1112-01-002-00 | 103,066.15 / — | 93,484.10 / 0 |
| 1112-01-003-00 | 37,059.07 / — | 36,483.88 / 0 |
| 1112-01-006-00 | 131,967.30 / — | 23,206.65 / 0 |
| 1112-01-013-00 | 75,495.79 / — | 103,463.22 / 360,343.60 |

Acceptance: the engine's `outstanding_inflow` / `outstanding_outflow` / `unbooked_inflow` /
`unbooked_outflow` reproduce these **or** every difference is explained by a specific,
enumerated finding. Known differences that must appear, not be papered over:

- `1112-01-013-00`: engine `unbooked_outflow` should be **360,343.80**, $0.20 above the
  schedule's 360,343.60 (20 × 18,017.19 vs 20 × 18,017.18).
- `1112-01-006-00`: an extra **1,051.61** of bank retiros the schedule left unexplained.
- account 001's schedule closes with **0.37 / −0.35** of its own slack (`06-24!F249/G249`); the
  engine's `closure_residual` must be **0.00**.

This is the last period the project leans on a human check (design spec Phase 1). After it
passes, `validation/legacy_schedule.py` may be deleted; invariants I1–I8 take over.

### 17.4 Test inventory

| Layer | Tests |
|---|---|
| ledger parser | §17.1 table; header-row discovery; missing `Total:` row; roll-up skipped; Spanish months; `Decimal` fidelity on `75495.79000000001`; fill colours ignored; unknown account ⇒ exit 4 |
| bank parser | §17.2 tables; wrapped descriptions; multi-account PDF; USD detection; `sniff()` rejects a non-BBVA PDF; corrupt PDF ⇒ diagnostic bundle written, exit 2 |
| matching | one golden case per pass; póliza group from `06-24` rows 216–225; subset-sum uniqueness rejection; the three coincidental-amount collisions land flagged; Construbasco/Arteck stays cross-account and unmatched; determinism (two runs ⇒ identical `matches` rows) |
| invariants | one deliberately broken input per gate I1–I8, each asserting exit 3 and no workbook written |
| carry-forward | synthetic Jun→Jul: item clears ⇒ resolved; doesn't ⇒ `periods_open=2`; 3 periods ⇒ anomaly |
| review round-trip | write Tab 3 → edit → `ingest-review` → re-run `recon run` → resolution survives; unknown `item_id` warns; `Yes` without text rejected; idempotent |
| workbook | sheet names and header rows match the prototype cell-for-cell; formulas present as formulas; opens in Excel without repair |
| end-to-end | June 2024 from raw inputs to workbook, all gates green |

---

## 18. Build phases and exit criteria

| Phase | Work | Exit criterion (verifiable) |
|---|---|---|
| **0 · Inventory & skeleton** | `config/entities/secontrol.yml` confirmed with Kai; repo, `pyproject.toml`, `store.py` DDL, `config.py` validation, CLI skeleton | `recon init-db` creates all 8 tables; config loads and rejects an account missing `bank_account` |
| **1a · Ledger parser** | `ledger_contpaq.py` + I1/I2/R2 | §17.1 table reproduced exactly for `06-24`; I1/I2/R2 green for `01-24`…`06-24` |
| **1b · Bank parser** | `diagnose-statement` first, then calibrate `banks/bbva.yml`, then `bbva.py` + I3 | §17.2 balances and the five deposit/withdrawal totals reproduced; the 20 × 18,017.19 loan debits found by count |
| **2 · Matching, single period** | passes 1–7, `derive.py`, I4–I7, `matching.yml` tuned | I4–I7 green for all 14 accounts; `closure_residual = 0.00` everywhere; `recon validate-against-schedule` passes §17.3 including the three known differences |
| **3 · Backfill + forward** | `carryforward.py`, I8, `recon backfill` | Jan–Jun 2024 backfilled in order, all gates green; a June deposit in transit auto-resolves in July rather than reappearing as new |
| **4 · Workbook** | `workbook.py`, `styles.py`, `anomalies.py` | generated workbook matches the prototype's sheet/column structure; Tab 3 contains the sign-divergence, CLABE-mislabel, dormant-balance, USD and stale-item findings the pilot made by hand |
| **5 · Exception round-trip** | `exceptions_io.py` | round-trip test in §17.4 passes; a resolution survives a full re-run |
| **6 · Deploy** | run on the OneDrive machine; `README` runbook | `recon run` completes there against the live synced folder; one real monthly close produced end-to-end |

Deferred beyond v1, in the order they would matter: email/share-link delivery (design §3.5);
scheduling; a second bank plug-in; Graph API source; FX translation for the USD account.

---

## 19. Open items needing Kai

Nothing here blocks Phases 0–2; each is flagged at the point it becomes load-bearing.

1. **A real BBVA statement PDF** (any one account, June 2024) — needed to calibrate
   `banks/bbva.yml` column geometry (§10.3). Blocks Phase 1b only. Everything else can be built
   and tested against synthetic statements first.
2. **The OneDrive folder layout** — is there an existing convention for where ledger exports and
   statement PDFs live? §8.2 assumes `ledger/` + `statements/<YYYY-MM>/` and will be adjusted to
   whatever exists. Blocks Phase 6.
3. **Dr accounts for `charge_account_map`** (§6.1) — the real CONTPAQi account codes for bank
   commissions, IVA on commissions, and the loan facility. Until then Tab 4 emits `TBD-*`
   placeholders with a confirmation note. Blocks nothing; degrades Tab 4.
4. **The `1112-01-019-00` Afirme account** — confirmed out of scope for v1 (clarification 6),
   but is it a live account? If so it silently under-reports and should be given an explicit
   out-of-scope reason in the workbook, which §6.1 already does.
5. **Materiality** — the prototype used $5,000 MXN. Confirm it stands, or set per-account.
6. **Which period is "now"** — v1 backfills Jan–Jun 2024. Are Jul 2024 onward exports and
   statements available, and from which period should the monthly run become the live process?

---

## Appendix A — facts established from the source files

Recorded so a future reader does not have to re-derive them.

**Ledger export (`01-12-2024-PatyMaiki2022-2-Kai-test.xlsx`)**
- Sheets: `01-24`…`06-24` (one per period), `07-04` and `08-04` (empty placeholders),
  `Gráfico1` (chart), and three `Busqueda <amount>` manual subset-sum scratch sheets.
- Layout is identical across all six period sheets: metadata rows 1–4, column headers row 7,
  roll-up `1112-00-000-00` at row 9, then account blocks.
- Account counts per sheet: 20, 23, 17, 18, 22, 22. Embedded manual schedules per sheet:
  17, 13, 13, 13, 13, **5**. The schedule habit is decaying, which is exactly why the engine
  must not depend on it.
- Column I manual bank-balance annotations per sheet: 0, 3, 3, 12, 10, 1.
- `03-24!1112-01-016-00` has no `Total:` row at all.
- `Busqueda 4069.87` shows the manual subset-sum workflow, and its own combination totals are
  cumulative rather than per-combination (`8,139.74`, `12,209.61` where each should be
  `4,069.87`) — a hand-tooling bug that pass 5 removes.

**Prototype workbook (`SECONTROL_Bank_Reconciliation_June2024_v2.xlsx`)**
- 5 sheets; Tab 1 has 14 in-scope accounts + total + 7 out-of-scope; Tab 2 has 106 items;
  Tab 3 has 24 rows; Tab 4 has 2 proposed entries.
- Tab 2's bank-side items were **copied from the bookkeeper's schedule**, and 8 of them carry
  `Not found in re-fetched bank text — needs follow-up`; 33 ledger-side items carry
  `Not independently re-traced this pass`. Both gaps are closed by construction in this design:
  the engine derives bank-side items from the statement and traces every item on every account.
- Materiality: $5,000 MXN.
