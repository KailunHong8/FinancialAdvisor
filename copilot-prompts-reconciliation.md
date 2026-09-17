# Working with Copilot in Excel — SECONTROL Reconciliation Workbook

**File this applies to:** `SECONTROL_Bank_Reconciliation_2024-06*.xlsx` (and future periods produced
by the same pipeline). Columns referenced below are the ones actually in this workbook as of
the run with `pos_batch_aggregate` matching — check the Cover sheet's `Run ID` if a future file's
columns drift from this list.

This is a working prompt library, not a one-time script — add to it as new anomaly types show up
in the Investigation Register.

---

## English

### 0. One-time setup, per file

1. Save the workbook in OneDrive (or open the one already saved there) with **AutoSave on**.
   Copilot in Excel won't engage with a local-only file.
2. Fix the one structural issue before converting to Tables: **"1. Reconciliation Summary" has
   two header blocks in one sheet** — the in-scope header is row 4, and the out-of-scope section
   repeats a header at row 22. Move the out-of-scope block (rows 21–29) to its own sheet, or at
   least give it its own named Table, before asking Copilot anything about "the whole summary
   sheet" — otherwise it may blend the two.
3. Select each sheet's data range and press `Ctrl+T` to make it a real Table. Name them something
   you'll reference in prompts:
   - `tbl_Summary` — 1. Reconciliation Summary (in-scope block only)
   - `tbl_ItemValidation` — 2. Item Validation Report
   - `tbl_Investigation` — 3. Investigation Register
   - `tbl_AdjustingEntries` — 4. Proposed Adjusting Entries
4. Size is a non-issue — even at ~1,460 rows this is well inside Copilot's comfortable range.

### 1. Column quick reference (so your prompts are precise)

| Column | Sheet | What it means |
|---|---|---|
| `item_id` | 2, 3 | Stable hash identifying one reconciling item — same value in both sheets if it's the same item. Use it to cross-reference between the two tables in a prompt. |
| `Item Type` | 2 | `Opening variance`, `Bank-side unbooked`, or `Ledger-side outstanding`. |
| `Clearance` | 2 | `Outstanding` or `Flagged` — not yet closed. |
| `Material?` | 2 | Whether the amount clears the $5,000 MXN threshold on the Cover sheet. |
| `Periods open` | 2 | How many periods running this item has stayed open — your aging signal. |
| `Bank verification` / `Verification detail` | 2 | The evidence trail (e.g. `stmt 0114091108 p15 l198`, `ledger row 195`) — this is what makes a match auditable. Never ask Copilot to "double check" this against the source PDF; it doesn't have the PDF. |
| `Item` | 3 | The anomaly category: `pos_batch_aggregate`, `sign_divergence`, `currency_mismatch`, `dormant_with_balance`, `unmatched_period_flow`, `Opening variance`. |
| `Resolution (fill in)` | 3 | Blank by design — this is where you (optionally with Copilot's help) write what the bookkeeper/client confirmed. **See the note in §3 before you rely on this column.** |

### 2. Prompt library

**Triage & aging**
> In `tbl_ItemValidation`, list every row where `Periods open` is 2 or more and `Material?` is
> "Material", grouped by `Ledger Acct`, sorted by amount descending.

> Summarize `tbl_ItemValidation` by `Ledger Acct`: count of outstanding items, total amount, and
> the oldest `Periods open` value in each group.

**POS batch aggregates — new this pass**
The pipeline now matches several ledger "corte de caja" pólizas against several bank settlement
credits *in aggregate*, per terminal, per account (see the `pos_batch_aggregate` rows in
`tbl_Investigation`). These are flagged for your confirmation, not auto-accepted.

> In `tbl_Investigation`, for every row where `Item` is "pos_batch_aggregate", extract the
> `Auditor conclusion` text into a table with one line per póliza: Ledger Acct, terminal number,
> póliza number, póliza date, póliza amount, and the settlement credit date(s) it's matched to.

> For the `1112-01-002-00` `pos_batch_aggregate` row, add up the póliza amounts quoted in
> `Auditor conclusion` and confirm they sum to the $329,655.05 total in the `Amount` column.
> Flag if they don't. *(This is a safe arithmetic check on what's already written — not a
> request to re-decide the match — see the guardrail in §3.)*

> Draft a one-paragraph request, per account, listing which acquirer/terminal settlement report
> I need from the bookkeeper to confirm each `pos_batch_aggregate` row — use the terminal number
> and cut dates already in `Auditor conclusion`.

**Other anomaly categories**
> In `tbl_Investigation`, for every row where `Item` is "sign_divergence", explain in one sentence
> each what a negative-vs-positive balance would mean in practice for that account.

