"""La API tiene que arrancar aunque Qdrant no responda.

No es una hipotesis: el free tier de Qdrant Cloud suspende los clusters tras una
semana sin uso, y la instancia publicada corre en un Render que reinicia el
contenedor cada vez que despierta. Sin esta garantia, una suspension deja el
servicio entero sin levantar — incluidos los caminos que ni siquiera usan el
vector store, como el modo demo.

Se apunta a un puerto cerrado a proposito: es la forma mas fiel de reproducir
"el cluster no esta" sin depender de la red.
"""

import pytest
from fastapi.testclient import TestClient

PUERTO_CERRADO = "http://127.0.0.1:6399"


@pytest.fixture
def cliente_sin_qdrant(monkeypatch, tmp_path):
    """La app levantada contra un Qdrant inexistente."""
    monkeypatch.setenv("CB_QDRANT_URL", PUERTO_CERRADO)
    monkeypatch.setenv("CB_DEMO_MODE", "true")
    monkeypatch.setenv("CB_ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("CB_VOYAGE_API_KEY", "test-key")

    from api.app.main import app

    # El lifespan no arma nada si ya hay servicios en app.state, y otros tests
    # los dejan puestos. Sin vaciarlo, esta prueba no ejercitaria el arranque.
    # Se toca `_state`, el dict interno de Starlette, y no `__dict__`: ese
    # contiene a `_state`, y borrarlo deja al State sin su propio almacen.
    previo = dict(app.state._state)
    app.state._state.clear()
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.state._state.clear()
        app.state._state.update(previo)


def test_la_api_arranca_aunque_qdrant_no_responda(cliente_sin_qdrant):
    """Que Qdrant este caido no puede impedir el arranque."""
    assert cliente_sin_qdrant.get("/health").status_code == 200


def test_el_panel_sigue_en_pie_sin_qdrant(cliente_sin_qdrant):
    """El panel es la puerta de entrada: tiene que responder igual."""
    assert cliente_sin_qdrant.get("/panel").status_code == 200


def test_el_modo_demo_no_depende_del_vector_store(cliente_sin_qdrant):
    """Los casos demo se sirven del disco, asi que Qdrant les es indiferente.

    El modo directo se pide por query string, que es lo que manda el panel. Este
    test mandaba `{"modo": "directo"}` en el cuerpo, un campo que la ruta nunca
    leyo: pasaba de rebote porque el modo demo cortaba antes de intentar n8n.
    """
    r = cliente_sin_qdrant.post(
        "/api/panel/analyze?direct=1",
        json={
            "transaction_id": "TXN-00051",
            "motivo": "No reconoce la compra",
        },
    )
    assert r.status_code == 200
    assert len(r.text) > 1000, "se esperaba el informe completo, no un error"


def test_health_reporta_que_qdrant_no_esta(cliente_sin_qdrant):
    """Arrancar igual no es fingir que esta todo bien: health lo tiene que decir."""
    estado = cliente_sin_qdrant.get("/health").json()
    assert estado["qdrant"] != "ok"
