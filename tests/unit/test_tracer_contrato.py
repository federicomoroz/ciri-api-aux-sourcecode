"""Las tres implementaciones de `Tracer`, sometidas al mismo cuerpo de tests.

Un Protocol declara metodos; lo que se puede sustituir por otro es el
*comportamiento*. Estos tests corren identicos sobre `NoOpTracer`,
`TrazadorLocal` y `LangfuseTracer`, que es la unica forma de ver si el contrato
se cumple de verdad — el chequeo de firmas ya lo hace el `runtime_checkable` y no
alcanza.

El contrato que se afirma tiene tres clausulas, y todas salen de como el sistema
usa el tracer, no de una teoria:

1. **`enabled` no miente.** Si dice que observa, `trace()` devuelve un id usable.
   Es de lo que depende `LangfuseStatsService` para decidir si cae al registro
   local: si `enabled` sigue en True mientras nada se anota, el panel muestra la
   fuente vacia en vez de la que si tiene datos.
2. **Anotar nunca tumba lo anotado.** Ningun metodo propaga: observar el sistema
   no puede ser una forma nueva de romperlo.
3. **`generation()` y `score()` toleran un trace_id vacio**, porque eso es
   exactamente lo que devuelve un tracer apagado.
"""

import pytest

from api.app.observability.tracer import LangfuseTracer, NoOpTracer
from api.app.observability.trazador_local import TrazadorLocal


@pytest.fixture(params=["NoOpTracer", "TrazadorLocal", "LangfuseTracer"])
def tracer(request, tmp_path):
    """Una instancia de cada implementacion, construida como en produccion.

    `LangfuseTracer` se construye con credenciales falsas a proposito: es el
    estado en el que realmente corre cuando alguien pone las variables de entorno
    mal, y es el que el contrato tiene que cubrir.
    """
    if request.param == "NoOpTracer":
        return NoOpTracer()
    if request.param == "TrazadorLocal":
        return TrazadorLocal(str(tmp_path / "trazas.db"))
    return LangfuseTracer(public_key="pk-falsa", secret_key="sk-falsa", host="http://127.0.0.1:1")


class TestElContratoQueTodasCumplen:

    def test_declara_si_observa(self, tracer):
        assert isinstance(tracer.enabled, bool)

    def test_anotar_no_propaga_nunca(self, tracer):
        """Tercera clausula, y la mas importante: el analisis vale mas que su traza."""
        tid = tracer.trace("analisis", {"txn": "TXN-00051"}, {"ok": True}, {"origen": "test"})
        tracer.generation(
            name="paso", model="modelo-x", input="", output="",
            tokens_in=10, tokens_out=20, latency_ms=1.5, trace_id=tid,
        )
        tracer.score(tid, "judge_score", 9.1)

    def test_tolera_un_trace_id_vacio(self, tracer):
        """Es lo que devuelve un tracer apagado, asi que los llamadores lo pasan."""
        tracer.generation(
            name="paso", model="m", input="", output="",
            tokens_in=1, tokens_out=1, latency_ms=1.0, trace_id="",
        )
        tracer.score("", "judge_score", 5.0)

    def test_trace_siempre_devuelve_un_texto(self, tracer):
        assert isinstance(tracer.trace("x", {}, {}), str)


class TestLaClausulaQueLangfuseIncumple:
    """`enabled` tiene que decir la verdad, y hoy no la dice.

    `LangfuseTracer.trace()` traga la excepcion, devuelve `""` y deja `_enabled`
    en `True`. El objeto queda afirmando que observa mientras no anota nada.

    No es teorico: `LangfuseStatsService.enabled` (`langfuse_stats.py:62`)
    pregunta justamente eso para decidir si usa Langfuse o cae al registro local.
    Con `enabled` mintiendo, **no** cae — y encender la observabilidad deja al
    panel con menos metricas que dejarla apagada.

    El `xfail` es estricto a proposito: cuando la fase 3 lo arregle, este test
    empieza a pasar y el marcador falla, obligando a sacarlo. Un incumplimiento
    conocido queda documentado; uno arreglado no se queda escondido detras de un
    xfail olvidado.
    """

    def test_si_dice_que_observa_entonces_traza(self, tracer):
        if not tracer.enabled:
            pytest.skip("apagado: la clausula no aplica")
        assert tracer.trace("analisis", {}, {}), (
            "dice que observa pero no devolvio un id de traza usable"
        )

    def test_langfuse_caido_se_declara_apagado(self):
        """El estado real: construyo bien y despues las llamadas fallan.

        Sin `langfuse` instalado, el constructor cae por ImportError y deja
        `_enabled` en False — ahi el contrato se cumple por accidente y el test
        de arriba se saltea. El incumplimiento aparece cuando el paquete SI esta:
        el cliente se construye, `enabled` queda en True, y recien la primera
        llamada falla. Eso es lo que pasa con el host equivocado, la red caida, o
        una version del SDK donde `.trace()` no existe.

        Se arma ese estado a mano porque es el unico que expone el defecto, y
        montarlo desde afuera seria simular una instalacion que este entorno no
        tiene.
        """
        class ClienteQueFalla:
            def trace(self, **_):
                raise RuntimeError("host inalcanzable")

        tracer = LangfuseTracer(public_key="pk", secret_key="sk", host="http://127.0.0.1:1")
        tracer._enabled = True
        tracer.langfuse = ClienteQueFalla()

        assert tracer.enabled, "el montaje del test es incorrecto: ya arrancaba apagado"
        assert tracer.trace("analisis", {}, {}) == "", "la llamada tenia que fallar"
        assert not tracer.enabled, (
            "sigue diciendo que observa despues de que la anotacion fallara: "
            "LangfuseStatsService no va a caer al registro local, y el panel va a "
            "mostrar la fuente remota vacia en vez de la local, que si tiene datos"
        )


