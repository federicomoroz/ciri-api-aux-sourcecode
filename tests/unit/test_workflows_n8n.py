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
        """Este test se llamaba asi y verificaba otra cosa.

        Comprobaba que la rama de error fuera al Error Handler, que es cierto y
        no alcanza: el Error Handler es otra ejecucion y no contesta la peticion
        original. La rama derivaba bien y el que llamaba seguia recibiendo
        `200 | 0 bytes`. Ahora se verifica lo que el nombre promete.
        """
        wf = cargar(ORQUESTADOR)
        assert nodo(wf, "Generar Reporte")["onError"] == "continueErrorOutput"
        ramas = salidas(wf, "Generar Reporte")
        assert len(ramas) == 2 and ramas[1], "la rama de error no va a ningun lado"

        respuesta = nodo(wf, ramas[1][0])
        assert respuesta["type"] == "n8n-nodes-base.respondToWebhook", (
            f"la rama de error entra a {ramas[1][0]}: nadie contesta la peticion"
        )
        assert respuesta["parameters"]["options"]["responseCode"] != 200
        # Y despues sigue derivando, que era lo que este test ya cuidaba.
        assert "Error Handler" in salidas(wf, respuesta["name"])[0][0]

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


class TestElContextoLlegaEntero:
    """Enumerar campos a mano en un nodo HTTP es una lista que se desincroniza.

    Regresion: `Compilar Contexto` empezo a incluir `sla` y `Sintetizar
    Resolucion` siguio mandando los diez `bodyParameters` de siempre. La API
    recibia peticiones sin SLA, la compensacion nunca se aplicaba por n8n y si
    por el pipeline directo — los dos caminos divergian justo en el unico campo
    con consecuencia monetaria.
    """

    def test_la_resolucion_recibe_el_contexto_compilado_completo(self):
        n = nodo(cargar(ORQUESTADOR), "Sintetizar Resolución")
        assert n["parameters"].get("specifyBody") == "json", (
            "con bodyParameters hay que enumerar los campos, y esa lista ya se desincronizo"
        )
        assert "JSON.stringify($json)" in n["parameters"]["jsonBody"]

    def test_el_sla_se_pide_con_el_id_de_la_transaccion(self):
        """Las fechas del reclamo las resuelve la API, no el canvas."""
        n = nodo(cargar(ORQUESTADOR), "Verificar SLA")
        assert "transaction_id" in n["parameters"]["jsonBody"]

    def test_compilar_contexto_incluye_el_sla(self):
        codigo = nodo(cargar(ORQUESTADOR), "Compilar Contexto")["parameters"]["jsCode"]
        assert "sla:" in codigo


class TestDerivacion:
    def test_el_enrutador_mira_requires_hitl(self):
        """Regresion: enrutaba por `risk_level`.

        Un caso MEDIUM con `requires_hitl=true` —un solo FAIL sin fraude severo—
        salia por la rama que cierra el caso y respondia 200 sin que ningun
        analista lo viera. El nivel de riesgo dice cuan grave es; quien dice si
        hace falta una persona es `requires_hitl`.
        """
        wf = cargar(ORQUESTADOR)
        sw = nodo(wf, "Switch — Derivación")
        primera = sw["parameters"]["rules"]["values"][0]["conditions"]["conditions"][0]
        assert "requires_hitl" in primera["leftValue"]
        assert salidas(wf, "Switch — Derivación")[0] == ["Avisar — Formulario HITL"]
        assert salidas(wf, "Avisar — Formulario HITL")[0] == ["Responder — Requiere Aprobación"]

    @pytest.mark.parametrize("archivo", TODOS)
    def test_toda_expresion_de_url_empieza_con_igual(self, archivo):
        """Sin el `=`, n8n manda `{{ ... }}` literal y la peticion falla."""
        malas = [
            n["name"] for n in cargar(archivo)["nodes"]
            if isinstance(n["parameters"].get("url"), str)
            and "{{" in n["parameters"]["url"]
            and not n["parameters"]["url"].startswith("=")
        ]
        assert not malas, f"urls que n8n no va a evaluar: {malas}"


