"""Tests de la agregacion de estadisticas de Langfuse.

El SDK devuelve objetos o diccionarios segun la version y el endpoint, asi que
se prueban las dos formas: era el motivo de los doce accesos defensivos que
ahora concentra `_campo`.
"""

from types import SimpleNamespace


from api.app.observability.tracer import NoOpTracer
from api.app.services.langfuse_stats import LangfuseStatsService


class RespuestaObj:
    def __init__(self, data):
        self.data = data


class LangfuseFalso:
    """Emula el SDK: trazas como objetos, observaciones y puntajes como dicts."""

    def __init__(self, trazas, observaciones, puntajes):
        self._trazas = trazas
        self._observaciones = observaciones
        self.client = SimpleNamespace(score=SimpleNamespace(get=lambda limit: RespuestaObj(puntajes)))

    def fetch_traces(self, limit):
        return RespuestaObj(self._trazas)

    def fetch_observations(self, type, limit):
        return RespuestaObj(self._observaciones)


class TracerFalso:
    enabled = True

    def __init__(self, langfuse):
        self.langfuse = langfuse


def _servicio(trazas, observaciones, puntajes, modelo="claude-sonnet-4-6"):
    return LangfuseStatsService(TracerFalso(LangfuseFalso(trazas, observaciones, puntajes)), modelo)


TRAZAS = [
    SimpleNamespace(id="t1", name="resolve", timestamp="2024-01-01T00:00:00Z"),
    SimpleNamespace(id="t2", name="judge", timestamp="2024-01-01T00:01:00Z"),
]
OBSERVACIONES = [
    {"trace_id": "t1", "usage": {"input": 1000, "output": 200}, "latency": 3.5},
    {"trace_id": "t1", "usage": {"input": 500, "output": 100}, "latency": 1.5},
    {"trace_id": "t2", "usage": {"input": 300, "output": 50}, "latency": 2.0},
]
PUNTAJES = [{"trace_id": "t2", "value": 9.0}]


class TestAgregacion:
    def test_suma_tokens_de_todas_las_observaciones_de_la_traza(self):
        stats = _servicio(TRAZAS, OBSERVACIONES, PUNTAJES).get_stats()
        por_id = {t["trace_id"]: t for t in stats["recent_traces"]}
        assert por_id["t1"]["tokens"] == 1800   # 1000+200+500+100
        assert por_id["t2"]["tokens"] == 350

    def test_suma_latencias(self):
        por_id = {t["trace_id"]: t for t in _servicio(TRAZAS, OBSERVACIONES, PUNTAJES).get_stats()["recent_traces"]}
        assert por_id["t1"]["latency_s"] == 5.0
        assert por_id["t2"]["latency_s"] == 2.0

    def test_el_resumen_totaliza(self):
        resumen = _servicio(TRAZAS, OBSERVACIONES, PUNTAJES).get_stats()["summary"]
        assert resumen["total_traces"] == 2
        assert resumen["total_tokens"] == 2150
        assert resumen["avg_latency_s"] == 3.5      # (5.0 + 2.0) / 2
        assert resumen["avg_judge_score"] == 9.0    # solo t2 tiene puntaje
        assert resumen["cost_usd"] > 0

    def test_el_costo_usa_la_tarifa_del_modelo(self):
        caro = _servicio(TRAZAS, OBSERVACIONES, PUNTAJES, "claude-sonnet-4-6").get_stats()
        barato = _servicio(TRAZAS, OBSERVACIONES, PUNTAJES, "claude-haiku-4-5").get_stats()
        assert caro["summary"]["cost_usd"] > barato["summary"]["cost_usd"]

    def test_modelo_desconocido_no_reporta_costo_cero(self):
        """Informar cero seria peor que estimar: se usa la tarifa de referencia."""
        stats = _servicio(TRAZAS, OBSERVACIONES, PUNTAJES, "modelo-que-no-existe").get_stats()
        assert stats["summary"]["cost_usd"] > 0

    def test_no_pierde_la_traza_sin_puntaje(self):
        stats = _servicio(TRAZAS, OBSERVACIONES, PUNTAJES).get_stats()
        assert {t["trace_id"] for t in stats["recent_traces"]} == {"t1", "t2"}
        assert next(t for t in stats["recent_traces"] if t["trace_id"] == "t1")["score"] is None

    def test_no_expone_el_desglose_interno_de_tokens(self):
        fila = _servicio(TRAZAS, OBSERVACIONES, PUNTAJES).get_stats()["recent_traces"][0]
        assert "tokens_in" not in fila and "tokens_out" not in fila


class TestCasosBorde:
    def test_sin_trazas(self):
        stats = _servicio([], [], []).get_stats()
        assert stats["enabled"] is True
        assert stats["recent_traces"] == []
        assert stats["summary"]["total_traces"] == 0
        assert stats["summary"]["avg_judge_score"] is None

    def test_traza_sin_observaciones(self):
        stats = _servicio(TRAZAS, [], []).get_stats()
        assert all(t["tokens"] == 0 for t in stats["recent_traces"])
        assert stats["summary"]["avg_latency_s"] is None

    def test_trazas_como_diccionarios(self):
        """El SDK a veces devuelve dicts: no puede cambiar el resultado."""
        como_dicts = [{"id": "t1", "name": "resolve", "timestamp": "2024-01-01T00:00:00Z"}]
        stats = _servicio(como_dicts, OBSERVACIONES, PUNTAJES).get_stats()
        assert stats["recent_traces"][0]["trace_id"] == "t1"
        assert stats["recent_traces"][0]["tokens"] == 1800

    def test_una_api_caida_no_tira_las_estadisticas(self):
        """Si fallan los puntajes, los tokens se siguen informando."""
        svc = _servicio(TRAZAS, OBSERVACIONES, PUNTAJES)

        def explota(limit):
            raise RuntimeError("429 rate limit")

        svc._tracer.langfuse.client.score.get = explota
        stats = svc.get_stats()
        assert stats["summary"]["total_tokens"] == 2150
        assert stats["summary"]["avg_judge_score"] is None

    def test_sin_langfuse_detras_no_hay_estadisticas(self):
        svc = LangfuseStatsService(NoOpTracer(), "claude-sonnet-4-6")
        assert svc.enabled is False
        assert svc.get_stats() == {"enabled": False, "summary": None, "recent_traces": []}


class TestCache:
    def test_no_vuelve_a_consultar_dentro_del_ttl(self):
        svc = _servicio(TRAZAS, OBSERVACIONES, PUNTAJES)
        llamadas = {"n": 0}
        original = svc.langfuse_fetch = svc._traer_trazas

        def contando():
            llamadas["n"] += 1
            return original()

        svc._traer_trazas = contando
        svc.get_stats()
        svc.get_stats()
        assert llamadas["n"] == 1
