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

_ESTILO_CARTEL = (
    'background:#fff4e5;border-bottom:2px solid #e8a33d;padding:14px 20px;'
    'font-family:system-ui,sans-serif;font-size:14px;line-height:1.55;color:#5c3d09'
)
_COMO_CORRERLO_DE_VERDAD = (
    "Para ver el pipeline ejecutandose, cargá tu propia clave de Anthropic en el campo "
    "<b>API key</b> del panel: ahí corre completo y este cartel no aparece."
)

CARTEL = (
    f'<div style="{_ESTILO_CARTEL}">'
    f'<b style="letter-spacing:.03em">{ETIQUETA}</b> &nbsp;Este informe no se genero '
    "recien: es el resultado guardado de una corrida anterior de este mismo caso. Se "
    "sirve asi para que el sistema se pueda evaluar sin consumir la cuenta del modelo. "
    f"{_COMO_CORRERLO_DE_VERDAD}"
    "</div>"
)


def linea_de_procedencia(procedencia: dict | None) -> str:
    """Cuando y con que version se genero este analisis.

    Un informe guardado envejece: los prompts y los umbrales cambian y el
    resultado deja de ser el que el sistema produciria hoy. Decirlo es la
    diferencia entre un ejemplo y una foto vieja presentada como actual.
    """
    if not procedencia:
        return ""
    versiones = procedencia.get("prompts") or {}
    detalle = " · ".join(f"{k} {v}" for k, v in versiones.items())
    return (
        '<div style="margin-top:9px;font-size:12.5px;opacity:.85">'
        f"Generado el <b>{procedencia.get('generado', 'fecha no registrada')}</b>"
        + (f" con los prompts {detalle}. " if detalle else ". ")
        + "El sistema siguio cambiando desde entonces, asi que este resultado no es "
        "necesariamente el que produciria hoy."
        "</div>"
    )


ETIQUETA_GRATIS = "ANÁLISIS REAL (modelo gratuito)"


def cartel_modelo_gratis(modelo: dict) -> str:
    """El cartel de un analisis recien hecho con el modelo del modo demo.

    Es lo contrario del caso prearmado: el pipeline corrio entero y el resultado
    es de ahora. Lo que hay que declarar es doble — con que se corrio, y que ese
    modelo no es el de la configuracion documentada. Quien lee el informe tiene
    que poder atribuirle al modelo lo que le corresponda al modelo.
    """
    return (
        f'<div style="{_ESTILO_CARTEL}">'
        f'<b style="letter-spacing:.03em">{ETIQUETA_GRATIS}</b> &nbsp;Este informe se genero '
        "recien: el pipeline corrio entero, con RAG, guardrails y juez. Se uso el modelo "
        f"<b>{modelo.get('modelo', '')}</b> ({modelo.get('proveedor', '')}) para que evaluar "
        "el sistema no requiera cuenta propia."
        '<div style="margin-top:9px;font-size:12.5px;opacity:.85">'
        "<b>Los resultados pueden variar.</b> La configuracion documentada es Claude "
        "(Haiku + Sonnet) y los prompts estan afinados para ella: un modelo mas chico "
        "puede citar menos evidencia o razonar con menos profundidad sobre los precedentes. "
        "Para ver el sistema en su configuracion real, pasa a <b>modo produccion</b> y "
        "carga tu clave de Anthropic — se usa solo mientras el panel este abierto."
        "</div></div>"
    )


