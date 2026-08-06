"""Genera el expediente visual del workflow: un unico HTML autocontenido.

n8n no exporta el canvas a imagen ni a PDF, y una captura queda atada al viewport.
Este script lee `n8n/workflow_ciri_agent.json` y arma una pagina que se abre en
cualquier navegador, sin dependencias ni conexion: los 32 nodos en orden de
ejecucion, agrupados por seccion, con el detalle de cada uno a un clic.

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

KIND_LABEL = {
    "webhook": "trigger",
    "formTrigger": "trigger",
    "respondToWebhook": "respuesta",
    "httpRequest": "http",
    "code": "code",
    "set": "set",
    "if": "bifurca",
    "switch": "enruta",
    "merge": "merge",
    "wait": "espera",
    "stopAndError": "corta",
}

SECTIONS = {
    "1": ("Entrada y validación", "Recibe el caso y verifica el formato del identificador antes de gastar un solo token."),
    "2": ("Caché y contexto", "Corta camino si el caso ya se investigó. Si no, la transacción primero y después seis consultas en paralelo: dos de búsqueda semántica sobre Qdrant y cuatro de verdad estructurada."),
    "3": ("Análisis con IA", "Evalúa las políticas, sintetiza la resolución, aplica guardrails y se autoevalúa."),
    "4": ("Ruteo por riesgo", "Clasifica y deriva. Sólo los casos de riesgo alto frenan y esperan a una persona."),
}

SECTION_NOTES = {
    "1": "La URL de la API se resuelve una sola vez, en «Validar Formato TXN», y los doce nodos HTTP la leen de ahí: "
         "body del webhook, variable de n8n, o la API pública por defecto. Por eso el workflow se importa y corre sin configurar nada.",
    "2": "Un acierto de caché devuelve el informe ya generado y saltea todo lo demás: dos segundos en lugar de ciento trece. "
         "Cuando hay que investigar, acá se unen el RAG y la base de datos: Qdrant aporta qué políticas aplican y qué se hizo en casos parecidos, "
         "SQLite aporta los hechos exactos. Ningún umbral de negocio vive en el canvas — los límites de SLA, el ratio de "
         "contracargos y las reglas de reincidencia se le piden a la API, que los lee de <code>domain/constants.py</code>.",
    "3": "El código decide, el LLM explica: seis de los once campos de la resolución los calcula Python y sobrescriben "
         "siempre lo que devuelve el modelo. Después un LLM-as-Judge puntúa esa resolución sobre cinco criterios con rúbricas.",
    "4": "Si el Juez puntuó 8.0 o más, el caso se reindexa como precedente en Qdrant. Cada investigación que sale bien "
         "mejora a la siguiente.",
}

DESCRIPTIONS: dict[str, str] = {
    "Webhook — Entrada": "Recibe el caso por POST. Necesita <code>transaction_id</code> y <code>motivo</code>; acepta <code>cliente_vip</code> y <code>api_base_url</code> como opcionales.",
    "Validar Formato — IF": "Valida el formato del ID con una expresión regular. Si no matchea, corta acá: ningún caso mal formado llega a gastar tokens de LLM.",
    "Validar Formato TXN": "Normaliza los campos de entrada y resuelve la URL de la API una sola vez. Los doce nodos HTTP del workflow leen ese valor, así que apuntar todo a otra API es cambiar un solo lugar.",
    "Propagar → Error Handler — TXN": "Corta la ejecución con contexto cuando el formato del ID es inválido, y dispara el workflow de errores.",
    "Despertar API": "Un <code>GET /health</code> antes de las consultas reales. Absorbe el arranque en frío del free tier de Render para que el primer nodo con datos no falle por timeout.",
    "Verificar Caché": "Caché de idempotencia. Si esta misma transacción ya se investigó, el informe ya existe y no hay nada que recalcular.",
    "¿Cache Hit?": "Bifurca según haya caché. Un acierto saltea el pipeline completo: dos segundos en lugar de ciento trece.",
    "Formatear Caché": "Toma el HTML ya generado y lo manda directo a responder, sin volver a renderizarlo ni a llamar al modelo.",
    "Obtener Transacción": "Verdad estructurada desde SQLite: monto, comercio, país, método de pago y score antifraude. Lookup exacto por ID.",
    "Obtener Logs": "Todos los eventos de procesamiento de esa transacción, completos. Se traen enteros porque acá la similitud semántica no aporta nada.",
    "Buscar Políticas": "Búsqueda semántica sobre Qdrant. La consulta se arma de forma determinística y se enriquece con reglas según método de pago, score y país.",
    "Buscar Casos Similares": "Búsqueda semántica sobre los sesenta casos históricos: qué se resolvió antes ante un contracargo parecido.",
    "Riesgo del Comercio": "Ratio de contracargos, volumen y señales del comercio. Los umbrales viven en la API, no en este canvas.",
    "Historial del Cliente": "Reincidencia, países usados y métodos de pago. Las señales llegan ya calculadas.",
    "Verificar SLA": "Límite según país y condición VIP: diez días en LATAM, quince fuera, cinco para clientes VIP. La regla vive en la API para que editarla no implique tocar el workflow.",
    "Merge — Contexto Paralelo": "Espera a las seis ramas. Recién cuando llegaron todas se arma el contexto que ve el modelo.",
    "Propagar → Error Handler — API": "Corta si la API no responde después de tres reintentos con espera creciente.",
    "Compilar Contexto": "Fusiona las salidas de las siete herramientas en un solo objeto. Es el punto donde el RAG y la base de datos se vuelven un único contexto.",
    "Sintetizar Resolución": "Evalúa las políticas recuperadas con Haiku y sintetiza la resolución con Sonnet. Prompt versionado v3.0.",
    "Verificar Guardrails": "Hace visibles en el canvas los cinco guardrails posteriores al modelo. El caso típico: aprobar con una política bloqueante activa se autocorrige a rechazo.",
    "Juez de Calidad": "Un segundo modelo evalúa la resolución sobre cinco criterios con rúbricas, en escala de uno a diez. Prompt versionado v2.0.",
    "Extraer Evaluación — Juez": "Expone la evaluación del juez para los nodos siguientes.",
    "¿Juez Aprueba? (≥7.0)": "Control de calidad. Por debajo de siete la resolución igual se entrega, pero marcada.",
    "Marcar — Calidad Baja": "Agrega la marca de calidad baja. No descarta la resolución: la entrega señalizada para que una persona la revise.",
    "Preparar Informe": "Arma el payload exacto que espera el generador de informes.",
    "Propagar → Error Handler — Análisis": "Corta si falla alguna de las llamadas al modelo.",
    "Switch — Nivel de Riesgo": "Enruta según el nivel de riesgo. Bloqueante, medio y bajo se resuelven solos; alto es el único que frena.",
    "Wait — Aprobación HITL": "Persona en el circuito. Pausa la ejecución y expone un formulario para que el analista apruebe o rechace.",
    "Procesar Respuesta HITL": "Fusiona la decisión del analista en el payload, para que el informe final refleje lo que decidió una persona.",
    "Registrar Feedback HITL": "Cierra el circuito de mejora. Si el juez puntuó ocho o más, el caso se reindexa como precedente en Qdrant.",
    "Generar Reporte": "Renderiza el informe con Jinja2: nueve secciones y formulario de aprobación condicional.",
    "Responder — Reporte": "Devuelve el HTML a quien disparó el webhook. Sirve igual a un navegador, a otro workflow o a un bot.",
}

# Que se rompio, dicho en el idioma del que lee, no en el del sistema.
ERROR_LABEL = {
    "Propagar → Error Handler — TXN": "el identificador no tiene el formato esperado",
    "Propagar → Error Handler — API": "la API no responde",
    "Propagar → Error Handler — Análisis": "falla una llamada al modelo",
}

# Nodos que merecen destacarse: son las dos patas del argumento tecnico.
ACCENT = {
    "Buscar Políticas": "rag",
    "Buscar Casos Similares": "rag",
    "Sintetizar Resolución": "llm",
    "Juez de Calidad": "llm",
    "Wait — Aprobación HITL": "human",
}


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def short_type(t: str) -> str:
    return t.rsplit(".", 1)[-1]


def endpoint_of(node: dict) -> str:
    url = node["parameters"].get("url")
    if not url:
        if node["type"].endswith("webhook"):
            path = node["parameters"].get("path", "")
            return f"POST /webhook/{path}" if path else ""
        return ""
    path = re.sub(r"\{\{[^}]*\}\}", "…", url.lstrip("=")).strip()
    path = path.split("?")[0]
    path = re.sub(r"…+", "…", path)
    path = path[1:] if path.startswith("…") else path   # el prefijo es la URL base
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
    edges = [(a, b) for a, _, b in wired]

    depth = {n["name"]: 0 for n in execs}
    for _ in range(len(execs)):
        changed = False
        for a, b in edges:
            if depth[b] < depth[a] + 1:
                depth[b] = depth[a] + 1
                changed = True
        if not changed:
            break

    feeds = defaultdict(list)
    fed_by = defaultdict(list)
    for a, b in edges:
        feeds[a].append(b)
        fed_by[b].append(a)

    # (nodo, salida) -> destinos. Dos nodos son paralelos solo si salen de la
    # misma salida del mismo nodo; si salen de salidas distintas son ramas.
    forks = defaultdict(list)
    for a, i, b in wired:
        forks[(a, i)].append(b)

    return {
        "forks": forks,
        "execs": {n["name"]: n for n in execs},
        "section": section,
        "depth": depth,
        "feeds": feeds,
        "fed_by": fed_by,
    }


def build(workflow: dict) -> str:
    a = analyse(workflow)
    execs, section, depth, forks = a["execs"], a["section"], a["depth"], a["forks"]

    def group_label(members: list[str]) -> str:
        """Paralelo si una misma salida alimenta a todos; si no, son ramas."""
        want = set(members)
        if any(want <= set(targets) for targets in forks.values()):
            return f"{len(members)} en paralelo"
        return "caminos alternativos"

    # Orden de lectura: seccion, profundidad, nombre. Los nodos que cortan la
    # ejecucion se muestran aparte: son caminos de excepcion, no del flujo feliz.
    ordered = sorted(execs, key=lambda n: (section[n], depth[n], n))
    flow = [n for n in ordered if short_type(execs[n]["type"]) != "stopAndError"]
    step = {name: i + 1 for i, name in enumerate(flow)}

    columns: dict[str, list[list[str]]] = defaultdict(list)
    for sec in SECTIONS:
        grouped: dict[int, list[str]] = defaultdict(list)
        for n in flow:
            if section[n] == sec:
                grouped[depth[n]].append(n)
        columns[sec] = [grouped[d] for d in sorted(grouped)]

    errors: dict[str, list[str]] = defaultdict(list)
    for n in ordered:
        if short_type(execs[n]["type"]) == "stopAndError":
            errors[section[n]].append(n)

    def card(name: str) -> str:
        node = execs[name]
        kind = KIND_LABEL.get(short_type(node["type"]), "nodo")
        ep = endpoint_of(node)
        accent = ACCENT.get(name, "")
        return (
            f'<button class="node{" acc-" + accent if accent else ""}" data-node="{esc(name)}" type="button">'
            f'<span class="node-top"><span class="step">{step[name]:02d}</span>'
            f'<span class="kind">{kind}</span></span>'
            f'<span class="node-name">{esc(name)}</span>'
            f'{f"<code class=\'ep\'>{esc(ep)}</code>" if ep else ""}'
            "</button>"
        )

    body: list[str] = []
    for sec, (title, subtitle) in SECTIONS.items():
        members = [n for n in flow if section[n] == sec]
        first, last = step[members[0]], step[members[-1]]
        body.append(f'<section class="sec" id="sec-{sec}">')
        body.append(
            f'<header class="sec-h"><span class="sec-n">§{sec}</span>'
            f"<h2>{esc(title)}</h2>"
            f'<span class="sec-range">pasos {first:02d}–{last:02d}</span>'
            f'<p class="sec-sub">{esc(subtitle)}</p></header>'
        )

        lines: list[list[list[str]]] = []
        for col in columns[sec]:
            wide = len(col) > 1
            if not lines or wide or len(lines[-1]) >= 4 or (lines[-1] and len(lines[-1][-1]) > 1):
                lines.append([col])
            else:
                lines[-1].append(col)

        body.append('<div class="flow">')
        for li, line in enumerate(lines):
            body.append(f'<div class="line{" cont" if li else ""}">')
            for i, col in enumerate(line):
                if i:
                    body.append('<span class="arrow" aria-hidden="true"></span>')
                if len(col) == 1:
                    body.append(card(col[0]))
                else:
                    cols = 3 if len(col) % 3 == 0 else 2
                    body.append(
                        f'<div class="par" style="--cols:{cols}">'
                        f'<span class="par-tag">{group_label(col)}</span>'
                        + "".join(card(n) for n in col)
                        + "</div>"
                    )
            body.append("</div>")
        body.append("</div>")

        if errors[sec]:
            chips = "".join(
                f'<button class="chip" data-node="{esc(n)}" type="button">{esc(ERROR_LABEL.get(n, n))}</button>'
                for n in errors[sec]
            )
            body.append(f'<div class="errs"><span class="errs-l">si algo falla</span>{chips}</div>')

        body.append(f'<p class="sec-note">{SECTION_NOTES[sec]}</p>')
        body.append("</section>")

    ribbon = []
    for sec in SECTIONS:
        ticks = "".join(
            f'<button class="tick" data-node="{esc(n)}" type="button" '
            f'aria-label="{step[n]:02d} {esc(n)}"></button>'
            for n in flow
            if section[n] == sec
        )
        ribbon.append(f'<span class="cluster" data-sec="{sec}">{ticks}</span>')

    detail = {
        name: {
            "step": f"{step[name]:02d}" if name in step else "—",
            "kind": KIND_LABEL.get(short_type(execs[name]["type"]), "nodo"),
            "type": short_type(execs[name]["type"]),
            "section": section[name],
            "endpoint": endpoint_of(execs[name]),
            "desc": DESCRIPTIONS.get(name, ""),
            "from": a["fed_by"].get(name, []),
            "to": a["feeds"].get(name, []),
        }
        for name in execs
    }

    return (
        TEMPLATE.replace("__RIBBON__", "".join(ribbon))
        .replace("__BODY__", "".join(body))
        .replace("__COUNT__", str(len(flow)))
        .replace("__EXTRA__", str(len(execs) - len(flow)))
        .replace("__DATA__", json.dumps(detail, ensure_ascii=False))
    )


TEMPLATE = r"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CIRI · Agente de contracargos — el flujo, paso por paso</title>
<style>
:root{
  --paper:#fbfbf9; --surface:#fff; --ink:#16161a; --muted:#6a6a72; --faint:#96969e;
  --rule:#e4e3de; --rule-strong:#d2d1ca; --shadow:0 1px 2px rgba(20,20,25,.05);
  --rag:#175cd3; --llm:#6941c6; --human:#b54708; --stop:#b42318;
  --hl:#16161a; --tick:#d8d7d0;
}
:root[data-theme="dark"], html:not([data-theme="light"]) {}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){
    --paper:#0e1013; --surface:#161920; --ink:#e9eaee; --muted:#9a9ca6; --faint:#71737d;
    --rule:#242832; --rule-strong:#333846; --shadow:none;
    --rag:#7aa5f5; --llm:#b699f7; --human:#f0a868; --stop:#f08a80;
    --hl:#e9eaee; --tick:#2c313c;
  }
}
:root[data-theme="dark"]{
  --paper:#0e1013; --surface:#161920; --ink:#e9eaee; --muted:#9a9ca6; --faint:#71737d;
  --rule:#242832; --rule-strong:#333846; --shadow:none;
  --rag:#7aa5f5; --llm:#b699f7; --human:#f0a868; --stop:#f08a80;
  --hl:#e9eaee; --tick:#2c313c;
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{
  margin:0; background:var(--paper); color:var(--ink);
  font:400 16px/1.6 Inter,"Segoe UI",system-ui,-apple-system,sans-serif;
  -webkit-font-smoothing:antialiased;
}
.mono,.step,.kind,.ep,.sec-n,.sec-range,.eyebrow,h1,.errs-l,.par-tag,.d-k{
  font-family:ui-monospace,"Cascadia Code","SF Mono",Menlo,Consolas,monospace;
}
.wrap{max-width:1220px;margin:0 auto;padding:0 40px}

/* ---------- encabezado ---------- */
.top{padding:64px 0 0}
.eyebrow{
  font-size:11px;letter-spacing:.2em;text-transform:uppercase;color:var(--faint);
  display:flex;justify-content:space-between;align-items:center;gap:24px;
}
h1{font-size:clamp(30px,4.4vw,46px);line-height:1.06;font-weight:600;letter-spacing:-.02em;margin:22px 0 0}
h1 em{font-style:normal;color:var(--muted)}
.lead{max-width:64ch;color:var(--muted);margin:18px 0 0;font-size:17px}
.theme{
  font:inherit;font-size:11px;letter-spacing:.14em;text-transform:uppercase;
  background:none;border:1px solid var(--rule-strong);color:var(--faint);
  padding:6px 12px;border-radius:99px;cursor:pointer;
}
.theme:hover{color:var(--ink);border-color:var(--ink)}

/* ---------- cinta de pasos (elemento firma) ---------- */
.ribbon{margin:44px 0 0;padding-bottom:56px;border-bottom:1px solid var(--rule)}
.ribbon-l{
  font-size:11px;letter-spacing:.18em;text-transform:uppercase;color:var(--faint);
  display:flex;justify-content:space-between;margin-bottom:12px;
  font-family:ui-monospace,"Cascadia Code","SF Mono",Menlo,Consolas,monospace;
}
.ticks{display:flex;gap:26px;align-items:flex-end}
.cluster{display:flex;gap:5px}
.tick{
  width:7px;height:26px;border:0;padding:0;border-radius:1px;background:var(--tick);
  cursor:pointer;transition:background .15s,transform .15s;transform-origin:bottom;
}
.tick:hover,.tick.on{background:var(--hl);transform:scaleY(1.28)}
.cluster[data-sec="2"] .tick{opacity:.86}

/* ---------- secciones ---------- */
.sec{padding:56px 0;border-bottom:1px solid var(--rule)}
.sec-h{display:grid;grid-template-columns:auto 1fr auto;align-items:baseline;gap:16px}
.sec-n{font-size:13px;color:var(--faint)}
.sec-h h2{font-size:26px;font-weight:600;letter-spacing:-.01em;margin:0}
.sec-range{font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--faint)}
.sec-sub{grid-column:2/4;margin:6px 0 0;color:var(--muted);max-width:70ch}

.flow{display:flex;flex-direction:column;gap:0;margin:34px 0 0}
.line{display:flex;align-items:stretch;flex-wrap:wrap;gap:10px}
.line.cont{margin-top:26px;position:relative}
.line.cont::before{
  content:"";position:absolute;left:26px;top:-22px;width:1px;height:14px;background:var(--rule-strong);
}
.line.cont::after{
  content:"";position:absolute;left:23px;top:-9px;
  border-top:6px solid var(--rule-strong);border-left:3.5px solid transparent;border-right:3.5px solid transparent;
}
.arrow{align-self:center;width:18px;height:1px;background:var(--rule-strong);position:relative;flex:none}
.arrow::after{
  content:"";position:absolute;right:0;top:-3px;
  border-left:6px solid var(--rule-strong);border-top:3.5px solid transparent;border-bottom:3.5px solid transparent;
}

.node{
  display:flex;flex-direction:column;gap:7px;align-items:flex-start;text-align:left;
  background:var(--surface);border:1px solid var(--rule);border-radius:11px;
  padding:14px 16px 15px;min-width:216px;max-width:246px;flex:1;
  box-shadow:var(--shadow);cursor:pointer;color:inherit;font:inherit;
  transition:border-color .14s,transform .14s;
}
.node:hover,.node:focus-visible{border-color:var(--ink);transform:translateY(-2px);outline:none}
.node.on{border-color:var(--ink)}
.node-top{display:flex;align-items:center;gap:10px}
.step{font-size:11px;color:var(--faint);letter-spacing:.04em}
.kind{
  font-size:9.5px;letter-spacing:.16em;text-transform:uppercase;color:var(--faint);
  border:1px solid var(--rule);border-radius:99px;padding:2px 7px;
}
.node-name{font-size:15.5px;font-weight:600;line-height:1.3}
.ep{font-size:12px;color:var(--muted);word-break:break-all}
.acc-rag{box-shadow:inset 3px 0 0 var(--rag),var(--shadow)}
.acc-llm{box-shadow:inset 3px 0 0 var(--llm),var(--shadow)}
.acc-human{box-shadow:inset 3px 0 0 var(--human),var(--shadow)}
.acc-rag .kind,.acc-rag .step{color:var(--rag)}
.acc-llm .kind,.acc-llm .step{color:var(--llm)}
.acc-human .kind,.acc-human .step{color:var(--human)}

.par{
  display:grid;grid-template-columns:repeat(var(--cols,2),minmax(0,1fr));gap:9px;
  border:1px dashed var(--rule-strong);border-radius:14px;padding:30px 12px 12px;
  position:relative;
}
.par .node{max-width:none;min-width:0}
@media (max-width:900px){.par{grid-template-columns:1fr}}
.par-tag{
  position:absolute;top:9px;left:14px;font-size:9.5px;letter-spacing:.16em;
  text-transform:uppercase;color:var(--faint);
}

.errs{display:flex;align-items:center;flex-wrap:wrap;gap:8px;margin:18px 0 0}
.errs-l{font-size:10px;letter-spacing:.16em;text-transform:uppercase;color:var(--faint);margin-right:4px}
.chip{
  font:inherit;font-size:12.5px;background:none;color:var(--stop);cursor:pointer;
  border:1px solid var(--rule-strong);border-radius:99px;padding:4px 12px;
}
.chip:hover{border-color:var(--stop)}
.sec-note{
  margin:30px 0 0;padding-left:16px;border-left:2px solid var(--rule-strong);
  color:var(--muted);max-width:82ch;font-size:15.5px;
}
.sec-note code{font-family:ui-monospace,monospace;font-size:13.5px;color:var(--ink)}

/* ---------- panel de detalle ---------- */
.panel{
  position:fixed;top:0;right:0;height:100%;width:400px;max-width:92vw;z-index:40;
  background:var(--surface);border-left:1px solid var(--rule);padding:34px 32px;
  overflow-y:auto;transform:translateX(101%);transition:transform .22s cubic-bezier(.4,0,.2,1);
}
.panel.open{transform:none;box-shadow:-24px 0 60px rgba(10,10,14,.14)}
.p-close{
  position:absolute;top:22px;right:24px;background:none;border:0;cursor:pointer;
  color:var(--faint);font-size:22px;line-height:1;padding:4px;
}
.p-close:hover{color:var(--ink)}
.p-top{display:flex;align-items:center;gap:10px;margin-bottom:14px}
.p-name{font-size:22px;font-weight:600;line-height:1.22;letter-spacing:-.01em;margin:0 0 16px}
.p-ep{
  display:block;font-size:13px;padding:9px 12px;border-radius:8px;
  background:var(--paper);border:1px solid var(--rule);color:var(--ink);margin-bottom:20px;
  font-family:ui-monospace,monospace;word-break:break-all;
}
.p-desc{color:var(--muted);margin:0 0 26px}
.p-desc code{font-family:ui-monospace,monospace;font-size:13.5px;color:var(--ink)}
.d-k{
  font-size:10px;letter-spacing:.16em;text-transform:uppercase;color:var(--faint);
  display:block;margin:0 0 8px;
}
.d-list{list-style:none;margin:0 0 22px;padding:0;display:flex;flex-direction:column;gap:6px}
.d-list button{
  font:inherit;font-size:14px;text-align:left;background:none;border:0;padding:0;
  color:var(--ink);cursor:pointer;border-bottom:1px solid transparent;
}
.d-list button:hover{border-bottom-color:var(--ink)}
.d-list .none{color:var(--faint);font-size:14px}
.scrim{
  position:fixed;inset:0;background:rgba(10,10,14,.28);opacity:0;pointer-events:none;
  transition:opacity .22s;z-index:30;
}
.scrim.on{opacity:1;pointer-events:auto}

footer{padding:44px 0 72px;color:var(--faint);font-size:13.5px}
footer code{font-family:ui-monospace,monospace}

@media (max-width:720px){
  .wrap{padding:0 22px}
  .panel{width:100%;top:auto;bottom:0;height:78vh;border-left:0;border-top:1px solid var(--rule);
    border-radius:18px 18px 0 0;transform:translateY(101%)}
  .panel.open{transform:none}
  .flow{flex-direction:column}
  .arrow{width:1px;height:16px;align-self:flex-start;margin-left:26px}
  .arrow::after{right:-3px;top:auto;bottom:0;border:0;border-top:6px solid var(--rule-strong);
    border-left:3.5px solid transparent;border-right:3.5px solid transparent}
}
@media (prefers-reduced-motion:reduce){*{transition:none!important;scroll-behavior:auto}}
@media print{
  .panel,.scrim,.theme{display:none}
  body{background:#fff;color:#000}
  .sec{break-inside:avoid}
}
</style>
</head>
<body>
<div class="wrap">

<header class="top">
  <div class="eyebrow">
    <span>CIRI · Continuous Improvement &amp; Risk Intelligence</span>
    <button class="theme" id="theme" type="button">Tema</button>
  </div>
  <h1>Agente de contracargos<br><em>el flujo, paso por paso</em></h1>
  <p class="lead">
    Orquestación explícita en n8n: __COUNT__ pasos que se ejecutan siempre en el mismo orden, más
    __EXTRA__ salidas de error. No hay un nodo de agente decidiendo qué herramienta llamar — por eso
    cada investigación es reproducible y auditable. Tocá cualquier paso para ver qué hace.
  </p>

  <div class="ribbon">
    <div class="ribbon-l"><span>Orden de ejecución</span><span>§1 → §4</span></div>
    <div class="ticks">__RIBBON__</div>
  </div>
</header>

__BODY__

<footer>
  Generado desde <code>n8n/workflow_ciri_agent.json</code> con <code>scripts/render_workflow_page.py</code>,
  así que no puede quedar desfasado del workflow real.
</footer>
</div>

<div class="scrim" id="scrim"></div>
<aside class="panel" id="panel" aria-live="polite">
  <button class="p-close" id="close" type="button" aria-label="Cerrar">×</button>
  <div class="p-top"><span class="step" id="p-step"></span><span class="kind" id="p-kind"></span></div>
  <h2 class="p-name" id="p-name"></h2>
  <code class="p-ep" id="p-ep"></code>
  <p class="p-desc" id="p-desc"></p>
  <span class="d-k">Recibe de</span><ul class="d-list" id="p-from"></ul>
  <span class="d-k">Continúa en</span><ul class="d-list" id="p-to"></ul>
</aside>

<script>
const DATA = __DATA__;
const $ = (s) => document.querySelector(s);
const panel = $("#panel"), scrim = $("#scrim");

const root = document.documentElement;
const stored = localStorage.getItem("ciri-theme");
if (stored) root.dataset.theme = stored;
$("#theme").addEventListener("click", () => {
  const dark = matchMedia("(prefers-color-scheme: dark)").matches;
  const now = root.dataset.theme || (dark ? "dark" : "light");
  root.dataset.theme = now === "dark" ? "light" : "dark";
  localStorage.setItem("ciri-theme", root.dataset.theme);
});

function mark(name) {
  document.querySelectorAll("[data-node]").forEach((el) =>
    el.classList.toggle("on", el.dataset.node === name)
  );
}

function link(names) {
  if (!names.length) return '<li class="none">—</li>';
  return names.map((n) => `<li><button data-jump="${n}">${n}</button></li>`).join("");
}

function open(name) {
  const d = DATA[name];
  if (!d) return;
  $("#p-step").textContent = d.step;
  $("#p-kind").textContent = d.kind;
  $("#p-name").textContent = name;
  $("#p-ep").textContent = d.endpoint || "—";
  $("#p-ep").style.display = d.endpoint ? "block" : "none";
  $("#p-desc").innerHTML = d.desc;
  $("#p-from").innerHTML = link(d.from);
  $("#p-to").innerHTML = link(d.to);
  panel.classList.add("open");
  scrim.classList.add("on");
  mark(name);
}

function close() {
  panel.classList.remove("open");
  scrim.classList.remove("on");
  mark(null);
}

document.addEventListener("click", (e) => {
  const node = e.target.closest("[data-node]");
  if (node) { open(node.dataset.node); return; }
  const jump = e.target.closest("[data-jump]");
  if (jump) {
    const name = jump.dataset.jump;
    open(name);
    const card = document.querySelector(`.node[data-node="${CSS.escape(name)}"]`);
    if (card) card.scrollIntoView({ block: "center" });
  }
});

document.querySelectorAll(".node, .chip").forEach((el) => {
  el.addEventListener("mouseenter", () => {
    const t = document.querySelector(`.tick[data-node="${CSS.escape(el.dataset.node)}"]`);
    if (t) t.classList.add("on");
  });
  el.addEventListener("mouseleave", () => mark(panel.classList.contains("open") ? $("#p-name").textContent : null));
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
    html = build(workflow)
    extra = int(html.split("__TOTAL__")[0].count("\0"))  # placeholder no usado
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    print(f"{OUT.relative_to(ROOT)}  {len(html) // 1024} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
