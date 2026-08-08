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
