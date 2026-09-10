# Reconciliation Engine — Design Spec

**Status:** discussion draft v0.2 — no engineering started
**Entity:** Secontrol Automatización SA de CV
**Basis:** June 2024 pilot (5 of 14 accounts, BBVA, hand-verified in conversation)
**Changelog:** v0.2 replaces v0.1 (published as an HTML page) — folds in the LLM/Copilot
advisory, drops the bookkeeper's schedule and yellow-fill marking as a permanent input,
generalizes the bank parser beyond BBVA, and concretizes the phased plan against Kai's
answers. This file is meant to travel into a repo and be built against with Claude Code —
it is the working spec, not a one-off memo.

---

## 0. End state

The tool is not a verifier that leans on the bookkeeper's own reconciliation schedule and
cell-color marking forever. Those two things — the embedded "Partidas en conciliación..."
block, and yellow/red fill on matched/unmatched transaction cells — are **artifacts of the
current manual process**, present in the June 2024 workbook because a human built them by
hand. They are useful *this pass* as ground truth to validate the engine against, and
nothing more. The end state has:

- no expectation that any future CONTPAQi export contains a bookkeeper-built schedule at all
- no reliance on cell fill color as a matching signal
- the engine independently establishing, from the raw ledger and the raw bank statements
  alone, everything the schedule used to represent — and doing it for all ~14+ accounts,
  not just the 5 that happened to get a hand-built schedule

Put plainly: **this replaces the bookkeeper's schedule-building step**, it doesn't perpetually
grade it.

---

## 1. What the four tabs are actually doing

*(unchanged from v0.1, restated because it drives the data model in §5)*

| Tab | Question it answers |
|---|---|
| 1 · Reconciliation Summary | Balance-level scaffold — ledger vs. bank, open/close, per account. Tells you *where* a gap exists and whether it's already explained by an opening-balance carry-forward. |
| 2 · Item Validation Report | Line-item layer — every individual outstanding/unbooked item, evidenced against a real bank statement line. |
| 3 · Investigation Register | The residue — anything Tab 2 couldn't close on its own, with a specific reason it's still open. |
| 4 · Proposed Adjusting Entries | Only items that are *both* individually identified (Tab 2) *and* not blocked by an open question (Tab 3) get a Dr/Cr proposed. |

The dependency is evidentiary, not computational: nothing in Tab 3 is a formula over Tab 2,
but every row in Tab 3 exists because something in Tab 1 or Tab 2 came up short. Design
consequence: these are four **views** over one `reconciling_items` table (see §5), not four
independently-built artifacts.

The categories the June schedule used (deposits in transit, unbooked bank items, opening
variance, structural anomalies) are still the right taxonomy for `status` and `side` —
they're just no longer sourced *from* a bookkeeper's schedule. They're what the engine itself
derives directly from ledger + bank data.

---

## 2. LLM in the loop — accuracy and Copilot integration

You asked for this from two angles: a reconciliation-accuracy view, and a Microsoft
Copilot-integration view (pricing and technical feasibility). Both, straight:

### 2.1 Accuracy: where deterministic wins, where an LLM earns its place

**Exact match** (same date, same amount, same account, unambiguous) — a rule-based lookup is
strictly better than an LLM call here. It's 100% precise, free, instant, and — critically for
an audit workpaper — *reproducible*: the same input always produces the same match, and the
match is explainable by a one-line rule, not a model's internal reasoning. Replacing this with
an LLM call would be a strict downgrade on cost, latency, and auditability, with zero
accuracy upside. **Do not do this.**

**Fuzzy match** (amount matches but date is off by a few days; description is a generic POS
code with no counterparty name; two transactions collide on amount by coincidence — like the
Arteck/Construbasco case found this pass) — this is where rule-based matching (date-window +
amount tolerance + string similarity) genuinely hits a ceiling, and where an LLM can add real
value: it can reason over context a regex can't ("this SPEI reference pattern and this
counterparty name is characteristic of a POS batch settlement, not a wire transfer").

