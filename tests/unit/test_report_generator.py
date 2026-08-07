"""
Unit tests for ReportGenerator — Jinja2 HTML rendering.
"""

import pytest

from api.app.domain.enums import PaymentMethod, ResolutionOutcome, RiskLevel, VerdictType
from api.app.reports.generator import ReportGenerator


@pytest.fixture
def generator():
    return ReportGenerator()


@pytest.fixture
def minimal_report_data():
    """Minimal data required to render a report."""
    return {
        "transaction": {
            "id": "TXN-00051", "client_id": "CLI-0003", "merchant": "Airbnb",
            "amount_usd": 2095.90, "date": "2024-09-23", "payment_method": PaymentMethod.CRYPTO,
            "country": "COL", "channel": "POS", "device": "Firefox/Mac",
            "fraud_score": 8, "status": "Contracargo iniciado", "notes": None,
        },
        "resolution": {
            "transaction_id": "TXN-00051", "recommended_action": ResolutionOutcome.REJECT,
            "confidence": 0.99, "justification": "BLOCKER cripto",
            "policy_verdicts": [{"policy_code": "POL-EXC-003", "verdict": VerdictType.BLOCKER,
                                 "reasoning": PaymentMethod.CRYPTO, "requires_human_review": False}],
            "precedent_summary": "", "log_summary": "", "risk_level": RiskLevel.BLOCKER,
            "compensation_applicable": False, "compensation_amount_usd": 0.0,
            "next_steps": ["Notificar al cliente"], "requires_hitl": False, "hitl_reason": None,
        },
        "judge_evaluation": {
            "overall_score": 9.2,
            "criteria": {"policy_consistency": 10.0, "justification_quality": 9.0,
                         "precedent_usage": 8.0, "risk_assessment": 9.5, "actionability": 9.5},
            "approved": True, "strengths": ["Correcto"], "weaknesses": [],
        },
        "agent_analysis": "BLOCKER detectado.",
        "merchant_risk": {"merchant": "Airbnb", "cb_ratio": 0.02, "total_transactions": 10,
                          "total_chargebacks": 2, "total_volume_usd": 5000, "avg_transaction_usd": 500,
                          "flags": [], "is_strategic": False},
        "client_profile": {"client_id": "CLI-0003", "total_transactions": 5, "total_chargebacks": 1,
                           "rejected_transactions": 0, "countries_used": ["COL"],
                           "payment_methods_used": [PaymentMethod.CRYPTO], "flags": []},
        "logs": [],
        "policies_evaluated": [{"policy_code": "POL-EXC-003", "verdict": VerdictType.BLOCKER,
                                "reasoning": PaymentMethod.CRYPTO, "requires_human_review": False}],
        "similar_cases": [],
        "hitl_decision": None,
        "cache_hit": False,
        "guardrail_warnings": [],
    }


class TestReportGenerator:

    def test_render_returns_html(self, generator, minimal_report_data):
        html = generator.render(minimal_report_data)
        assert isinstance(html, str)
        assert len(html) > 100

    def test_html_contains_transaction_id(self, generator, minimal_report_data):
        html = generator.render(minimal_report_data)
        assert "TXN-00051" in html

    def test_html_contains_risk_level(self, generator, minimal_report_data):
        html = generator.render(minimal_report_data)
        assert RiskLevel.BLOCKER in html

    def test_html_contains_merchant(self, generator, minimal_report_data):
        html = generator.render(minimal_report_data)
        assert "Airbnb" in html

    def test_html_contains_judge_score(self, generator, minimal_report_data):
        html = generator.render(minimal_report_data)
        assert "9.2" in html

    def test_html_contains_generation_timestamp(self, generator, minimal_report_data):
        html = generator.render(minimal_report_data)
        assert "UTC" in html

    def test_autoescape_prevents_xss(self, generator, minimal_report_data):
        """Jinja2 autoescape should escape HTML in user-provided data."""
        minimal_report_data["agent_analysis"] = '<script>alert("xss")</script>'
        html = generator.render(minimal_report_data)
        # Jinja2 autoescape should prevent raw script tags
        assert '<script>alert' not in html

    def test_render_with_guardrail_warnings(self, generator, minimal_report_data):
        minimal_report_data["guardrail_warnings"] = [
            "GUARDRAIL: APPROVE con BLOCKER activo"
        ]
        html = generator.render(minimal_report_data)
        assert "GUARDRAIL" in html

    def test_render_with_hitl_decision(self, generator, minimal_report_data):
        minimal_report_data["hitl_decision"] = {
            "decision": "APPROVED",
            "notes": "Analyst approved after review",
        }
        html = generator.render(minimal_report_data)
        assert isinstance(html, str)


