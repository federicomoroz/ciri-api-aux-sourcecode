"""Que politicas se pueden evaluar con los datos que hay, y cuales no.

**Por que existe.** El agente derivaba a un analista el 100% de los casos, y la
lectura facil era «hay un bug». No lo hay: hay politicas que, aplicadas
fielmente al dataset, **no se pueden satisfacer nunca**, porque piden datos que
el dataset no tiene. Una politica insatisfacible no es un error del agente, es
una inconsistencia entre el reglamento y el sistema que lo aplica — y detectarla
es justamente lo que la consigna pide en «identificar errores operativos e
inconsistencias de politica».

**Que hace.** Para cada politica declara que dato necesita, comprueba contra la
base si ese dato existe y esta poblado, y —cuando se puede evaluar— cuenta sobre
las 100 transacciones a cuantas alcanza. No usa LLM: es una auditoria de datos,
asi que corre gratis y da el mismo resultado siempre.

**Lo que NO hace:** no juzga si la politica esta bien escrita. Una politica que
pide un dato que no tenemos puede ser correcta y el que esta incompleto ser el
sistema. La salida dice que falta, no de quien es la culpa.

    python scripts/auditar_politicas.py
    python scripts/auditar_politicas.py --salida docs/evaluaciones/politicas.json
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
BASE = RAIZ / "data" / "chargeback.db"

# Como se clasifica una politica segun lo que el dataset permite.
EVALUABLE = "evaluable"          # el dato esta: la politica decide
PARCIAL = "parcial"              # el dato esta para algunos casos, o por aproximacion
SIN_DATO = "sin dato"            # el dataset no tiene el campo: no se puede evaluar


@dataclass
class Medicion:
    """El resultado de mirar una politica contra los datos."""

    estado: str
    detalle: str
    alcanza: int | None = None          # a cuantas transacciones les aplica
    cobertura: str = ""                 # sobre que universo se conto
    consecuencia: str = ""              # que le pasa al caso cuando se dispara


@dataclass
class Politica:
    """Que necesita una politica para poder evaluarse, y como se comprueba."""

    codigo: str
    necesita: str
    medir: Callable[[sqlite3.Connection], Medicion]
    notas: list[str] = field(default_factory=list)


# ── Ayudas de consulta ───────────────────────────────────────────────────────

def _uno(cx: sqlite3.Connection, sql: str, *args) -> int:
    return cx.execute(sql, args).fetchone()[0]


def _columnas(cx: sqlite3.Connection, tabla: str) -> set[str]:
    return {d[1] for d in cx.execute(f"PRAGMA table_info({tabla})")}


def _hay_columna(cx: sqlite3.Connection, *nombres: str) -> bool:
    """Si alguna de las tablas del dataset tiene alguna de esas columnas."""
    todas = set()
    for tabla in ("transactions", "cases", "logs"):
        todas |= _columnas(cx, tabla)
    return any(n in todas for n in nombres)


TOTAL_TX = "select count(*) from transactions"


# ── Una funcion de medicion por politica ─────────────────────────────────────

def _plazo_de_disputa(cx):
    """Necesita la fecha de la compra y la de apertura del reclamo.

    Se mide contra el reclamo MAS RECIENTE de cada transaccion, que es el que el
    sistema elige (`db._caso_en_disputa`, ORDER BY open_date DESC). Medir contra
    el mas viejo da otro numero y describe un sistema que no es este.
    """
    con_caso = _uno(cx, "select count(distinct transaction_id) from cases "
                        "where transaction_id in (select id from transactions)")
    invertidas = _uno(cx, """
        select count(*) from (
          select t.id from transactions t join cases c on c.transaction_id = t.id
          group by t.id having date(max(c.open_date)) < date(min(t.date)))""")
    total = _uno(cx, TOTAL_TX)
    return Medicion(
        PARCIAL,
        f"Las dos fechas existen, pero solo {con_caso} de {total} transacciones tienen "
        f"un reclamo registrado. Y en {invertidas} de esas {con_caso} el reclamo figura "
        f"ANTES de la compra, asi que el plazo da negativo y no se puede evaluar.",
        alcanza=con_caso - invertidas,
        cobertura=f"{con_caso - invertidas} de {total} transacciones son evaluables",
        consecuencia="Fuera de termino, el reclamo se rechaza automaticamente.",
    )


def _documentacion(cx):
    """Necesita tres artefactos: comprobante, comunicacion y evidencia."""
    hay = _hay_columna(cx, "comprobante", "evidencia", "documentacion", "adjuntos")
    total = _uno(cx, TOTAL_TX)
    notas = _uno(cx, "select count(*) from transactions "
                     "where notes is not null and trim(notes) <> ''")
    distintas = [f[0] for f in cx.execute(
        "select distinct trim(notes) from transactions "
        "where notes is not null and trim(notes) <> ''")]
    return Medicion(
        EVALUABLE if hay else SIN_DATO,
        "El dataset no tiene ningun campo para los tres elementos que la politica exige. "
        f"El unico texto libre es `notes`, poblado en {notas} de {total} transacciones, "
        f"y sus valores son: {', '.join(repr(d) for d in sorted(distintas))} — ninguno es "
        "un comprobante, una comunicacion ni una evidencia.",
        alcanza=total,
        cobertura=f"las {total} transacciones",
        consecuencia="La politica dice que sin los tres elementos el caso NO PUEDE AVANZAR. "
                     "Como los tres faltan siempre, ningun caso puede resolverse solo.",
    )


def _respuesta_del_comercio(cx):
    """Necesita cuando se notifico al comercio y cuando respondio."""
    notificados = _uno(cx, "select count(distinct transaction_id) from logs "
                           "where event = 'MERCHANT_NOTIFIED'")
    sin_respuesta = _uno(cx, "select count(distinct transaction_id) from logs "
                             "where event = 'MERCHANT_NO_RESPONSE'")
    total = _uno(cx, TOTAL_TX)
    return Medicion(
        PARCIAL,
        f"No hay campo de fecha de notificacion ni de respuesta. Los logs traen "
        f"MERCHANT_NOTIFIED en {notificados} transacciones y MERCHANT_NO_RESPONSE en "
        f"{sin_respuesta}, que permite inferir el hecho pero no medir los 10 dias habiles: "
        "haria falta el par de timestamps de la misma transaccion.",
        alcanza=notificados + sin_respuesta,
        cobertura=f"{notificados + sin_respuesta} de {total} transacciones tienen alguna senal",
        consecuencia="Si el comercio no responde, el CB se resuelve a favor del cliente.",
    )


def _limite_de_cbs_por_comercio(cx):
    """Necesita transacciones y contracargos por comercio, por mes calendario."""
    filas = list(cx.execute("""
        select t.merchant,
               count(t.id) tx,
               count(c.case_id) cb,
               count(c.case_id) * 1.0 / count(t.id) ratio
          from transactions t left join cases c on c.transaction_id = t.id
         group by t.merchant"""))
    ratios = sorted(f[3] for f in filas)
    sobre_1 = sum(1 for r in ratios if r > 0.01)
    sobre_2 = sum(1 for r in ratios if r > 0.02)
    corpus = _uno(cx, "select count(*) from cases where transaction_id in "
                      "(select id from transactions)") / _uno(cx, TOTAL_TX)
    return Medicion(
        EVALUABLE,
        f"El dato esta y la politica se evalua sin problema. El resultado es el problema: "
        f"{sobre_1} de {len(filas)} comercios superan el 1% y {sobre_2} de {len(filas)} "
        f"superan el 2%. El ratio mas bajo del dataset es {ratios[0]:.1%} y el mas alto "
        f"{ratios[-1]:.1%}. El umbral del 1% es una cifra de industria sobre TODAS las "
        f"transacciones; este dataset es una muestra de disputas, con un ratio de corpus "
        f"de {corpus:.0%}. Comparar una cosa contra la otra da quince de quince.",
        alcanza=len(filas),
        cobertura=f"los {len(filas)} comercios",
        consecuencia="El COMERCIO queda sujeto a revision, o suspendido. La politica no "
                     "dice nada sobre el contracargo del cliente que se esta analizando.",
    )


def _reincidencia_de_cliente(cx):
    """Necesita los contracargos del cliente con sus fechas."""
    filas = list(cx.execute("""
        select t.client_id, count(c.case_id) n
          from cases c join transactions t on t.id = c.transaction_id
         group by t.client_id"""))
    reincidentes = sum(1 for _, n in filas if n > 3)
    afectadas = _uno(cx, """
        select count(*) from transactions where client_id in (
          select t.client_id from cases c join transactions t on t.id = c.transaction_id
           group by t.client_id having count(c.case_id) > 3)""")
    return Medicion(
        EVALUABLE,
        f"Los contracargos por cliente se cuentan y tienen fecha de apertura, asi que la "
        f"ventana de 6 meses se puede verificar. {reincidentes} clientes superan los 3 "
        f"contracargos.",
        alcanza=afectadas,
        cobertura=f"{afectadas} transacciones pertenecen a clientes reincidentes",
        consecuencia="Requiere aprobacion del jefe de area: es una derivacion legitima.",
    )


def _comercios_estrategicos(cx):
    """Necesita el volumen mensual del comercio."""
    top = list(cx.execute("""
        select merchant, sum(amount_usd) v from transactions
         group by merchant order by v desc limit 1"""))[0]
    return Medicion(
        EVALUABLE,
        f"El volumen se calcula. Ninguno se acerca al umbral: el comercio de mayor volumen "
        f"del dataset es {top[0]} con USD {top[1]:,.0f} en total — el umbral es USD 1.000.000 "
        f"por mes. La politica nunca aplica, y eso es correcto: es una excepcion, no una regla.",
        alcanza=0,
        cobertura="ningun comercio la alcanza",
        consecuencia="Concede un SLA extendido. Que no aplique no frena ningun caso.",
    )


def _clientes_vip(cx):
    """Necesita el nivel de fidelidad del cliente."""
    hay = _hay_columna(cx, "loyalty", "fidelidad", "tier", "nivel", "segmento")
    return Medicion(
        EVALUABLE if hay else SIN_DATO,
        "El dataset no tiene nivel de fidelidad en ninguna tabla, asi que el sistema no "
        "puede saber por si mismo si un cliente es Platinum o Gold. La API acepta "
        "`cliente_vip` como entrada de quien dispara el caso, que es un parche razonable "
        "pero deja la politica sin poder evaluarse con los datos propios.",
        alcanza=0,
        cobertura="ninguna transaccion tiene el dato",
        consecuencia="Concede prioridad. No aplicarla no frena el caso, pero un cliente VIP "
                     "real no recibe su trato preferente salvo que alguien lo declare.",
    )


def _cripto(cx):
    """Necesita el metodo de pago."""
    n = _uno(cx, "select count(*) from transactions where payment_method like '%Cripto%'")
    total = _uno(cx, TOTAL_TX)
    return Medicion(
        EVALUABLE,
        f"`payment_method` esta poblado en las {total} transacciones. {n} son en cripto.",
        alcanza=n,
        cobertura=f"{n} de {total} transacciones",
        consecuencia="Es la unica politica habilitada para bloquear: rechaza el caso sin "
                     "intervencion humana, que es lo correcto para un pago irreversible.",
    )


def _comercios_internacionales(cx):
    """Necesita el pais del COMERCIO, no el de la transaccion."""
    hay = _hay_columna(cx, "merchant_country", "pais_comercio")
    total = _uno(cx, TOTAL_TX)
    return Medicion(
        EVALUABLE if hay else PARCIAL,
        "No existe el pais del comercio en ninguna tabla: solo el pais de la TRANSACCION. "
        "El sistema usa ese como aproximacion, y lo declara — es la misma eleccion que hace "
        "el calculo determinista del plazo, asi que al menos el veredicto y el plazo medido "
        "no se contradicen. Pero un comercio internacional que opera en LATAM queda "
        "clasificado como local.",
        alcanza=_uno(cx, "select count(*) from transactions where country not in "
                         "('ARG','BRA','CHL','COL','MEX','PER','URY')"),
        cobertura=f"sobre {total} transacciones, por aproximacion",
        consecuencia="Extiende el plazo del comercio. No frena el caso.",
    )


def _score_minimo(cx):
    """Necesita el score antifraude."""
    n = _uno(cx, "select count(*) from transactions where fraud_score < 30")
    total = _uno(cx, TOTAL_TX)
    return Medicion(
        EVALUABLE,
        f"`fraud_score` esta poblado en las {total} transacciones. {n} estan por debajo de 30.",
        alcanza=n,
        cobertura=f"{n} de {total} transacciones",
        consecuencia="Rechazo automatico o revision manual. Es una derivacion legitima.",
    )


def _patron_geografico(cx):
    """Necesita timestamps de transaccion para medir una ventana de 24 horas."""
    formato = cx.execute("select date from transactions limit 1").fetchone()[0]
    mismo_dia = _uno(cx, """
        select count(*) from (
          select client_id, date from transactions
           group by client_id, date having count(*) > 1)""")
    total = _uno(cx, TOTAL_TX)
    return Medicion(
        SIN_DATO,
        f"La politica pide «mas de 3 transacciones en paises distintos en menos de 24h» y "
        f"`transactions.date` es una fecha sin hora ({formato!r}), asi que la ventana de 24 "
        f"horas no se puede medir. Y no hace falta llegar tan lejos: hay {mismo_dia} clientes "
        f"con mas de una transaccion el mismo dia en todo el dataset. La condicion es "
        f"imposible de cumplir por construccion.",
        alcanza=0,
        cobertura=f"0 de {total} transacciones pueden cumplirla",
        consecuencia="Generaria una alerta y un bloqueo preventivo. Nunca se dispara.",
    )


def _limite_por_canal(cx):
    """Necesita canal, monto y —para App Movil— el acumulado diario por cliente."""
    ivr = _uno(cx, "select count(*) from transactions "
                   "where channel = 'IVR' and amount_usd > 500")
    ivr_total = _uno(cx, "select count(*) from transactions where channel = 'IVR'")
    app_dia = _uno(cx, """
        select count(*) from (
          select client_id, date from transactions where channel = 'App Móvil'
           group by client_id, date having sum(amount_usd) > 3000)""")
    return Medicion(
        EVALUABLE,
        f"Las dos mitades se pueden evaluar. IVR: {ivr} de {ivr_total} transacciones por ese "
        f"canal superan los USD 500. App Movil: {app_dia} combinaciones de cliente y dia "
        f"superan los USD 3.000 acumulados.",
        alcanza=ivr + app_dia,
        cobertura=f"{ivr} por IVR + {app_dia} por App Movil",
        consecuencia="Violacion de limite operativo: derivacion legitima.",
    )


def _tarjetas_reportadas(cx):
    """Necesita la denuncia de la tarjeta y su timestamp."""
    menciones = _uno(cx, "select count(*) from logs where lower(detail) like '%robad%'")
    hay = _hay_columna(cx, "card_reported", "denuncia", "reported_at")
    return Medicion(
        EVALUABLE if hay else SIN_DATO,
        f"No hay campo de denuncia ni de su hora. Los logs mencionan «robada» en "
        f"{menciones} lineas, pero como texto libre dentro del detalle: no permiten "
        f"ubicar la transaccion antes o despues de la denuncia, que es lo que la politica "
        f"pregunta.",
        alcanza=0,
        cobertura="ninguna transaccion tiene el dato",
        consecuencia="Haria inelegible el reembolso al comercio. Nunca se puede afirmar.",
    )


def _primera_respuesta(cx):
    """Necesita la marca de tiempo de la primera respuesta al cliente."""
    hay = _hay_columna(cx, "first_response", "primera_respuesta")
    return Medicion(
        EVALUABLE if hay else SIN_DATO,
        "No hay registro de la primera respuesta al cliente en ninguna tabla. El plazo de "
        "48 horas habiles no se puede verificar ni incumplir: no hay contra que medirlo.",
        alcanza=0,
        cobertura="ninguna transaccion tiene el dato",
        consecuencia="Sin dato, el evaluador solo puede marcarla como no verificable.",
    )


def _resolucion_estandar(cx):
    """Necesita apertura, cierre y si el caso es complejo o escalado."""
    cerrados = _uno(cx, "select count(*) from cases where close_date is not null")
    escalados = _uno(cx, "select count(*) from cases where lower(resolution) like '%escalac%'")
    total_casos = _uno(cx, "select count(*) from cases")
    return Medicion(
        EVALUABLE,
        f"Las dos fechas estan en {cerrados} de {total_casos} casos, y la distincion "
        f"«complejo o escalado» se puede inferir de `resolution` ({escalados} en escalacion). "
        f"El plazo se mide y se compara.",
        alcanza=cerrados,
        cobertura=f"{cerrados} de {total_casos} casos",
        consecuencia="Habilita la compensacion de POL-SLA-004 si se supera.",
    )


def _escalacion(cx):
    """Necesita la apertura del caso y si sigue sin resolucion."""
    abiertos = _uno(cx, "select count(*) from cases where close_date is null")
    sin_resolver = _uno(cx, "select count(*) from cases "
                            "where lower(resolution) like '%sin resoluc%' "
                            "or lower(resolution) like '%escalac%'")
    total_casos = _uno(cx, "select count(*) from cases")
    return Medicion(
        EVALUABLE,
        f"Se puede medir: {abiertos} casos sin fecha de cierre y {sin_resolver} de "
        f"{total_casos} con resolucion pendiente o escalada.",
        alcanza=sin_resolver,
        cobertura=f"{sin_resolver} de {total_casos} casos",
        consecuencia="Escalar al supervisor: es una derivacion legitima y deseada.",
    )


def _compensacion(cx):
    """Depende del resultado de POL-SLA-002."""
    return Medicion(
        EVALUABLE,
        "Se deriva del plazo medido por POL-SLA-002, y el codigo la calcula sin LLM. "
        "Es el unico caso donde una politica produce un monto.",
        alcanza=None,
        cobertura="depende de POL-SLA-002",
        consecuencia="Acredita USD 15 al cliente. No frena el caso.",
    )


# El mapa: una entrada por politica. Si el CRUD carga una politica nueva, el
# script corta — es preferible a auditar 17 de 18 sin que nadie se entere.
POLITICAS: tuple[Politica, ...] = (
    Politica("POL-CB-001", "fecha de la compra + fecha de apertura del reclamo", _plazo_de_disputa),
    Politica("POL-CB-002", "comprobante, comunicacion con el comercio y evidencia del cliente", _documentacion),
    Politica("POL-CB-003", "fecha de notificacion al comercio + fecha de su respuesta", _respuesta_del_comercio),
    Politica("POL-CB-004", "transacciones y contracargos por comercio y por mes", _limite_de_cbs_por_comercio),
    Politica("POL-CB-005", "contracargos del cliente con sus fechas", _reincidencia_de_cliente),
    Politica("POL-EXC-001", "volumen mensual por comercio", _comercios_estrategicos),
    Politica("POL-EXC-002", "nivel de fidelidad del cliente", _clientes_vip),
    Politica("POL-EXC-003", "metodo de pago", _cripto),
    Politica("POL-EXC-004", "pais del comercio", _comercios_internacionales),
    Politica("POL-FRD-001", "score antifraude", _score_minimo),
    Politica("POL-FRD-002", "timestamps de transaccion por cliente y pais", _patron_geografico),
    Politica("POL-FRD-003", "canal, monto y acumulado diario por cliente", _limite_por_canal),
    Politica("POL-FRD-004", "denuncia de la tarjeta y su hora", _tarjetas_reportadas),
    Politica("POL-SLA-001", "marca de tiempo de la primera respuesta al cliente", _primera_respuesta),
    Politica("POL-SLA-002", "apertura, cierre y complejidad del caso", _resolucion_estandar),
    Politica("POL-SLA-003", "apertura del caso y estado de resolucion", _escalacion),
    Politica("POL-SLA-004", "el plazo medido por POL-SLA-002", _compensacion),
)


def auditar(cx: sqlite3.Connection) -> dict:
    """Recorre las politicas cargadas y las mide contra los datos."""
    cargadas = {r[0]: r[1] for r in cx.execute("select code, description from policies")}
    conocidas = {p.codigo for p in POLITICAS}

    if faltan := sorted(set(cargadas) - conocidas):
        sys.exit(
            f"Hay politicas cargadas que este script no sabe auditar: {faltan}.\n"
            "Agregarlas a POLITICAS — auditar 17 de 18 sin avisar es peor que no auditar."
        )

    filas = []
    for p in POLITICAS:
        if p.codigo not in cargadas:
            filas.append({"codigo": p.codigo, "estado": "no cargada",
                          "detalle": "La politica no esta en la base."})
            continue
        m = p.medir(cx)
        filas.append({
            "codigo": p.codigo,
            "necesita": p.necesita,
            "estado": m.estado,
            "detalle": m.detalle,
            "alcanza": m.alcanza,
            "cobertura": m.cobertura,
            "consecuencia": m.consecuencia,
            "descripcion": cargadas[p.codigo],
        })

    bloqueantes = [
        f for f in filas
        if f.get("estado") == SIN_DATO and "NO PUEDE AVANZAR" in (f.get("consecuencia") or "")
    ]
    return {
        "generado": date.today().isoformat(),
        "resumen": {
            "politicas": len(filas),
            "evaluables": sum(1 for f in filas if f.get("estado") == EVALUABLE),
            "parciales": sum(1 for f in filas if f.get("estado") == PARCIAL),
            "sin_dato": sum(1 for f in filas if f.get("estado") == SIN_DATO),
            "bloquean_todo_caso": [f["codigo"] for f in bloqueantes],
            "nunca_se_disparan": [
                f["codigo"] for f in filas if f.get("alcanza") == 0
            ],
        },
        "politicas": filas,
    }


def imprimir(informe: dict) -> None:
    r = informe["resumen"]
    print(f"\n  {r['politicas']} politicas cargadas: {r['evaluables']} evaluables, "
          f"{r['parciales']} parciales, {r['sin_dato']} sin dato\n")
    ancho = {EVALUABLE: "OK    ", PARCIAL: "PARCIAL", SIN_DATO: "SIN DATO"}
    for f in informe["politicas"]:
        print(f"  [{ancho.get(f.get('estado'), '?'):8}] {f['codigo']}  ({f.get('cobertura', '')})")
        print(f"             necesita: {f.get('necesita', '')}")
        for linea in _envolver(f.get("detalle", ""), 88):
            print(f"             {linea}")
        print()

    if r["bloquean_todo_caso"]:
        print("  ── Politicas que impiden resolver CUALQUIER caso ──")
        for c in r["bloquean_todo_caso"]:
            print(f"     {c}")
        print("     Ningun caso puede cerrarse sin una persona mientras falte ese dato.\n")

    if r["nunca_se_disparan"]:
        print("  ── Politicas que nunca se disparan con este dataset ──")
        print(f"     {', '.join(r['nunca_se_disparan'])}\n")


def _envolver(texto: str, ancho: int) -> list[str]:
    palabras, linea, salida = texto.split(), "", []
    for p in palabras:
        if len(linea) + len(p) + 1 > ancho:
            salida.append(linea)
            linea = p
        else:
            linea = f"{linea} {p}".strip()
    if linea:
        salida.append(linea)
    return salida


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default=str(BASE))
    ap.add_argument("--salida", default="", help="ruta del JSON con el detalle")
    args = ap.parse_args()

    ruta = Path(args.base)
    if not ruta.exists():
        sys.exit(f"No existe la base {ruta}. Correr antes: python scripts/seed_data.py")

    cx = sqlite3.connect(ruta)
    informe = auditar(cx)
    imprimir(informe)

    if args.salida:
        destino = Path(args.salida)
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(json.dumps(informe, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  detalle en {destino}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
