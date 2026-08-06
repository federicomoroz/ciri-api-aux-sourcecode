# Entregables — Agente Inteligente de Contracargos

Mapa de lo que pide la consigna y dónde está cada cosa.

| Entregable pedido | Archivo |
|---|---|
| Flujo exportado de n8n | `n8n/workflow_ciri_agent.json` (+ `workflow_ciri_errors.json`, `workflow_ciri_form.json`) |
| README con explicación de arquitectura | `Documentacion/README.md` |
| Diagrama de la solución | `Documentacion/architecture.md` (Mermaid) y `Documentacion/workflow_diagram.html` |
| Prompts documentados | `Documentacion/prompts.md` |
| Explicación del RAG (documento separado) | `Documentacion/rag_explanation.md` |
| Explicación del proceso de mejora continua | `Documentacion/mejora_continua.md` |
| HTML mostrando resultados | `Reports Examples/` (3 casos: BLOCKER, HIGH con HITL, MEDIUM) |

Extras, no pedidos por la consigna:

| Qué | Dónde |
|---|---|
| Decisiones técnicas con trade-offs | `Documentacion/decisions.md` |
| Escenarios demo reproducibles | `Documentacion/demo_scenarios.md` |
| Panel interactivo de testing | `Panel de Testing — CIRI.url` |

## Cómo probarlo en 2 minutos

Importá `n8n/workflow_ciri_agent.json` en cualquier instancia de n8n y disparalo:

```bash
curl -X POST https://<tu-n8n>/webhook/chargeback-agent \
  -H "Content-Type: application/json" \
  -d '{"transaction_id": "TXN-00051", "motivo": "No reconoce la compra"}' \
  -o reporte.html
```

No hay que configurar variables, credenciales ni API keys: los nodos apuntan por defecto a la
API pública del proyecto. Los detalles y las otras dos formas de probarlo (panel web y Docker
Compose local) están en `Documentacion/README.md`.

> Esta carpeta es un empaquetado del repositorio. Se regenera con
> `python scripts/sync_entregables.py`, así que no debería divergir de las fuentes.
