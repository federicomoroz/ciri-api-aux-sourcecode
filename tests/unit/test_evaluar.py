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

from api.app.llm.prompts import versiones as versiones_de_prompt  # noqa: E402
from scripts.evaluar import SEMILLA, _elegir  # noqa: E402

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
        """Las lee `llm.prompts.versiones()`, que es la misma fuente del informe.

        El script tenia su propia copia del regex sobre los mismos archivos, y
        las dos ya nombraban distinto lo mismo: `judge` aca, `v1_judge` alla. Dos
        lecturas de una sola verdad que ademas discrepaban en la clave.
        """
        versiones = versiones_de_prompt()
        assert set(versiones) == {"v1_policy_eval", "v1_resolution", "v1_judge"}
        assert all(v.startswith("v") for v in versiones.values())

    def test_las_versiones_coinciden_con_las_cabeceras(self):
        import re

        for nombre, version in versiones_de_prompt().items():
            archivo = RAIZ / "api" / "app" / "llm" / "prompts" / f"{nombre}.py"
            primera = archivo.read_text(encoding="utf-8").splitlines()[0]
            assert re.search(rf"PROMPT VERSION:\s*{re.escape(version)}\b", primera)

    def test_los_campos_que_lee_de_la_resolucion_existen(self):
        """Los `.get()` del harness contra los campos reales de `ResolveResponse`.

        `.get()` con default no avisa si la clave no existe: cuando
        `_propuesta_del_modelo` perdio el guion bajo, este lector quedo con el
        nombre viejo y dos artefactos salieron con `{}` en ese campo sin que
        nada fallara. Si el servicio renombra un campo, esto tiene que romper.
        """
        import re

        from api.app.domain.models import ResolveResponse

        fuente = (RAIZ / "scripts" / "evaluar.py").read_text(encoding="utf-8")
        leidas = set(re.findall(r'resolucion\.get\("([^"]+)"', fuente))
        assert leidas, "el harness ya no lee la resolucion con .get()? revisar el regex"
        # `_usage` es interno del servicio —tokens para calcular el costo— y el
        # serializador lo quita a proposito; el harness lo ve porque llama al
        # servicio directo, sin pasar por HTTP. Es la unica excepcion valida.
        permitidas = set(ResolveResponse.model_fields) | {"_usage"}
        assert leidas <= permitidas, (
            f"el harness lee campos que la resolucion no tiene: "
            f"{sorted(leidas - permitidas)}"
        )


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
