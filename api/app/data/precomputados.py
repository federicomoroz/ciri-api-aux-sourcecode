"""Modo DEMO: los casos de ejemplo se sirven ya resueltos, sin gastar en el modelo.

Investigar un caso cuesta dinero real: dos modelos, varias llamadas. Esto es una
prueba tecnica, y quien la evalua no tiene por que consumir la cuenta de nadie
para ver como funciona. Por eso los casos de demostracion viajan con su informe
ya generado.

**El modo demo no llama al modelo.** No es que intente y falle: no gasta. Quien
quiera ver el pipeline ejecutandose de verdad carga su propia clave en el panel,
y ahi corre completo — con su cuenta.

Y lo declara por todos lados, porque un informe prearmado no puede hacerse pasar
por un analisis recien hecho:

  - el HTML abre con un cartel "DEMO (Caso prearmado)"
  - la respuesta lleva la cabecera X-Modo-Demo
  - el servidor deja un warning en el log
"""

import json
import logging
import os
import re

logger = logging.getLogger(__name__)

# El nombre del archivo lleva el caso: report_<riesgo>_<TXN>.html
_PATRON = re.compile(r"^report_[a-z]+_(TXN-\d+)\.html$", re.IGNORECASE)
# El JSON de la resolucion y del juez, junto al informe del mismo caso
_PREFIJO_ANALISIS = "analisis_"

ETIQUETA = "DEMO (Caso prearmado)"

# Lo que informa la respuesta cuando el caso salio prearmado. Ceros de verdad:
# no hubo llamadas ni tokens. Los nombres son los que lee el panel — si falta
# alguno, la vista de resultados se rompe.
USO_DEMO = {
    "demo": True,
    "cache_hit": False,
    "cost_usd": 0.0,
    "total_tokens": 0,
    "call_count": 0,
    "model": "sin llamadas al modelo",
}

CARTEL = (
    '<div style="background:#fff4e5;border-bottom:2px solid #e8a33d;padding:14px 20px;'
    'font-family:system-ui,sans-serif;font-size:14px;line-height:1.55;color:#5c3d09">'
    f'<b style="letter-spacing:.03em">{ETIQUETA}</b> &nbsp;Este informe no se genero '
    "recien: es el resultado guardado de una corrida anterior de este mismo caso. Se "
    "sirve asi para que el sistema se pueda evaluar sin consumir la cuenta del modelo. "
    "Para verlo ejecutarse de verdad, cargá tu propia clave de Anthropic en el campo "
    "<b>API key</b> del panel: ahí corre el pipeline completo y este cartel no aparece."
    "</div>"
)


def _informes_de(carpeta: str) -> list[tuple[str, str]]:
    """Los pares (archivo, transaccion) que hay en la carpeta, ordenados."""
    if not os.path.isdir(carpeta):
        return []
    encontrados = []
    for nombre in sorted(os.listdir(carpeta)):
        coincidencia = _PATRON.match(nombre)
        if coincidencia:
            encontrados.append((nombre, coincidencia.group(1).upper()))
    return encontrados


def casos_demo(carpeta: str) -> list[str]:
    """Que transacciones se pueden ver resueltas sin gastar en el modelo.

    Se lee de la carpeta en vez de escribirse en una lista: si manana se agrega o
    se saca un informe, no hay una segunda copia que quede desactualizada.
    """
    return [txn for _, txn in _informes_de(carpeta)]


def informe_demo(carpeta: str, transaction_id: str) -> str | None:
    """El informe guardado de ese caso, ya con el cartel puesto. None si no hay.

    El cartel se agrega aca y no en el archivo para que los mismos HTML sirvan de
    ejemplo en la documentacion, donde el aviso no viene al caso.
    """
    buscado = (transaction_id or "").upper()
    for nombre, txn in _informes_de(carpeta):
        if txn != buscado:
            continue
        try:
            with open(os.path.join(carpeta, nombre), encoding="utf-8") as f:
                return _con_cartel(f.read())
        except Exception:
            logger.warning("No se pudo leer el informe demo %s", nombre, exc_info=True)
            return None
    return None


def analisis_demo(carpeta: str, transaction_id: str) -> dict | None:
    """La resolucion y la evaluacion del juez guardadas de ese caso.

    Devuelve {"resolution": {...}, "judge": {...}} o None si no hay.

    Con esto el workflow de n8n corre entero sin gastar: las siete consultas de
    contexto son reales, el compilado es real y el informe se genera de verdad;
    lo unico pregrabado es lo que hubiera respondido el modelo.
    """
    ruta = os.path.join(carpeta, f"{_PREFIJO_ANALISIS}{(transaction_id or '').upper()}.json")
    if not os.path.isfile(ruta):
        return None
    try:
        with open(ruta, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        logger.warning("No se pudo leer el analisis demo de %s", transaction_id, exc_info=True)
        return None


def _con_cartel(html: str) -> str:
    """Mete el aviso apenas abre el body, para que sea lo primero que se lee."""
    inicio = html.find("<body")
    cierre = html.find(">", inicio) if inicio != -1 else -1
    if cierre == -1:
        return CARTEL + html
    return html[: cierre + 1] + CARTEL + html[cierre + 1 :]
