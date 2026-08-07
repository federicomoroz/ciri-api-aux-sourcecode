"""El canvas de n8n es un entregable: se verifica como el codigo.

Un workflow puede importar sin un solo error y no hacer nada de lo que dice
hacer. Un `Wait` sin modo de reanudacion es un `sleep`; un nodo colgado de una
pantalla de cierre nunca corre; un umbral copiado al canvas deja de seguir a su
constante. Nada de eso rompe al importar, y todo eso rompe en produccion.
"""

import json
from pathlib import Path

import pytest

N8N = Path(__file__).resolve().parents[2] / "n8n"
ORQUESTADOR = "workflow_ciri_agent.json"
FORMULARIO = "workflow_ciri_form.json"
MANEJADOR = "workflow_ciri_errors.json"


def cargar(nombre: str) -> dict:
    return json.loads((N8N / nombre).read_text(encoding="utf-8"))


def nodo(wf: dict, nombre: str) -> dict:
    for n in wf["nodes"]:
        if n["name"] == nombre:
            return n
    raise AssertionError(f"'{nombre}' no esta en el workflow")


def salidas(wf: dict, origen: str) -> list[list[str]]:
    ramas = wf["connections"].get(origen, {}).get("main", [])
    return [[c["node"] for c in (rama or [])] for rama in ramas]


TODOS = [ORQUESTADOR, FORMULARIO, MANEJADOR]


class TestHITL:
    """El HITL tiene que ser una compuerta humana, no una pausa."""

    def test_el_wait_espera_a_una_persona(self):
        wait = nodo(cargar(ORQUESTADOR), "Wait — Aprobación HITL")
        resume = wait["parameters"].get("resume")
        assert resume, (
            "sin `resume`, n8n usa 'After Time Interval' por defecto: el nodo "
            "es un sleep y el caso sigue viaje sin que nadie lo apruebe"
        )
        assert resume in ("form", "webhook")

    def test_la_espera_tiene_un_limite(self):
        wait = nodo(cargar(ORQUESTADOR), "Wait — Aprobación HITL")
        assert wait["parameters"].get("limitWaitTime") is True, (
            "una espera sin limite deja ejecuciones colgadas para siempre"
        )

    def test_no_hay_decision_por_defecto(self):
        """Regresion: `|| 'APPROVE'` aprobaba solo los casos de riesgo alto."""
        codigo = nodo(cargar(ORQUESTADOR), "Procesar Respuesta HITL")["parameters"]["jsCode"]
        assert "|| 'APPROVE'" not in codigo and '|| "APPROVE"' not in codigo, (
            "sin respuesta del analista el caso se aprobaba solo, y quedaba "
            "registrado como si lo hubiera aprobado una persona"
        )
        assert "SIN_RESPUESTA" in codigo, "falta la rama de plazo vencido"

    def test_el_feedback_lleva_la_resolucion(self):
        """Sin `resolution`, el caso nunca se indexa como precedente."""
        codigo = nodo(cargar(ORQUESTADOR), "Procesar Respuesta HITL")["parameters"]["jsCode"]
        assert "resolution:" in codigo.split("_feedback_payload")[0] or "resolution:" in codigo
        assert "motivo:" in codigo

    def test_el_feedback_viaja_como_json(self):
        """`resolution` es un objeto: bodyParameters lo aplanaba a string."""
        fb = nodo(cargar(ORQUESTADOR), "Registrar Feedback HITL")
        assert fb["parameters"].get("specifyBody") == "json"
        assert "_feedback_payload" in fb["parameters"]["jsonBody"]

    def test_un_caso_sin_revisar_no_se_vuelve_precedente(self):
        codigo = nodo(cargar(ORQUESTADOR), "Procesar Respuesta HITL")["parameters"]["jsCode"]
        assert "huboAnalista ? resolution : null" in codigo, (
            "un caso que nadie reviso no puede convertirse en el ejemplo con el "
            "que se resuelve el proximo"
        )


