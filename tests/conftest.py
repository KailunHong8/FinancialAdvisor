import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

LEDGER = ROOT / "01-12-2024-PatyMaiki2022-2-Kai-test.xlsx"
# The June BBVA statements live in the repo-root `statements/` folder (gitignored client data),
# one PDF per account, filename tail = account last-4. Tests skip if the folder is absent.
STATEMENTS_DIR = ROOT / "statements"


def find_statement(tail: str) -> Path | None:
    if not STATEMENTS_DIR.exists():
        return None
    return next((p for p in STATEMENTS_DIR.glob("*.pdf")
                 if f"JUNIO" in p.name.upper() and tail in p.name), None)


# account 0114091108 — used by the single-statement bank tests
STATEMENT_FILE = find_statement("1108")


@pytest.fixture
def config():
    from recon.config import Config
    return Config(str(ROOT / "config"))


@pytest.fixture
def ledger_path():
    if not LEDGER.exists():
        pytest.skip("real ledger workbook not present")
    return str(LEDGER)


@pytest.fixture
def statement_bytes():
    if STATEMENT_FILE is None or not STATEMENT_FILE.exists():
        pytest.skip("real BBVA statement not present")
    return STATEMENT_FILE.read_bytes()


@pytest.fixture
def june_data_root(tmp_path):
    """Stage every June statement + the ledger into a run root; skip if inputs absent."""
    if not (LEDGER.exists() and STATEMENTS_DIR.exists()):
        pytest.skip("real ledger and/or statements not present")
    junio = [p for p in STATEMENTS_DIR.glob("*.pdf") if "JUNIO" in p.name.upper()]
    if not junio:
        pytest.skip("no June statements present")
    import shutil
    (tmp_path / "ledger").mkdir()
    (tmp_path / "statements" / "2024-06").mkdir(parents=True)
    shutil.copy(LEDGER, tmp_path / "ledger" / LEDGER.name)
    for p in junio:
        shutil.copy(p, tmp_path / "statements" / "2024-06" / p.name)
    return tmp_path, len(junio)


@pytest.fixture
def memdb():
    from recon import store
    conn = store.connect(":memory:")
    store.init_db(conn)
    return conn
