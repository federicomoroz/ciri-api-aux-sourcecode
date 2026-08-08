"""«Los prompts son deterministas: deberian servir con cualquier modelo decente».

Esa afirmacion es verificable, y esto es lo que la verifica sin gastar un centavo.

Lo que cambia entre modelos no es si entienden la consigna —los prompts piden una
estructura explicita— sino **como envuelven la respuesta**. Claude devuelve el
JSON pelado. Los modelos mas chicos y los de razonamiento lo rodean de prosa, lo
meten en un bloque de codigo, dejan una coma colgando, o lo envuelven en un objeto
con una clave que nadie pidio.

Cada caso de aca es una forma real de esa familia. Si el parseo las cubre, cambiar
de proveedor es cambiar una linea de configuracion; si no, es un bug por modelo.

Lo que estos tests NO prueban es la calidad del razonamiento: eso lo mide
`scripts/evaluar.py` contra el modelo de verdad. Aca se prueba el contrato.
"""

import pytest

from api.app.domain.models import (
    JudgeEvaluationOutput,
    PolicyVerdictOutput,
    ResolutionOutput,
)
from api.app.llm.parsing import parse_json_safely, validate_llm_output

VEREDICTOS = [
    {"policy_code": "POL-EXC-003", "verdict": "BLOCKER", "reasoning": "cripto irreversible"},
    {"policy_code": "POL-FRD-001", "verdict": "FAIL", "reasoning": "score 8 < 15"},
]
RESOLUCION = {
    "transaction_id": "TXN-00051",
    "recommended_action": "REJECT",
    "confidence": 0.95,
    "risk_level": "BLOCKER",
    "justification": "cripto",
}


class TestFormasDeEnvolver:
    """Cada una es como responde alguna familia de modelos."""

    def test_json_pelado(self):
        """Claude. El caso facil."""
        assert parse_json_safely('{"a": 1}', {}) == {"a": 1}

    def test_bloque_de_codigo_con_lenguaje(self):
        """Llama, Qwen, Mistral: casi siempre lo meten en un fence."""
        crudo = '```json\n{"a": 1}\n```'
        assert parse_json_safely(crudo, {}) == {"a": 1}

    def test_prosa_antes_y_despues(self):
        """Gemini tiende a presentar la respuesta antes de darla."""
        crudo = 'Claro, aca esta el analisis:\n\n{"a": 1}\n\nEspero que sirva.'
        assert parse_json_safely(crudo, {}) == {"a": 1}

    def test_cadena_de_razonamiento_antes_del_json(self):
        """Los modelos de razonamiento anteponen su `<think>`. No es un error."""
        crudo = (
            "<think>El metodo de pago es cripto, asi que POL-EXC-003 aplica.\n"
            'Deberia devolver BLOCKER.</think>\n{"verdict": "BLOCKER"}'
        )
        assert parse_json_safely(crudo, {}) == {"verdict": "BLOCKER"}

    def test_razonamiento_y_bloque_de_codigo_juntos(self):
        crudo = '<thinking>hmm</thinking>\n\n```json\n{"a": 1}\n```'
        assert parse_json_safely(crudo, {}) == {"a": 1}

    def test_coma_colgando(self):
        """El error de sintaxis mas comun en los modelos chicos."""
        assert parse_json_safely('{"a": 1, "b": 2,}', {}) == {"a": 1, "b": 2}

    def test_coma_colgando_en_una_lista(self):
        assert parse_json_safely('[{"a": 1}, {"b": 2},]', []) == [{"a": 1}, {"b": 2}]

    def test_lista_envuelta_en_un_objeto(self):
        """Cumplen el contenido y agregan un envoltorio que nadie pidio."""
        crudo = '{"policy_verdicts": [{"policy_code": "POL-EXC-003"}]}'
        assert parse_json_safely(crudo, []) == [{"policy_code": "POL-EXC-003"}]

    def test_lista_envuelta_con_otro_nombre_de_clave(self):
        """El nombre del envoltorio lo inventa el modelo: no se puede asumir."""
        assert parse_json_safely('{"resultados": [1, 2]}', []) == [1, 2]

    def test_un_objeto_con_dos_claves_no_se_desenvuelve(self):
        """Desenvolver ahi seria adivinar cual de las dos era la respuesta."""
        crudo = '{"verdicts": [1], "razonamiento": "x"}'
        assert parse_json_safely(crudo, []) == {"verdicts": [1], "razonamiento": "x"}

    def test_todo_junto(self):
        """El peor caso realista: razonamiento, fence, prosa y coma colgando."""
        crudo = (
            "<think>Analizando...</think>\n"
            "Aca esta el resultado:\n"
            '```json\n{"policy_verdicts": [{"policy_code": "POL-EXC-003"},]}\n```\n'
            "Avisame si necesitas otra cosa."
        )
        assert parse_json_safely(crudo, []) == [{"policy_code": "POL-EXC-003"}]


