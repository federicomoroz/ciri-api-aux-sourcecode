# Prompt para ChatGPT — Infografía de LinkedIn

Copiá todo lo que está debajo de la línea y pegalo en ChatGPT.

---

Generame una infografía profesional de 1080×1350 píxeles (formato 4:5 portrait para LinkedIn).

## QUÉ ES

Es una imagen para mi portfolio de LinkedIn que muestra un proyecto que construí: un **agente de IA que investiga contracargos bancarios**. NO es una venta de producto. Es una pieza de portfolio para mostrar capacidad técnica.

## ESTÉTICA

- Fondo oscuro (#07070b o similar dark tech)
- Acentos de color: verde (#34d399), celeste (#38bdf8), indigo (#818cf8), ámbar (#fbbf24)
- Tipografía monospace para términos técnicos
- Estilo premium, limpio, denso en información
- Similar a infografías tech de roadmaps que se ven en LinkedIn

## ESTRUCTURA Y CONTENIDO EXACTO (no inventes nada, usá solo estos datos)

### HEADER
- Badge superior: "PORTFOLIO · AI AUTOMATION"
- Título: "Agente de Investigación de Contracargos"
- Subtítulo: "Decisiones determinísticas. IA como explicación, no como decisión. Trazabilidad completa."

### STATS (4 tarjetas en fila)
Estos son los datos REALES del proyecto. No los cambies ni inventes otros:
- **54** — nodos n8n
- **277** — tests
- **9.1/10** — Judge score (puntuación de calidad por LLM-as-Judge)
- **~$0.04** — USD por caso

### PIPELINE DE INVESTIGACIÓN (sección principal, la más grande)

El pipeline tiene exactamente 4 etapas. Mostralas como un flujo vertical tipo timeline con números circulares y flechas:

**01 ENTRADA** (color verde)
- Descripción: "Un contracargo llega por webhook. Se valida el formato y se consulta un cache de idempotencia para no reprocesar casos ya resueltos."
- Nodos: Webhook → Validar TXN → Cache lookup → ¿Nuevo?

**02 CONTEXTO** (color celeste)
- Descripción: "Se arma el expediente completo consultando 6 fuentes de datos en paralelo."
- Las 6 fuentes (mostrar como grilla 3×2):
  - Transacción (SQLite)
  - Logs (SQLite)
  - Políticas aplicables (RAG — Qdrant)
  - Casos similares (RAG — Qdrant)
  - Perfil del comercio (SQLite)
  - Plazo SLA (cálculo)

**03 ANÁLISIS IA** (color indigo)
- Descripción: "La IA analiza el expediente en dos fases. Entre ambas, 5 guardrails determinísticos validan que no haya errores ni alucinaciones."
- Flujo: Policy Eval [Haiku] → 5 Guardrails → Resolución [Sonnet] → Juez de Calidad [Sonnet]
- Nota: "Langfuse traza cada llamada al LLM: tokens, latencia, costo, versión de prompt"

**04 DECISIÓN + REPORTE** (color ámbar)
- Descripción: "Código Python determinístico elige la acción según nivel de riesgo. La IA NO decide — solo explica por qué."
- 4 niveles de riesgo:
  - BLOCKER → Auto-rechazo (rojo)
  - HIGH → Revisión humana (ámbar)
  - MEDIUM → Reporte (celeste)
  - LOW → Reporte (verde)
- Feedback loop: "Judge ≥ 8.0 → caso auto-indexado como precedente en Qdrant"

### SIDEBAR DE INFRAESTRUCTURA (al lado del pipeline)

4 tarjetas verticales:
- **Qdrant** — Vector DB, 3 colecciones: policies, cases, cache
- **SQLite** — Datos estructurados: transactions, logs, cases
- **Langfuse** — Observabilidad LLM: trazas, costos, latencia
- **Voyage AI** — Embeddings multilingüe, 1024 dimensiones

### SECCIÓN "EL CÓDIGO DECIDE, LA IA EXPLICA"

Dos columnas lado a lado:

Columna izquierda (verde) — **Python decide (6 de 11 campos)**:
- recommended_action
- risk_level
- requires_hitl
- hitl_reason
- policy_verdicts
- precedent_summary

Columna derecha (indigo) — **LLM genera (5 de 11 campos)**:
- reasoning
- summary
- confidence
- compensation
- observations

Callout destacado (ámbar):
"BLOCKER + APPROVE = alucinación detectada → auto-corregido a REJECT"

### STACK TECNOLÓGICO (badges/pills en una fila)
n8n · FastAPI · Qdrant · SQLite · Claude Haiku + Sonnet · Voyage AI · Langfuse · Docker

### FOOTER
- Izquierda: "¿Te interesa? Escribime por privado." + "Acceso al repositorio y demo en vivo."
- Derecha: "Federico Palatnik Moroz" + "Construido con Claude Opus 4.6 como asistente de desarrollo"

## REGLAS ESTRICTAS

1. **NO inventes datos ni estadísticas**. Solo usá los números que te doy arriba. No agregues "+25% recuperación", "24/7", ni nada que no esté en este brief.
2. **NO es una venta de producto**. Es portfolio. No pongas "¿Te interesa implementarlo?" ni "Automatiza. Recupera. Escala." ni nada de marketing.
3. **NO pongas links a GitHub** (el repo es privado).
4. **NO pongas el nombre "CIRI"** en ningún lado.
5. **NO cambies los nombres de las etapas del pipeline**. Son exactamente: ENTRADA, CONTEXTO, ANÁLISIS IA, DECISIÓN + REPORTE. No las renombres a "Detección", "Preparación", "Envío", etc.
6. **El pipeline tiene 4 etapas, no 6**. No agregues etapas.
7. Los modelos de IA usados son **Claude Haiku** (evaluación de políticas) y **Claude Sonnet** (resolución + juez). No pongas GPT ni otros modelos.
8. La imagen debe ser exactamente **1080×1350 píxeles**.
9. Todo el texto en **español**.
10. **No pongas ilustraciones 3D** tipo escudos flotantes. Prefiero íconos simples o sin íconos.