> For the `currency_mismatch` row on `1112-02-001-00`, draft the specific question to ask the
> bookkeeper about the FX policy used to translate this account, referencing the $21,530.10
> variance.

> List every `dormant_with_balance` and `unmatched_period_flow` row in `tbl_Investigation`
> with its `Supporting docs needed` column, as a checklist I can hand to the bookkeeper.

**Drafting resolutions**
> I confirmed with the bookkeeper that [paste your own notes]. Write the `Resolution (fill in)`
> text for item_id `<paste id>` in the same style as the `Auditor conclusion` column.

**Client / bookkeeper communication**
> Draft a short email to the bookkeeper listing every unresolved row in `tbl_Investigation`,
> grouped by `Item` category, each with its `Supporting docs needed` text.

**Visual summaries**
> Build a PivotTable from `tbl_ItemValidation` summarizing outstanding amount by `Ledger Acct`
> and `Item Type`.

> Chart the `Opening variance` amounts from `tbl_Investigation` by account, largest to smallest.

### 3. Guardrails — what not to ask Copilot to do

- **Don't ask Copilot to re-decide a match, or to "double-check" a `Bank verification` entry
  against the source bank statement.** It doesn't have the PDF, and the whole point of the
  `item_id` / evidence-string design is that every match traces to a specific, deterministic
  rule the Python pipeline applied — not a judgment call made fresh each time someone opens the
  file. Use Copilot for triage, arithmetic QA on numbers already in the sheet, and drafting —
  not for re-reconciling.