class TestElContratoSeMantiene:
    """Los tres prompts tienen que producir su estructura, venga como venga."""

    @pytest.mark.parametrize("envoltorio", [
        '{crudo}',
        '```json\n{crudo}\n```',
        'Aca esta:\n\n{crudo}',
        '<think>razonando</think>\n{crudo}',
    ])
    def test_los_veredictos_de_politica_sobreviven(self, envoltorio):
        import json

        crudo = envoltorio.format(crudo=json.dumps(VEREDICTOS))
        salida = validate_llm_output(crudo, PolicyVerdictOutput, [])
        assert len(salida) == 2
        assert salida[0]["policy_code"] == "POL-EXC-003"
        assert salida[0]["verdict"] == "BLOCKER"

    @pytest.mark.parametrize("envoltorio", [
        '{crudo}',
        '```json\n{crudo}\n```',
        '<thinking>x</thinking>{crudo}',
    ])
    def test_la_resolucion_sobrevive(self, envoltorio):
        import json

        crudo = envoltorio.format(crudo=json.dumps(RESOLUCION))
        salida = validate_llm_output(crudo, ResolutionOutput, {})
        assert salida["recommended_action"] == "REJECT"
        assert salida["risk_level"] == "BLOCKER"

    def test_la_evaluacion_del_juez_sobrevive(self):
        crudo = (
            '```json\n{"overall_score": 8.4, "criteria": {"policy_consistency": 9.0}, '
            '"approved": true, "strengths": ["ok"], "weaknesses": [],}\n```'
        )
        salida = validate_llm_output(crudo, JudgeEvaluationOutput, {})
        assert salida["overall_score"] == 8.4
        assert salida["approved"] is True

    def test_los_veredictos_envueltos_igual_llegan_completos(self):
        crudo = '{"verdicts": [{"policy_code": "POL-EXC-003", "verdict": "BLOCKER"}]}'
        salida = validate_llm_output(crudo, PolicyVerdictOutput, [])
        assert isinstance(salida, list) and salida[0]["verdict"] == "BLOCKER"


class TestCuandoNoHayNadaQueRescatar:
    """Reparar envoltorio es legitimo; inventar contenido no.

    Con el fallback vacio, `decision.decidir` deriva a revision humana en vez de
    aprobar: es el fail-closed de `decisions.md` y la red que hace que un modelo
    que no cumple el contrato sea un caso pendiente y no uno aprobado solo.
    """

    def test_una_disculpa_no_es_un_json(self):
        crudo = "Lo siento, no puedo procesar esta solicitud."
        assert parse_json_safely(crudo, []) == []

    def test_un_json_truncado_a_la_mitad_no_se_completa(self):
        """Pasa cuando el modelo choca contra max_tokens."""
        crudo = '[{"policy_code": "POL-EXC-003", "verdict": "BLOCK'
        assert parse_json_safely(crudo, []) == []

    def test_una_respuesta_vacia_da_el_fallback(self):
        assert validate_llm_output("", PolicyVerdictOutput, []) == []

    def test_un_veredicto_que_no_existe_no_se_traduce(self):
        """Inventar el enum mas parecido seria decidir por el modelo."""
        crudo = '[{"policy_code": "POL-X", "verdict": "TAL_VEZ"}]'
        salida = validate_llm_output(crudo, PolicyVerdictOutput, [])
        assert salida[0]["verdict"] == "TAL_VEZ", "se valido en silencio algo invalido"


