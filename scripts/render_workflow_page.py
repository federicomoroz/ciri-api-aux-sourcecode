"""Dibuja el circuito del workflow de n8n como una pagina HTML autocontenida.

n8n no exporta el canvas a imagen ni a PDF, y una captura queda atada al viewport.
Este script lee `n8n/workflow_ciri_agent.json` y redibuja el grafo completo: cada
nodo en su capa de ejecucion, cada conexion trazada con angulos rectos, y las
salidas de error apartadas a un carril propio.

El ruteo asigna carriles horizontales por canal para que ningun cable se encime
con otro, y carriles verticales laterales para los saltos que cruzan varias capas.

Al generarse desde el JSON del workflow, no puede quedar desfasado del flujo real.

    python scripts/render_workflow_page.py
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "n8n" / "workflow_ciri_agent.json"
OUT = ROOT / "docs" / "diagrams" / "workflow.html"

STICKY = "n8n-nodes-base.stickyNote"

# ---------------------------------------------------------------------------
# Que es cada nodo. El color comunica con que habla, no de que tipo es en n8n:
# es lo que el que lee necesita saber.
# ---------------------------------------------------------------------------
FAMILY_OF_TYPE = {
    "webhook": "io",
    "formTrigger": "io",
    "respondToWebhook": "io",
    "httpRequest": "api",
    "code": "logic",
    "set": "logic",
    "if": "decide",
    "switch": "decide",
    "merge": "logic",
    "wait": "human",
    "stopAndError": "error",
}

# Excepciones: nodos HTTP que en realidad consultan el vector store o disparan
# un modelo. Es la distincion que importa al leer el circuito.
FAMILY_OVERRIDE = {
    "Buscar Políticas": ("rag", "RAG · Qdrant"),
    "Buscar Casos Similares": ("rag", "RAG · Qdrant"),
    "Sintetizar Resolución": ("llm", "LLM · Haiku+Sonnet"),
    "Juez de Calidad": ("llm", "LLM · Sonnet"),
}

BADGE_OF_FAMILY = {
    "io": "Entrada",
    "api": "API",
    "logic": "Lógica",
    "decide": "Decide",
    "human": "Persona",
    "error": "Corta",
}

FAMILIES = [
    ("api", "Llamada a la API", "Trae datos de SQLite o ejecuta una regla de negocio."),
    ("rag", "Búsqueda semántica", "Consulta el vector store Qdrant."),
    ("llm", "Llamada a un modelo", "Va por la API, que es quien habla con Claude."),
    ("decide", "Bifurca", "Elige por dónde sigue el caso."),
    ("logic", "Lógica del canvas", "Arma, fusiona o transforma datos. Sin reglas de negocio."),
    ("human", "Espera a una persona", "Frena hasta que un analista decide."),
    ("io", "Entrada y salida", "Por dónde entra el caso y por dónde sale el informe."),
    ("error", "Salida de error", "Corta la ejecución y avisa."),
]

# Numeracion de las secciones. Romana para que no se confunda con los numeros
# de paso de cada nodo, que son arabigos.
ROMAN = {"1": "I", "2": "II", "3": "III", "4": "IV"}

SECTIONS = {
    "1": ("Entrada y validación", "Recibe el caso y verifica el formato del identificador antes de gastar un solo token."),
    "2": ("Caché y contexto", "Corta camino si el caso ya se investigó. Si no, la transacción primero y después seis consultas en paralelo."),
    "3": ("Análisis con IA", "Evalúa las políticas, sintetiza la resolución, aplica guardrails y se autoevalúa."),
    "4": ("Ruteo por riesgo", "Clasifica y deriva. Sólo los casos de riesgo alto frenan y esperan a una persona."),
}

SECTION_NOTES = {
    "1": "La URL de la API se resuelve una sola vez, en «Validar Formato TXN», y los doce nodos HTTP la leen de ahí: "
         "body del webhook, variable de n8n, o la API pública por defecto. Por eso el workflow se importa y corre sin configurar nada.",
    "2": "Un acierto de caché devuelve el informe ya generado y saltea todo lo demás: dos segundos en lugar de ciento trece. "
         "Cuando hay que investigar, acá se unen el RAG y la base de datos: Qdrant aporta qué políticas aplican y qué se hizo "
         "en casos parecidos, SQLite aporta los hechos exactos. Ningún umbral de negocio vive en el canvas — los límites de SLA, "
         "el ratio de contracargos y las reglas de reincidencia se le piden a la API, que los lee de <code>domain/constants.py</code>.",
    "3": "El código decide, el LLM explica: seis de los once campos de la resolución los calcula Python y sobrescriben siempre "
         "lo que devuelve el modelo. Después un segundo modelo puntúa esa resolución sobre cinco criterios con rúbricas.",
    "4": "Si el Juez puntuó 8.0 o más, el caso se reindexa como precedente en Qdrant. Cada investigación que sale bien mejora "
         "a la siguiente.",
}

DESCRIPTIONS: dict[str, str] = {
    "Webhook — Entrada": "Recibe el caso por POST. Necesita <code>transaction_id</code> y <code>motivo</code>; acepta <code>cliente_vip</code> y <code>api_base_url</code> como opcionales.",
    "Validar Formato — IF": "Valida el formato del identificador con una expresión regular. Si no matchea, corta acá: ningún caso mal formado llega a gastar tokens.",
    "Validar Formato TXN": "Normaliza los campos de entrada y resuelve la URL de la API una sola vez. Los doce nodos HTTP leen ese valor, así que apuntar todo a otra API es cambiar un solo lugar.",
    "Propagar → Error Handler — TXN": "Corta la ejecución con contexto cuando el identificador no tiene el formato esperado, y dispara el workflow de errores.",
    "Despertar API": "Un <code>GET /health</code> antes de las consultas reales. Absorbe el arranque en frío del free tier de Render para que el primer nodo con datos no falle por timeout.",
    "Verificar Caché": "Caché de idempotencia. Si esta misma transacción ya se investigó, el informe ya existe y no hay nada que recalcular.",
    "¿Cache Hit?": "Bifurca según haya caché. Un acierto saltea el pipeline completo: dos segundos en lugar de ciento trece.",
    "Formatear Caché": "Toma el HTML ya generado y lo manda directo a responder, sin volver a renderizarlo ni a llamar al modelo.",
    "Obtener Transacción": "Verdad estructurada desde SQLite: monto, comercio, país, método de pago y score antifraude. Lookup exacto por identificador.",
    "Obtener Logs": "Todos los eventos de procesamiento de esa transacción, completos. Se traen enteros porque acá la similitud semántica no aporta nada.",
    "Buscar Políticas": "Búsqueda semántica sobre Qdrant. La consulta se arma de forma determinística y se enriquece con reglas según método de pago, score y país.",
    "Buscar Casos Similares": "Búsqueda semántica sobre los sesenta casos históricos: qué se resolvió antes ante un contracargo parecido.",
    "Riesgo del Comercio": "Ratio de contracargos, volumen y señales del comercio. Los umbrales viven en la API, no en este canvas.",
    "Historial del Cliente": "Reincidencia, países usados y métodos de pago. Las señales llegan ya calculadas.",
    "Verificar SLA": "Límite según país y condición VIP: diez días en LATAM, quince fuera, cinco para clientes VIP. La regla vive en la API para que editarla no implique tocar el workflow.",
    "Merge — Contexto Paralelo": "Espera a las seis ramas. Recién cuando llegaron todas se arma el contexto que ve el modelo.",
    "Propagar → Error Handler — API": "Corta si la API no responde después de tres reintentos con espera creciente.",
    "Compilar Contexto": "Fusiona las salidas de las siete herramientas en un solo objeto. Es el punto donde el RAG y la base de datos se vuelven un único contexto.",
    "Sintetizar Resolución": "Dos llamadas encadenadas: Haiku evalúa cada política recuperada, Sonnet sintetiza la resolución. Prompt versionado v3.0.",
    "Verificar Guardrails": "Hace visibles en el canvas los cinco guardrails posteriores al modelo. El caso típico: aprobar con una política bloqueante activa se autocorrige a rechazo.",
    "Juez de Calidad": "Un segundo modelo evalúa la resolución sobre cinco criterios con rúbricas, en escala de uno a diez. Prompt versionado v2.0.",
    "Extraer Evaluación — Juez": "Expone la evaluación del juez para los nodos siguientes.",
    "¿Juez Aprueba? (≥7.0)": "Control de calidad. Por debajo de siete la resolución igual se entrega, pero marcada.",
    "Marcar — Calidad Baja": "Agrega la marca de calidad baja. No descarta la resolución: la entrega señalizada para que una persona la revise.",
    "Preparar Informe": "Arma el payload exacto que espera el generador de informes.",
    "Propagar → Error Handler — Análisis": "Corta si falla alguna de las llamadas al modelo.",
    "Switch — Nivel de Riesgo": "Enruta según el nivel de riesgo calculado. Bloqueante, medio y bajo se resuelven solos; alto es el único que frena.",
    "Wait — Aprobación HITL": "Persona en el circuito. Pausa la ejecución y expone un formulario para que el analista apruebe o rechace.",
    "Procesar Respuesta HITL": "Fusiona la decisión del analista en el payload, para que el informe final refleje lo que decidió una persona.",
    "Registrar Feedback HITL": "Cierra el circuito de mejora: si el juez puntuó ocho o más, el caso se reindexa como precedente en Qdrant. Corre en paralelo con la generación del informe y termina ahí — no forma parte de la respuesta al webhook.",
    "Generar Reporte": "Renderiza el informe con Jinja2: nueve secciones y formulario de aprobación condicional.",
    "Responder — Reporte": "Devuelve el HTML a quien disparó el webhook. Sirve igual a un navegador, a otro workflow o a un bot.",
}

ERROR_LABEL = {
    "Propagar → Error Handler — TXN": "formato inválido",
    "Propagar → Error Handler — API": "la API no responde",
    "Propagar → Error Handler — Análisis": "falla el modelo",
}

BRANCH_LABEL = {
    ("Validar Formato — IF", 0): "formato válido",
    ("Validar Formato — IF", 1): "formato inválido",
    ("¿Cache Hit?", 0): "hay caché",
    ("¿Cache Hit?", 1): "hay que investigar",
    ("¿Juez Aprueba? (≥7.0)", 0): "score ≥ 7.0",
    ("¿Juez Aprueba? (≥7.0)", 1): "score < 7.0",
    ("Verificar Caché", 1): "si falla",
    ("Obtener Transacción", 1): "si falla",
    ("Sintetizar Resolución", 1): "si falla",
    ("Juez de Calidad", 1): "si falla",
}

# --- geometria -------------------------------------------------------------
CARD_W, CARD_H = 152, 96
COL_GAP = 16
CHANNEL = 56                      # espacio entre filas, donde corren los cables
ROW_PITCH = CARD_H + CHANNEL
TRACKS = [14, 26, 38, 50]         # carriles horizontales dentro de cada canal
MAX_COLS = 6
GRID_W = MAX_COLS * CARD_W + (MAX_COLS - 1) * COL_GAP
LABEL_W = 208                     # columna izquierda: los titulos de seccion
SIDE_LANE = 80                    # espacio para los carriles verticales
GRID_X0 = LABEL_W + SIDE_LANE
PAD_TOP, SEC_GAP = 34, 58
ERR_W = 150
ERR_X = GRID_X0 + GRID_W + 46
CANVAS_W = ERR_X + ERR_W + 24
CENTER = GRID_X0 + GRID_W / 2
L_GUTTERS = [GRID_X0 - 38, GRID_X0 - 70]
R_GUTTERS = [GRID_X0 + GRID_W + 38, GRID_X0 + GRID_W + 70]


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def short_type(t: str) -> str:
    return t.rsplit(".", 1)[-1]


def family_of(name: str, node: dict) -> tuple[str, str]:
    if name in FAMILY_OVERRIDE:
        return FAMILY_OVERRIDE[name]
    fam = FAMILY_OF_TYPE.get(short_type(node["type"]), "logic")
    if short_type(node["type"]) == "respondToWebhook":
        return fam, "Salida"
    return fam, BADGE_OF_FAMILY[fam]


def endpoint_of(node: dict) -> str:
    url = node["parameters"].get("url")
    if not url:
        if node["type"].endswith("webhook"):
            path = node["parameters"].get("path", "")
            return f"POST /webhook/{path}" if path else ""
        return ""
    path = re.sub(r"\{\{[^}]*\}\}", "…", url.lstrip("=")).strip().split("?")[0]
    path = re.sub(r"…+", "…", path)
    path = path[1:] if path.startswith("…") else path
    path = re.sub(r"/{2,}", "/", path).rstrip("/") or "/"
    method = node["parameters"].get("method", "GET")
    return f"{method} {path}" if path.startswith("/") else ""


def analyse(workflow: dict) -> dict:
    execs = [n for n in workflow["nodes"] if n["type"] != STICKY]
    names = {n["name"] for n in execs}

    bands = []
    for s in workflow["nodes"]:
        if s["type"] != STICKY:
            continue
        content = s["parameters"].get("content", "")
        first = content.strip().splitlines()[0] if content.strip() else ""
        m = re.search(r"§\s*(\d)", first)
        if m:
            x, y = s["position"]
            bands.append((m.group(1), x, y, x + s["parameters"]["width"], y + s["parameters"]["height"]))

    section = {}
    for n in execs:
        x, y = n["position"]
        section[n["name"]] = next(
            (sec for sec, x1, y1, x2, y2 in bands if x1 <= x <= x2 and y1 <= y <= y2), "1"
        )

    wired = [
        (src, out_i, link["node"])
        for src, conn in workflow.get("connections", {}).items()
        for out_i, outs in enumerate(conn.get("main", []))
        for link in outs or []
        if src in names and link["node"] in names
    ]

    depth = {n["name"]: 0 for n in execs}
    for _ in range(len(execs)):
        changed = False
        for src, _i, dst in wired:
            if depth[dst] < depth[src] + 1:
                depth[dst] = depth[src] + 1
                changed = True
        if not changed:
            break

    switch_keys = {}
    for n in execs:
        for i, rule in enumerate(n["parameters"].get("rules", {}).get("values", [])):
            if rule.get("outputKey"):
                switch_keys[(n["name"], i)] = rule["outputKey"]

    feeds, fed_by = defaultdict(list), defaultdict(list)
    for src, _i, dst in wired:
        feeds[src].append(dst)
        fed_by[dst].append(src)

    return {
        "execs": {n["name"]: n for n in execs},
        "section": section,
        "depth": depth,
        "wired": wired,
        "switch_keys": switch_keys,
        "feeds": feeds,
        "fed_by": fed_by,
    }


def place(a: dict) -> dict:
    """El recorrido principal baja por el centro; las salidas de error se apartan
    al carril derecho, porque son excepciones y no parte del camino."""
    execs, depth, section = a["execs"], a["depth"], a["section"]
    is_err = {n: short_type(execs[n]["type"]) == "stopAndError" for n in execs}

    layers = defaultdict(list)
    for n in execs:
        if not is_err[n]:
            layers[depth[n]].append(n)

    sec_shift, seen, zones = {}, set(), []
    shift = 0
    for d in sorted(layers):
        sec = section[layers[d][0]]
        if sec not in seen:
            seen.add(sec)
            zones.append((sec, PAD_TOP + d * ROW_PITCH + shift))
            shift += SEC_GAP
        sec_shift[d] = shift

    order: dict[str, float] = {}
    pos: dict[str, tuple[float, float]] = {}
    for d in sorted(layers):
        members = layers[d]
        known = lambda n: [p for p in a["fed_by"].get(n, []) if p in order]
        members.sort(key=lambda n: (
            sum(order[p] for p in known(n)) / len(known(n)) if known(n) else CENTER,
            n,
        ))
        span = len(members) * CARD_W + (len(members) - 1) * COL_GAP
        x = CENTER - span / 2
        for n in members:
            pos[n] = (x, PAD_TOP + d * ROW_PITCH + sec_shift[d])
            order[n] = x + CARD_W / 2
            x += CARD_W + COL_GAP

    err_pos, used = {}, []
    for n in sorted(execs, key=lambda n: depth[n]):
        if not is_err[n]:
            continue
        parents = [p for p in a["fed_by"].get(n, []) if p in pos]
        y = max((pos[p][1] for p in parents), default=PAD_TOP) + 16
        while any(abs(y - u) < 56 for u in used):
            y += 56
        used.append(y)
        err_pos[n] = (ERR_X, y)

    height = max([y for _, y in pos.values()] + [y for _, y in err_pos.values()]) + CARD_H + 56
    return {"pos": pos, "err": err_pos, "zones": zones, "height": height, "is_err": is_err}


def route(a: dict, lay: dict) -> str:
    """Traza cada conexion con angulos rectos.

    Los tramos horizontales de un mismo canal se reparten en carriles distintos
    salvo que compartan origen o destino: ahi conviene que se vean como un bus.
    Los saltos que cruzan varias capas bajan por un carril vertical lateral
    propio, para no pisar ni a los nodos ni a los otros cables.
    """
    pos, err, is_err = lay["pos"], lay["err"], lay["is_err"]
    depth = a["depth"]

    merged: dict[tuple[str, str], list[str]] = {}
    for src, out_i, dst in a["wired"]:
        label = BRANCH_LABEL.get((src, out_i)) or a["switch_keys"].get((src, out_i), "")
        merged.setdefault((src, dst), [])
        if label and label not in merged[(src, dst)]:
            merged[(src, dst)].append(label)

    normal = [(s, d, l) for (s, d), l in merged.items() if not is_err[d]]
    errors = [(s, d, l) for (s, d), l in merged.items() if is_err[d]]

    # Agrupar por canal y por bus (mismo origen o mismo destino).
    channels: dict[int, list[dict]] = defaultdict(list)
    for src, dst, labels in normal:
        gap = depth[dst] - depth[src]
        sx = pos[src][0] + CARD_W / 2
        dx = pos[dst][0] + CARD_W / 2
        if gap <= 1:
            channels[depth[src]].append(
                {"kind": "direct", "src": src, "dst": dst, "labels": labels, "x1": sx, "x2": dx}
            )
        else:
            side_left = (sx + dx) / 2 < CENTER
            channels[depth[src]].append(
                {"kind": "exit", "src": src, "dst": dst, "labels": labels, "x1": sx, "x2": None, "left": side_left}
            )
            channels[depth[dst] - 1].append(
                {"kind": "enter", "src": src, "dst": dst, "labels": [], "x1": None, "x2": dx, "left": side_left}
            )

    # Carril vertical propio para cada salto largo.
    long_edges = sorted({(s, d) for s, d, _ in normal if depth[d] - depth[s] > 1})
    gutter: dict[tuple[str, str], float] = {}
    li = ri = 0
    for src, dst in long_edges:
        sx = pos[src][0] + CARD_W / 2
        dx = pos[dst][0] + CARD_W / 2
        if (sx + dx) / 2 < CENTER:
            gutter[(src, dst)] = L_GUTTERS[min(li, len(L_GUTTERS) - 1)]
            li += 1
        else:
            gutter[(src, dst)] = R_GUTTERS[min(ri, len(R_GUTTERS) - 1)]
            ri += 1

    for segs in channels.values():
        for s in segs:
            if s["kind"] == "exit":
                s["x2"] = gutter[(s["src"], s["dst"])]
            elif s["kind"] == "enter":
                s["x1"] = gutter[(s["src"], s["dst"])]

    # Reparto de carriles: dos tramos comparten carril si comparten extremo
    # (se leen como un bus) o si sus rangos horizontales no se tocan.
    track_of: dict[int, dict[int, int]] = defaultdict(dict)
    for d, segs in channels.items():
        lanes: list[list[dict]] = []
        for seg in sorted(segs, key=lambda s: min(s["x1"], s["x2"])):
            lo, hi = sorted((seg["x1"], seg["x2"]))
            for i, lane in enumerate(lanes):
                if all(
                    o["src"] == seg["src"]
                    or o["dst"] == seg["dst"]
                    or hi < min(o["x1"], o["x2"]) - 16
                    or lo > max(o["x1"], o["x2"]) + 16
                    for o in lane
                ):
                    lane.append(seg)
                    track_of[d][id(seg)] = i
                    break
            else:
                lanes.append([seg])
                track_of[d][id(seg)] = len(lanes) - 1

    def track_y(d: int, seg: dict) -> float:
        base = pos_of_layer_bottom(d)
        return base + TRACKS[min(track_of[d][id(seg)], len(TRACKS) - 1)]

    layer_bottom: dict[int, float] = {}
    for n, (_x, y) in pos.items():
        layer_bottom[depth[n]] = y + CARD_H

    def pos_of_layer_bottom(d: int) -> float:
        return layer_bottom[d]

    out: list[str] = []
    seg_by_edge: dict[tuple[str, str], dict[str, dict]] = defaultdict(dict)
    for d, segs in channels.items():
        for seg in segs:
            seg["y"] = track_y(d, seg)
            seg_by_edge[(seg["src"], seg["dst"])][seg["kind"]] = seg

    for (src, dst), parts in seg_by_edge.items():
        sx = pos[src][0] + CARD_W / 2
        sy = pos[src][1] + CARD_H
        dx = pos[dst][0] + CARD_W / 2
        dy = pos[dst][1]
        if "direct" in parts:
            seg = parts["direct"]
            y = seg["y"]
            d_attr = f"M {sx:.0f} {sy:.0f} V {y:.0f} H {dx:.0f} V {dy:.0f}"
            labels, anchor = seg["labels"], "start"
            lx, ly = dx + 9, (y + dy) / 2 + 4
        else:
            ex, en = parts["exit"], parts["enter"]
            g = ex["x2"]
            d_attr = (
                f"M {sx:.0f} {sy:.0f} V {ex['y']:.0f} H {g:.0f} "
                f"V {en['y']:.0f} H {dx:.0f} V {dy:.0f}"
            )
            labels = ex["labels"]
            lx, ly, anchor = (sx + g) / 2, ex["y"] - 8, "middle"
        out.append(
            f'<path class="w" data-from="{esc(src)}" data-to="{esc(dst)}" d="{d_attr}" '
            f'marker-end="url(#tip)"/>'
        )
        if labels:
            out.append(
                f'<text class="wl" x="{lx:.0f}" y="{ly:.0f}" text-anchor="{anchor}">'
                f"{esc(' · '.join(labels))}</text>"
            )

    for src, dst, _labels in errors:
        sx, sy = pos[src][0] + CARD_W, pos[src][1] + CARD_H / 2
        ex, ey = err[dst][0], err[dst][1] + 18
        mid = (sx + ex) / 2
        d_attr = f"M {sx:.0f} {sy:.0f} H {mid:.0f} V {ey:.0f} H {ex:.0f}"
        out.append(
            f'<path class="w err" data-from="{esc(src)}" data-to="{esc(dst)}" d="{d_attr}" '
            f'marker-end="url(#tip-err)"/>'
        )

    return "".join(out)


def build(workflow: dict) -> str:
    a = analyse(workflow)
    lay = place(a)
    execs, pos, err, depth = a["execs"], lay["pos"], lay["err"], a["depth"]

    flow = sorted(pos, key=lambda n: (depth[n], pos[n][0]))
    step = {n: i + 1 for i, n in enumerate(flow)}

    cards: list[str] = []
    for name in flow:
        node = execs[name]
        fam, badge = family_of(name, node)
        x, y = pos[name]
        ep = endpoint_of(node)
        short_ep = ep.replace(" /api/", " /") if ep else ""
        cards.append(
            f'<button class="node f-{fam}" data-node="{esc(name)}" type="button" '
            f'style="left:{x:.0f}px;top:{y:.0f}px">'
            f'<span class="n-top"><span class="step">{step[name]:02d}</span>'
            f'<span class="badge">{esc(badge)}</span></span>'
            f'<span class="n-name">{esc(name)}</span>'
            f'{f"<code class=\'ep\'>{esc(short_ep)}</code>" if ep else ""}'
            "</button>"
        )
    terminal = [
        n for n in flow
        if not a["feeds"].get(n) and family_of(n, execs[n])[0] != "io"
    ]
    for name in terminal:
        x, y = pos[name]
        cards.append(
            f'<span class="tail" style="left:{x:.0f}px;top:{y + CARD_H + 8:.0f}px">'
            "termina acá</span>"
        )

    for name, (x, y) in err.items():
        cards.append(
            f'<button class="chip" data-node="{esc(name)}" type="button" '
            f'style="left:{x:.0f}px;top:{y:.0f}px">{esc(ERROR_LABEL.get(name, name))}</button>'
        )

    zones = []
    for sec, y in lay["zones"]:
        title, subtitle = SECTIONS[sec]
        zones.append(
            f'<div class="zone" style="top:{y:.0f}px">'
            f'<span class="z-n">{ROMAN.get(sec, sec)}</span>'
            f'<span class="z-t">{esc(title)}</span>'
            f'<span class="z-s">{esc(subtitle)}</span></div>'
        )

    notes = []
    for sec, y in lay["zones"]:
        notes.append((sec, y))

    legend = "".join(
        f'<span class="lg f-{fam}"><i></i><b>{esc(title)}</b><em>{esc(desc)}</em></span>'
        for fam, title, desc in FAMILIES
    )

    detail = {
        name: {
            "step": f"{step[name]:02d}" if name in step else "—",
            "badge": family_of(name, execs[name])[1],
            "family": family_of(name, execs[name])[0],
            "endpoint": endpoint_of(execs[name]),
            "desc": DESCRIPTIONS.get(name, ""),
            "from": a["fed_by"].get(name, []),
            "to": a["feeds"].get(name, []),
        }
        for name in execs
    }

    section_blocks = "".join(
        f'<section class="note"><span class="n-sec">{ROMAN.get(sec, sec)}</span>'
        f"<p>{SECTION_NOTES[sec]}</p></section>"
        for sec in SECTIONS
    )

    return (
        TEMPLATE.replace("__WIRES__", route(a, lay))
        .replace("__CARDS__", "".join(cards))
        .replace("__ZONES__", "".join(zones))
        .replace("__LEGEND__", legend)
        .replace("__NOTES__", section_blocks)
        .replace("__W__", str(int(CANVAS_W)))
        .replace("__H__", str(int(lay["height"])))
        .replace("__COUNT__", str(len(flow)))
        .replace("__EXTRA__", str(len(err)))
        .replace("__DATA__", json.dumps(detail, ensure_ascii=False))
    )


TEMPLATE = r"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CIRI · Agente de contracargos — el circuito</title>
<style>
:root{
  --paper:#fcfcfa; --surface:#fff; --ink:#17171b; --muted:#68686f; --faint:#95959d;
  --rule:#e5e4df; --rule-mid:#d3d2cb; --wire:#b9b8b0; --shadow:0 1px 2px rgba(20,20,25,.06);
  --api:#2563eb; --rag:#0d9488; --llm:#7c3aed; --decide:#b45309;
  --human:#be185d; --logic:#64748b; --io:#0f766e; --error:#dc2626;
  --tint:12%;
}
@media (prefers-color-scheme:dark){
  :root:not([data-theme=light]){
    --paper:#0d0f13; --surface:#161a21; --ink:#e9eaee; --muted:#9a9ca6; --faint:#71737d;
    --rule:#232732; --rule-mid:#333846; --wire:#454b5a; --shadow:none;
    --api:#7ea8f8; --rag:#4fd1c5; --llm:#b39cf7; --decide:#f0b355;
    --human:#f491bb; --logic:#9aa6b8; --io:#5eead4; --error:#f08a80;
    --tint:16%;
  }
}
:root[data-theme=dark]{
  --paper:#0d0f13; --surface:#161a21; --ink:#e9eaee; --muted:#9a9ca6; --faint:#71737d;
  --rule:#232732; --rule-mid:#333846; --wire:#454b5a; --shadow:none;
  --api:#7ea8f8; --rag:#4fd1c5; --llm:#b39cf7; --decide:#f0b355;
  --human:#f491bb; --logic:#9aa6b8; --io:#5eead4; --error:#f08a80;
  --tint:16%;
}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);
  font:400 16px/1.6 Inter,"Segoe UI",system-ui,-apple-system,sans-serif;-webkit-font-smoothing:antialiased}
.mono,.step,.badge,.ep,.eyebrow,h1,.z-n,.n-sec,.chip,.wl{
  font-family:ui-monospace,"Cascadia Code","SF Mono",Menlo,Consolas,monospace}
.wrap{max-width:1180px;margin:0 auto;padding:0 40px}

.top{padding:60px 0 0}
.eyebrow{font-size:11px;letter-spacing:.2em;text-transform:uppercase;color:var(--faint);
  display:flex;justify-content:space-between;align-items:center;gap:24px}
h1{font-size:clamp(30px,4.2vw,44px);line-height:1.06;font-weight:600;letter-spacing:-.02em;margin:22px 0 0}
h1 em{font-style:normal;color:var(--muted)}
.lead{max-width:66ch;color:var(--muted);margin:18px 0 0;font-size:17px}
.theme{font:inherit;font-size:11px;letter-spacing:.14em;text-transform:uppercase;background:none;
  border:1px solid var(--rule-mid);color:var(--faint);padding:6px 12px;border-radius:99px;cursor:pointer}
.theme:hover{color:var(--ink);border-color:var(--ink)}

.legend{display:grid;grid-template-columns:repeat(auto-fit,minmax(232px,1fr));gap:14px 26px;
  margin:38px 0 0;padding:26px 0 30px;border-top:1px solid var(--rule);border-bottom:1px solid var(--rule)}
.lg{display:grid;grid-template-columns:auto 1fr;gap:3px 11px;align-items:baseline}
.lg i{width:11px;height:11px;border-radius:3px;grid-row:1/3;align-self:center}
.lg b{font-size:14.5px;font-weight:600}
.lg em{font-style:normal;font-size:13px;color:var(--faint);line-height:1.4}
.lg.f-api i{background:var(--api)} .lg.f-rag i{background:var(--rag)}
.lg.f-llm i{background:var(--llm)} .lg.f-decide i{background:var(--decide)}
.lg.f-human i{background:var(--human)} .lg.f-logic i{background:var(--logic)}
.lg.f-io i{background:var(--io)} .lg.f-error i{background:var(--error)}

.fine{margin:18px 0 0;font-size:13px;color:var(--faint)}
.fine code{font-family:ui-monospace,monospace}
.figure{margin:34px 0 0;overflow-x:auto;padding-bottom:18px}
.canvas{position:relative;width:__W__px;height:__H__px;margin:0 auto}
.wires{position:absolute;inset:0;width:100%;height:100%;overflow:visible}
.w{fill:none;stroke:var(--wire);stroke-width:1.6;stroke-linejoin:round}
.w.err{stroke:var(--error);stroke-dasharray:5 4;opacity:.75}
.wl{font-size:11px;fill:var(--faint);letter-spacing:.02em}
.canvas.lit .w{opacity:.22}
.canvas.lit .w.on{opacity:1;stroke:var(--ink);stroke-width:2.2}
.canvas.lit .node{opacity:.42}
.canvas.lit .node.on,.canvas.lit .node.near{opacity:1}

.zone{position:absolute;left:24px;width:176px;display:grid;
  grid-template-columns:auto 1fr;gap:2px 10px;align-items:baseline}
.z-n{font-size:12px;color:var(--faint);letter-spacing:.1em;min-width:24px}
.z-t{font-size:17.5px;font-weight:600;letter-spacing:-.01em;line-height:1.2}
.z-s{font-size:12.8px;color:var(--faint);grid-column:2;line-height:1.45;margin-top:6px}

.node{position:absolute;width:152px;height:96px;display:flex;flex-direction:column;gap:5px;
  align-items:flex-start;text-align:left;background:var(--surface);border:1px solid var(--rule);
  border-left:3px solid var(--logic);border-radius:9px;padding:9px 12px;cursor:pointer;
  color:inherit;font:inherit;box-shadow:var(--shadow);transition:box-shadow .13s,border-color .13s}
.node:hover,.node:focus-visible{outline:none;box-shadow:0 4px 14px rgba(20,20,25,.11)}
.n-top{display:flex;align-items:center;gap:8px}
.step{font-size:10.5px;color:var(--faint)}
.badge{font-size:9px;letter-spacing:.05em;white-space:nowrap;text-transform:uppercase;font-weight:700}
.n-name{font-size:12.8px;font-weight:600;line-height:1.24;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.ep{font-size:10px;color:var(--muted);margin-top:auto;white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis;max-width:100%}
.f-api{border-left-color:var(--api)} .f-api .badge{color:var(--api)}
.f-rag{border-left-color:var(--rag)} .f-rag .badge{color:var(--rag)}
.f-llm{border-left-color:var(--llm)} .f-llm .badge{color:var(--llm)}
.f-decide{border-left-color:var(--decide)} .f-decide .badge{color:var(--decide)}
.f-human{border-left-color:var(--human)} .f-human .badge{color:var(--human)}
.f-logic{border-left-color:var(--logic)} .f-logic .badge{color:var(--logic)}
.f-io{border-left-color:var(--io)} .f-io .badge{color:var(--io)}
.node.on{border-color:var(--ink)}

.tail{position:absolute;width:152px;text-align:center;font-size:10.5px;color:var(--faint);font-family:ui-monospace,"Cascadia Code","SF Mono",Menlo,Consolas,monospace}
.chip{position:absolute;width:150px;font:inherit;font-size:11.5px;line-height:1.3;
  background:var(--surface);color:var(--error);cursor:pointer;text-align:left;
  border:1px dashed var(--error);border-radius:8px;padding:8px 10px}
.chip:hover{border-style:solid}

.notes{margin-bottom:72px;display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:30px 40px;
  margin:56px 0 0;padding-top:34px;border-top:1px solid var(--rule)}
.note{display:grid;grid-template-columns:auto 1fr;gap:14px}
.n-sec{font-size:12px;color:var(--faint);letter-spacing:.1em}
.note p{margin:0;color:var(--muted);font-size:15px}
.note code{font-family:ui-monospace,monospace;font-size:13px;color:var(--ink)}

.panel{position:fixed;top:0;right:0;height:100%;width:400px;max-width:92vw;z-index:40;
  background:var(--surface);border-left:1px solid var(--rule);padding:34px 32px;overflow-y:auto;
  transform:translateX(101%);transition:transform .2s cubic-bezier(.4,0,.2,1)}
.panel.open{transform:none;box-shadow:-24px 0 60px rgba(10,10,14,.15)}
.p-close{position:absolute;top:20px;right:22px;background:none;border:0;cursor:pointer;
  color:var(--faint);font-size:22px;line-height:1;padding:4px}
.p-close:hover{color:var(--ink)}
.p-top{display:flex;align-items:center;gap:9px;margin-bottom:12px}
.p-name{font-size:21px;font-weight:600;line-height:1.22;margin:0 0 15px}
.p-ep{display:block;font-size:12.5px;padding:9px 12px;border-radius:8px;background:var(--paper);
  border:1px solid var(--rule);color:var(--ink);margin-bottom:19px;font-family:ui-monospace,monospace;
  word-break:break-all}
.p-desc{color:var(--muted);margin:0 0 24px}
.p-desc code{font-family:ui-monospace,monospace;font-size:13px;color:var(--ink)}
.d-k{font-size:10px;letter-spacing:.16em;text-transform:uppercase;color:var(--faint);
  display:block;margin:0 0 8px;font-family:ui-monospace,monospace}
.d-list{list-style:none;margin:0 0 20px;padding:0;display:flex;flex-direction:column;gap:6px}
.d-list button{font:inherit;font-size:14px;text-align:left;background:none;border:0;padding:0;
  color:var(--ink);cursor:pointer;border-bottom:1px solid transparent}
.d-list button:hover{border-bottom-color:var(--ink)}
.d-list .none{color:var(--faint);font-size:14px}
.scrim{position:fixed;inset:0;background:rgba(10,10,14,.26);opacity:0;pointer-events:none;
  transition:opacity .2s;z-index:30}
.scrim.on{opacity:1;pointer-events:auto}

@media (prefers-reduced-motion:reduce){*{transition:none!important}}
@media print{.panel,.scrim,.theme{display:none}body{background:#fff}}
</style>
</head>
<body>
<div class="wrap">
<header class="top">
  <div class="eyebrow">
    <span>CIRI · Continuous Improvement &amp; Risk Intelligence</span>
    <button class="theme" id="theme" type="button">Tema</button>
  </div>
  <h1>Agente de contracargos<br><em>el circuito completo</em></h1>
  <p class="lead">
    Orquestación explícita en n8n. __COUNT__ nodos que se ejecutan siempre en el mismo orden, más
    __EXTRA__ salidas de error. No hay un nodo de agente decidiendo qué herramienta llamar: por eso
    cada investigación es reproducible y auditable. Tocá cualquier nodo para ver qué hace y cómo se
    conecta.
  </p>
  <div class="legend">__LEGEND__</div>
  <p class="fine">Las rutas se muestran sin el prefijo <code>/api</code>, que comparten todas. El detalle completo de cada nodo está a un clic.</p>
</header>
</div>

<div class="figure">
  <div class="canvas" id="canvas">
    <svg class="wires" viewBox="0 0 __W__ __H__" preserveAspectRatio="none" aria-hidden="true">
      <defs>
        <marker id="tip" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto">
          <path d="M0 0 L8 4 L0 8 z" fill="var(--wire)"/>
        </marker>
        <marker id="tip-err" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto">
          <path d="M0 0 L8 4 L0 8 z" fill="var(--error)"/>
        </marker>
      </defs>
      __WIRES__
    </svg>
    __ZONES__
    __CARDS__
  </div>
</div>

<div class="wrap">
  <div class="notes">__NOTES__</div>
</div>

<div class="scrim" id="scrim"></div>
<aside class="panel" id="panel" aria-live="polite">
  <button class="p-close" id="close" type="button" aria-label="Cerrar">×</button>
  <div class="p-top"><span class="step" id="p-step"></span><span class="badge" id="p-badge"></span></div>
  <h2 class="p-name" id="p-name"></h2>
  <code class="p-ep" id="p-ep"></code>
  <p class="p-desc" id="p-desc"></p>
  <span class="d-k">Recibe de</span><ul class="d-list" id="p-from"></ul>
  <span class="d-k">Continúa en</span><ul class="d-list" id="p-to"></ul>
</aside>

<script>
const DATA = __DATA__;
const $ = (s) => document.querySelector(s);
const panel = $("#panel"), scrim = $("#scrim"), canvas = $("#canvas");

const root = document.documentElement;
const stored = localStorage.getItem("ciri-theme");
if (stored) root.dataset.theme = stored;
$("#theme").addEventListener("click", () => {
  const dark = matchMedia("(prefers-color-scheme: dark)").matches;
  const now = root.dataset.theme || (dark ? "dark" : "light");
  root.dataset.theme = now === "dark" ? "light" : "dark";
  localStorage.setItem("ciri-theme", root.dataset.theme);
});

function highlight(name) {
  canvas.querySelectorAll(".w, .node").forEach((el) => el.classList.remove("on", "near"));
  if (!name) { canvas.classList.remove("lit"); return; }
  canvas.classList.add("lit");
  canvas.querySelectorAll(`[data-node="${CSS.escape(name)}"]`).forEach((el) => el.classList.add("on"));
  canvas.querySelectorAll(`.w[data-from="${CSS.escape(name)}"], .w[data-to="${CSS.escape(name)}"]`)
    .forEach((w) => {
      w.classList.add("on");
      [w.dataset.from, w.dataset.to].forEach((n) => {
        const el = canvas.querySelector(`.node[data-node="${CSS.escape(n)}"], .chip[data-node="${CSS.escape(n)}"]`);
        if (el) el.classList.add("near");
      });
    });
}

const link = (names) => names.length
  ? names.map((n) => `<li><button data-jump="${n}">${n}</button></li>`).join("")
  : '<li class="none">—</li>';

function open(name) {
  const d = DATA[name];
  if (!d) return;
  $("#p-step").textContent = d.step;
  $("#p-badge").textContent = d.badge;
  $("#p-badge").className = "badge f-" + d.family;
  $("#p-badge").style.color = `var(--${d.family})`;
  $("#p-name").textContent = name;
  $("#p-ep").textContent = d.endpoint || "";
  $("#p-ep").style.display = d.endpoint ? "block" : "none";
  $("#p-desc").innerHTML = d.desc;
  $("#p-from").innerHTML = link(d.from);
  $("#p-to").innerHTML = link(d.to);
  panel.classList.add("open");
  scrim.classList.add("on");
  highlight(name);
}

function close() {
  panel.classList.remove("open");
  scrim.classList.remove("on");
  highlight(null);
}

document.addEventListener("click", (e) => {
  const node = e.target.closest("[data-node]");
  if (node) return open(node.dataset.node);
  const jump = e.target.closest("[data-jump]");
  if (jump) {
    open(jump.dataset.jump);
    const el = canvas.querySelector(`[data-node="${CSS.escape(jump.dataset.jump)}"]`);
    if (el) el.scrollIntoView({ block: "center", behavior: "smooth" });
  }
});

canvas.addEventListener("mouseover", (e) => {
  const el = e.target.closest("[data-node]");
  if (el && !panel.classList.contains("open")) highlight(el.dataset.node);
});
canvas.addEventListener("mouseleave", () => {
  if (!panel.classList.contains("open")) highlight(null);
});

$("#close").addEventListener("click", close);
scrim.addEventListener("click", close);
addEventListener("keydown", (e) => e.key === "Escape" && close());
</script>
</body>
</html>
"""


def main() -> int:
    workflow = json.loads(SRC.read_text(encoding="utf-8"))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(build(workflow), encoding="utf-8")
    print(f"{OUT.relative_to(ROOT)}  {OUT.stat().st_size // 1024} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
