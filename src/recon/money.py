"""Money is Decimal, quantized to 2 places. Never float (§5).

Floats are how you get 75495.79000000001 (cell F1646 of the June ledger) and a residual that
fails an exact-zero invariant.
"""
from __future__ import annotations

import re
from decimal import Decimal, ROUND_HALF_UP

CENTS = Decimal("0.01")
ZERO = Decimal("0.00")


def q(value) -> Decimal:
    """Quantize any Decimal/int/float/str to 2 places.

    A float is stringified via round() first, so 3158224.0599999996 -> Decimal('3158224.06'),
    never Decimal('3158224.0599999996') (§9.2).
    """
    if isinstance(value, float):
        value = round(value, 2)
    return Decimal(str(value)).quantize(CENTS, rounding=ROUND_HALF_UP)


_NUM_RE = re.compile(r"-?[\d,]+\.\d{2}")


def parse_amount(text: str) -> Decimal:
    """Parse a printed amount like '3,394,770.89' or '-1,051.61' into a quantized Decimal."""
    m = _NUM_RE.search(text)
    if not m:
        raise ValueError(f"no amount in {text!r}")
    return q(m.group(0).replace(",", ""))
