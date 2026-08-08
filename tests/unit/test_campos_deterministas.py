"""«El codigo decide, el LLM explica», contado por el codigo y no por la doc.

`docs/prompts.md` abre con la tabla de que campos fija el codigo y cuales
escribe el modelo. Es la afirmacion central del proyecto y decia «8 de 11»
cuando eran 9 de 13: contaba un campo que no existe (`risk_reason`), omitia dos
que si, y marcaba `log_summary` como determinista cuando el modelo lo
parafraseaba y su version era la que quedaba.

Nada ataba la tabla al codigo. Estos tests la atan por los dos lados: que el
reparto sea el que la doc dice, y que cada funcion que la tabla nombra exista.
"""

import re
from pathlib import Path

import pytest

from api.app.domain import decision, precedentes
from api.app.domain.models import ResolutionOutput

RAIZ = Path(__file__).resolve().parents[2]
RESOLUTION = (RAIZ / "api/app/services/resolution.py").read_text(encoding="utf-8")
PROMPTS_MD = (RAIZ / "docs/prompts.md").read_text(encoding="utf-8")

# Los que el modelo escribe y nadie sobrescribe.
DEL_MODELO = frozenset({"justification", "confidence", "next_steps", "transaction_id"})


def _fijados_por_el_codigo() -> set[str]:
    """Los campos que `resolve()` sobrescribe despues de la llamada al modelo."""
    return set(re.findall(r'resolution\["(\w+)"\]\s*=', RESOLUTION))


class TestElRepartoEsElQueLaDocAfirma:

    def test_cada_campo_o_lo_fija_el_codigo_o_lo_escribe_el_modelo(self):
        campos = set(ResolutionOutput.model_fields)
        fijados = _fijados_por_el_codigo()
        sin_clasificar = campos - fijados - DEL_MODELO
        assert not sin_clasificar, (
            f"{sorted(sin_clasificar)} no esta en ninguno de los dos lados: o el "
            "codigo dejo de fijarlo, o es un campo nuevo que nadie clasifico"
        )

    def test_el_codigo_no_dice_fijar_algo_que_no_es_campo(self):
        sobrantes = _fijados_por_el_codigo() - set(ResolutionOutput.model_fields)
        assert not sobrantes, f"resolve() escribe {sorted(sobrantes)}, que no son campos"

    def test_la_doc_declara_el_reparto_real(self):
        campos = len(ResolutionOutput.model_fields)
        fijados = len(_fijados_por_el_codigo())
        m = re.search(r"De los (\d+) campos de `ResolutionOutput`, \*\*(\d+) los fija el codigo\*\*", PROMPTS_MD)
        assert m, "docs/prompts.md perdio la frase que declara el reparto"
        assert (int(m.group(1)), int(m.group(2))) == (campos, fijados), (
            f"la doc dice {m.group(1)} campos y {m.group(2)} fijados; "
            f"el codigo tiene {campos} y fija {fijados}"
        )


class TestLoQueLaDocPrometeDeterministaLoEs:
    """Los cuatro que mas caro salen si el modelo los reescribe."""

    @pytest.mark.parametrize(
        "campo",
        ["recommended_action", "risk_level", "requires_hitl", "log_summary"],
    )
    def test_resolve_lo_sobrescribe(self, campo):
        assert f'resolution["{campo}"] =' in RESOLUTION, (
            f"{campo} figura como determinista en docs/prompts.md pero resolve() "
            "no lo sobrescribe: lo que quede es lo que escribio el modelo"
        )

    @pytest.mark.parametrize(
        "campo", ["log_summary", "precedent_summary"],
    )
    def test_y_el_prompt_no_se_lo_pide_al_modelo(self, campo):
        """Pedir lo que despues se descarta es lo que hizo divergir a `log_summary`."""
        from api.app.llm.prompts import v1_resolution

        tarea = next(
            linea for linea in v1_resolution.SYSTEM.splitlines()
            if linea.startswith("Tu tarea:")
        )
        assert campo not in tarea, (
            f"el prompt le pide {campo} al modelo y el codigo lo pisa despues"
        )


class TestLasFuncionesQueLaTablaNombraExisten:
    """Cinco nombres de la doc murieron en el refactor y nadie se entero."""

    def test_todas(self):
        citadas = set(re.findall(r"`(decision|precedentes|patrones)\.(\w+)\(\)`", PROMPTS_MD))
        assert citadas, "la tabla dejo de citar funciones — cambio de formato?"
        modulos = {"decision": decision, "precedentes": precedentes}
        faltantes = [
            f"{mod}.{fn}" for mod, fn in citadas
            if mod in modulos and not hasattr(modulos[mod], fn)
        ]
        assert not faltantes, f"docs/prompts.md cita funciones que no existen: {faltantes}"
