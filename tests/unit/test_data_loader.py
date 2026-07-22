"""
Unit tests for Excel data loader.
Tests the real dataset file to verify parsing is correct.
"""

import pytest

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
    assert tx["payment_method"] == "Cripto", f"Expected 'Cripto', got '{tx['payment_method']}'"
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
