"""Generic YAML-driven BBVA statement parser (§10.2).

All markers and column geometry live in config/banks/bbva.yml; this module carries only the
generic logic that consumes them, so a layout change is usually a config edit (clarification 3).
"""
from __future__ import annotations

import re
from datetime import date

import pdfplumber

from ...config import BankConfig
from ...models import BankStatement, StatementLine
from ...money import q, parse_amount, ZERO
from .base import BankParseError


class BBVAParser:
    def __init__(self, cfg: BankConfig):
        self.cfg = cfg
        self.bank_name = cfg.bank_name
        self.parser_version = cfg.parser_version
        self._hf = {k: re.compile(v) for k, v in cfg.header_fields.items()}
        self._months = {m: i for i, m in enumerate(cfg.table["month_names_es"], start=1)}
        self._date_word = re.compile(r"^(\d{2})/([A-Z]{3})$")
        self._cols = cfg.table["columns"]
        self._ref_re = re.compile(cfg.reference_regex) if cfg.reference_regex else None
        oper = self._cols["oper_date"]
        self._oper_lo, self._oper_hi = oper[0], oper[1]

    # -- sniffing -----------------------------------------------------------
    def sniff(self, pdf_bytes: bytes) -> bool:
        import io
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            text = pdf.pages[0].extract_text() or ""
        return any(marker in text for marker in self.cfg.sniff_any)

    # -- helpers ------------------------------------------------------------
    def _mkdate(self, token: str, year: int) -> date:
        m = self._date_word.match(token)
        if not m:
            raise BankParseError(f"bad statement date {token!r}")
        month = self._months.get(m.group(2))
        if month is None:
            raise BankParseError(f"unknown month {m.group(2)!r}")
        return date(year, month, int(m.group(1)))

    def _col_of(self, word) -> str | None:
        center = (word["x0"] + word["x1"]) / 2
        for name, (lo, hi) in self._cols.items():
            if lo <= center < hi:
                return name
        return None

    @staticmethod
    def _rows(page):
        words = page.extract_words()
        buckets: dict[int, list] = {}
        for w in words:
            buckets.setdefault(round(w["top"]), []).append(w)
        return [sorted(buckets[t], key=lambda w: w["x0"]) for t in sorted(buckets)]

    def _is_txn_start(self, row) -> bool:
        w = row[0]
        return (w["x0"] < self._oper_hi and self._oper_lo - 2 <= w["x0"]
                and bool(self._date_word.match(w["text"])))

    # -- parsing ------------------------------------------------------------
    def parse(self, pdf_bytes: bytes, source_file: str, source_sha256: str) -> list[BankStatement]:
        import io
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            page1 = pdf.pages[0].extract_text() or ""
            fields = self._header_fields(page1)
            year = fields["period_start"].year
            lines: list[StatementLine] = []
            line_no = 0
            for pi, page in enumerate(pdf.pages, start=1):
                rows = self._rows(page)
                i = 0
                while i < len(rows):
                    row = rows[i]
                    if not self._is_txn_start(row):
                        i += 1
                        continue
                    # collect continuation rows (no date anchor) until next txn / table noise
                    cont = []
                    j = i + 1
                    while j < len(rows) and not self._is_txn_start(rows[j]):
                        cont.append(rows[j])
                        j += 1
                    line_no += 1
                    lines.append(self._build_line(pi, line_no, row, cont, year))
                    i = j

        stmt = self._finalize(fields, lines, source_file, source_sha256)
        return [stmt]

    def _header_fields(self, text: str) -> dict:
        def grp(name, idx=1):
            m = self._hf[name].search(text)
            return m.group(idx) if m else None

        pm = self._hf["period"].search(text)
        if not pm:
            raise BankParseError("could not read statement period from page 1")
        ps = self._d(pm.group(1))
        pe = self._d(pm.group(2))
        bank_account = grp("bank_account")
        if not bank_account:
            raise BankParseError("could not read 'No. de Cuenta' from page 1")
        opening = grp("opening"); closing = grp("closing")
        ta = grp("total_abonos"); tc = grp("total_cargos")
        if None in (opening, closing, ta, tc):
            raise BankParseError("missing a printed summary total (opening/closing/abonos/cargos)")
        currency = "USD" if self._hf["currency_usd"].search(text) else "MXN"
        return {
            "bank_account": bank_account,
            "clabe": grp("clabe"),
            "currency": currency,
            "period_start": ps, "period_end": pe,
            "opening": q(opening.replace(",", "")),
            "closing": q(closing.replace(",", "")),
            "total_abonos": q(ta.replace(",", "")),
            "total_cargos": q(tc.replace(",", "")),
        }

    @staticmethod
    def _d(s: str) -> date:
        d, m, y = s.split("/")
        return date(int(y), int(m), int(d))

    def _build_line(self, page_no, line_no, row, cont, year) -> StatementLine:
        oper_date = liq_date = None
        code = None
        desc_parts, cargo, abono, s_oper, s_liq = [], None, None, None, None
        dates_seen = []
        for w in row:
            col = self._col_of(w)
            t = w["text"]
            if col in ("oper_date", "liq_date") and self._date_word.match(t):
                dates_seen.append((col, t))
            elif col == "code" and code is None and re.match(r"^[A-Z]\d{2}$", t):
                code = t
            elif col == "description":
                desc_parts.append(t)
            elif col == "cargo" and _is_amount(t):
                cargo = parse_amount(t)
            elif col == "abono" and _is_amount(t):
                abono = parse_amount(t)
            elif col == "saldo_operacion" and _is_amount(t):
                s_oper = parse_amount(t)
            elif col == "saldo_liquidacion" and _is_amount(t):
                s_liq = parse_amount(t)
            elif col is None:
                desc_parts.append(t)
        for col, t in dates_seen:
            if col == "oper_date" and oper_date is None:
                oper_date = self._mkdate(t, year)
            elif col == "liq_date" and liq_date is None:
                liq_date = self._mkdate(t, year)
        if oper_date is None and dates_seen:
            oper_date = self._mkdate(dates_seen[0][1], year)

        cont_text = " ".join(w["text"] for r in cont for w in r)
        description = " ".join(desc_parts + ([cont_text] if cont_text else [])).strip()
        reference = None
        if self._ref_re:
            m = self._ref_re.search(description)
            if m:
                reference = m.group(1)
        return StatementLine(
            page_no=page_no, line_no=line_no, oper_date=oper_date, liq_date=liq_date,
            code=code, description=description, reference=reference,
            cargo=cargo or ZERO, abono=abono or ZERO,
            running_balance=s_liq if s_liq is not None else s_oper,
        )

    def _finalize(self, f, lines, source_file, source_sha256) -> BankStatement:
        return BankStatement(
            bank_name=self.bank_name, bank_account=f["bank_account"], clabe=f["clabe"],
            currency=f["currency"], period_start=f["period_start"], period_end=f["period_end"],
            opening_balance=f["opening"], closing_balance=f["closing"],
            total_abonos=f["total_abonos"], total_cargos=f["total_cargos"],
            lines=lines, source_file=source_file, source_sha256=source_sha256,
            parser_version=self.parser_version,
        )


_AMOUNT_RE = re.compile(r"^-?[\d,]+\.\d{2}$")


def _is_amount(text: str) -> bool:
    return bool(_AMOUNT_RE.match(text))
