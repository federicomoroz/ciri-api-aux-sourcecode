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
