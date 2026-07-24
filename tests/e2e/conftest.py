"""
E2E test configuration.

These tests hit a REAL running API instance (no mocks).
Set CB_E2E_BASE_URL to point to your target:

    # Local (docker-compose up)
    CB_E2E_BASE_URL=http://localhost:8000 pytest tests/e2e/ -v

    # Render deploy
    CB_E2E_BASE_URL=https://ciri-chargeback-agent.onrender.com pytest tests/e2e/ -v
"""

import os

import httpx
import pytest

BASE_URL = os.environ.get("CB_E2E_BASE_URL", "https://ciri-chargeback-agent.onrender.com")


@pytest.fixture(scope="session")
def base_url() -> str:
    return BASE_URL


@pytest.fixture(scope="session")
def client() -> httpx.Client:
    """Shared httpx client with long timeout (LLM calls can take 30s+)."""
    with httpx.Client(base_url=BASE_URL, timeout=120.0) as c:
        yield c


@pytest.fixture(scope="session", autouse=True)
def _check_api_reachable(client: httpx.Client):
    """Fail fast if the API is not reachable."""
    try:
        r = client.get("/health")
        assert r.status_code == 200, f"Health check failed: {r.status_code}"
    except httpx.ConnectError:
        pytest.skip(f"API not reachable at {BASE_URL}")
