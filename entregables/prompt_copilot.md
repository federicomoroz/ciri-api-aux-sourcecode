# Prompt para Copilot — copiar TODO desde la línea de abajo

---

Generame una infografía de 1080×1350 píxeles (portrait, 4:5) para LinkedIn. Fondo oscuro tech (#07070b). Colores: verde #34d399, celeste #38bdf8, indigo #818cf8, ámbar #fbbf24. Tipografía legible, monospace para términos técnicos. Sin ilustraciones 3D. Todo en español.

HEADER:
- Badge: "AI AUTOMATION"
- Título grande: "Agente de Investigación de Contracargos"
- Subtítulo: "Decisiones determinísticas. IA como explicación, no como decisión."

STATS (4 tarjetas en fila horizontal):
- 54 nodos n8n
- 277 tests
- 9.1/10 Judge score
- ~$0.04 USD/caso

PIPELINE DE INVESTIGACIÓN (sección principal, timeline vertical con 4 etapas numeradas):

01 ENTRADA (verde): "Un contracargo llega por webhook. Se valida y se consulta cache de idempotencia." Nodos: Webhook → Validar TXN → Cache → ¿Nuevo?

02 CONTEXTO (celeste): "6 fuentes de datos en paralelo para armar el expediente completo." Grilla 3×2: Transacción (SQLite) | Logs (SQLite) | Políticas (RAG Qdrant) | Casos similares (RAG Qdrant) | Comercio (SQLite) | SLA (Cálculo)

03 ANÁLISIS IA (indigo): "IA evalúa en dos fases. 5 guardrails determinísticos validan entre ambas." Flujo: Policy Eval [Haiku] → 5 Guardrails → Resolución [Sonnet] → Juez [Sonnet]. Nota: "Langfuse traza cada llamada: tokens, latencia, costo"

04 DECISIÓN + REPORTE (ámbar): "Python decide con reglas determinísticas. La IA no decide, solo explica." 4 niveles: BLOCKER→Auto-rechazo | HIGH→Revisión humana | MEDIUM→Reporte | LOW→Reporte. "Judge ≥ 8.0 → caso auto-indexado como precedente en Qdrant"

SIDEBAR junto al pipeline (4 tarjetas verticales):
- Qdrant: Vector DB, colecciones: policies, cases, cache
- SQLite: transactions, logs, cases
- Langfuse: trazas LLM, costos, latencia
- Voyage AI: embeddings multilingüe, 1024 dims

SECCIÓN "EL CÓDIGO DECIDE, LA IA EXPLICA" (dos columnas):
Izquierda verde — Python decide (6/11): recommended_action, risk_level, requires_hitl, hitl_reason, policy_verdicts, precedent_summary
Derecha indigo — LLM genera (5/11): reasoning, summary, confidence, compensation, observations
Callout ámbar: "BLOCKER + APPROVE = alucinación detectada → auto-corregido a REJECT"

MINI PREVIEW DEL PANEL (rectángulo estilo app, fondo claro):
TXN-00051 | Cripto · score 8 | RECHAZADO AUTOMÁTICO (rojo) | Riesgo: BLOCKER | Confianza: 95% | Judge: 9.4/10 | Costo: $0.03 | Tiempo: 11.8s
"SSE streaming · 3 modos de pipeline · BYOK (tu propia API key)"

STACK (badges en fila): n8n · FastAPI · Qdrant · SQLite · Claude Haiku+Sonnet · Voyage AI · Langfuse · Docker

FOOTER:
Izquierda: "¿Te interesa? Escribime por privado." + "Acceso al repositorio y demo en vivo."
Derecha: "Federico Palatnik Moroz"

REGLAS: No inventes datos. No agregues stats que no estén acá. No cambies nombres de etapas. Son 4 etapas, no 6. No pongas "CIRI" ni links a GitHub. No es una venta de producto. Tipografía suficientemente grande para leer en celular.
