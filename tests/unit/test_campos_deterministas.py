"""«El codigo decide, el LLM explica», contado por el codigo y no por la doc.

`docs/prompts.md` abre con la tabla de que campos fija el codigo y cuales
escribe el modelo. Es la afirmacion central del proyecto y decia «8 de 11»
cuando eran 9 de 13: contaba un campo que no existe (`risk_reason`), omitia dos
que si, y marcaba `log_summary` como determinista cuando el modelo lo
parafraseaba y su version era la que quedaba.

Nada ataba la tabla al codigo. Estos tests la atan por los dos lados: que el
reparto sea el que la doc dice, y que cada funcion que la tabla nombra exista.

**Y no alcanzaba con vigilar `prompts.md`.** El test original miraba un solo
archivo, asi que la correccion llego ahi y el numero viejo sobrevivio en otros
cinco lugares — incluido `docs/diagrams/api.html`, que es por donde entra quien
evalua sin instalar nada. Vigilar la afirmacion en su archivo canonico y no en
todos los que la repiten es la misma clase de error que no vigilarla: la doc
equivocada sigue siendo la que alguien lee.
"""

import re
from pathlib import Path

import pytest

from api.app.domain import decision, precedentes
from api.app.domain.models import ResolutionOutput
from api.app.services.guardrails import ANTES_DEL_OVERRIDE, DESPUES_DEL_OVERRIDE

RAIZ = Path(__file__).resolve().parents[2]
RESOLUTION = (RAIZ / "api/app/services/resolution.py").read_text(encoding="utf-8")
PROMPTS_MD = (RAIZ / "docs/prompts.md").read_text(encoding="utf-8")

# Los que el modelo escribe y nadie sobrescribe.
DEL_MODELO = frozenset({"justification", "confidence", "next_steps", "transaction_id"})

# Artefactos congelados: informes ya generados y corridas de evaluacion. Narran
# lo que el sistema dijo aquel dia, no lo que hace hoy, y corregirlos seria
# falsear la evidencia.
CONGELADOS = ("HTML_Output_Examples", "evaluaciones")


def _documentos() -> list[Path]:
    """Todo lo que un evaluador lee: los .md y los diagramas que se abren solos."""
    docs = [*(RAIZ / "docs").rglob("*.md"), *(RAIZ / "docs").rglob("*.html")]
    return [
        p for p in [*docs, RAIZ / "README.md"]
        if not any(c in p.parts for c in CONGELADOS)
    ]


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


# Los diagramas escriben los numeros con letra. La primera version de estos
# tests solo miraba digitos y por eso dejo pasar «ocho de los once campos» en
# `n8n_workflow_analysis.html` — el mismo error, en el archivo que mas se mira.
PALABRAS = {
    "cero": 0, "uno": 1, "un": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
    "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "once": 11,
    "doce": 12, "trece": 13, "catorce": 14, "quince": 15,
}
_NUM = r"\d+|" + "|".join(sorted(PALABRAS, key=len, reverse=True))


def _valor(txt: str) -> int:
    return int(txt) if txt.isdigit() else PALABRAS[txt.lower()]