class TestFormularioHITL:
    """El formulario del informe es uno de los dos disparadores del feedback.

    Regresion: mandaba 5 campos y ninguno era la resolucion. Sin ella,
    `FeedbackService.submit` registra el feedback pero no indexa el caso como
    precedente — la mitad del circuito de mejora continua no corria nunca.
    """

    @staticmethod
    def _con_hitl(data: dict) -> dict:
        return {
            **data,
            "resolution": {**data["resolution"], "requires_hitl": True,
                           "hitl_reason": "Riesgo alto con cliente VIP"},
            "motivo": "Cargo no reconocido",
        }

    def test_el_formulario_manda_la_resolucion(self, generator, minimal_report_data):
        html = generator.render(self._con_hitl(minimal_report_data))
        assert "hitl-form" in html, "el formulario HITL no se renderizo"
        assert "resolution: _resolucionDelCaso()" in html
        assert "datos-del-caso" in html

    def test_el_formulario_manda_el_motivo(self, generator, minimal_report_data):
        """El motivo es el campo contra el que se matchean los precedentes futuros."""
        html = generator.render(self._con_hitl(minimal_report_data))
        assert 'name="motivo" value="Cargo no reconocido"' in html
        assert "motivo: data.motivo" in html

    def test_la_resolucion_viaja_en_el_bloque_de_datos(self, generator, minimal_report_data):
        import json

        html = generator.render(self._con_hitl(minimal_report_data))
        crudo = html.split('id="datos-del-caso">')[1].split("</script>")[0]
        datos = json.loads(crudo.replace("\u003c", "<").replace("\u003e", ">").replace("\u0026", "&"))
        assert datos["resolution"]["recommended_action"]
        assert datos["resolution"]["requires_hitl"] is True

    def test_sin_hitl_no_hay_formulario(self, generator, minimal_report_data):
        html = generator.render(minimal_report_data)
        assert "hitl-form" not in html

    def test_la_marca_de_calidad_baja_se_muestra(self, generator, minimal_report_data):
        """`Marcar — Calidad Baja` escribia un campo que ninguna plantilla leia."""
        data = {**minimal_report_data, "judge_evaluation": {
            **minimal_report_data["judge_evaluation"],
            "quality_flag": "LOW_QUALITY — Revisar resolución manualmente",
        }}
        assert "LOW_QUALITY" in generator.render(data)