But be precise about *what* value it adds. It is not more accurate than a well-tuned
similarity score at the core arithmetic (amount within tolerance, date within a window) — its
value-add is (a) writing the human-readable rationale for *why* two items are the same
transaction, and (b) handling genuinely novel patterns a hardcoded parser wasn't built to
recognize, across seven-plus different Mexican bank statement formats. For an external-audit
style workpaper, the match itself should stay attributable to a fixed rule wherever possible;
the LLM's role is best scoped to **flagging and explaining**, with a human (you) confirming
before a fuzzy match is trusted — not silently deciding matches in bulk. That preserves the
same evidentiary standard this engagement has been holding to all along.

**Recommendation:** deterministic exact + fuzzy matching does the reconciliation. An LLM is
called only for items the deterministic pass can't close — one item at a time, with only that
item's fields in context, never a whole PDF. This matches what you already leaned toward in
§4 below.

### 2.2 Copilot, disambiguated

"Copilot" is four different Microsoft products with different pricing and different
integration paths. Worth being precise, because only one of them is actually callable from a
Python script:

| Product | What it is | Callable from your reconciliation script? | Pricing (per public sources, Sep 2026) |
|---|---|---|---|
| **Microsoft 365 Copilot** (in Personal/Premium) | A chat assistant *inside* Word/Excel/Outlook, invoked by a person typing into a pane | **No.** It's a UI surface, not an API. | M365 Premium: $19.99/mo, replaces the old $20/mo Copilot Pro (Copilot Pro stops for new subscribers; existing subscribers migrate by Aug 1, 2026). Business/Enterprise add-on: $30/user/mo.[^1] |
| **Copilot Studio** | A platform for building custom agents with tool-calling, grounded on your data | Technically yes — but it needs a Microsoft Entra tenant + Power Platform environment, which a **personal** M365 subscription does not include. This is normally an organizational product. | $200/tenant/mo for 25,000 messages (annual), or pay-as-you-go ≈$0.01/message. Cost scales sharply by message type: a generative answer is ~2 credits, document-grounded calls are 5–30× the standard rate.[^2] |
| **GitHub Copilot** | Code-completion / pair-programming assistant | Not relevant to a matching pipeline — this is what a developer uses *while building* the app, unrelated to runtime matching logic. | (Separate product, not costed here.) |
| **Azure OpenAI Service** | Raw LLM API — GPT-4o etc., callable directly from Python | **Yes.** This is the actual integration point for "call an LLM from my script." Needs only a standard Azure account (free to create; you pay per token used), no Entra/Power Platform tenant complexity beyond that. | GPT-4o: $2.50 / $10.00 per million input/output tokens, pay-as-you-go.[^3] |
| **Claude API** (Anthropic) | Same category as Azure OpenAI — raw LLM API | **Yes** — same integration shape as Azure OpenAI. Worth listing since you're building this with Claude Code; using the Claude API for exception review keeps one vendor across build-time and run-time tooling, though that's a preference, not a requirement. | Claude Sonnet 5: $2 / $10 per million input/output tokens through Aug 31, 2026 (then $3/$15); batch API is a flat 50% off both.[^4] |

**Bottom line on your specific setup:** an M365 Personal or Premium subscription gives *you*
a better Copilot experience when *you* work in Excel — it does not give the reconciliation
pipeline anything to call. Upgrading to Premium buys you a nicer personal assistant, not
cheaper or better exception-review matching. Copilot Studio could theoretically host this as
a chat-driven agent, but it's built for organizations with a tenant, and its message-based
pricing (with document-grounding at 5–30× the base rate) would likely cost more, for less
control, than simply calling Azure OpenAI or the Claude API directly from the exception-review
step — for what's realistically a handful of flagged items per entity per month. **Recommend
Azure OpenAI or the Claude API, called directly from Python, and skip Copilot Studio and the
M365 Copilot subscription entirely for this purpose.** At the volumes this pipeline implies
(a few dozen flagged items a month, each a few hundred tokens of context), either option costs
low single-digit dollars a month — pricing is not the deciding factor; API simplicity and
whichever ecosystem you're already comfortable maintaining is.

