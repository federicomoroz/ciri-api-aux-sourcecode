"""
Unit tests for Excel data loader.
Tests the real dataset file to verify parsing is correct.
"""

import pytest

from api.app.domain.enums import PaymentMethod

EXCEL_PATH = "data/Similación_dataset_contracargos_.xlsx"


@pytest.fixture(scope="module")
def loaded_data():
    """Load the real Excel file once for all tests in this module."""
    try:
        from api.app.data.loader import load_excel
        return load_excel(EXCEL_PATH)
    except FileNotFoundError:
        pytest.skip(f"Dataset file not found: {EXCEL_PATH}")


def test_transaction_count(loaded_data):
    """Should load exactly 100 transactions."""
    assert len(loaded_data["transactions"]) == 100


def test_case_count(loaded_data):
    """Should load exactly 60 historical cases."""
    assert len(loaded_data["cases"]) == 60


def test_policy_count(loaded_data):
    """Should load exactly 17 policies."""
    assert len(loaded_data["policies"]) == 17


def test_log_count(loaded_data):
    """Should load exactly 150 log events."""
    assert len(loaded_data["logs"]) == 150


def test_transaction_types(loaded_data):
    """Verify correct data types for key fields."""
    tx = loaded_data["transactions"][0]
    assert isinstance(tx["amount_usd"], float), "amount_usd must be float"
    assert isinstance(tx["fraud_score"], int), "fraud_score must be int"
    assert isinstance(tx["date"], str), "date must be string"


def test_txn_00051_crypto_blocker(loaded_data):
    """TXN-00051 must have Cripto payment method and fraud_score=8 (BLOCKER scenario)."""
    txns = {t["id"]: t for t in loaded_data["transactions"]}
    assert "TXN-00051" in txns, "TXN-00051 must exist in dataset"
    tx = txns["TXN-00051"]
    assert tx["payment_method"] == PaymentMethod.CRYPTO, f"Expected 'Cripto', got '{tx['payment_method']}'"
    assert tx["fraud_score"] == 8, f"Expected score=8, got {tx['fraud_score']}"
    assert tx["country"] == "COL", f"Expected 'COL', got '{tx['country']}'"


def test_policy_code_parsing(loaded_data):
    """Policy codes should be extracted correctly from 'POL-XXX-NNN — Name' format."""
    policies = {p["code"]: p for p in loaded_data["policies"]}
    assert "POL-FRD-001" in policies, "POL-FRD-001 must exist"
    assert "POL-EXC-003" in policies, "POL-EXC-003 must exist"
    # Names should not contain the code
    assert "POL-FRD-001" not in policies["POL-FRD-001"]["name"], \
        "Policy name should not contain the code"


def test_log_code_is_string(loaded_data):
    """Logs.Codigo must be a string, not an integer."""
    log = loaded_data["logs"][0]
    assert isinstance(log["code"], str), f"Log code must be str, got {type(log['code'])}"
    assert log["code"] in {"200", "201", "401", "402", "408", "409", "429", "500", "503", "504"}, \
        f"Unexpected HTTP code: {log['code']}"


def test_dates_are_strings(loaded_data):
    """All date fields must be strings (not datetime objects)."""
    tx = loaded_data["transactions"][0]
    assert isinstance(tx["date"], str), "Transaction date must be string"

    case = loaded_data["cases"][0]
    assert isinstance(case["open_date"], str), "Case open_date must be string"
    assert isinstance(case["close_date"], str), "Case close_date must be string"

    log = loaded_data["logs"][0]
    assert isinstance(log["timestamp"], str), "Log timestamp must be string"


def test_policy_categories(loaded_data):
    """Should have 4 FRAUDE, 5 CHARGEBACK, 4 SLA, 4 EXCEPCION policies."""
    from collections import Counter
    cats = Counter(p["category"] for p in loaded_data["policies"])
    assert cats.get("FRAUDE", 0) == 4
    assert cats.get("CHARGEBACK", 0) == 5
    assert cats.get("SLA", 0) == 4
    assert cats.get("EXCEPCIÓN", 0) + cats.get("EXCEPCION", 0) == 4  # Excel uses EXCEPCIÓN


