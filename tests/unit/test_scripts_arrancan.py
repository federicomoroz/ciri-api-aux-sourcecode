"""Los scripts que el entregable ofrece como herramienta tienen que arrancar.

`scripts/evaluar.py` es lo que el README ofrece cuando dice que el 9.1 no es
reproducible pero el instrumento para volver a medir si viaja. Estaba roto: el
refactor se llevo `db.ensure_modelos_table()` y `Analyzer.check_sla()`, y el
script moria en la primera linea util. Nadie lo noto porque ningun test lo
ejecutaba — se habian escrito tests de sus funciones puras, que es distinto.

Estos tests no miden calidad ni gastan un token: arman las dependencias y
verifican que los simbolos que cada script usa existan. Es lo minimo que
convierte «hay un script» en «hay un script que corre».
"""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
SCRIPTS = sorted(RAIZ.joinpath("scripts").glob("*.py"))


def _usa_argparse(script: Path) -> bool:
    return "ArgumentParser" in script.read_text(encoding="utf-8")


CON_PARSER = [s for s in SCRIPTS if _usa_argparse(s)]


@pytest.mark.parametrize("script", CON_PARSER, ids=lambda p: p.name)
def test_responde_a_help_sin_reventar(script):
    """`--help` recorre el parser entero y los imports del modulo.

    Solo los que tienen parser: `seguir_el_flujo.py` toma un TXN posicional y
    saldria a la red a buscar la transaccion «--help».
    """
    r = subprocess.run(
        [sys.executable, str(script), "--help"],
        capture_output=True, text=True, cwd=RAIZ, timeout=120,
    )
    assert r.returncode == 0, (
        f"{script.name} --help fallo:\n{(r.stderr or r.stdout)[-800:]}"
    )


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_al_menos_es_sintacticamente_valido(script):
    ast.parse(script.read_text(encoding="utf-8"))


class TestEvaluarUsaLaApiQueExisteHoy:
    """El script llama a metodos de servicios del paquete. Que existan.

    No alcanza con que importe: `db.ensure_modelos_table()` importaba bien y
    reventaba al llamarse. Se leen las llamadas del AST y se verifican contra
    las clases reales.
    """

    # (nombre de variable en el script, clase que la provee)
    ORIGENES = {
        "db": "api.app.data.db:Database",
        "analyzer": "api.app.analysis.analyzer:Analyzer",
        "retriever": "api.app.rag.retriever:QdrantRetriever",
    }

    @pytest.fixture(scope="class")
    def llamadas(self) -> dict[str, set[str]]:
        arbol = ast.parse((RAIZ / "scripts/evaluar.py").read_text(encoding="utf-8"))
        encontradas: dict[str, set[str]] = {v: set() for v in self.ORIGENES}
        for nodo in ast.walk(arbol):
            if (
                isinstance(nodo, ast.Call)
                and isinstance(nodo.func, ast.Attribute)
                and isinstance(nodo.func.value, ast.Name)
                and nodo.func.value.id in encontradas
            ):
                encontradas[nodo.func.value.id].add(nodo.func.attr)
        return encontradas

    @pytest.mark.parametrize("variable", list(ORIGENES))
    def test_cada_metodo_que_llama_existe(self, variable, llamadas):
        import importlib

        modulo, clase = self.ORIGENES[variable].split(":")
        tipo = getattr(importlib.import_module(modulo), clase)
        faltantes = sorted(m for m in llamadas[variable] if not hasattr(tipo, m))
        assert not faltantes, (
            f"scripts/evaluar.py llama a {clase}.{faltantes} y no existen: "
            "el script no llega a correr"
        )


def test_evaluar_no_cae_a_la_fecha_de_compra_para_el_sla():
    """La regresion que el script arrastraba del codigo viejo.

    `analysis/sla.py` documenta que sin fecha de apertura del reclamo no se
    mide el plazo, porque entre la compra y el reclamo pueden pasar meses y
    contarlos inventa un incumplimiento. El script se caia a `tx["date"]`, asi
    que la medicion habria reportado incumplimientos que el sistema no comete.
    """
    texto = (RAIZ / "scripts/evaluar.py").read_text(encoding="utf-8")
    assert 'case_open_date=caso.get("open_date") or str(tx.get("date"' not in texto, (
        "el harness vuelve a caer a la fecha de compra cuando no hay reclamo"
    )
