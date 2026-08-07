"""El harness de evaluacion: lo que se puede probar sin gastar modelo.

Corre el pipeline completo, asi que sus llamadas al LLM no se testean aca — eso
es lo que cuesta plata. Lo que si se fija es todo lo demas: que la muestra sea
reproducible, que no invente casos, que registre la configuracion con la que
corrio, y que aborte antes de gastar si falta la clave. Un harness de medicion
cuyo muestreo no es reproducible no sirve para medir nada.
"""

import argparse
import subprocess
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))

from scripts.evaluar import SEMILLA, _elegir, _versiones_de_prompt  # noqa: E402

TRANSACCIONES = [{"id": f"TXN-{i:05d}", "merchant": "X"} for i in range(1, 101)]


class DBFalsa:
    def get_all_transactions(self):
        return list(TRANSACCIONES)


def args(**kw):
    base = {"casos": "", "todos": False, "n": 20, "semilla": SEMILLA}
    return argparse.Namespace(**{**base, **kw})


class TestMuestreo:
    def test_la_misma_semilla_da_la_misma_muestra(self):
        """«Al azar» tiene que seguir siendo reproducible, o no se puede auditar."""
        a = _elegir(DBFalsa(), args())
        b = _elegir(DBFalsa(), args())
        assert [t["id"] for t in a] == [t["id"] for t in b]

    def test_semillas_distintas_dan_muestras_distintas(self):
        a = _elegir(DBFalsa(), args(semilla=1))
        b = _elegir(DBFalsa(), args(semilla=2))
        assert [t["id"] for t in a] != [t["id"] for t in b]

    def test_respeta_el_n_pedido(self):
        assert len(_elegir(DBFalsa(), args(n=7))) == 7

    def test_todos_devuelve_el_dataset_entero(self):
        assert len(_elegir(DBFalsa(), args(todos=True))) == len(TRANSACCIONES)

    def test_una_lista_explicita_se_respeta(self):
        elegidas = _elegir(DBFalsa(), args(casos="TXN-00051,TXN-00042"))
        assert {t["id"] for t in elegidas} == {"TXN-00051", "TXN-00042"}

    def test_un_caso_inexistente_aborta_antes_de_gastar(self):
        with pytest.raises(SystemExit) as e:
            _elegir(DBFalsa(), args(casos="TXN-99999"))
        assert "TXN-99999" in str(e.value)

    def test_pedir_mas_de_los_que_hay_no_explota(self):
        assert len(_elegir(DBFalsa(), args(n=500))) == len(TRANSACCIONES)


class TestConfiguracionRegistrada:
    """Un resultado sin la configuracion con la que se obtuvo no es evidencia."""

    def test_lee_las_versiones_reales_de_los_prompts(self):
        versiones = _versiones_de_prompt()
        assert set(versiones) == {"policy_eval", "resolution", "judge"}
        assert all(v.startswith("v") for v in versiones.values())

    def test_las_versiones_coinciden_con_las_cabeceras(self):
        import re

        for nombre, version in _versiones_de_prompt().items():
            archivo = RAIZ / "api" / "app" / "llm" / "prompts" / f"v1_{nombre}.py"
            primera = archivo.read_text(encoding="utf-8").splitlines()[0]
            assert re.search(rf"PROMPT VERSION:\s*{re.escape(version)}\b", primera)


def test_sin_clave_aborta_sin_gastar():
    """El primer control es no gastar por accidente."""
    import os

    # El entorno se hereda —si no, ni siquiera encuentra las dependencias— y
    # sólo se vacía la clave. `.env` tampoco debe rescatarlo: se apunta a un
    # directorio sin archivo de configuración.
    entorno = {**os.environ, "CB_ANTHROPIC_API_KEY": "", "CB_ENV_FILE": "no-existe.env"}
    r = subprocess.run(
        [sys.executable, "scripts/evaluar.py", "--n", "1"],
        cwd=RAIZ, capture_output=True, text=True, env=entorno,
    )
    assert r.returncode != 0
    assert "CB_ANTHROPIC_API_KEY" in (r.stderr + r.stdout)