class TestElFormularioDeAprobacionSeAnuncia:
    """La URL del formulario del Wait solo existe mientras se corre.

    n8n la genera al llegar al nodo y la expone en `$execution.resumeFormUrl`,
    que **solo se puede leer antes**. Si nadie la manda a ningun lado, el caso
    queda esperando una decision que nadie puede tomar: quien disparo el webhook
    se cuelga hasta que vence el plazo y no hay link en ninguna parte. Pasaba con
    todo caso HIGH, que es la mayoria — cualquier FAIL de politica deriva ahi.
    """

    def test_el_aviso_va_antes_del_wait(self):
        """Despues del Wait la URL ya no se puede leer: el orden es el arreglo.

        Los dos que la usan —la alerta y la respuesta al que llamo— tienen que
        estar antes.
        """
        wf = cargar(ORQUESTADOR)
        cadena = ["Avisar — Formulario HITL", "Responder — Requiere Aprobación"]
        for actual, siguiente in zip(
            cadena, cadena[1:] + ["Wait — Aprobación HITL"], strict=True,
        ):
            assert salidas(wf, actual)[0] == [siguiente]

    def test_manda_la_url_del_formulario(self):
        cuerpo = nodo(cargar(ORQUESTADOR), "Avisar — Formulario HITL")["parameters"]["jsonBody"]
        assert "$execution.resumeFormUrl" in cuerpo

    def test_va_a_las_alertas_que_el_panel_lista(self):
        """Un aviso que no se puede leer no es un aviso."""
        url = nodo(cargar(ORQUESTADOR), "Avisar — Formulario HITL")["parameters"]["url"]
        assert url.endswith("/api/alerts/")
        assert url.startswith("=")

    def test_dice_de_que_transaccion_es(self):
        cuerpo = nodo(cargar(ORQUESTADOR), "Avisar — Formulario HITL")["parameters"]["jsonBody"]
        assert "transaction_id" in cuerpo

    def test_si_el_aviso_falla_el_caso_igual_espera(self):
        """Perder el link es molesto; aprobar solo un riesgo alto, no."""
        n = nodo(cargar(ORQUESTADOR), "Avisar — Formulario HITL")
        assert n["onError"] == "continueRegularOutput"


class TestElFormularioDelWaitSeSirve:
    """El formulario devolvia 500 y nadie podia aprobar nada.

    Un Wait con `resume: form` sirve una pagina, o sea produce una respuesta
    HTTP. Si aguas abajo hay un `Respond to Webhook`, n8n exige que el Wait
    declare quien responde; con el default (`onReceived`) se niega a servir la
    pagina y tira «Unused Respond to Webhook node found in the workflow».

    O sea que la rama HITL entera —la mayoria de los casos— nunca se pudo
    completar. No se veia porque el error recien aparece al ABRIR el formulario,
    y hasta ahora nadie tenia su URL para abrirlo.
    """

    @staticmethod
    def _waits_con_formulario(wf):
        return [n for n in wf["nodes"]
                if n["type"] == "n8n-nodes-base.wait"
                and n["parameters"].get("resume") == "form"]

    @pytest.mark.parametrize("archivo", TODOS)
    def test_declara_quien_responde_si_hay_respond_abajo(self, archivo):
        wf = cargar(archivo)
        hay_respond = any(
            n["type"] == "n8n-nodes-base.respondToWebhook" for n in wf["nodes"]
        )
        if not hay_respond:
            pytest.skip("sin nodo Respond, el default sirve")
        malos = [
            n["name"] for n in self._waits_con_formulario(wf)
            if n["parameters"].get("responseMode") != "responseNode"
        ]
        assert not malos, f"el formulario de estos Wait va a devolver 500: {malos}"

    def test_el_analista_recibe_el_informe_al_aprobar(self):
        """`responseNode` no es un tramite: es lo que cierra el circuito.

        Quien aprueba recibe el informe terminado en el mismo browser donde
        acaba de decidir, en vez de aprobar a ciegas y buscarlo despues.
        """
        wf = cargar(ORQUESTADOR)
        wait = nodo(wf, "Wait — Aprobación HITL")
        assert wait["parameters"]["responseMode"] == "responseNode"
        assert "Generar Reporte" in salidas(wf, "Procesar Respuesta HITL")[0]


class TestElFormularioSeAbreSolo:
    """Que el link exista no alcanza: hay que llegar.

    Primero no se publicaba en ningun lado. Despues se publicaba en una alerta,
    que habia que ir a buscar. Despues en una pagina con un boton, que habia que
    apretar. Cada paso dejaba el ultimo tramo a mano.
    """

    def test_el_formulario_dice_de_que_caso_es(self):
        """Al redirigir, el formulario es lo PRIMERO que ve el analista.

        Antes el contexto lo daba la pagina intermedia. Un formulario que pide
        aprobar o rechazar sin decir que transaccion es, es una pregunta que no
        se puede contestar.
        """
        wait = nodo(cargar(ORQUESTADOR), "Wait — Aprobación HITL")
        assert "transaction_id" in wait["parameters"]["formTitle"]
        descripcion = wait["parameters"]["formDescription"]
        assert "risk_level" in descripcion and "hitl_reason" in descripcion

    def test_el_formulario_sigue_avisando_que_vence(self):
        wait = nodo(cargar(ORQUESTADOR), "Wait — Aprobación HITL")
        assert "24 horas" in wait["parameters"]["formDescription"]
        assert wait["parameters"]["limitWaitTime"] is True