class TestQueDeclaraElInforme:
    """Una grabacion y un analisis recien hecho no se declaran igual.

    El cartel salia siempre como «caso prearmado» y el panel lo corregia
    despues, sobre el HTML ya renderizado. n8n llama a `/api/reports/html`
    derecho, sin pasar por el panel: por ahi salia un analisis calculado en el
    momento —con su juez, sus 17 veredictos y su BLOCKER— rotulado como una
    grabacion vieja. Se detecto disparando el webhook, no en los tests: ninguno
    miraba el cartel.

    Ahora el cartel sale del dato. El que pide el informe no tiene que saber
    nada de esto.
    """

    @staticmethod
    def _con(data: dict, **resolucion) -> dict:
        return {**data, "resolution": {**data["resolution"], **resolucion}}

    def test_una_corrida_real_con_modelo_gratuito_lo_dice(self, generator, minimal_report_data):
        from api.app.data.precomputados import ETIQUETA, ETIQUETA_GRATIS

        html = generator.render(self._con(
            minimal_report_data, demo=True,
            demo_modelo={"proveedor": "gemini", "modelo": "gemini-flash-lite-latest"},
        ))
        assert ETIQUETA_GRATIS in html
        assert ETIQUETA not in html, "un analisis de ahora rotulado como grabacion"

    def test_y_nombra_el_modelo_para_que_se_le_atribuya(self, generator, minimal_report_data):
        """Quien lee tiene que poder atribuirle al modelo lo que es del modelo."""
        html = generator.render(self._con(
            minimal_report_data, demo=True,
            demo_modelo={"proveedor": "gemini", "modelo": "gemini-flash-lite-latest"},
        ))
        assert "gemini-flash-lite-latest" in html

    def test_un_caso_guardado_sigue_siendo_una_grabacion(self, generator, minimal_report_data):
        from api.app.data.precomputados import ETIQUETA, ETIQUETA_GRATIS

        html = generator.render(self._con(minimal_report_data, demo=True))
        assert ETIQUETA in html
        assert ETIQUETA_GRATIS not in html

    def test_una_corrida_normal_no_lleva_cartel(self, generator, minimal_report_data):
        from api.app.data.precomputados import ETIQUETA, ETIQUETA_GRATIS

        html = generator.render(minimal_report_data)
        assert ETIQUETA not in html and ETIQUETA_GRATIS not in html

    def test_el_panel_no_repite_el_cartel_que_ya_esta(self, generator, minimal_report_data):
        """El panel marca el HTML despues; ahora suele encontrarlo puesto."""
        from api.app.data.precomputados import (
            ETIQUETA_GRATIS,
            _con_cartel,
            cartel_modelo_gratis,
        )

        modelo = {"proveedor": "gemini", "modelo": "gemini-flash-lite-latest"}
        html = generator.render(self._con(minimal_report_data, demo=True, demo_modelo=modelo))
        assert _con_cartel(html, cartel_modelo_gratis(modelo)).count(ETIQUETA_GRATIS) == 1


class TestElDesvioDeLaNotaSeDeclara:
    """La nota de un modelo gratuito no es la nota del sistema.

    El juez corre en el mismo modelo que resolvio, asi que un modelo mas chico
    se penaliza dos veces: razona con menos profundidad y despues se puntua a si
    mismo. Medido sobre TXN-00051, que en desarrollo daba alrededor de 9 y con
    Gemini Flash Lite dio 6.8.

    Declararlo no es una disculpa: es lo que separa una nota informativa de una
    medicion del sistema. La seccion del juez es ademas la que se cita fuera de
    contexto, asi que el aviso va ahi y no solo en el cartel de arriba.
    """

    MODELO = {"proveedor": "gemini", "modelo": "gemini-flash-lite-latest"}

    def _demo(self, data: dict) -> dict:
        return {**data, "resolution": {**data["resolution"], "demo": True,
                                       "demo_modelo": self.MODELO}}

    def test_el_desvio_aparece_junto_a_la_nota(self, generator, minimal_report_data):
        from api.app.domain.constants import DEMO_DESVIO_JUEZ

        html = generator.render(self._demo(minimal_report_data))
        assert f"±{DEMO_DESVIO_JUEZ:.1f} puntos" in html

    def test_dice_que_es_informativo(self, generator, minimal_report_data):
        html = generator.render(self._demo(minimal_report_data))
        assert "fines informativos" in html

    def test_deriva_a_anthropic_para_el_mejor_resultado(self, generator, minimal_report_data):
        html = generator.render(self._demo(minimal_report_data))
        assert "Anthropic en" in html and "producción" in html

    def test_nombra_el_modelo_que_puso_la_nota(self, generator, minimal_report_data):
        """Sin el nombre, el desvio no se le puede atribuir a nadie."""
        html = generator.render(self._demo(minimal_report_data))
        assert self.MODELO["modelo"] in html

    def test_una_corrida_normal_no_relativiza_su_nota(self, generator, minimal_report_data):
        """Con la configuracion documentada la nota vale lo que dice."""
        html = generator.render(minimal_report_data)
        assert "fines informativos" not in html
        assert "puntos</b>" not in html

    def test_el_cartel_de_arriba_tambien_lo_dice(self, generator, minimal_report_data):
        """Quien no baja hasta la seccion del juez igual se entera."""
        from api.app.domain.constants import DEMO_DESVIO_JUEZ

        html = generator.render(self._demo(minimal_report_data))
        cartel = html.split("Evaluación de Calidad")[0]
        assert f"±{DEMO_DESVIO_JUEZ:.1f} puntos" in cartel
        assert "gratuito" in cartel