class TestNingunDocumentoContradiceElReparto:
    """El numero vive en seis lugares. Vigilar uno solo fue lo que dejo pasar esto.

    El **denominador** no admite version: los campos de `ResolutionOutput` son
    los que son, y una frase que diga «de 11» esta mal aunque narre el pasado.
    El **numerador** si: el changelog cuenta legitimamente que en v3.0 eran 6 y
    en v3.1 eran 8. Lo que no puede es superar a los que el codigo fija hoy —
    eso ya no es historia, es una promesa que nadie cumple.
    """

    # «9 de los 13 campos», «8 de 11 campos», «9/13 campos», «ocho de los once campos».
    REPARTO = re.compile(
        rf"({_NUM})\s*(?:/|\s+de\s+(?:los\s+)?)\s*({_NUM})\s+campos", re.I
    )
    # «De los 13 campos de `ResolutionOutput`…»
    TOTAL = re.compile(rf"[Dd]e los ({_NUM}) campos", re.I)

    @pytest.mark.parametrize("doc", _documentos(), ids=lambda p: p.name)
    def test_el_denominador_es_el_real(self, doc):
        campos = len(ResolutionOutput.model_fields)
        texto = doc.read_text(encoding="utf-8")
        malos = [
            f"«{m.group(0)}»" for m in self.REPARTO.finditer(texto)
            if _valor(m.group(2)) != campos
        ] + [
            f"«{m.group(0)}»" for m in self.TOTAL.finditer(texto)
            if _valor(m.group(1)) != campos
        ]
        assert not malos, (
            f"{doc.relative_to(RAIZ)} dice {', '.join(malos)} y `ResolutionOutput` "
            f"tiene {campos} campos"
        )

    @pytest.mark.parametrize("doc", _documentos(), ids=lambda p: p.name)
    def test_ningun_documento_promete_mas_deterministas_de_los_que_hay(self, doc):
        fijados = len(_fijados_por_el_codigo())
        texto = doc.read_text(encoding="utf-8")
        excesivos = [
            f"«{m.group(0)}»" for m in self.REPARTO.finditer(texto)
            if _valor(m.group(1)) > fijados
        ]
        assert not excesivos, (
            f"{doc.relative_to(RAIZ)} promete {', '.join(excesivos)} deterministas; "
            f"resolve() sobrescribe {fijados}"
        )

    def test_el_patron_ve_los_numeros_escritos_con_letra(self):
        """Regresion: la version anterior solo veia digitos y dejo pasar el diagrama."""
        m = self.REPARTO.search("ocho de los once campos de la resolucion")
        assert m and (_valor(m.group(1)), _valor(m.group(2))) == (8, 11), (
            "el patron dejo de leer numeros con letra; asi sobrevivio «ocho de los once»"
        )

    def test_el_reparto_vigente_esta_declarado_en_los_dos_documentos_que_lo_explican(self):
        """`prompts.md` es el canonico, pero `mejora_continua.md` tiene la tabla larga."""
        campos, fijados = len(ResolutionOutput.model_fields), len(_fijados_por_el_codigo())
        for nombre in ("docs/prompts.md", "docs/mejora_continua.md"):
            texto = (RAIZ / nombre).read_text(encoding="utf-8")
            declara = {
                (int(m.group(1)), int(m.group(2)))
                for m in re.finditer(
                    r"De los (\d+) campos de `ResolutionOutput`,\s*\*\*(\d+) los (?:fija el codigo|calcula Python)\*\*",
                    texto,
                )
            }
            assert (campos, fijados) in declara, (
                f"{nombre} no declara el reparto vigente ({fijados} de {campos}); "
                f"encontrado: {declara or 'nada'}"
            )


class TestElNumeroDeGuardrailsQueLaDocPublicita:
    """Mismo patron, otra cifra: «seis guardrails» es un diferenciador anunciado.

    La decision 22 los volvio una tupla justamente para que el numero pudiera
    crecer. Si crece, estas frases quedan viejas — y son las que alguien lee.
    """

    def test_las_reglas_son_las_que_la_doc_anuncia(self):
        reales = len(ANTES_DEL_OVERRIDE) + len(DESPUES_DEL_OVERRIDE)
        anuncios = re.compile(r"«?(seis|6)\s+guardrails|guardrails.{0,40}?\bSon seis\b", re.I)
        declarantes = [
            doc for doc in _documentos() if anuncios.search(doc.read_text(encoding="utf-8"))
        ]
        assert declarantes, "ningun documento anuncia ya el numero de guardrails — cambio el texto?"
        assert reales == 6, (
            f"hay {reales} reglas de guardrail y {len(declarantes)} documentos siguen "
            f"diciendo «seis»: {[str(d.relative_to(RAIZ)) for d in declarantes]}"
        )

    def test_los_dos_momentos_no_se_mezclaron(self):
        """Detectar antes de corregir es la mitad del valor: si se fusionan, se pierde."""
        assert ANTES_DEL_OVERRIDE and DESPUES_DEL_OVERRIDE, (
            "los guardrails dejaron de estar partidos en dos momentos; la contradiccion "
            "del modelo solo es observable antes del override"
        )
        assert not set(ANTES_DEL_OVERRIDE) & set(DESPUES_DEL_OVERRIDE), (
            "una regla corre en los dos momentos: el mismo aviso se registraria dos veces"
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