class TestNingunCaminoContestaVacio:
    """Un 200 sin cuerpo es indistinguible de un exito, y eso pasaba mucho.

    El webhook usa `responseMode: responseNode`: contesta lo que diga un nodo
    `Respond to Webhook`, y si ninguno corre, n8n responde 200 con el cuerpo
    vacio. Cada `stopAndError` cortaba la ejecucion antes de llegar a uno, asi
    que un `transaction_id` mal formado, una transaccion inexistente y la API
    caida se veian igual que un informe generado. Medido con curl: los tres
    devolvian `HTTP 200 | 0 bytes`.

    Lo mismo pasaba con el caso que necesita una persona: quedaba esperando y el
    que llamo no se enteraba ni de eso ni de donde estaba el formulario.
    """

    STOP = "n8n-nodes-base.stopAndError"
    RESPOND = "n8n-nodes-base.respondToWebhook"

    @staticmethod
    def _entradas(wf, nombre):
        return [
            origen for origen, salidas in wf["connections"].items()
            for salida in salidas.get("main", [])
            for c in (salida or []) if c["node"] == nombre
        ]

    @pytest.mark.parametrize("archivo", TODOS)
    def test_todo_final_de_error_contesta_antes_de_cortar(self, archivo):
        wf = cargar(archivo)
        tipos = {n["name"]: n["type"] for n in wf["nodes"]}
        if not any(t == "n8n-nodes-base.webhook" for t in tipos.values()):
            pytest.skip("sin webhook no hay a quien contestarle")
        mudos = [
            n["name"] for n in wf["nodes"] if n["type"] == self.STOP
            and not any(tipos.get(e) == self.RESPOND for e in self._entradas(wf, n["name"]))
        ]
        assert not mudos, f"cortan la ejecucion sin contestar: {mudos}"

    def test_cada_falla_tiene_su_codigo(self):
        """Un 400 y un 503 no son lo mismo: uno lo arregla quien llama."""
        wf = cargar(ORQUESTADOR)
        codigos = {
            n["name"]: n["parameters"]["options"].get("responseCode")
            for n in wf["nodes"] if n["type"] == self.RESPOND
        }
        assert codigos["Responder — Formato Inválido"] == 400
        # Este es una expresion: 404 si el caso no existe, 503 si la API no
        # respondio. Quien llama actua distinto — corrige el id, o reintenta.
        assert "404" in str(codigos["Responder — API No Disponible"])
        assert "503" in str(codigos["Responder — API No Disponible"])
        assert codigos["Responder — Falla del Análisis"] == 502
        assert codigos["Responder — Falla del Informe"] == 502
        fijos = [c for c in codigos.values() if isinstance(c, int)]
        assert all(200 <= c < 600 for c in fijos)

    def test_ninguna_falla_contesta_200(self):
        """Que el informe salga bien es lo unico que merece un 200."""
        wf = cargar(ORQUESTADOR)
        for n in wf["nodes"]:
            if n["type"] != self.RESPOND or n["name"] == "Responder — Reporte":
                continue
            assert str(n["parameters"]["options"]["responseCode"]) != "200", n["name"]

    def test_el_caso_que_espera_una_persona_va_derecho_al_formulario(self):
        """Y se entera ANTES del Wait, que es cuando todavia hay a quien decirselo.

        Con una pagina intermedia el formulario seguia sin abrirse: habia que
        apretar un boton. Un 303 con `Location` lo abre solo.
        """
        wf = cargar(ORQUESTADOR)
        assert salidas(wf, "Responder — Requiere Aprobación")[0] == ["Wait — Aprobación HITL"]
        n = nodo(wf, "Responder — Requiere Aprobación")
        assert n["parameters"]["options"]["responseCode"] == 303
        cabeceras = {
            e["name"].lower(): e["value"]
            for e in n["parameters"]["options"]["responseHeaders"]["entries"]
        }
        assert "$execution.resumeFormUrl" in cabeceras.get("location", ""), (
            "sin Location el 303 no lleva a ningun lado"
        )
        cuerpo = n["parameters"]["responseBody"]
        assert "$execution.resumeFormUrl" in cuerpo, (
            "la respuesta no lleva el link al formulario: hay que ir a buscarlo a las alertas"
        )
        # `$json` ahi es la respuesta de /api/alerts/, que no sabe nada del caso:
        # la pagina salia con el id y el nivel de riesgo en blanco.
        assert "$json.resolution" not in cuerpo, "lee la resolucion del nodo equivocado"
        assert "$('Preparar Informe').first().json.resolution" in cuerpo

    def test_el_aviso_de_aprobacion_no_depende_de_internet(self):
        """Se sirve desde n8n, que ademas lo sandboxea: un CDN no cargaria."""
        cuerpo = nodo(cargar(ORQUESTADOR), "Responder — Requiere Aprobación")["parameters"]["responseBody"]
        assert "http://" not in cuerpo and "https://" not in cuerpo
