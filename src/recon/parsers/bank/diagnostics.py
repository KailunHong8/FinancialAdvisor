"""Diagnostic bundle for an unrecognized layout (§10.4). No automated fallback, no guessing:
the run stops and emits everything a human needs to hand to Copilot for a template update."""
from __future__ import annotations

import io
import json
import re
from pathlib import Path

import pdfplumber

from ...config import BankConfig


COPILOT_PROMPT = """# Update the {bank} statement layout template

The reconciliation engine could not parse this statement with the current
`config/banks/{bank}.yml`. Nothing was guessed — this bundle contains the raw evidence.

## What to do
1. Open `words_page01.csv` (columns: text, x0, x1, top, bottom) and `raw_text_page01.txt`.
2. Complete `draft_template.yml`:
   - Fix each regex under `header_fields` so it captures the value shown in the raw text
     (bank_account, clabe, period, opening/closing balances, total abonos/cargos, currency flag).
   - Set the `table.columns` x-ranges from the `column_histogram.txt` clusters — each column is a
     tight [x_lo, x_hi] band around the words that belong to it.
3. Copy the completed YAML to `config/banks/{bank}.yml` and bump `parser_version`.
4. Add this PDF (redacted if needed) as a regression fixture with its expected BankStatement.
5. Run `recon verify-template {bank}` — it reruns every fixture plus invariant I3.

## Acceptance test the result MUST pass (I3)
- parsed opening + Σabonos − Σcargos == parsed closing (to the cent)
- Σ of parsed ABONO lines == the printed 'Depósitos / Abonos' total
- Σ of parsed CARGO lines == the printed 'Retiros / Cargos' total
Only after these pass is the template promoted (registry marked copilot_assisted_confirmed).
"""


def write_bundle(out_dir: Path, pdf_bytes: bytes, cfg: BankConfig,
                 failure_report: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for pi, page in enumerate(pdf.pages, start=1):
            tag = f"page{pi:02d}"
            (out_dir / f"raw_text_{tag}.txt").write_text(page.extract_text() or "")
            words = page.extract_words()
            rows = ["text,x0,x1,top,bottom"]
            for w in words:
                txt = w["text"].replace('"', "'")
                rows.append(f'"{txt}",{w["x0"]:.1f},{w["x1"]:.1f},{w["top"]:.1f},{w["bottom"]:.1f}')
            (out_dir / f"words_{tag}.csv").write_text("\n".join(rows))
            try:
                tables = page.extract_tables()
                (out_dir / f"tables_{tag}.txt").write_text(
                    "\n\n".join(str(t) for t in tables) if tables else "(no tables)")
            except Exception as e:  # noqa: BLE001
                (out_dir / f"tables_{tag}.txt").write_text(f"(extract_table failed: {e})")
            if pi == 1:
                _markers(out_dir, page.extract_text() or "", cfg)
                _histogram(out_dir, words)
    (out_dir / "draft_template.yml").write_text(_draft(cfg))
    (out_dir / "COPILOT_PROMPT.md").write_text(COPILOT_PROMPT.format(bank=cfg.bank_name))
    (out_dir / "failure_report.md").write_text(failure_report)
    return out_dir


def _markers(out_dir: Path, text: str, cfg: BankConfig) -> None:
    result = {}
    for name, rx in cfg.header_fields.items():
        m = re.search(rx, text)
        result[name] = {"hit": bool(m), "value": (m.groups() if m else None)}
    (out_dir / "markers_found.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))


def _histogram(out_dir: Path, words) -> None:
    xs = sorted(round(w["x0"]) for w in words)
    counts: dict[int, int] = {}
    for x in xs:
        bucket = (x // 5) * 5
        counts[bucket] = counts.get(bucket, 0) + 1
    lines = ["x0_bucket,count,suggested_column_boundary"]
    prev = None
    for b in sorted(counts):
        boundary = "<--" if prev is not None and b - prev > 10 else ""
        lines.append(f"{b},{counts[b]},{boundary}")
        prev = b
    (out_dir / "column_histogram.txt").write_text("\n".join(lines))


def _draft(cfg: BankConfig) -> str:
    import yaml
    return yaml.safe_dump(cfg.model_dump(), sort_keys=False, allow_unicode=True)