class TestLoQueLangfuseLeManda:
    """Que se le pide al SDK, con un cliente espia en vez del real.

    Estos caminos no se ejercitaban: sin el paquete instalado el constructor
    apaga el tracer y ninguna llamada llega al cuerpo. Es la parte del codigo que
    solo corre en produccion con Langfuse encendido — o sea, la que nadie ve
    fallar hasta que falla ahi.
    """

    @pytest.fixture
    def espia(self):
        class Espia:
            def __init__(self):
                self.llamadas = []

            def trace(self, **kw):
                self.llamadas.append(("trace", kw))
                return type("T", (), {"id": "traza-1"})()

            def generation(self, **kw):
                self.llamadas.append(("generation", kw))

            def score(self, **kw):
                self.llamadas.append(("score", kw))

        return Espia()

    @pytest.fixture
    def tracer(self, espia):
        t = LangfuseTracer(public_key="pk", secret_key="sk", host="http://127.0.0.1:1")
        t._enabled = True
        t.langfuse = espia
        return t

    def test_la_traza_devuelve_el_id_que_dio_langfuse(self, tracer):
        assert tracer.trace("analisis", {"a": 1}, {"b": 2}, {"c": 3}) == "traza-1"

    def test_la_generacion_manda_los_tokens_como_usage(self, tracer, espia):
        tracer.generation(
            name="juez", model="sonnet", input="i", output="o",
            tokens_in=100, tokens_out=50, latency_ms=1500.0, trace_id="traza-1",
        )
        _, kw = next(c for c in espia.llamadas if c[0] == "generation")
        assert kw["usage"] == {"input": 100, "output": 50}
        assert kw["trace_id"] == "traza-1"

    def test_la_latencia_se_manda_como_ventana_de_tiempo(self, tracer, espia):
        """Langfuse no recibe milisegundos: recibe inicio y fin."""
        tracer.generation(
            name="p", model="m", input="", output="",
            tokens_in=1, tokens_out=1, latency_ms=2000.0,
        )
        _, kw = next(c for c in espia.llamadas if c[0] == "generation")
        transcurrido = (kw["end_time"] - kw["start_time"]).total_seconds()
        assert transcurrido == pytest.approx(2.0)

    def test_el_puntaje_del_juez_se_adjunta_a_la_traza(self, tracer, espia):
        tracer.score("traza-1", "judge_score", 9.1)
        _, kw = next(c for c in espia.llamadas if c[0] == "score")
        assert kw == {"trace_id": "traza-1", "name": "judge_score", "value": 9.1}

    def test_sin_trace_id_no_se_puntua(self, tracer, espia):
        """Un puntaje suelto no tiene a que pertenecer."""
        tracer.score("", "judge_score", 9.1)
        assert not [c for c in espia.llamadas if c[0] == "score"]

    @pytest.mark.parametrize("metodo,argumentos", [
        ("generation", {"name": "p", "model": "m", "input": "", "output": "",
                        "tokens_in": 1, "tokens_out": 1, "latency_ms": 1.0}),
        ("score", {"trace_id": "t", "name": "n", "value": 1.0}),
    ])
    def test_si_el_sdk_revienta_no_se_propaga(self, tracer, metodo, argumentos):
        def revienta(**_):
            raise RuntimeError("Langfuse caido")

        setattr(tracer.langfuse, metodo, revienta)
        getattr(tracer, metodo)(**argumentos)   # no debe levantar
