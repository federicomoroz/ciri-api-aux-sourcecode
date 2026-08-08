"""El limitador reparte turnos sin llegar al 429, y sin dormir en los tests.

Existe porque esto vivía dentro de `LLMManager` como método privado: el camino del
modelo tenía control preventivo y el de embeddings no. Voyage tiene el límite más
ajustado del sistema —3 por minuto— y se comió un 429 en producción por eso.

Todos los tests inyectan reloj y `dormir`. Un test que tarda un minuto en
verificar un límite de un minuto no se corre nunca, y parchear el módulo `time`
entero afecta a cualquier cosa que corra en el mismo proceso.
"""

import threading

import pytest

from api.app.rate_limiter import SIN_LIMITE, RateLimiter


@pytest.fixture
def reloj():
    return {"t": 1000.0, "dormido": 0.0}


@pytest.fixture
def limitador(reloj):
    def dormir(s):
        reloj["dormido"] += s
        reloj["t"] += s      # el reloj avanza, si no el bucle no termina

    return RateLimiter(ventana_s=60.0, reloj=lambda: reloj["t"], dormir=dormir)


class TestPorDebajoDelTechoNoSeEspera:
    """La primera investigación es la que alguien está mirando."""

    def test_las_primeras_pasan_derecho(self, limitador, reloj):
        for _ in range(5):
            limitador.esperar_turno("gemini", 5)
        assert reloj["dormido"] == 0.0

    def test_devuelve_cuanto_espero(self, limitador):
        assert limitador.esperar_turno("gemini", 5) == 0.0


class TestAlLlenarLaVentanaSeEspera:

    def test_la_siguiente_espera_lo_que_falta(self, limitador, reloj):
        for _ in range(5):
            limitador.esperar_turno("gemini", 5)
        limitador.esperar_turno("gemini", 5)
        assert reloj["dormido"] == pytest.approx(60.0, abs=0.1)

    def test_y_lo_informa(self, limitador):
        for _ in range(3):
            limitador.esperar_turno("groq", 3)
        assert limitador.esperar_turno("groq", 3) == pytest.approx(60.0, abs=0.1)

    def test_la_ventana_es_deslizante_y_no_acumula_deuda(self, limitador, reloj):
        for _ in range(5):
            limitador.esperar_turno("gemini", 5)
        reloj["t"] += 61.0                      # pasó el minuto
        limitador.esperar_turno("gemini", 5)
        assert reloj["dormido"] == 0.0

    def test_solo_vence_la_mas_vieja(self, limitador, reloj):
        """Media ventana vencida deja medio turno, no la ventana entera."""
        for _ in range(3):
            limitador.esperar_turno("x", 3)
        reloj["t"] += 61.0
        for _ in range(3):
            limitador.esperar_turno("x", 3)
        assert limitador.turnos_usados("x") == 3


class TestCadaClaveLlevaSuPropiaCuenta:
    """Que Gemini esté en su techo no tiene por qué frenar a Groq."""

    def test_no_se_pisan(self, limitador, reloj):
        for _ in range(5):
            limitador.esperar_turno("gemini", 5)
        limitador.esperar_turno("groq", 5)
        assert reloj["dormido"] == 0.0

    def test_se_cuentan_por_separado(self, limitador):
        limitador.esperar_turno("gemini", 5)
        limitador.esperar_turno("gemini", 5)
        limitador.esperar_turno("groq", 5)
        assert limitador.turnos_usados("gemini") == 2
        assert limitador.turnos_usados("groq") == 1


class TestSinLimiteConocidoNoSeFrena:
    """Cero significa «no se conoce techo», y es el caso de los planes pagos.

    Inventarle un límite a una cuenta que pagó por no tenerlo sería peor que no
    limitar: le agrega latencia a cambio de nada.
    """

    @pytest.mark.parametrize("limite", [SIN_LIMITE, 0, -1])
    def test_pasa_de_largo(self, limitador, reloj, limite):
        for _ in range(50):
            limitador.esperar_turno("anthropic", limite)
        assert reloj["dormido"] == 0.0

    def test_ni_siquiera_lleva_la_cuenta(self, limitador):
        for _ in range(10):
            limitador.esperar_turno("anthropic", SIN_LIMITE)
        assert limitador.turnos_usados("anthropic") == 0


class TestOlvidar:

    def test_una_clave(self, limitador):
        limitador.esperar_turno("a", 5)
        limitador.esperar_turno("b", 5)
        limitador.olvidar("a")
        assert limitador.turnos_usados("a") == 0
        assert limitador.turnos_usados("b") == 1

    def test_todas(self, limitador):
        limitador.esperar_turno("a", 5)
        limitador.esperar_turno("b", 5)
        limitador.olvidar()
        assert limitador.turnos_usados("a") == 0 and limitador.turnos_usados("b") == 0


class TestEsSeguroEntreHilos:
    """Las rutas sincrónicas de FastAPI corren en un pool: dos investigaciones
    simultáneas comparten esta instancia.

    Con el reloj congelado, N hilos pidiendo turno contra un techo de N tienen que
    repartirse exactamente N turnos — ni uno más, que sería pasarse del límite.
    """

    def test_no_se_otorgan_mas_turnos_que_el_techo(self):
        # Reloj congelado y `dormir` que no avanza el tiempo: el que no entra,
        # espera para siempre. Por eso se piden exactamente los que entran.
        limitador = RateLimiter(ventana_s=60.0, reloj=lambda: 1000.0, dormir=lambda s: None)
        barrera = threading.Barrier(8)

        def pedir():
            barrera.wait()
            limitador.esperar_turno("compartida", 8)

        hilos = [threading.Thread(target=pedir) for _ in range(8)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join(timeout=5)

        assert not any(h.is_alive() for h in hilos), "alguno quedo esperando de mas"
        assert limitador.turnos_usados("compartida") == 8
