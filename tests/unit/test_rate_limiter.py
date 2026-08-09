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

from api.app.rate_limiter import SIN_LIMITE, RateLimiter, avisar_esperas


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


class TestAvisarLaEspera:
    """Esperar turno es correcto; parecer colgado no.

    Desde afuera, un limitador haciendo su trabajo y un proceso trabado se ven
    exactamente igual: el panel mostraba «Sintetizando resolucion...» y un latido
    durante un minuto sin decir por que. Y es el caso NORMAL del modo demo —
    Gemini free son 5 pedidos por minuto y una investigacion hace 3 llamadas, o
    sea que el segundo analisis seguido siempre espera.
    """

    def test_avisa_antes_de_dormir_y_con_lo_que_falta(self, limitador, reloj):
        """Antes, no despues: un aviso posterior llega cuando ya no sirve."""
        avisos = []
        with avisar_esperas(lambda clave, seg: avisos.append((clave, seg, reloj["dormido"]))):
            for _ in range(6):
                limitador.esperar_turno("gemini", 5)

        assert len(avisos) == 1, "la sexta llamada es la unica que espera"
        clave, segundos, dormido_al_avisar = avisos[0]
        assert clave == "gemini"
        assert segundos > 0, "un aviso sin cuanto falta no permite mostrar una cuenta atras"
        assert dormido_al_avisar == 0.0, "el aviso llego DESPUES de dormir: ya no sirve"

    def test_sin_nadie_escuchando_no_pasa_nada(self, limitador):
        """El limitador se usa tambien fuera del panel: scripts, n8n, tests."""
        otorgados = [limitador.esperar_turno("gemini", 5) for _ in range(6)]
        assert len(otorgados) == 6, "alguna llamada no volvio"
        assert otorgados[-1] > 0, "la sexta tenia que esperar"

    def test_un_aviso_que_revienta_no_tumba_la_espera(self, limitador):
        """El que escucha puede haberse ido: cerro la pestania a mitad del analisis.

        Informar sobre la espera no puede ser una forma nueva de que la espera
        falle — el mismo criterio que el tracer.
        """
        def se_rompe(clave, segundos):
            raise RuntimeError("el cliente se fue")

        with avisar_esperas(se_rompe):
            otorgados = [limitador.esperar_turno("gemini", 5) for _ in range(6)]
        assert len(otorgados) == 6, "el aviso roto corto la espera"
        assert otorgados[-1] > 0, "la sexta tenia que esperar igual"

    def test_el_aviso_no_se_filtra_a_otra_peticion(self, limitador):
        """El limitador se COMPARTE entre peticiones; el aviso no puede.

        Guardarlo en la instancia le mandaria los avisos de una investigacion al
        panel de otra. Por eso es un ContextVar y no un atributo.
        """
        mios = []
        otro_hilo_recibio = []

        def otra_peticion():
            # Sin contexto propio: no tiene que recibir los avisos de la de al lado.
            from api.app.rate_limiter import AVISO_DE_ESPERA
            otro_hilo_recibio.append(AVISO_DE_ESPERA.get())

        with avisar_esperas(lambda c, s: mios.append(c)):
            for _ in range(6):
                limitador.esperar_turno("gemini", 5)
            hilo = threading.Thread(target=otra_peticion)
            hilo.start()
            hilo.join(timeout=5)

        assert mios == ["gemini"], "el aviso propio no llego"
        assert otro_hilo_recibio == [None], "el aviso se filtro a otro hilo"

    def test_al_salir_del_bloque_deja_de_avisar(self, limitador):
        avisos = []
        with avisar_esperas(lambda c, s: avisos.append(c)):
            for _ in range(6):
                limitador.esperar_turno("gemini", 5)
        limitador.olvidar()
        for _ in range(6):
            limitador.esperar_turno("gemini", 5)
        assert len(avisos) == 1, "siguio avisando fuera del bloque"


class TestLaEsperaLlegaAlPanel:
    """El circuito entero: del limitador al stream que mira el evaluador.

    Las piezas se prueban arriba por separado; esto verifica que esten
    conectadas. El aviso nace tres capas abajo del generador —`RateLimiter`,
    dentro del cliente, dentro del servicio— y tiene que salir por la misma cola
    SSE que el resto, que es el unico hilo autorizado a escribir en el stream.
    """

    def test_una_espera_por_cuota_sale_como_evento_sse(self):
        import json

        import api.app.routes.panel as panel

        reloj = {"t": 0.0}
        limitador = RateLimiter(
            ventana_s=60.0, reloj=lambda: reloj["t"],
            dormir=lambda s: reloj.__setitem__("t", reloj["t"] + s),
        )

        def eventos():
            yield panel._sse("resolving", {})
            for _ in range(6):          # la sexta se topa con el techo de 5/min
                limitador.esperar_turno("gemini", 5)
            yield panel._sse("done", {"html": "<html/>", "usage": {}})

        salida = list(panel._con_latido(eventos(), cada_s=0.05))
        esperas = [linea for linea in salida if '"step": "espera"' in linea]
        assert esperas, "la espera no llego al stream: el panel la ve como un cuelgue"

        evento = json.loads(esperas[0].removeprefix("data: ").strip())
        assert evento["proveedor"] == "gemini"
        assert evento["segundos"] > 0, "sin cuanto falta no se puede mostrar una cuenta atras"
        # Y llega ANTES de que el paso termine, que es cuando sirve.
        assert salida.index(esperas[0]) < len(salida) - 1
