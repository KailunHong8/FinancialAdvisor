from decimal import Decimal

from recon.money import q, parse_amount
from recon.models import normalize_description


def test_decimal_fidelity_on_lossy_float():
    # cell F1646 of the June ledger is 75495.79000000001 (§5)
    assert q(75495.79000000001) == Decimal("75495.79")
    assert str(q(75495.79000000001)) == "75495.79"


def test_parse_amount_thousands():
    assert parse_amount("3,394,770.89") == Decimal("3394770.89")
    assert parse_amount("SPEI 2,746.49 x") == Decimal("2746.49")
    assert parse_amount("-1,051.61") == Decimal("-1051.61")


def test_normalize_strips_high_cardinality_tokens():
    # Trf9359103819 Ame de Quintana Roo SA F/202147 -> AME DE QUINTANA ROO SA (§11.1)
    assert normalize_description("Trf9359103819 Ame de Quintana Roo SA F/202147") \
        == "AME DE QUINTANA ROO SA"
