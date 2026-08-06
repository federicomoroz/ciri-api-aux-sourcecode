"""Confirmar que el n8n de quien evalúa llega hasta la API.

Quien importa el workflow y lo dispara no tiene forma, desde su lado, de saber
si la llamada llegó. Esto registra que pasó y hace cuánto, para que el panel se
lo confirme.

Lo que estos tests fijan además: que NO se guarde de dónde vino. La API es
pública y compartida; anotar la instancia de quien la prueba y mostrársela a
otro sería filtrarla.
"""

import pytest

from api.app.observability.contacto_n8n import ContactoN8n


class RelojFalso:
    """Un reloj que avanza cuando se le dice, para no dormir en los tests."""

    def __init__(self):
        self.ahora = 1000.0

    def __call__(self) -> float:
        return self.ahora

    def avanzar(self, segundos: float) -> None:
        self.ahora += segundos


@pytest.fixture
def reloj():
    return RelojFalso()


@pytest.fixture
def registro(reloj):
    return ContactoN8n(reloj=reloj)


class TestSinContactoTodavia:
    def test_arranca_sin_nada_que_confirmar(self, registro):
        assert registro.hace_cuanto() is None

    def test_no_cuenta_contactos_que_no_hubo(self, registro):
        assert registro.total == 0


class TestCuandoLlegaUnaLlamada:
    def test_queda_registrada(self, registro):
        registro.registrar()
        assert registro.hace_cuanto() == 0.0

    def test_la_antiguedad_crece_con_el_tiempo(self, registro, reloj):
        registro.registrar()
        reloj.avanzar(45)
        assert registro.hace_cuanto() == 45

    def test_una_llamada_nueva_reinicia_la_cuenta(self, registro, reloj):
        registro.registrar()
        reloj.avanzar(300)
        registro.registrar()
        assert registro.hace_cuanto() == 0.0

    def test_lleva_el_total(self, registro):
        for _ in range(3):
            registro.registrar()
        assert registro.total == 3

    def test_un_reloj_que_retrocede_no_da_antiguedad_negativa(self, registro, reloj):
        """Nada deberia haber pasado 'hace -5 segundos'."""
        registro.registrar()
        reloj.avanzar(-5)
        assert registro.hace_cuanto() == 0.0


class TestNoGuardaDeDondeVino:
    def test_no_expone_ninguna_url_ni_host(self, registro):
        """La API es publica: la instancia de uno no puede aparecerle a otro."""
        registro.registrar()
        expuesto = {n for n in dir(registro) if not n.startswith("_")}
        assert expuesto == {"registrar", "hace_cuanto", "total"}

    def test_registrar_no_acepta_datos_del_llamador(self, registro):
        """Si no se puede pasar el origen, no se puede filtrar por descuido."""
        with pytest.raises(TypeError):
            registro.registrar("http://localhost:5678")


class TestConcurrencia:
    def test_no_se_pierden_contactos_simultaneos(self, registro):
        """Varias ramas del workflow pueden llegar a la vez."""
        import threading

        hilos = [threading.Thread(target=registro.registrar) for _ in range(50)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()
        assert registro.total == 50