- **Anything you type into `Resolution (fill in)` will be overwritten the next time the pipeline
  runs**, until the pipeline is updated to read resolutions back in before regenerating the
  workbook. Keep a copy of anything important outside the sheet (or in a separate tab you know
  the pipeline won't touch) until that's fixed.
- If a prompt's answer references a `Periods open` count or dollar total, spot-check it against
  the sheet once — Copilot is very good at drafting text and building formulas from existing
  values, less reliable the moment a prompt implies it should independently recompute something.

---

## Español

### 0. Configuración inicial, por archivo

1. Guarda el libro en OneDrive (o abre el que ya está guardado ahí) con **Autoguardado
   activado**. Copilot en Excel no funciona con un archivo que solo existe localmente.
2. Corrige este problema estructural antes de convertir a Tablas: **"1. Reconciliation Summary"
   tiene dos bloques de encabezado en la misma hoja** — el encabezado de las cuentas en alcance
   está en la fila 4, y la sección de cuentas fuera de alcance repite un encabezado en la fila
   22. Mueve ese bloque (filas 21–29) a su propia hoja, o al menos dale su propia Tabla con
   nombre, antes de pedirle a Copilot algo sobre "toda la hoja de resumen" — de lo contrario
   puede mezclar los dos bloques.
3. Selecciona el rango de datos de cada hoja y presiona `Ctrl+T` para convertirlo en una Tabla
   real. Nómbralas de forma que puedas referenciarlas en tus instrucciones:
   - `tbl_Summary` — 1. Reconciliation Summary (solo el bloque en alcance)
   - `tbl_ItemValidation` — 2. Item Validation Report
   - `tbl_Investigation` — 3. Investigation Register
   - `tbl_AdjustingEntries` — 4. Proposed Adjusting Entries
4. El tamaño no es un problema — incluso con ~1,460 filas esto está muy por debajo del límite
   cómodo de Copilot.

### 1. Referencia rápida de columnas (para instrucciones precisas)

| Columna | Hoja | Qué significa |
|---|---|---|
| `item_id` | 2, 3 | Hash estable que identifica una partida de conciliación — el mismo valor aparece en ambas hojas si es la misma partida. Úsalo para cruzar información entre las dos tablas en una instrucción. |
| `Item Type` | 2 | `Opening variance`, `Bank-side unbooked`, o `Ledger-side outstanding`. |
| `Clearance` | 2 | `Outstanding` o `Flagged` — aún no cerrado. |
| `Material?` | 2 | Si el monto supera el umbral de $5,000 MXN indicado en la hoja Cover. |
| `Periods open` | 2 | Cuántos periodos consecutivos lleva abierta esta partida — tu señal de antigüedad. |
| `Bank verification` / `Verification detail` | 2 | El rastro de evidencia (p. ej. `stmt 0114091108 p15 l198`, `ledger row 195`) — esto es lo que hace que un cruce sea auditable. Nunca le pidas a Copilot que "verifique de nuevo" esto contra el PDF original; no tiene acceso al PDF. |
| `Item` | 3 | La categoría de la anomalía: `pos_batch_aggregate`, `sign_divergence`, `currency_mismatch`, `dormant_with_balance`, `unmatched_period_flow`, `Opening variance`. |
| `Resolution (fill in)` | 3 | Vacía a propósito — aquí escribes (opcionalmente con ayuda de Copilot) lo que confirmó el contador/cliente. **Ver la nota en §3 antes de confiar en esta columna.** |

### 2. Biblioteca de instrucciones (prompts)

**Triage y antigüedad**
> En `tbl_ItemValidation`, enlista cada fila donde `Periods open` sea 2 o más y `Material?` sea
> "Material", agrupado por `Ledger Acct`, ordenado de mayor a menor monto.

> Resume `tbl_ItemValidation` por `Ledger Acct`: número de partidas pendientes, monto total, y el
> valor más alto de `Periods open` en cada grupo.

**Agregados de lotes POS — novedad de este corte**
El pipeline ahora concilia varias pólizas de "corte de caja" del libro contra varios depósitos de
liquidación bancaria *en conjunto*, por terminal, por cuenta (ver las filas `pos_batch_aggregate`
en `tbl_Investigation`). Están marcadas para tu confirmación, no aceptadas automáticamente.

> En `tbl_Investigation`, para cada fila donde `Item` sea "pos_batch_aggregate", extrae el texto
> de `Auditor conclusion` en una tabla con una línea por póliza: Ledger Acct, número de terminal,
> número de póliza, fecha de póliza, monto de póliza, y la(s) fecha(s) de liquidación con la que
> quedó cruzada.

> Para la fila `pos_batch_aggregate` de la cuenta `1112-01-002-00`, suma los montos de las
> pólizas citadas en `Auditor conclusion` y confirma que suman el total de $329,655.05 en la
> columna `Amount`. Márcalo si no coinciden. *(Esta es una verificación aritmética segura sobre
> lo que ya está escrito — no una solicitud para redecidir el cruce — ver la salvaguarda en §3.)*

> Redacta una solicitud de un párrafo, por cuenta, indicando qué reporte de liquidación del
> adquirente/terminal necesito pedirle al contador para confirmar cada fila
> `pos_batch_aggregate` — usa el número de terminal y las fechas de corte que ya están en
> `Auditor conclusion`.

**Otras categorías de anomalías**
> En `tbl_Investigation`, para cada fila donde `Item` sea "sign_divergence", explica en una
> oración qué significaría en la práctica un saldo negativo en vez de positivo para esa cuenta.

> Para la fila `currency_mismatch` de la cuenta `1112-02-001-00`, redacta la pregunta específica
> que debo hacerle al contador sobre la política de tipo de cambio usada para traducir esta
> cuenta, mencionando la variación de $21,530.10.

> Enlista cada fila `dormant_with_balance` y `unmatched_period_flow` en `tbl_Investigation` junto
> con su columna `Supporting docs needed`, como una lista de verificación para el contador.

**Redacción de resoluciones**
> Confirmé con el contador que [pega aquí tus notas]. Redacta el texto de `Resolution (fill in)`
> para el item_id `<pega el id>` en el mismo estilo que la columna `Auditor conclusion`.

**Comunicación con el contador / cliente**
> Redacta un correo breve para el contador enlistando cada fila sin resolver en
> `tbl_Investigation`, agrupada por categoría `Item`, con su texto de `Supporting docs needed`.

**Resúmenes visuales**
> Crea una Tabla Dinámica a partir de `tbl_ItemValidation` que resuma el monto pendiente por
> `Ledger Acct` y `Item Type`.

> Grafica los montos de `Opening variance` de `tbl_Investigation` por cuenta, de mayor a menor.

### 3. Salvaguardas — qué no pedirle a Copilot

- **No le pidas a Copilot que redecida un cruce, ni que "verifique de nuevo" una entrada de
  `Bank verification` contra el estado de cuenta original.** No tiene el PDF, y todo el diseño
  de `item_id` / las cadenas de evidencia existe precisamente para que cada cruce se rastree a
  una regla determinística que aplicó el pipeline en Python — no a un juicio hecho de nuevo cada
  vez que alguien abre el archivo. Usa Copilot para triage, verificación aritmética sobre
  números que ya están en la hoja, y redacción — no para volver a conciliar.
- **Todo lo que escribas en `Resolution (fill in)` se sobrescribirá la próxima vez que corra el
  pipeline**, hasta que el pipeline se actualice para leer las resoluciones antes de regenerar
  el libro. Guarda una copia de cualquier cosa importante fuera de la hoja (o en una pestaña
  separada que sepas que el pipeline no toca) mientras eso no esté resuelto.
- Si la respuesta a una instrucción menciona un conteo de `Periods open` o un total en pesos,
  verifícalo una vez contra la hoja — Copilot es muy bueno redactando texto y construyendo
  fórmulas a partir de valores existentes, y menos confiable en el momento en que una
  instrucción implica que debería recalcular algo de forma independiente.
