"""Los prompts no pueden reescribir valores que el codigo ya decide.

Un prompt es texto, asi que nada impide copiar adentro una lista de paises o un
umbral. Cuando eso pasa hay dos fuentes de verdad y el informe termina mostrando
las dos en la misma pagina: el codigo aplica una y el LLM razona con la otra.

Estos tests no revisan la redaccion — revisan que los valores del dominio lleguen
al prompt por inyeccion y no por copia.
"""

from api.app.domain.constants import JUDGE_APPROVAL_THRESHOLD
from api.app.domain.enums import LATAM_COUNTRIES
from api.app.llm import prompts


class TestLosPaisesLatamSalenDelDominio:
    """`check_sla` y el prompt tienen que estar de acuerdo sobre que es LATAM.

    El prompt traia su propia lista de 20 paises y el enum tenia 7. Para un ECU,
    `check_sla` aplicaba el plazo extendido de no-LATAM y el LLM marcaba
    POL-EXC-004 como no aplicable porque para el ECU si era LATAM.
    """

    def _linea(self) -> str:
        return next(
            linea for linea in prompts.v1_policy_eval.SYSTEM.splitlines() if "Paises LATAM" in linea
        )

    def test_el_prompt_nombra_todos_los_paises_del_dominio(self):
        linea = self._linea()
        faltan = [p for p in LATAM_COUNTRIES if p not in linea]
        assert not faltan, f"el prompt no conoce {faltan}: volvio a ser una lista aparte"

    def test_el_prompt_no_agrega_paises_que_el_codigo_no_reconoce(self):
        # Tres letras mayusculas en la enumeracion de la linea.
        del_prompt = {
            t.strip(" .") for t in self._linea().split(":")[1].split(",")
        }
        assert del_prompt == LATAM_COUNTRIES, (
            "el prompt reconoce paises que `check_sla` trataria como no-LATAM"
        )

    def test_no_quedo_el_marcador_sin_reemplazar(self):
        assert "{latam}" not in prompts.v1_policy_eval.SYSTEM


class TestElUmbralDelJuezSaleDeLaConstante:
    """El codigo solo aplica la constante cuando el modelo omite `approved`.

    O sea que la copia del prompt es la que gobierna el flag en la practica.
    Mientras estuvo escrita ahi, mover `JUDGE_APPROVAL_THRESHOLD` cambiaba el
    color del informe y el respaldo, pero no lo que el juez devolvia.
    """

    def test_el_prompt_declara_el_umbral_configurado(self):
        linea = next(
            x for x in prompts.v1_judge.SYSTEM.splitlines() if x.startswith("approved =")
        )
        assert str(JUDGE_APPROVAL_THRESHOLD) in linea, (
            f"el prompt aprueba con otro umbral que {JUDGE_APPROVAL_THRESHOLD}"
        )

    def test_no_quedo_el_marcador_sin_reemplazar(self):
        assert "{umbral}" not in prompts.v1_judge.SYSTEM


class TestNingunPromptDictaLoQueUnaPoliticaHace:
    """Las politicas son datos: el prompt no puede traer su semantica escrita.

    Hasta v1.3, dos reglas del system prompt decian «POL-EXC-003 aplica SIEMPRE
    como BLOCKER cuando el metodo de pago es Cripto» y otra tanto para
    POL-FRD-001. Editar esas politicas por la API —que es lo que el proyecto
    ofrece como su principio central— no cambiaba lo que el prompt forzaba, y
    borrarlas dejaba al prompt citando una politica inexistente.

    Hoy quien puede bloquear se lo dice la marca `[PUEDE BLOQUEAR]` que el
    formateador pone leyendo `puede_bloquear` de la politica. Los ejemplos si
    pueden nombrar codigos: muestran la politica con su texto al lado, asi que
    ensenian la forma del razonamiento y no una regla sobre un codigo.
    """

    # Donde arrancan los ejemplos. Antes de esta marca esta la parte imperativa
    # del prompt, que es la que no puede nombrar politicas.
    INICIO_DE_EJEMPLOS = "EJEMPLO"

    def _parte_imperativa(self, modulo) -> str:
        texto = modulo.SYSTEM
        corte = texto.find(self.INICIO_DE_EJEMPLOS)
        return texto[:corte] if corte != -1 else texto

    def test_las_reglas_no_nombran_ninguna_politica(self):
        import re

        reglas = self._parte_imperativa(prompts.v1_policy_eval)
        nombradas = set(re.findall(r"POL-[A-Z]{2,4}-\d{3}", reglas))
        assert not nombradas, (
            f"las reglas del prompt nombran {sorted(nombradas)}: editar esas "
            "politicas por la API no cambiaria lo que el prompt fuerza"
        )

    def test_la_regla_generica_esta_en_su_lugar(self):
        reglas = self._parte_imperativa(prompts.v1_policy_eval)
        assert "[PUEDE BLOQUEAR]" in reglas, (
            "sin la regla que apunta a la marca, el prompt no tiene forma de "
            "saber quien puede bloquear"
        )

    def test_el_formateador_pone_la_marca_leyendo_la_politica(self):
        from api.app.rag.formatter import format_policies_for_prompt

        puede = {"code": "POL-XXX-001", "name": "n", "description": "d",
                 "category": "c", "reference": "r", "puede_bloquear": True}
        no_puede = {**puede, "code": "POL-XXX-002", "puede_bloquear": False}

        texto = format_policies_for_prompt([puede, no_puede])
        marcadas = [ln for ln in texto.splitlines() if "[PUEDE BLOQUEAR]" in ln]
        assert len(marcadas) == 1, f"se marcaron {len(marcadas)} politicas, esperaba 1"

    def test_apagar_la_marca_por_la_api_se_refleja_en_el_prompt(self):
        """El escenario que el defecto hacia imposible."""
        from api.app.rag.formatter import format_policies_for_prompt

        cripto = {"code": "POL-EXC-003", "name": "Criptomonedas", "description": "d",
                  "category": "c", "reference": "r", "puede_bloquear": True}
        assert "[PUEDE BLOQUEAR]" in format_policies_for_prompt([cripto])

        # Compliance la edita para que deje de bloquear.
        assert "[PUEDE BLOQUEAR]" not in format_policies_for_prompt(
            [{**cripto, "puede_bloquear": False}]
        )