def test_transaction_ids_format(loaded_data):
    """All transaction IDs should match TXN-XXXXX format."""
    import re
    pattern = re.compile(r"^TXN-\d{5}$")
    for tx in loaded_data["transactions"]:
        assert pattern.match(tx["id"]), f"Invalid TXN ID format: {tx['id']}"


def test_seed_is_idempotent(loaded_data, tmp_path):
    """Sembrar dos veces tiene que dejar la base igual que sembrar una.

    Los logs no tienen clave de negocio con la que deduplicar, asi que una
    segunda corrida los duplicaba y falseaba la deteccion de patrones de error.
    """
    import sqlite3

    from api.app.data.loader import init_sqlite

    db = str(tmp_path / "seed.db")
    init_sqlite(db, loaded_data)
    conn = sqlite3.connect(db)
    first = {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("transactions", "cases", "policies", "logs")
    }
    conn.close()

    init_sqlite(db, loaded_data)
    conn = sqlite3.connect(db)
    second = {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("transactions", "cases", "policies", "logs")
    }
    conn.close()

    assert first == second, f"la segunda corrida cambio los conteos: {first} -> {second}"
    assert first["logs"] == len(loaded_data["logs"])


class TestLaListaDelPanelTraeElMotivo:
    """El motivo es un dato del caso, no una eleccion de quien evalua.

    El panel lo pedia siempre en un desplegable aparte, aunque 47 de las 100
    transacciones tienen un caso abierto con su motivo registrado. Se podia
    analizar «Cargo duplicado» sobre un caso asentado como fraude con tarjeta
    robada: el informe salia coherente y describia algo que no ocurrio.
    """

    @staticmethod
    def _db(tmp_path):
        import sqlite3

        from api.app.data.db import Database

        ruta = str(tmp_path / "t.db")
        c = sqlite3.connect(ruta)
        c.executescript(
            "CREATE TABLE transactions (id TEXT, merchant TEXT, amount_usd REAL,"
            " country TEXT, payment_method TEXT, fraud_score INT, channel TEXT, status TEXT);"
            "CREATE TABLE cases (case_id TEXT, transaction_id TEXT, motivo TEXT, open_date TEXT);"
            "INSERT INTO transactions VALUES ('TXN-00001','Amazon',10,'ARG','Credito',50,'Web','x');"
            "INSERT INTO transactions VALUES ('TXN-00002','eBay',20,'PER','Debito',30,'POS','x');"
            "INSERT INTO cases VALUES ('CB-1','TXN-00002','Fraude con tarjeta robada','2024-01-01');"
            "INSERT INTO cases VALUES ('CB-2','TXN-00002','Cargo duplicado','2024-06-01');"
        )
        c.commit()
        c.close()
        return Database(ruta)

    def test_la_transaccion_con_caso_trae_su_motivo(self, tmp_path):
        filas = {t["id"]: t for t in self._db(tmp_path).list_transactions_compact()}
        assert filas["TXN-00002"]["motivo"] == "Cargo duplicado"

    def test_gana_el_caso_mas_reciente(self, tmp_path):
        """Es el que se esta disputando, no el que se archivo hace meses."""
        filas = {t["id"]: t for t in self._db(tmp_path).list_transactions_compact()}
        assert filas["TXN-00002"]["motivo"] != "Fraude con tarjeta robada"

    def test_la_que_no_tiene_caso_no_inventa_uno(self, tmp_path):
        """Ahi el panel tiene que seguir preguntandolo: no hay de donde sacarlo."""
        filas = {t["id"]: t for t in self._db(tmp_path).list_transactions_compact()}
        assert filas["TXN-00001"]["motivo"] is None

    def test_una_transaccion_es_una_fila(self, tmp_path):
        """Con un JOIN, la que tenia dos casos aparecia dos veces en el desplegable."""
        filas = self._db(tmp_path).list_transactions_compact()
        ids = [t["id"] for t in filas]
        assert len(ids) == len(set(ids)) == 2