class TestReintentoDeLoTransitorio:
    """Un free tier rechaza pedidos cuando el proveedor esta cargado.

    Medido contra Gemini: la primera llamada pasa y la siguiente rebota con 503.
    `httpx.HTTPTransport(retries=...)` no cubre eso —solo fallos de conexion—,
    asi que el 503 llegaba como respuesta valida y tumbaba el analisis.
    """

    @staticmethod
    def _cliente(respuestas, monkeypatch):
        import httpx

        from api.app.llm.client import OpenAICompatibleClient

        c = OpenAICompatibleClient(api_key="k", model="m", base_url="https://x/v1")
        monkeypatch.setattr("time.sleep", lambda _: None)   # sin esperas reales
        llamadas = {"n": 0}

        def post(_ruta, json=None):
            i = min(llamadas["n"], len(respuestas) - 1)
            llamadas["n"] += 1
            estado, cuerpo = respuestas[i]
            return httpx.Response(
                estado, json=cuerpo, request=httpx.Request("POST", "https://x/v1/chat/completions"),
            )

        monkeypatch.setattr(c.client, "post", post)
        c.llamadas = llamadas
        return c

    OK = (200, {"choices": [{"message": {"content": "hola"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1}})

    def test_un_503_se_reintenta_y_sale_bien(self, monkeypatch):
        c = self._cliente([(503, {"error": "sobrecargado"}), self.OK], monkeypatch)
        assert c.complete("s", "u").text == "hola"
        assert c.llamadas["n"] == 2

    def test_un_429_tambien(self, monkeypatch):
        c = self._cliente([(429, {"error": "rate limit"}), self.OK], monkeypatch)
        assert c.complete("s", "u").text == "hola"

    def test_una_clave_invalida_no_se_reintenta(self, monkeypatch):
        """Insistir con un 401 solo quema tiempo: no va a mejorar."""
        import httpx

        c = self._cliente([(401, {"error": "clave invalida"})], monkeypatch)
        with pytest.raises(httpx.HTTPStatusError):
            c.complete("s", "u")
        assert c.llamadas["n"] == 1

    def test_un_modelo_inexistente_tampoco(self, monkeypatch):
        import httpx

        c = self._cliente([(404, {"error": "no such model"})], monkeypatch)
        with pytest.raises(httpx.HTTPStatusError):
            c.complete("s", "u")
        assert c.llamadas["n"] == 1

    def test_se_rinde_despues_del_ultimo_intento(self, monkeypatch):
        """Reintentar para siempre convertiria una caida en un cuelgue."""
        import httpx

        c = self._cliente([(503, {"error": "x"})], monkeypatch)
        with pytest.raises(httpx.HTTPStatusError):
            c.complete("s", "u")
        assert c.llamadas["n"] == c.max_retries + 1

    def test_respeta_el_retry_after_del_proveedor(self):
        import httpx

        from api.app.domain.constants import LLM_RETRY_MAX_WAIT_S
        from api.app.llm.client import OpenAICompatibleClient

        r = httpx.Response(429, headers={"Retry-After": "7"})
        assert OpenAICompatibleClient._espera(r, 0) == 7.0
        # Y no se queda esperando un numero absurdo.
        r = httpx.Response(429, headers={"Retry-After": "9999"})
        assert OpenAICompatibleClient._espera(r, 0) == LLM_RETRY_MAX_WAIT_S

    def test_sin_retry_after_la_espera_crece(self):
        import httpx

        from api.app.llm.client import OpenAICompatibleClient

        r = httpx.Response(503)
        assert OpenAICompatibleClient._espera(r, 1) > OpenAICompatibleClient._espera(r, 0)


class TestFrecuenciaDeLlamadas:
    """No pasarse de los pedidos por minuto del proveedor.

    Un 429 se reintenta, pero reintentarlo es reaccionar tarde: la llamada ya se
    gasto contra la cuota diaria. Medido contra el free tier de Gemini, que da 5
    por minuto en el Flash grande: un analisis dispara tres llamadas casi
    seguidas, asi que dos investigaciones a la vez ya se pasan.

    El espaciado vive en el manager y no en el cliente porque la cuota es del
    PROVEEDOR: si dos pasos del pipeline corren en modelos distintos de la misma
    casa, comparten el techo.
    """

    @staticmethod
    def _manager(monkeypatch, **ajustes):
        """Un manager con reloj falso: el test controla el tiempo.

        El reloj se INYECTA en el `RateLimiter` en vez de parchear `time` global.
        Es la razon por la que el limitador los recibe como parametros: un test
        que tarda un minuto en verificar un limite de un minuto no se corre nunca,
        y parchear el modulo `time` entero afecta a cualquier otra cosa que corra
        en el mismo proceso.
        """
        from types import SimpleNamespace

        from api.app.domain.constants import LLM_RPM_PAUSA_MINIMA_S, LLM_RPM_VENTANA_S
        from api.app.llm.manager import LLMManager
        from api.app.observability.tracer import NoOpTracer
        from api.app.rate_limiter import RateLimiter

        reloj = {"t": 1000.0, "dormido": 0.0}

        def dormir(s):
            reloj["dormido"] += s
            reloj["t"] += s      # el reloj avanza, si no el bucle no termina

        limitador = RateLimiter(
            ventana_s=LLM_RPM_VENTANA_S, pausa_minima_s=LLM_RPM_PAUSA_MINIMA_S,
            reloj=lambda: reloj["t"], dormir=dormir,
        )
        settings = SimpleNamespace(llm_rpm={}, **ajustes)
        m = LLMManager(settings, NoOpTracer(), limitador=limitador)
        m.reloj = reloj
        return m

    @staticmethod
    def _turno(m, proveedor, modelo):
        """Lo que hace `completar` antes de llamar: pedir turno."""
        m.limitador.esperar_turno(proveedor, m.rpm_de(proveedor, modelo))

    def test_por_debajo_del_limite_no_espera(self, monkeypatch):
        """La primera investigacion es la que alguien esta mirando."""
        m = self._manager(monkeypatch)
        for _ in range(5):
            self._turno(m, "gemini", "gemini-flash-latest")
        assert m.reloj["dormido"] == 0.0

    def test_al_llegar_al_limite_espera_a_que_se_libere(self, monkeypatch):
        m = self._manager(monkeypatch)
        for _ in range(5):                       # llena la ventana en t=1000
            self._turno(m, "gemini", "gemini-flash-latest")
        self._turno(m, "gemini", "gemini-flash-latest")   # la sexta tiene que esperar
        assert m.reloj["dormido"] == pytest.approx(60.0, abs=0.1)

    def test_una_llamada_vieja_ya_no_cuenta(self, monkeypatch):
        """La ventana es deslizante: no se acumula deuda para siempre."""
        m = self._manager(monkeypatch)
        for _ in range(5):
            self._turno(m, "gemini", "gemini-flash-latest")
        m.reloj["t"] += 61.0                     # paso el minuto
        self._turno(m, "gemini", "gemini-flash-latest")
        assert m.reloj["dormido"] == 0.0

    def test_anthropic_no_se_espacia(self, monkeypatch):
        """Es un plan pago con su propio limite: frenarlo seria inventar uno."""
        m = self._manager(monkeypatch)
        for _ in range(50):
            self._turno(m, "anthropic", "claude-haiku-4-5-20251001")
        assert m.reloj["dormido"] == 0.0

    def test_cada_proveedor_lleva_su_propia_cuenta(self, monkeypatch):
        """Gastar la de Gemini no tiene por que frenar a Groq."""
        m = self._manager(monkeypatch)
        for _ in range(5):
            self._turno(m, "gemini", "gemini-flash-latest")
        self._turno(m, "groq", "llama-3.3-70b-versatile")
        assert m.reloj["dormido"] == 0.0

    def test_la_configuracion_pisa_al_adaptador(self, monkeypatch):
        """La cuota es de la cuenta: un plan pago no arrastra el free tier."""
        m = self._manager(monkeypatch)
        m.settings.llm_rpm = {"gemini": 15}
        assert m.rpm_de("gemini", "gemini-flash-lite-latest") == 15
        for _ in range(15):
            self._turno(m, "gemini", "gemini-flash-lite-latest")
        assert m.reloj["dormido"] == 0.0

    def test_se_puede_apagar_el_espaciado(self, monkeypatch):
        """Cero significa «no se conoce limite», y sirve para desactivarlo."""
        from api.app.domain.constants import LLM_RPM_SIN_LIMITE

        m = self._manager(monkeypatch)
        m.settings.llm_rpm = {"gemini": LLM_RPM_SIN_LIMITE}
        for _ in range(30):
            self._turno(m, "gemini", "gemini-flash-latest")
        assert m.reloj["dormido"] == 0.0

    def test_completar_pide_turno_antes_de_llamar(self, monkeypatch):
        """El limitador esta en el camino real, no solo disponible.

        Sin este test, `esperar_turno` podria existir y no llamarse nunca: el
        resto de la clase probaria un componente que nadie usa.
        """
        m = self._manager(monkeypatch)
        pasos = []
        monkeypatch.setattr(
            m.limitador, "esperar_turno",
            lambda clave, limite: pasos.append(("espacio", clave)) or 0.0,
        )
        monkeypatch.setattr(
            m, "cliente",
            lambda p, mo, k="": SimpleNamespaceCliente(pasos),
        )
        m.completar("gemini", "gemini-flash-latest", "s", "u")
        assert pasos == [("espacio", "gemini"), ("llamada",)]


class SimpleNamespaceCliente:
    """Un cliente que solo anota que lo llamaron."""

    def __init__(self, pasos):
        self.pasos = pasos

    def complete(self, *a, **k):
        self.pasos.append(("llamada",))
        return "ok"