---

## 3. Architecture

One governing principle, unchanged: the LLM sits at the edges — judgment calls on flagged
exceptions, and a fallback path for statement layouts the parser doesn't yet recognize —
never in the path between a routine PDF and a routine number.

```
 Cloud Storage         Fetch Layer          Parse Layer            Match / Reconcile        Output Layer
 ┌───────────┐        ┌───────────┐        ┌──────────────┐       ┌──────────────────┐     ┌───────────────┐
 │  OneDrive  │──────▶│ MS Graph   │──────▶│ Ledger parser │──┐    │  Deterministic     │──▶│ 4-tab workbook │
 │ (personal) │       │ API,       │       │ (openpyxl)    │  │    │  exact + fuzzy     │    │ (openpyxl)     │
 └───────────┘        │ MSAL       │       ├──────────────┤  ├──▶│  match engine,     │    └───────────────┘
                       │ device-    │       │ Bank stmt     │  │    │  per-account       │            │
                       │ code flow  │       │ parser        │──┘    │  thresholds        │            ▼
                       └───────────┘       │ (per-bank      │       └─────────┬──────────┘   ┌───────────────┐
                                            │  plug-ins)     │                 │              │ Save to        │
                                            └──────────────┘                 ▼              │ OneDrive +     │
                                                     │                ┌───────────────┐       │ email link     │
                                                     │ unrecognized   │  Persistent    │       │ (Graph API)    │
                                                     │ layout         │  store         │       └───────────────┘
                                                     ▼                │ reconciling_   │
                                            ┌──────────────┐          │ items (SQLite) │
                                            │ Exception /   │◀─────────┴───────────────┘
                                            │ novel-layout  │  flagged items only,
                                            │ review (LLM)  │  one item at a time —
                                            └──────────────┘  never a whole PDF
```

Everything except the two labeled LLM boxes is deterministic Python — no tokens, no
non-determinism, no audit-trail gap.

### 3.1 Storage connector — OneDrive, personal account

Microsoft Graph API supports personal (consumer) Microsoft accounts for OneDrive today: the
`Files.ReadWrite` delegated permission is valid on personal accounts, using the `/consumers`
(or `/common`) OAuth endpoint with an `MSAL` `PublicClientApplication` — a device-code or
interactive flow is the right shape for a script running unattended on your own machine
(cache the refresh token locally after first login; no client secret needed for a public
client).[^5] App registration in Microsoft Entra is free; there's no extra cost beyond the
LLM token spend in §2.2 — this doesn't require Copilot Studio, a Power Platform tenant, or a
business subscription of any kind.

### 3.2 Bank statement parser — all Mexican banks, not just BBVA

Generalize to a **per-bank plug-in** architecture: each bank (BBVA today; Santander, Banorte,
HSBC, Banamex, Scotiabank, Inbursa as they show up) gets its own extractor module implementing
one interface — `parse(pdf_bytes) -> list[StatementLine]`, where `StatementLine` is
`(date, code, description, reference, cargo, abono, running_balance)`. Two tiers:

1. **Primary path — deterministic:** `pdfplumber`/`camelot` table extraction + a per-bank
   layout template (column positions, header markers, footer/glossary boundaries — the same
   boundaries I hand-identified for BBVA this pass, e.g. "Detalle de Movimientos Realizados"
   as the start marker and "Total de Movimientos" as the end marker).
2. **Fallback path — for a layout the templates don't recognize** (a new bank, or an existing
   bank that changes its statement format): route the PDF to Azure AI Document Intelligence's
   prebuilt Layout model ($10 per 1,000 pages — far cheaper than transcribing full statements
   through a chat LLM as this pass did[^6]) or a Claude/GPT vision call against the PDF pages,
   to produce a first-draft structured extraction. **Require a human confirmation step**
   before a fallback-derived template gets promoted to a permanent per-bank plug-in — this is
   exactly the same "verify once, then trust" pattern used for the deterministic templates,
   and it keeps a new bank format from silently becoming a source of bad matches.

