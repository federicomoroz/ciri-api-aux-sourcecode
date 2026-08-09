"""Los numeros del analisis de politicas son los que la auditoria mide.

`docs/politicas_vs_dataset.md` afirma cuantas politicas se pueden evaluar con los
datos del dataset y cuales no. Eso es prosa, y la prosa envejece: el CRUD puede
cargar una politica nueva, o el seed puede traer otro dataset, y el documento
seguiria diciendo lo mismo.

Estos tests corren la auditoria de verdad contra la base y comparan con lo que el
documento declara. Es el mismo criterio que el resto de `test_documentacion_verificable`:
un numero publicado que nadie recalcula es un numero que va a mentir.
"""

import re
import sqlite3
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))

from scripts.auditar_politicas import EVALUABLE, PARCIAL, POLITICAS, SIN_DATO, auditar  # noqa: E402

BASE = RAIZ / "data" / "chargeback.db"
DOC = RAIZ / "docs" / "politicas_vs_dataset.md"


@pytest.fixture(scope="module")
def informe():
    # La base es una salida del seed y esta en el .gitignore, asi que no viaja en
    # el paquete. Sin ella no hay nada que auditar y saltear es lo correcto.
    if not BASE.exists():
        pytest.skip("data/chargeback.db no existe (correr scripts/seed_data.py)")
    with sqlite3.connect(BASE) as cx:
        return auditar(cx)


@pytest.fixture(scope="module")
def doc():
    return DOC.read_text(encoding="utf-8")


class TestElDocumentoDiceLoQueLaAuditoriaMide:

    def test_los_tres_conteos_de_la_tabla(self, informe, doc):
        """La tabla de arriba del documento, contra la medicion."""
        r = informe["resumen"]
        declarados = {int(m) for m in re.findall(r"\*\*(\d+)\*\*", doc)}
        for cuantas, que in (
            (r["evaluables"], "evaluables"),
            (r["parciales"], "parciales"),
            (r["sin_dato"], "sin dato"),
        ):
            assert cuantas in declarados, (
                f"la auditoria cuenta {cuantas} politicas {que} y el documento no lo declara"
            )

    def test_nombra_las_politicas_que_bloquean_todo_caso(self, informe, doc):
        """Si una politica impide cerrar cualquier caso, tiene que estar nombrada."""
        for codigo in informe["resumen"]["bloquean_todo_caso"]:
            assert codigo in doc, f"{codigo} bloquea todos los casos y el documento no la nombra"

    def test_nombra_las_politicas_que_nunca_se_disparan(self, informe, doc):
        for codigo in informe["resumen"]["nunca_se_disparan"]:
            assert codigo in doc, f"{codigo} nunca se dispara y el documento no la nombra"


class TestLaAuditoriaCubreElReglamentoEntero:

    def test_no_queda_ninguna_politica_sin_auditar(self, informe):
        """Auditar 16 de 17 sin avisar seria peor que no auditar."""
        auditadas = {f["codigo"] for f in informe["politicas"]}
        with sqlite3.connect(BASE) as cx:
            cargadas = {r[0] for r in cx.execute("select code from policies")}
        assert cargadas <= auditadas, f"sin auditar: {sorted(cargadas - auditadas)}"

    def test_cada_politica_declara_que_dato_necesita(self):
        """El «necesita» es el trabajo analitico: sin el, la clasificacion no se revisa."""
        for p in POLITICAS:
            assert p.necesita.strip(), f"{p.codigo} no declara que dato necesita"

    def test_toda_politica_recibe_uno_de_los_tres_estados(self, informe):
        validos = {EVALUABLE, PARCIAL, SIN_DATO}
        for f in informe["politicas"]:
            assert f["estado"] in validos, f"{f['codigo']} quedo en estado {f['estado']!r}"

    def test_la_que_bloquea_explica_por_que(self, informe):
        """Un bloqueo sin motivo escrito es una afirmacion sin defensa."""
        for f in informe["politicas"]:
            if f["codigo"] in informe["resumen"]["bloquean_todo_caso"]:
                assert len(f["detalle"]) > 80, f"{f['codigo']} bloquea y casi no lo explica"
                assert f["consecuencia"], f"{f['codigo']} no dice que le pasa al caso"


def test_el_documento_no_propone_editar_las_politicas(doc):
    """La tentacion obvia es subir el umbral para que el agente apruebe.

    Seria falsificar la entrada: las politicas vienen con el enunciado. El
    documento tiene que decirlo explicitamente, porque es la conclusion que
    cualquiera saca al leer que 15 de 15 comercios fallan.
    """
    assert "no hay que hacer es cambiar las políticas" in doc.lower() or \
           "no hay que hacer es cambiar las politicas" in doc.lower(), (
        "el documento dejo de advertir contra editar las politicas del enunciado"
    )