class TestElInformeSeBastaSolo:
    """El HTML no puede depender de bajar nada de internet.

    Traia los estilos del CDN de Tailwind, que los genera en el browser. Anda
    casi siempre, y por eso tardo en aparecer: n8n sirve la respuesta del
    formulario HITL con `Content-Security-Policy: sandbox` sin
    `allow-same-origin`, el documento queda en un origen opaco y el script no
    aplica nada. El analista aprobaba un contracargo y recibia un informe sin
    una sola regla de estilo — texto plano con los datos apilados.

    El mismo HTML se entrega en un ZIP y se abre desde el disco, donde puede no
    haber red o el CDN puede estar bloqueado. Un entregable que necesita
    internet para leerse no es un entregable.
    """

    @staticmethod
    def _externos(html: str) -> list[str]:
        import re

        return re.findall(r'<(?:script|link|img|iframe)[^>]*\s(?:src|href)=["\']([^"\']+)', html)

    def test_no_trae_nada_de_afuera(self, generator, minimal_report_data):
        html = generator.render(minimal_report_data)
        remotos = [u for u in self._externos(html) if u.startswith(("http://", "https://", "//"))]
        assert not remotos, f"el informe no se ve sin internet: {remotos}"

    def test_los_estilos_viajan_adentro(self, generator, minimal_report_data):
        html = generator.render(minimal_report_data)
        assert "<style>" in html
        assert ".rounded-2xl" in html and ".shadow-lg" in html

    def test_toda_clase_usada_tiene_su_regla(self, generator, minimal_report_data):
        """Agregar una clase a la plantilla sin agregar su regla no se nota mirando.

        Con el CDN cualquier utilidad de Tailwind andaba sola. Ahora las reglas
        son las que estan escritas: una clase sin regla es un elemento sin
        estilo, y eso solo se ve abriendo el informe.
        """
        import re
        from pathlib import Path

        plantilla = Path("api/app/reports/templates/case_report.html").read_text(encoding="utf-8")
        css = Path("api/app/reports/templates/_estilos.css").read_text(encoding="utf-8")

        usadas = set()
        for bloque in re.findall(r'class="((?:[^"]|\{\{[^}]*\}\})*)"', plantilla):
            limpio = re.sub(r"\{%.*?%\}|\{\{.*?\}\}", " ", bloque, flags=re.S)
            # Un token cortado —`log-` sale de `class="log-{{ nivel }}"`— es un
            # prefijo que se completa al renderizar, no una clase.
            usadas.update(c for c in limpio.split() if c and not c.endswith("-"))

        # En CSS los `:` y los `.` del nombre van escapados —`.md\:grid-cols-2`—
        # y un `:` SIN escapar arranca una pseudoclase: `.hover\:bg-gray-50:hover`
        # define `hover:bg-gray-50`, no `hover:bg-gray-50:hover`.
        definidas = {
            re.split(r"(?<!\\):", s)[0].replace("\\", "")
            for s in re.findall(r"\.([A-Za-z][\w:.\\-]*)", css)
        }
        faltan = sorted(c for c in usadas if c not in definidas)
        assert not faltan, f"clases sin regla en _estilos.css: {faltan}"

    def test_el_ancho_de_la_grilla_sigue_siendo_responsive(self, generator, minimal_report_data):
        """Escribir el CSS a mano es facil que se coma el breakpoint."""
        from pathlib import Path

        css = Path("api/app/reports/templates/_estilos.css").read_text(encoding="utf-8")
        assert "@media (min-width: 768px)" in css
        assert r"md\:grid-cols-3" in css