### 3.3 Matching engine

- **Exact pass:** date + amount + account, unambiguous → `matched`, `match_method = exact`.
- **Fuzzy pass:** amount within tolerance + date within a configurable window + description
  similarity above a configurable threshold → candidate match, `match_method = fuzzy`,
  confidence score attached.
- **Per-account configuration** (your §5.6 answer) — a small config table/file, e.g.:

  ```yaml
  1112-01-013-00:      # accounts with mostly POS-terminal deposits: tight amount, loose desc
    date_window_days: 1
    amount_tolerance: 0.00
    description_min_similarity: 0.2   # bank text is often just a generic POS code
  1112-01-006-00:
    date_window_days: 10              # this account showed a 9-day date-tag lag this pass
    amount_tolerance: 0.00
    description_min_similarity: 0.6
  ```

- **Exception queue:** anything below the fuzzy threshold, or with more than one plausible
  candidate match (the Arteck/Construbasco pattern — same counterparties, different amount,
  different account), goes to the LLM exception-review step from §2, one item at a time.

### 3.4 Self-verification (replacing hand-verification)

Since there's no hand-verified figure to check against going forward, the pipeline needs its
own built-in invariants, run automatically every period, in place of what a human
spot-checking the workbook used to catch:

- **Flow tie-out:** `ledger_close = ledger_open + Σledger_cargos − Σledger_abonos`, and the
  bank-side equivalent, both asserted before any matching runs — a failure here means the
  parser mis-read something, not that the business has a reconciling item.
- **Closure invariant:** every `matched` item nets to zero variance; every account's residual
  after matching either equals its carried opening variance (self-explained) or gets a new
  Investigation Register row (never silently dropped).
- **Carry-forward check:** an item `outstanding` in period N must either turn `matched` in
  period N+1 or get an explicit reason it's still open — an item outstanding for 3+ periods
  running is itself worth a flag.

### 3.5 Scheduling & delivery

Local scheduled job (cron on macOS/Linux, Task Scheduler on Windows) runs the pipeline
end-to-end, then:

1. Uploads the generated workbook to a OneDrive folder via Graph API (`PUT /me/drive/...`).
2. Creates or reuses a sharing link (`POST /me/drive/items/{id}/createLink`).
3. Sends you an email with that link — either via Graph's own `sendMail` (using the same
   delegated Outlook.com permission you're already authenticating with) or plain SMTP if you'd
   rather not grant mail-send scope. Graph `sendMail` is the simpler option since it reuses one
   auth flow for OneDrive + Outlook together.

---

## 4. Data model

```
reconciling_items
  id                  text primary key
  entity              text            -- "Secontrol Automatización SA de CV"
  ledger_account      text            -- e.g. "1112-01-013-00"
  bank_account        text            -- e.g. "0118893039"
  bank_name           text            -- "BBVA", "Santander", ...
  period              text            -- "2024-06"
  side                text            -- ledger_outstanding | bank_unbooked
  amount              numeric
  txn_date            date
  description         text
  source_ref          text            -- ledger row #, or bank stmt line #
  match_method        text            -- exact | fuzzy | llm_reviewed | manual
  match_confidence    numeric nullable
  status              text            -- matched | outstanding | flagged | resolved
  resolved_in_period  text nullable   -- e.g. cleared in "2024-07"
  evidence_json       json            -- the matched bank line / ledger cell, verbatim
  note                text nullable   -- write-up, only populated by exception review

account_config
  ledger_account            text primary key
  date_window_days          integer
  amount_tolerance          numeric
  description_min_similarity numeric

bank_format_registry
  bank_name           text primary key
  parser_version      text
  template_source     text            -- "deterministic" | "llm_fallback_confirmed"
  last_verified_period text
```

Tab 1 aggregates `reconciling_items` by `ledger_account` + `period`. Tab 2 is every row with
`status != resolved`. Tab 3 is every `flagged` row plus account-level opening variances. Tab 4
is every `matched` or exactly-quantified row not blocked by an open `flagged` row on the same
account.

---

