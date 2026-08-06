"""Dibuja el workflow de n8n como un unico SVG interactivo, a partir del JSON exportado.

n8n no exporta el canvas a imagen ni a PDF, y una captura queda atada al viewport.
Este script lee los nodos y las conexiones del workflow y los redibuja con un layout
propio: las coordenadas de n8n estan pensadas para editar, no para leer, y arrastran
mucho espacio muerto. Acá se recalculan por capas para que el flujo se recorra de un
vistazo, de izquierda a derecha.

El SVG resultante es vectorial (zoom sin perder calidad, texto seleccionable) y muestra
una ficha explicativa al pasar el mouse sobre cada nodo. Abierto en un navegador se
puede imprimir a PDF conservando el vector.

    python scripts/render_workflow_svg.py
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "n8n" / "workflow_ciri_agent.json"
OUT = ROOT / "docs" / "diagrams" / "workflow_canvas.svg"

STICKY = "n8n-nodes-base.stickyNote"

NODE_W, NODE_H = 300, 96
COL_GAP, ROW_GAP = 96, 34
SECTION_PAD = 44
SECTION_GAP = 64
HEADER_H = 108
TOP_BAR = 150
BOTTOM_BAR = 116

TYPE_STYLE: dict[str, tuple[str, str, str]] = {
    "webhook":          ("#052e26", "#10b981", "TRIGGER"),
    "formTrigger":      ("#052e26", "#10b981", "TRIGGER"),
    "respondToWebhook": ("#052e26", "#10b981", "RESPUESTA"),
    "httpRequest":      ("#0c1f47", "#60a5fa", "HTTP"),
    "code":             ("#2b1054", "#a78bfa", "CODE"),
    "set":              ("#1c222c", "#94a3b8", "SET"),
    "if":               ("#39220a", "#fbbf24", "IF"),
    "switch":           ("#39220a", "#fbbf24", "SWITCH"),
    "merge":            ("#082e2b", "#2dd4bf", "MERGE"),
    "wait":             ("#3d0c22", "#f472b6", "WAIT"),
    "stopAndError":     ("#3d1111", "#f87171", "ERROR"),
}
DEFAULT_STYLE = ("#18181b", "#71717a", "NODO")

SECTION_TINT = {"1": "#10b981", "2": "#3b82f6", "3": "#a855f7", "4": "#f59e0b"}

# Nota al costado de cada banda: la idea de fondo de esa seccion, no el detalle.
SECTION_NOTES: dict[str, str] = {
    "1": "La URL de la API se resuelve una sola vez, en «Validar Formato TXN», y los 12 nodos HTTP la leen de ahí: "
         "body del webhook → variable de n8n → API pública. Por eso el workflow se importa y corre sin configurar nada.",
    "2": "Unir el RAG con la base de datos: Qdrant aporta qué políticas aplican y qué se hizo en casos parecidos; "
         "SQLite aporta los hechos exactos. Las 6 ramas salen en paralelo y el Merge espera a todas. "
         "Ningún umbral de negocio vive acá: los pide a la API, que los lee de constants.py.",
    "3": "El código decide, el LLM explica: 6 de los 11 campos de la resolución los calcula Python y sobrescriben "
         "siempre lo que devuelve el modelo. Después un LLM-as-Judge puntúa esa resolución sobre 5 criterios con rúbricas.",
    "4": "Sólo los casos HIGH frenan y esperan a un analista. Y si el Juez puntuó 8.0 o más, el caso se reindexa "
         "como precedente en Qdrant: cada investigación que sale bien mejora la siguiente.",
}

# Aclaraciones sobre conexiones que no son obvias por su recorrido.
EDGE_LABELS: dict[tuple[str, str], str] = {
    ("Formatear Caché", "Responder — Reporte"): "atajo de caché: responde el HTML ya generado",
}

# Que hace cada nodo, en una linea. Es la ficha que aparece al pasar el mouse.
DESCRIPTIONS: dict[str, str] = {
    "Webhook — Entrada": "Recibe el caso por POST. Espera transaction_id y motivo; acepta cliente_vip y api_base_url como opcionales.",
    "Validar Formato — IF": "Valida que el ID matchee TXN-XXXXX con una regex. Si falla, corta antes de gastar un solo token de LLM.",
    "Validar Formato TXN": "Normaliza los campos de entrada y resuelve la URL de la API una sola vez: body > variable de n8n > API publica. Los 12 nodos HTTP leen de acá.",
    "Propagar → Error Handler — TXN": "Corta la ejecucion con contexto cuando el formato del ID es invalido. Dispara el workflow de error.",
    "Despertar API": "GET /health antes de las consultas. Absorbe el cold start del free tier de Render para que el primer nodo real no falle por timeout.",
    "Verificar Caché": "GET /api/cache/lookup. Caché de idempotencia: si esta TXN ya se investigo, el reporte ya existe.",
    "¿Cache Hit?": "Bifurca segun haya caché. Un hit saltea el pipeline completo: 2 segundos en vez de 113.",
    "Formatear Caché": "Toma el HTML ya cacheado y lo manda directo a responder, sin volver a renderizarlo.",
    "Obtener Transacción": "GET /api/transactions/{id}. Verdad estructurada desde SQLite: monto, comercio, pais, metodo de pago, score antifraude.",
    "Obtener Logs": "GET /api/logs/{tx_id}. Todos los eventos de procesamiento de esa transaccion, completos: la similitud semantica no aporta acá.",
    "Buscar Políticas": "GET /api/policies/search. RAG sobre Qdrant. La query se arma determinísticamente y se enriquece con reglas segun metodo de pago, score y pais.",
    "Buscar Casos Similares": "GET /api/cases/similar. RAG sobre los 60 casos historicos: que se hizo antes ante un contracargo parecido.",
    "Riesgo del Comercio": "GET /api/merchants/{name}/risk. Ratio de contracargos, volumen y flags. Los umbrales viven en constants.py, no en este canvas.",
    "Historial del Cliente": "GET /api/clients/{id}/history. Reincidencia, paises usados y metodos de pago. Los flags vienen ya calculados por la API.",
    "Verificar SLA": "POST /api/sla/check. Limite segun pais y condicion VIP: LATAM 10 dias, fuera de LATAM 15, VIP 5. La regla vive en la API.",
    "Merge — Contexto Paralelo": "Espera las 6 ramas paralelas. Recien cuando llegaron todas se arma el contexto del LLM.",
    "Propagar → Error Handler — API": "Corta si la API no responde tras 3 reintentos con backoff.",
    "Compilar Contexto": "Fusiona los outputs de las 7 herramientas en un solo objeto: la union del RAG con la base de datos que pide la consigna.",
    "Sintetizar Resolución": "POST /api/analyze/resolve. Evalua las politicas recuperadas (Haiku) y sintetiza la resolucion (Sonnet). Prompt v3.0.",
    "Verificar Guardrails": "Hace visibles en el canvas los 5 guardrails post-LLM. El caso canonico: APPROVE con un veredicto BLOCKER activo se autocorrige a REJECT.",
    "Juez de Calidad": "POST /api/analyze/judge. LLM-as-Judge sobre 5 criterios con rubricas, escala 1 a 10. Prompt v2.0.",
    "Extraer Evaluación — Juez": "Expone la evaluacion del juez como judge_evaluation para los nodos siguientes.",
    "¿Juez Aprueba? (≥7.0)": "Quality gate. Por debajo de 7.0 la resolucion sigue viaje, pero marcada.",
    "Marcar — Calidad Baja": "Agrega el flag LOW_QUALITY. No descarta la resolucion: la entrega senalizada para revision humana.",
    "Preparar Informe": "Construye el payload exacto que espera POST /api/reports/html.",
    "Propagar → Error Handler — Análisis": "Corta si falla alguna de las llamadas al LLM.",
    "Switch — Nivel de Riesgo": "Enruta por risk_level. BLOCKER, MEDIUM y LOW se resuelven solos; HIGH es el unico que frena y espera a una persona.",
    "Wait — Aprobación HITL": "Human-in-the-Loop. Pausa la ejecucion y expone un formulario para que el analista apruebe o rechace.",
    "Procesar Respuesta HITL": "Fusiona la decision del analista en el payload, para que el reporte final refleje lo que decidio una persona.",
    "Registrar Feedback HITL": "POST /api/feedback. Cierra el loop de mejora: si el juez puntuo 8.0 o mas, el caso se reindexa como precedente en Qdrant.",
    "Generar Reporte": "POST /api/reports/html. Renderiza el informe con Jinja2: 9 secciones y formulario HITL condicional.",
    "Responder — Reporte": "Devuelve el HTML al que disparo el webhook. Sirve igual a un browser, a otro workflow o a un bot.",
}


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def short_type(t: str) -> str:
    return t.rsplit(".", 1)[-1]


def wrap(text: str, width: int, max_lines: int = 99) -> list[str]:
    lines, cur = [], ""
    for w in text.split():
        cand = f"{cur} {w}".strip()
        if len(cand) <= width:
            cur = cand
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[: max_lines - 1] + [lines[max_lines - 1][: width - 1] + "…"]
    return lines or [""]


NOTE_WRAP = 118
NOTE_LINE_H = 28


def note_lines(sec: str) -> list[str]:
    note = SECTION_NOTES.get(sec)
    return wrap(note, NOTE_WRAP) if note else []


def note_height(sec: str) -> float:
    lines = note_lines(sec)
    return len(lines) * NOTE_LINE_H + 34 if lines else 0.0


def endpoint_of(node: dict) -> str:
    """Metodo + ruta, leidos de los parametros reales del nodo."""
    url = node["parameters"].get("url")
    if not url:
        return ""
    path = re.sub(r"\{\{[^}]*\}\}", "", url.lstrip("=")).strip()
    path = path.split("?")[0].rstrip("/") or "/"
    method = node["parameters"].get("method", "GET")
    return f"{method} {path}" if path.startswith("/") else path


def assign_sections(workflow: dict) -> dict[str, str]:
    """Usa los sticky notes originales como fuente de la agrupacion por seccion."""
    bands = []
    for s in workflow["nodes"]:
        if s["type"] != STICKY:
            continue
        content = s["parameters"].get("content", "")
        first = content.strip().splitlines()[0] if content.strip() else ""
        m = re.search(r"§\s*(\d)", first)
        if not m:
            continue
        x, y = s["position"]
        bands.append((m.group(1), x, y, x + s["parameters"]["width"], y + s["parameters"]["height"]))

    out: dict[str, str] = {}
    for n in workflow["nodes"]:
        if n["type"] == STICKY:
            continue
        x, y = n["position"]
        for sec, x1, y1, x2, y2 in bands:
            if x1 <= x <= x2 and y1 <= y <= y2:
                out[n["name"]] = sec
                break
        else:
            out[n["name"]] = "1"
    return out


def layout(workflow: dict) -> tuple[dict[str, tuple[float, float]], list[tuple[str, float, float, float]]]:
    """Posiciona por capas dentro de cada seccion. Devuelve posiciones y bandas."""
    execs = [n for n in workflow["nodes"] if n["type"] != STICKY]
    names = [n["name"] for n in execs]
    section = assign_sections(workflow)

    edges = [
        (src, link["node"])
        for src, conn in workflow.get("connections", {}).items()
        for outputs in conn.get("main", [])
        for link in outputs or []
        if src in names and link["node"] in names
    ]

    # Profundidad = camino mas largo desde los nodos sin entrada (orden de lectura real).
    incoming = defaultdict(list)
    outgoing = defaultdict(list)
    for a, b in edges:
        incoming[b].append(a)
        outgoing[a].append(b)

    depth = {n: 0 for n in names}
    for _ in range(len(names)):
        changed = False
        for a, b in edges:
            if depth[b] < depth[a] + 1:
                depth[b] = depth[a] + 1
                changed = True
        if not changed:
            break

    # Columnas dentro de cada seccion, respetando el orden global de profundidad.
    per_section: dict[str, dict[int, list[str]]] = defaultdict(lambda: defaultdict(list))
    for n in names:
        per_section[section[n]][depth[n]].append(n)

    # Las secciones se apilan verticalmente: se recorre scrolleando hacia abajo,
    # que es como se lee en pantalla. Dentro de cada una, el flujo va de izquierda
    # a derecha, ordenado por profundidad.
    pos: dict[str, tuple[float, float]] = {}
    bands: list[tuple[str, float, float, float]] = []
    cursor_y = TOP_BAR

    for sec in sorted(per_section):
        cols = sorted(per_section[sec])
        max_rows = max(len(per_section[sec][c]) for c in cols)
        band_y = cursor_y
        inner_x = SECTION_PAD * 2
        rows_top = band_y + HEADER_H
        for c in cols:
            members = sorted(per_section[sec][c], key=lambda m: (len(outgoing[m]) == 0, m))
            block_h = len(members) * NODE_H + (len(members) - 1) * ROW_GAP
            start_y = rows_top + (max_rows * NODE_H + (max_rows - 1) * ROW_GAP - block_h) / 2
            for i, m in enumerate(members):
                pos[m] = (inner_x, start_y + i * (NODE_H + ROW_GAP))
            inner_x += NODE_W + COL_GAP
        band_w = inner_x - COL_GAP - SECTION_PAD * 2 + SECTION_PAD * 2
        band_h = (
            HEADER_H
            + max_rows * NODE_H
            + (max_rows - 1) * ROW_GAP
            + SECTION_PAD
            + note_height(sec)
        )
        bands.append((sec, band_y, band_w, band_h))
        cursor_y = band_y + band_h + SECTION_GAP

    # Todas las bandas comparten el ancho de la mas ancha: se ven como un documento.
    widest = max(w for _, _, w, _ in bands)
    bands = [(sec, y, widest, h) for sec, y, w, h in bands]
    return pos, bands


def build(workflow: dict, show_tips: bool = True) -> str:
    execs = {n["name"]: n for n in workflow["nodes"] if n["type"] != STICKY}
    pos, bands = layout(workflow)

    width = SECTION_PAD + max(w for _, _, w, _ in bands) + SECTION_PAD
    height = max(y + h for _, y, _, h in bands) + BOTTOM_BAR

    titles = {"1": "Entrada y caché", "2": "Contexto paralelo", "3": "Análisis con IA", "4": "Ruteo por riesgo"}
    subtitles = {
        "1": "valida, despierta la API y corta camino si el caso ya se investigó",
        "2": "7 herramientas: 2 de RAG sobre Qdrant, 5 de verdad estructurada",
        "3": "evalúa políticas, sintetiza, aplica guardrails y se autoevalúa",
        "4": "clasifica y deriva; sólo HIGH espera a una persona",
    }

    section = assign_sections(workflow)
    p: list[str] = []
    p.append(f'<rect width="{width:.0f}" height="{height:.0f}" fill="#09090b"/>')

    # --- encabezado --------------------------------------------------------
    p.append(
        f'<text x="{SECTION_PAD}" y="66" font-size="42" font-weight="800" fill="#fafafa">'
        f"CIRI · Agente de Contracargos — orquestación explícita en n8n</text>"
    )
    p.append(
        f'<text x="{SECTION_PAD}" y="106" font-size="21" fill="#a1a1aa">'
        f"{len(execs)} nodos ejecutables, sin nodo AI Agent: cada paso es visible y siempre ocurre en el mismo orden. "
        f"Pasá el mouse sobre un nodo para ver qué hace.</text>"
    )

    # --- bandas de seccion -------------------------------------------------
    for sec, y, w, h in bands:
        tint = SECTION_TINT.get(sec, "#52525b")
        p.append(
            f'<rect x="{SECTION_PAD}" y="{y:.0f}" width="{w:.0f}" height="{h:.0f}" rx="22" '
            f'fill="{tint}" fill-opacity="0.05" stroke="{tint}" stroke-opacity="0.4" stroke-width="2.5"/>'
        )
        p.append(
            f'<text x="{SECTION_PAD * 2:.0f}" y="{y + 52:.0f}" font-size="32" font-weight="800" '
            f'fill="{tint}">§{sec} · {esc(titles.get(sec, ""))}</text>'
        )
        p.append(
            f'<text x="{SECTION_PAD * 2:.0f}" y="{y + 86:.0f}" font-size="20" fill="#a1a1aa">'
            f'{esc(subtitles.get(sec, ""))}</text>'
        )

        # Nota al pie de la banda: la idea de fondo de la seccion.
        lines = note_lines(sec)
        if lines:
            nx = SECTION_PAD * 2
            top = y + h - note_height(sec) + 10
            p.append(
                f'<rect x="{nx - 22:.0f}" y="{top - 20:.0f}" width="4" '
                f'height="{len(lines) * NOTE_LINE_H + 8:.0f}" rx="2" fill="{tint}" fill-opacity="0.6"/>'
            )
            for i, line in enumerate(lines):
                p.append(
                    f'<text x="{nx:.0f}" y="{top + i * NOTE_LINE_H:.0f}" font-size="20" '
                    f'fill="#c4c4c8">{esc(line)}</text>'
                )

    # --- conexiones --------------------------------------------------------
    # Dentro de una seccion, curva lateral. Entre secciones, la conexion baja al
    # pasillo que las separa y entra por arriba: evita diagonales que crucen el
    # diagrama entero.
    band_of = {sec: (y, h) for sec, y, _, h in bands}
    section = assign_sections(workflow)

    for src, conn in workflow.get("connections", {}).items():
        if src not in pos:
            continue
        for outputs in conn.get("main", []):
            for link in outputs or []:
                dst = link["node"]
                if dst not in pos:
                    continue
                error = dst.startswith("Propagar")
                color = "#ef4444" if error else "#52525b"
                dash = ' stroke-dasharray="9 7"' if error else ""

                if section[src] == section[dst]:
                    x1, y1 = pos[src][0] + NODE_W, pos[src][1] + NODE_H / 2
                    x2, y2 = pos[dst][0], pos[dst][1] + NODE_H / 2
                    dx = max(70.0, abs(x2 - x1) * 0.42)
                    d = (
                        f"M {x1:.0f} {y1:.0f} C {x1 + dx:.0f} {y1:.0f}, "
                        f"{x2 - dx:.0f} {y2:.0f}, {x2:.0f} {y2:.0f}"
                    )
                else:
                    x1 = pos[src][0] + NODE_W / 2
                    y1 = pos[src][1] + NODE_H
                    x2 = pos[dst][0] + NODE_W / 2
                    y2 = pos[dst][1]
                    lane = band_of[section[dst]][0] - SECTION_GAP / 2
                    r = 22
                    sweep_right = x2 > x1
                    sx = r if sweep_right else -r
                    d = (
                        f"M {x1:.0f} {y1:.0f} L {x1:.0f} {lane - r:.0f} "
                        f"Q {x1:.0f} {lane:.0f} {x1 + sx:.0f} {lane:.0f} "
                        f"L {x2 - sx:.0f} {lane:.0f} "
                        f"Q {x2:.0f} {lane:.0f} {x2:.0f} {lane + r:.0f} "
                        f"L {x2:.0f} {y2:.0f}"
                    )
                    color = "#ef4444" if error else "#71717a"
                    label = EDGE_LABELS.get((src, dst))
                    if label:
                        p.append(
                            f'<text x="{x1 + 18:.0f}" y="{lane - 14:.0f}" font-size="17" '
                            f'fill="#a1a1aa" font-style="italic">{esc(label)}</text>'
                        )

                p.append(
                    f'<path d="{d}" fill="none" stroke="{color}" stroke-width="2.5" '
                    f'stroke-opacity="0.85"{dash} marker-end="url(#arrow)"/>'
                )

    # --- nodos -------------------------------------------------------------
    for name, node in execs.items():
        fill, stroke, tag = TYPE_STYLE.get(short_type(node["type"]), DEFAULT_STYLE)
        x, y = pos[name]
        p.append(f'<g class="n">')
        p.append(
            f'<rect x="{x:.0f}" y="{y:.0f}" width="{NODE_W}" height="{NODE_H}" rx="14" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="2.5"/>'
        )
        p.append(
            f'<rect x="{x:.0f}" y="{y:.0f}" width="6" height="{NODE_H}" rx="3" fill="{stroke}"/>'
        )
        p.append(
            f'<text x="{x + 20:.0f}" y="{y + 26:.0f}" font-family="ui-monospace, monospace" '
            f'font-size="13" font-weight="700" fill="{stroke}" letter-spacing="1">{tag}</text>'
        )
        for i, line in enumerate(wrap(name, 30, 2)):
            p.append(
                f'<text x="{x + 20:.0f}" y="{y + 52 + i * 22:.0f}" font-size="19" '
                f'font-weight="650" fill="#f4f4f5">{esc(line)}</text>'
            )
        ep = endpoint_of(node)
        if ep:
            p.append(
                f'<text x="{x + 20:.0f}" y="{y + NODE_H - 12:.0f}" font-family="ui-monospace, monospace" '
                f'font-size="14" fill="#a1a1aa">{esc(ep[:38])}</text>'
            )
        p.append("</g>")

    # --- leyenda -----------------------------------------------------------
    ly = height - BOTTOM_BAR + 44
    p.append(f'<text x="{SECTION_PAD}" y="{ly - 22:.0f}" font-size="18" font-weight="700" fill="#a1a1aa">Referencias</text>')
    lx = SECTION_PAD
    for label, (fill, stroke, _) in [
        ("HTTP a FastAPI", TYPE_STYLE["httpRequest"]),
        ("Code / Set", TYPE_STYLE["code"]),
        ("Control de flujo", TYPE_STYLE["if"]),
        ("Merge", TYPE_STYLE["merge"]),
        ("HITL", TYPE_STYLE["wait"]),
        ("Trigger / respuesta", TYPE_STYLE["webhook"]),
        ("Camino de error", TYPE_STYLE["stopAndError"]),
    ]:
        p.append(f'<rect x="{lx:.0f}" y="{ly:.0f}" width="26" height="18" rx="5" fill="{fill}" stroke="{stroke}" stroke-width="2"/>')
        p.append(f'<text x="{lx + 36:.0f}" y="{ly + 14:.0f}" font-size="17" fill="#d4d4d8">{esc(label)}</text>')
        lx += 40 + len(label) * 9.5 + 34

    # --- fichas al pasar el mouse (dibujadas al final para quedar por encima)
    p.append('<g class="tips">')
    for name in execs if show_tips else []:
        x, y = pos[name]
        desc = DESCRIPTIONS.get(name, "")
        if not desc:
            continue
        lines = wrap(desc, 46)
        cw, ch = 470, 46 + len(lines) * 24
        cx = min(x, width - cw - SECTION_PAD)
        cy = y + NODE_H + 14
        if cy + ch > height - BOTTOM_BAR:
            cy = y - ch - 14
        p.append('<g class="tip">')
        p.append(f'<rect class="hit" x="{x:.0f}" y="{y:.0f}" width="{NODE_W}" height="{NODE_H}" rx="14" fill="transparent"/>')
        p.append('<g class="card">')
        p.append(
            f'<rect x="{cx:.0f}" y="{cy:.0f}" width="{cw}" height="{ch:.0f}" rx="14" '
            f'fill="#18181b" stroke="#3f3f46" stroke-width="2"/>'
        )
        p.append(
            f'<text x="{cx + 20:.0f}" y="{cy + 30:.0f}" font-size="18" font-weight="700" '
            f'fill="#fafafa">{esc(name)}</text>'
        )
        for i, line in enumerate(lines):
            p.append(
                f'<text x="{cx + 20:.0f}" y="{cy + 58 + i * 24:.0f}" font-size="16" '
                f'fill="#d4d4d8">{esc(line)}</text>'
            )
        p.append("</g></g>")
    p.append("</g>")

    style = (
        "<style>"
        "text{font-family:Inter,'Segoe UI',system-ui,sans-serif}"
        ".tip .card{opacity:0;pointer-events:none;transition:opacity .12s}"
        ".tip:hover .card{opacity:1}"
        ".tip .hit{cursor:help}"
        "</style>"
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:.0f} {height:.0f}" '
        f'width="{width:.0f}" height="{height:.0f}" role="img" '
        f'aria-label="Canvas del workflow CIRI">\n'
        f"<title>CIRI · Agente de Contracargos — workflow n8n</title>\n{style}\n"
        '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
        'markerHeight="7" orient="auto-start-reverse">'
        '<path d="M 0 0 L 10 5 L 0 10 z" fill="#52525b"/></marker></defs>\n'
        + "\n".join(p)
        + "\n</svg>\n"
    )


def main() -> int:
    workflow = json.loads(SRC.read_text(encoding="utf-8"))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    svg = build(workflow)
    OUT.write_text(svg, encoding="utf-8")
    m = re.search(r'viewBox="0 0 (\d+) (\d+)"', svg)
    print(f"{OUT.relative_to(ROOT)}  {m.group(1)}x{m.group(2)}  {len(svg) // 1024} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
