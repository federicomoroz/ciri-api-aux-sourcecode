"""Cuanto coincide el agente con los analistas que ya resolvieron estos casos.

**Por que existe.** La unica medida de calidad del sistema era el Juez, que es un
modelo puntuando a otro modelo en cinco criterios subjetivos. Y al lado, sin
usar, habia 60 resoluciones humanas etiquetadas en `cases.resolution`: los mismos
casos que el RAG ya recupera como precedentes. Un numero que sale de comparar
contra decisiones reales vale mas que uno que sale de pedirle una nota a un LLM.

**Que hace.** Toma los casos cuya resolucion humana es comparable con el espacio
de decision del agente, corre el pipeline sobre cada uno y arma la matriz de
confusion. El resultado no es una nota: es en que coincide y en que no, y sobre
que clase de caso se equivoca.

**Lo que NO hace.** No fuerza el mapeo. De las cinco resoluciones que usan los
analistas, dos no tienen equivalente en el vocabulario del agente —«Reembolso
parcial» y «Caso cerrado sin resolucion»— y forzarlas seria fabricar acuerdo o
fabricar error. Esas quedan afuera del porcentaje y se informan aparte, con lo
que el agente dijo de ellas.

    python scripts/medir_acuerdo.py --modo demo            # todos los comparables
    python scripts/medir_acuerdo.py --modo demo --por-clase 4
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from api.app.config import Settings  # noqa: E402
from api.app.domain.enums import ResolutionOutcome  # noqa: E402
from scripts.evaluar import SEMILLA, _contexto, _modo_efectivo, _servicios  # noqa: E402

# ── El mapeo, que es el trabajo analitico y por eso esta a la vista ──────────
#
# A la izquierda, como resuelve un analista en el dataset. A la derecha, la
# accion equivalente del agente. Solo tres de las cinco tienen equivalente:
#
#   «A favor del cliente»  -> el contracargo procede            -> APPROVE
#   «A favor del comercio» -> el contracargo se rechaza         -> REJECT
#   «En escalación»        -> el caso necesita a alguien mas    -> PENDING_HITL
#
# Las otras dos no son comparables, y decirlo es parte del resultado:
#
#   «Reembolso parcial»    el agente no tiene una accion parcial. Mapearla a
#                          APPROVE contaria como acuerdo una decision que el
#                          agente no puede tomar.
#   «Caso cerrado sin resolución»  no es una decision: es la ausencia de una.
#                          No hay contra que comparar.
COMPARABLES: dict[str, str] = {
    "A favor del cliente": ResolutionOutcome.APPROVE,
    "A favor del comercio": ResolutionOutcome.REJECT,
    "En escalación": ResolutionOutcome.PENDING_HITL,
}

SIN_EQUIVALENTE: dict[str, str] = {
    "Reembolso parcial":
        "el agente no tiene una accion de reembolso parcial: su vocabulario es "
        "aprobar, rechazar o derivar",
    "Caso cerrado sin resolución":
        "no es una decision sino la ausencia de una; no hay contra que comparar",
}

MOTIVO_POR_DEFECTO = "No reconoce la compra"


def _casos_etiquetados(db) -> list[dict]:
    """Los casos con su resolucion humana y la transaccion que les corresponde.

    Se toma el caso mas reciente de cada transaccion, que es el que el sistema
    considera en disputa: medir contra otro seria medir un caso que el agente no
    esta mirando.
    """
    filas = db.consulta("""
        SELECT c.case_id, c.transaction_id, c.motivo, c.resolution, c.open_date
          FROM cases c
          JOIN transactions t ON t.id = c.transaction_id
         WHERE c.open_date = (
               SELECT MAX(c2.open_date) FROM cases c2
                WHERE c2.transaction_id = c.transaction_id)
         ORDER BY c.transaction_id
    """) if hasattr(db, "consulta") else None
    if filas is not None:
        return [dict(f) for f in filas]

    # `Database` no expone una consulta libre a proposito. Se usa su conexion.
    with db._conn() as cx:                                       # noqa: SLF001
        return [dict(f) for f in cx.execute("""
            SELECT c.case_id, c.transaction_id, c.motivo, c.resolution, c.open_date
              FROM cases c
              JOIN transactions t ON t.id = c.transaction_id
             WHERE c.open_date = (
                   SELECT MAX(c2.open_date) FROM cases c2
                    WHERE c2.transaction_id = c.transaction_id)
             ORDER BY c.transaction_id
        """)]


def _exigir_que_toda_resolucion_este_clasificada(casos: list[dict]) -> None:
    """Ninguna resolucion humana puede quedar fuera sin que alguien lo decida.

    El riesgo no es teorico: el filtro de comparables y el de excluidas son dos
    listas, asi que una resolucion que no este en ninguna desaparece del informe
    en silencio — ni del porcentaje ni del apartado. El acuerdo seguiria dando un
    numero, calculado sobre menos casos de los que hay, y nada lo delataria.
    """
    vistas = {c["resolution"] for c in casos if c.get("resolution")}
    if huerfanas := sorted(vistas - set(COMPARABLES) - set(SIN_EQUIVALENTE)):
        sys.exit(
            f"Hay resoluciones humanas que este script no sabe clasificar: {huerfanas}.\n"
            "Agregarlas a COMPARABLES (con su accion equivalente) o a SIN_EQUIVALENTE "
            "(con el motivo). Medir sobre un subconjunto sin avisar da un porcentaje "
            "que parece del corpus y no lo es."
        )


def _elegir(casos: list[dict], por_clase: int | None) -> list[dict]:
    """Muestra estratificada: la misma cantidad por cada resolucion humana.

    Estratificar no es un lujo. Una muestra al azar de un corpus con 16 «a favor
    del comercio» y 10 «a favor del cliente» puede dejar una clase sin casos, y
    una matriz de confusion con una fila vacia no dice nada de esa clase.
    """
    comparables = [c for c in casos if c["resolution"] in COMPARABLES]
    if por_clase is None:
        return sorted(comparables, key=lambda c: c["transaction_id"])

    import random

    elegidos: list[dict] = []
    for etiqueta in COMPARABLES:
        de_esa_clase = sorted(
            (c for c in comparables if c["resolution"] == etiqueta),
            key=lambda c: c["transaction_id"],
        )
        rnd = random.Random(SEMILLA)
        rnd.shuffle(de_esa_clase)
        elegidos.extend(de_esa_clase[:por_clase])
    return sorted(elegidos, key=lambda c: c["transaction_id"])


def _matriz(filas: list[dict]) -> dict:
    """La matriz de confusion: que dijo el humano contra que dijo el agente."""
    m: dict[str, Counter] = defaultdict(Counter)
    for f in filas:
        if "accion" in f:
            m[f["resolution"]][f["accion"]] += 1
    return {humana: dict(cuenta) for humana, cuenta in m.items()}


def _acuerdo(filas: list[dict]) -> tuple[int, int]:
    """(coincidencias, comparaciones). Solo sobre los casos comparables."""
    medidos = [f for f in filas if "accion" in f]
    return sum(1 for f in medidos if f["accion"] == COMPARABLES[f["resolution"]]), len(medidos)


def _correr(caso: dict, db, retriever, analyzer, servicio) -> dict:
    """Corre el pipeline sobre un caso y devuelve lo que decidio el agente."""
    tx = db.get_transaction(caso["transaction_id"])
    if not tx:
        return {"error": "la transaccion no existe"}
    motivo = (caso.get("motivo") or MOTIVO_POR_DEFECTO).strip() or MOTIVO_POR_DEFECTO
    ctx = _contexto(db, retriever, analyzer, tx, motivo)
    resolucion = servicio.resolve(ctx)
    return {
        "accion": resolucion.get("recommended_action"),
        "riesgo": resolucion.get("risk_level"),
        "motivo_hitl": resolucion.get("hitl_reason"),
        "codigos_fallidos": sorted({
            v.get("policy_code") for v in resolucion.get("policy_verdicts", [])
            if v.get("verdict") in ("FAIL", "BLOCKER")
        } - {None}),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--modo", choices=("auto", "demo", "produccion"), default="auto")
    ap.add_argument("--por-clase", type=int, default=None,
                    help="cuantos casos por resolucion humana (por defecto, todos)")
    ap.add_argument("--salida", default="docs/evaluaciones/acuerdo_con_analistas.json")
    args = ap.parse_args()

    settings = Settings()
    demo = _modo_efectivo(settings, args.modo)
    db, retriever, analyzer, servicio, modelos = _servicios(settings, demo=demo)

    todos = _casos_etiquetados(db)
    _exigir_que_toda_resolucion_este_clasificada(todos)
    elegidos = _elegir(todos, args.por_clase)
    aparte = [c for c in todos if c["resolution"] in SIN_EQUIVALENTE]

    print(f"\n  Midiendo el acuerdo contra {len(elegidos)} resoluciones humanas"
          f"{' — MODO DEMO (free tier)' if demo else ''}")
    print(f"  Fuera del porcentaje por no tener equivalente: {len(aparte)} casos\n")

    filas: list[dict] = []
    destino = Path(args.salida)
    destino.parent.mkdir(parents=True, exist_ok=True)

    for i, caso in enumerate(elegidos, 1):
        arranque = time.monotonic()
        try:
            salida = _correr(caso, db, retriever, analyzer, servicio)
        except Exception as e:                                    # noqa: BLE001
            salida = {"error": f"{type(e).__name__}: {e}"}
        fila = {**{k: caso[k] for k in ("case_id", "transaction_id", "resolution")},
                **salida, "segundos": round(time.monotonic() - arranque, 1)}
        filas.append(fila)

        if "error" in fila:
            print(f"  [{i:>3}/{len(elegidos)}] {fila['transaction_id']}  ERROR  {fila['error'][:60]}")
        else:
            esperada = COMPARABLES[fila["resolution"]]
            marca = "=" if fila["accion"] == esperada else "≠"
            print(f"  [{i:>3}/{len(elegidos)}] {fila['transaction_id']}  "
                  f"humano={fila['resolution']:22} agente={fila['accion']:14} {marca}")

        # Se escribe en cada vuelta: una corrida que muere contra la cuota del
        # free tier a mitad de camino conserva lo medido hasta ahi.
        destino.write_text(json.dumps(
            _informe(filas, elegidos, aparte, demo, modelos, args),
            ensure_ascii=False, indent=2), encoding="utf-8")

    coincide, comparados = _acuerdo(filas)
    print("\n" + "=" * 58)
    if comparados:
        print(f"  Acuerdo con los analistas: {coincide}/{comparados} "
              f"({coincide / comparados:.0%})\n")
        print("  Matriz de confusion — fila: resolucion humana, columna: accion del agente")
        for humana, cuentas in sorted(_matriz(filas).items()):
            detalle = "  ".join(f"{a}={n}" for a, n in sorted(cuentas.items()))
            print(f"    {humana:24} -> {detalle}")
    else:
        print("  Ningun caso se pudo medir.")
    print(f"\n  {destino}")
    print("  Ese archivo es la evidencia: versionalo y citalo junto al numero.\n")
    return 0


def _informe(filas, elegidos, aparte, demo, modelos, args) -> dict:
    coincide, comparados = _acuerdo(filas)
    return {
        "generado": datetime.now(UTC).isoformat(timespec="seconds"),
        "resumen": {
            "comparables_elegidos": len(elegidos),
            "medidos": comparados,
            "con_error": sum(1 for f in filas if "error" in f),
            "coincidencias": coincide,
            "acuerdo": round(coincide / comparados, 3) if comparados else None,
            "fuera_del_porcentaje": {
                etiqueta: {
                    "casos": sum(1 for c in aparte if c["resolution"] == etiqueta),
                    "por_que": razon,
                }
                for etiqueta, razon in SIN_EQUIVALENTE.items()
            },
            "matriz_de_confusion": _matriz(filas),
            "configuracion": {
                "modo": "demo" if demo else "produccion",
                "es_free_tier": demo,
                "modelos_por_paso": modelos.vigente_para(demo=demo)
                if hasattr(modelos, "vigente_para") else modelos.vigente(),
                "muestreo": (f"{args.por_clase} por clase, semilla {SEMILLA}"
                             if args.por_clase else "todos los comparables"),
            },
        },
        "mapeo": {h: str(a) for h, a in COMPARABLES.items()},
        "casos": filas,
    }


if __name__ == "__main__":
    raise SystemExit(main())
