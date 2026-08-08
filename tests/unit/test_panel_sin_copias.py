"""El panel no puede reescribir valores que el dominio o el informe ya deciden.

El panel es HTML con JavaScript adentro, asi que nada impide escribir ahi un
umbral o el nombre de un valor de enum. Cuando eso pasa, el mismo caso se ve de
dos maneras: el panel lo pinta con una escala y el informe del mismo analisis con
otra.

Se renderiza la plantilla de verdad —no se lee el archivo— para que lo que se
comprueba sea lo que el navegador recibe.
"""

import re

import pytest

from api.app.domain.constants import (
    FRAUD_SCORE_HIGH_RISK_THRESHOLD,
    FRAUD_SCORE_MODERADO,
    JUDGE_APPROVAL_THRESHOLD,
    JUDGE_NEEDS_REVIEW_THRESHOLD,
)
from api.app.domain.enums import Severity
from api.app.reports.generator import ReportGenerator
from api.app.routes.panel import serve_panel


@pytest.fixture(scope="module")
def panel() -> str:
    """Lo que devuelve la ruta, no lo que dice el archivo.

    Renderizar la plantilla desde el test probaria que la plantilla espera los
    valores, no que `serve_panel` se los pasa — que es justamente la mitad que
    se puede olvidar.
    """
    return serve_panel(ReportGenerator()).body.decode("utf-8")


class TestCadaSeveridadTieneSuColor:
    """La clase CSS se arma concatenando el valor del enum.

    La regla decia `.alert-sev-WARNING` y el enum vale `WARN`, asi que no
    matcheaba nunca: la alerta de un caso que necesita analista —la que mas
    importa que se vea— salia sin color.
    """

    @pytest.mark.parametrize("severidad", list(Severity))
    def test_hay_una_regla_para_esta_severidad(self, panel, severidad):
        assert f".alert-sev-{severidad.value}" in panel, (
            f"{severidad.value} se pinta sin color: la regla CSS quedo con otro nombre"
        )

    def test_no_hay_reglas_para_severidades_que_no_existen(self, panel):
        declaradas = set(re.findall(r"\.alert-sev-([A-Z]+)\s", panel))
        assert declaradas <= {s.value for s in Severity}, (
            f"sobran reglas: {declaradas - {s.value for s in Severity}}"
        )


class TestLosUmbralesSonLosMismosQueLosDelInforme:

    def test_la_nota_del_juez_usa_los_umbrales_del_dominio(self, panel):
        assert f"JUEZ_APRUEBA   = {JUDGE_APPROVAL_THRESHOLD}" in panel
        assert f"JUEZ_REVISA    = {JUDGE_NEEDS_REVIEW_THRESHOLD}" in panel

    def test_el_score_antifraude_usa_la_banda_de_presentacion(self, panel):
        # No la de decision: `RISK_FRAUD_SEVERE` decide el nivel de riesgo, no
        # como se pinta. Con esa, un score 20 era «medio» aca y rojo en el informe.
        assert f"SCORE_ALTO     = {FRAUD_SCORE_HIGH_RISK_THRESHOLD}" in panel
        assert f"SCORE_MODERADO = {FRAUD_SCORE_MODERADO}" in panel

    def test_no_quedaron_marcadores_de_jinja_sin_resolver(self, panel):
        assert "{{" not in panel, "la ruta no le esta pasando todo lo que la plantilla pide"


class TestElPanelNoSeSirveCacheado:
    """Una copia vieja del panel se diagnostica como un bug del servidor.

    El panel es JavaScript que cambia con cada deploy. Sin cabecera, el navegador
    lo guarda por heuristica y sigue corriendo el codigo anterior: los sintomas
    aparecen del lado del servidor —«el formulario no se abre», «el boton pide una
    URL que ya esta»— y la causa es el cache del que mira.

    Es una pagina de herramienta, no un activo estatico: no hay nada que ganar
    guardandola.
    """

    def test_pide_no_guardar(self):
        respuesta = serve_panel(ReportGenerator())
        assert respuesta.headers.get("cache-control") == "no-store"


class TestElPanelNoAfirmaQueModeloCorre:
    """El nombre del modelo sale de la configuracion, no del codigo del panel.

    El paso «Sintetizando resolucion» anunciaba «Haiku (eval) + Sonnet
    (sintesis)» escrito a mano. Con el modo demo encendido los tres pasos corren
    por el modelo gratuito, asi que ese renglon contradecia al cartel de arriba
    —que si dice el modelo real— en la misma pantalla. Y el modelo de cada paso
    es configurable desde el propio panel, con lo cual el texto podia quedar mal
    sin que nadie tocara codigo.
    """

    @pytest.mark.parametrize("modelo", ["Haiku", "Sonnet", "claude-haiku", "claude-sonnet"])
    def test_ningun_nombre_de_modelo_escrito_a_mano_en_los_pasos(self, panel, modelo):
        import re

        pasos = re.search(r"const STEPS = \[(.*?)\];", panel, re.S)
        assert pasos, "el panel perdio su lista de pasos"
        assert modelo.lower() not in pasos.group(1).lower(), (
            f"«{modelo}» esta escrito en la lista de pasos: con otro modelo configurado, miente"
        )

    def test_el_detalle_se_arma_con_el_modelo_vigente(self, panel):
        assert "function modeloDelPaso(" in panel
        assert "detalleDeSintesis()" in panel and "detalleDeJuicio()" in panel

    def test_y_contempla_el_modo_demo(self, panel):
        """Con free tier, los tres pasos van por el mismo modelo gratuito."""
        assert "demoMode && demoCorre && demoModelo" in panel