class TestSinLogicaDeNegocioEnElCanvas:
    """La consigna lo prohibe explicitamente: n8n orquesta, la API decide."""

    UMBRALES_PROHIBIDOS = ["0.95", "1.1", ">= 2", "&gt;= 2"]

    @staticmethod
    def _sin_comentarios(codigo: str) -> str:
        """Un comentario que explica por que un umbral no esta aca no es el umbral."""
        return "\n".join(
            linea for linea in codigo.splitlines() if not linea.lstrip().startswith("//")
        )

    @pytest.mark.parametrize("archivo", TODOS)
    def test_ningun_nodo_code_recalcula_umbrales(self, archivo):
        for n in cargar(archivo)["nodes"]:
            codigo = self._sin_comentarios(n.get("parameters", {}).get("jsCode", ""))
            for umbral in self.UMBRALES_PROHIBIDOS:
                assert umbral not in codigo, (
                    f"'{n['name']}' repite el umbral {umbral}, que vive en "
                    f"domain/constants.py — moverlo en Python no lo mueve aca"
                )

    def test_el_juez_lee_approved_y_no_compara_contra_un_numero(self):
        cond = nodo(cargar(ORQUESTADOR), "¿Juez Aprueba?")["parameters"]["conditions"]["conditions"][0]
        assert "approved" in cond["leftValue"], (
            "el umbral del juez es JUDGE_APPROVAL_THRESHOLD, no un 7 escrito a mano"
        )
        assert cond["operator"]["type"] == "boolean"

    def test_los_guardrails_se_muestran_pero_no_se_recalculan(self):
        codigo = nodo(cargar(ORQUESTADOR), "Verificar Guardrails")["parameters"]["jsCode"]
        assert "guardrail_warnings" in codigo
        assert "confidence" not in codigo, "el nodo volvia a evaluar la confianza del modelo"


class TestManejoDeErrores:
    @pytest.mark.parametrize("archivo", [ORQUESTADOR, FORMULARIO])
    def test_el_error_workflow_viaja_en_el_export(self, archivo):
        assert cargar(archivo)["settings"].get("errorWorkflow"), (
            "sin esto los Stop and Error quedan inertes al importar"
        )

    @pytest.mark.parametrize("archivo", TODOS)
    def test_todos_los_http_reintentan(self, archivo):
        wf = cargar(archivo)
        sin_reintento = [
            n["name"] for n in wf["nodes"]
            if n["type"] == "n8n-nodes-base.httpRequest" and not n.get("retryOnFail")
        ]
        assert not sin_reintento, (
            f"{sin_reintento}: el mensaje del error handler promete 3 reintentos "
            f"y Render tarda ~50s en despertar del free tier"
        )

    def test_un_fallo_al_generar_el_informe_no_responde_200_vacio(self):
        wf = cargar(ORQUESTADOR)
        assert nodo(wf, "Generar Reporte")["onError"] == "continueErrorOutput"
        ramas = salidas(wf, "Generar Reporte")
        assert len(ramas) == 2 and ramas[1], "la rama de error no va a ningun lado"
        assert "Error Handler" in ramas[1][0]

    @pytest.mark.parametrize("archivo", TODOS)
    def test_no_hay_nodos_colgados_de_una_pantalla_de_cierre(self, archivo):
        """Los nodos Form con operation=completion son terminales."""
        wf = cargar(archivo)
        terminales = {
            n["name"] for n in wf["nodes"]
            if n["type"] == "n8n-nodes-base.form"
            and n.get("parameters", {}).get("operation") == "completion"
        }
        muertos = [
            f"{origen} -> {destino}"
            for origen in terminales
            for rama in salidas(wf, origen)
            for destino in rama
        ]
        assert not muertos, f"nunca se ejecutan: {muertos}"

    @pytest.mark.parametrize("archivo", TODOS)
    def test_ninguna_conexion_apunta_a_un_nodo_inexistente(self, archivo):
        wf = cargar(archivo)
        nombres = {n["name"] for n in wf["nodes"]}
        rotas = [
            f"{origen} -> {destino}"
            for origen in wf["connections"]
            for rama in salidas(wf, origen)
            for destino in rama
            if destino not in nombres
        ]
        assert not rotas, rotas
        assert not [o for o in wf["connections"] if o not in nombres]