def cartel_sustituto(solicitada: str, mostrada: str) -> str:
    """El cartel cuando el caso pedido no tiene analisis guardado.

    Decir de que transaccion es el informe es lo que separa un ejemplo util de
    un informe enganoso: nadie puede confundir este con el analisis de su caso.
    """
    return (
        f'<div style="{_ESTILO_CARTEL}">'
        f'<b style="letter-spacing:.03em">{ETIQUETA}</b> &nbsp;Pediste '
        f"<b>{solicitada}</b>, pero ese caso no tiene un analisis guardado y el modelo "
        f"no esta disponible. Lo que sigue es el informe completo de <b>{mostrada}</b>, "
        f"a modo de ejemplo: <u>los datos y la resolucion son de {mostrada}, no de "
        f"{solicitada}</u>. "
        f"{_COMO_CORRERLO_DE_VERDAD}"
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


def _leer(carpeta: str, nombre: str) -> str | None:
    try:
        with open(os.path.join(carpeta, nombre), encoding="utf-8") as f:
            return f.read()
    except Exception:
        logger.warning("No se pudo leer el informe demo %s", nombre, exc_info=True)
        return None


def informe_demo(carpeta: str, transaction_id: str, solicitada: str = "") -> str | None:
    """El informe guardado de ese caso, con su cartel. None si ese caso no tiene.

    `solicitada` es el caso que pidieron, cuando no es el mismo que se devuelve:
    entonces el cartel nombra a los dos, para que nadie lea este informe como si
    fuera el analisis de su transaccion.

    El cartel se agrega aca y no en el archivo para que los mismos HTML sirvan de
    ejemplo en la documentacion, donde el aviso no viene al caso.
    """
    buscado = (transaction_id or "").upper()
    pedida = (solicitada or "").upper()
    for nombre, txn in _informes_de(carpeta):
        if txn != buscado:
            continue
        html = _leer(carpeta, nombre)
        if html is None:
            return None
        cartel = CARTEL if not pedida or pedida == txn else cartel_sustituto(pedida, txn)
        procedencia = (analisis_demo(carpeta, txn) or {}).get("procedencia")
        cartel = cartel.replace("</div>", linea_de_procedencia(procedencia) + "</div>", 1)
        return _con_cartel(html, cartel)
    return None


def analisis_demo(carpeta: str, transaction_id: str) -> dict | None:
    """La resolucion, la evaluacion del juez y los datos guardados de ese caso.

    Devuelve {"resolution": {...}, "judge": {...}, "caso": {...}} o None si no hay.

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


def distancia_de_riesgo(pedida: dict, candidato: dict) -> float:
    """Cuanto se aleja en riesgo un caso guardado del que se pidio.

    Compara el score antifraude y nada mas. Es la unica medida de riesgo que se
    tiene sin correr el pipeline, y es la que decide POL-FRD-001. Mezclarla con
    metodo de pago o pais seria comparar otra cosa.
    """
    try:
        return abs(float(pedida.get("fraud_score", 0)) - float(candidato.get("fraud_score", 0)))
    except (TypeError, ValueError):
        return float("inf")


def caso_mas_cercano_en_riesgo(carpeta: str, pedida: dict) -> str | None:
    """De los casos guardados, el mas cercano en riesgo al que se pidio."""
    candidatos = [
        (distancia_de_riesgo(pedida, (analisis_demo(carpeta, txn) or {}).get("caso", {})), txn)
        for txn in casos_demo(carpeta)
    ]
    # A igual distancia decide el id, para que la eleccion sea reproducible.
    return min(candidatos)[1] if candidatos else None


def informe_de_ejemplo(carpeta: str, pedida: dict) -> tuple[str, str] | None:
    """El informe del caso guardado mas cercano en riesgo. (html, su transaccion).

    Devolver algo util es mejor que devolver un error, pero el informe es de otra
    transaccion y el cartel lo dice sin vueltas. Nunca se mezcla: sale el informe
    entero del caso de ejemplo, jamas los datos de uno con la resolucion de otro.
    """
    txn = caso_mas_cercano_en_riesgo(carpeta, pedida)
    if txn is None:
        return None
    solicitada = (pedida.get("id") or pedida.get("transaction_id") or "").upper()
    html = informe_demo(carpeta, txn, solicitada=solicitada or "-")
    if html is None:
        return None
    logger.warning(
        "MODO DEMO: %s no tiene analisis guardado; se responde con %s, el caso de "
        "ejemplo mas cercano en riesgo. Queda declarado en el propio informe.",
        solicitada or "(sin id)", txn,
    )
    return html, txn


def _con_cartel(html: str, cartel: str) -> str:
    """Mete el aviso apenas abre el body, para que sea lo primero que se lee.

    No lo repite si el informe ya lo trae: desde que la plantilla elige el
    cartel segun con que se corrio, el panel suele encontrarlo puesto. Dos
    carteles iguales no enganan a nadie, pero se leen como un error.
    """
    if ETIQUETA_GRATIS in html and ETIQUETA_GRATIS in cartel:
        return html
    inicio = html.find("<body")
    cierre = html.find(">", inicio) if inicio != -1 else -1
    if cierre == -1:
        return cartel + html
    return html[: cierre + 1] + cartel + html[cierre + 1 :]