## 5. Phased build plan

Concretized against your answers.

| Phase | Scope |
|---|---|
| **0 — Inventory** | Build the complete Secontrol Automatización account/bank list: every CONTPAQi Bancos sub-account, its real bank + account number, and which bank format it needs (BBVA confirmed; others TBD as they appear). This removes the "9 accounts with no statement" ambiguity from the June pilot. |
| **1 — Parsers** | Ledger parser (generalized from this pass's account-block + schedule + fill-color logic — though the schedule/fill-color path becomes historical-validation-only, per §0) and the BBVA statement parser, producing structured data, no matching yet. Validate against June 2024's hand-verified figures as ground truth *one last time* — the last period this project leans on a human check instead of the invariants in §3.4. |
| **2 — Matching engine, single period** | Exact + fuzzy matching over June 2024, persisted to `reconciling_items` for the first time. Per-account config table (§3.3) stood up here. |
| **3 — Backfill + forward ingestion** | Re-run January–June 2024 (backfill) and stand up ongoing monthly ingestion from July 2024 forward as new CONTPAQi exports arrive — this is the point where "one-off pilot" becomes "running system." Confirm carry-forward works: an item outstanding in month N closes itself in month N+1 rather than reappearing as new. |
| **4 — Exception review + output** | Wire up the single-item LLM hook (Azure OpenAI or Claude API, per §2.2) for flagged rows, and generate the four-tab workbook from saved queries over `reconciling_items`. |
| **5 — Scheduling & delivery** | Local scheduled job, OneDrive upload, email-with-link, per §3.5. |
| **6 — Extend** | Additional Mexican bank formats as Secontrol's accounts require them (Santander, Banorte, HSBC, Banamex, Scotiabank, Inbursa), via the plug-in + fallback path in §3.2. |

---

## 6. Open questions

Most of v0.1's list is resolved by your answers above. What's left:

1. **LLM vendor for exception review** — Azure OpenAI or the Claude API (§2.2)? Either is
   fine technically; this is really about which ecosystem you'd rather hold API keys and
   billing for.
2. **Fallback extraction for novel bank layouts** — Azure AI Document Intelligence (cheap,
   Microsoft-native, pairs naturally with the OneDrive/Graph choice) or a vision-capable LLM
   call (more flexible, marginally more expensive per page)?
3. **Email delivery mechanic** — Graph `sendMail` (reuses the OneDrive auth) or plain SMTP
   (avoids granting a mail-send scope)?
4. **Bank format priority order** — once Phase 0's inventory is built, which non-BBVA bank
   should get a parser plug-in first, based on which accounts actually need it?

---

[^1]: [Microsoft 365 Copilot Pricing Calculator (2026)](https://www.velosio.com/blog/m365-copilot-pricing-calculator/), [Microsoft Copilot Pricing 2026 — GoSearch](https://www.gosearch.ai/blog/microsoft-copilot-pricing/)
[^2]: [Copilot Studio Pricing in 2026 — Coworker.ai](https://coworker.ai/blog/copilot-studio-pricing), [Copilot Studio Pricing 2026 — CloudZero](https://www.cloudzero.com/blog/copilot-studio-pricing/)
[^3]: [Azure OpenAI pricing in 2026 — CloudZero](https://www.cloudzero.com/blog/azure-openai-pricing/)
[^4]: [Claude pricing in 2026 — CloudZero](https://www.cloudzero.com/blog/claude-pricing/), [Claude Platform Docs — Pricing](https://platform.claude.com/docs/en/about-claude/pricing)
[^5]: [Understanding OneDrive API permission scopes — Microsoft Learn](https://learn.microsoft.com/en-us/onedrive/developer/rest-api/concepts/permissions_reference?view=odsp-graph-online), [MSA OAuth — Microsoft Learn](https://learn.microsoft.com/en-us/onedrive/developer/rest-api/getting-started/msa-oauth)
[^6]: [Azure AI Document Intelligence Pricing 2026 — DocuOCR](https://docuocr.com/blog/azure-document-intelligence-pricing)