class TestElPlazoMedidoLlegaAlEvaluador:
    """El evaluador de politicas pedia fechas que el sistema ya tenia medidas.

    `check_sla` devuelve la apertura del reclamo, la fecha de corte y los dias
    habiles contra el limite que aplica. Ese resultado se usaba solo para la
    compensacion y no entraba al prompt, asi que varias politicas de plazo
    salian en WARNING con la razon «no se proporciona la fecha de inicio del
    reclamo» — sobre un dato que el paso anterior habia calculado. Cada uno de
    esos WARNING pide revision humana: el caso se derivaba a una persona por
    como se armaba el contexto y no por su riesgo.
    """

    SLA = {
        "within_sla": True, "days_elapsed": 6, "sla_limit_days": 10,
        "medido_desde": "2024-08-08", "medido_hasta": "2024-08-18",
        "policy_reference": "POL-SLA-002", "sin_reclamo_registrado": False,
    }

    def _usuario(self, **kw) -> str:
        _, usuario = prompts.v1_policy_eval.render(
            transaction={"id": "TXN-00006"}, policies_text="POL-SLA-002", policy_count=1, **kw,
        )
        return usuario

    def test_las_fechas_medidas_viajan_al_prompt(self):
        usuario = self._usuario(sla=self.SLA)
        assert "2024-08-08" in usuario, "la apertura del reclamo tiene que llegar"
        assert "2024-08-18" in usuario
        assert '"days_elapsed": 6' in usuario

    def test_sin_plazo_medido_lo_dice_en_vez_de_callarse(self):
        """Un hueco en silencio invita a inventar; declarado, produce WARNING."""
        usuario = self._usuario()
        assert prompts.v1_policy_eval.SIN_PLAZOS in usuario

    def test_un_sla_vacio_se_trata_como_ausente(self):
        """`{}` es lo que llega cuando el nodo de SLA no respondio."""
        assert prompts.v1_policy_eval.SIN_PLAZOS in self._usuario(sla={})


class TestNoSeDeclaraCumplimientoPorFaltaDePrueba:
    """PASS es «los datos muestran que no se viola», no «no encontre nada malo».

    POL-SLA-003 salia PASS «sin registro de que el caso haya permanecido abierto
    por mas de 15 dias», y en el mismo informe POL-SLA-002 salia WARNING por
    faltarle las mismas fechas. Dos veredictos opuestos sobre el mismo hueco.
    """

    def test_la_regla_esta_en_el_prompt(self):
        assert "NO DECLARES CUMPLIMIENTO POR FALTA DE PRUEBA" in prompts.v1_policy_eval.SYSTEM

    def test_manda_que_dos_politicas_con_el_mismo_hueco_digan_lo_mismo(self):
        sistema = prompts.v1_policy_eval.SYSTEM
        assert "tienen que decir lo mismo" in sistema


class TestLaRegionSaleDelPaisDeLaTransaccion:
    """No hay pais del comercio en ningun lado, y el prompt lo pedia igual.

    La regla mandaba marcar WARNING si el pais del comercio «no estaba
    confirmado», y no esta nunca: no existe la columna, ni la tabla. La politica
    de plazos extendidos quedaba en WARNING permanente por un dato inexistente,
    mientras el ejemplo del mismo prompt resolvia el caso por el pais de la
    transaccion. El criterio ahora es uno solo, y es el que aplica `check_sla`.
    """

    def test_ya_no_pide_el_pais_del_comercio(self):
        sistema = prompts.v1_policy_eval.SYSTEM
        assert "merchant_country" not in sistema
        assert "pais del comercio [merchant] no confirmado" not in sistema

    def test_manda_usar_el_country_de_la_transaccion(self):
        sistema = prompts.v1_policy_eval.SYSTEM
        assert 'campo "country" de la TRANSACCION' in sistema
