# Agente de Investigación de Contracargos

![Python](https://img.shields.io/badge/python-3.11+-blue)
![Tests](https://img.shields.io/badge/tests-463%20passed-brightgreen)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688)
![n8n](https://img.shields.io/badge/n8n-orchestrator-ff6d00)
![Claude](https://img.shields.io/badge/Claude-Haiku%20%2B%20Sonnet-blueviolet)
![Qdrant](https://img.shields.io/badge/Qdrant-vector%20DB-dc382c)
![Judge](https://img.shields.io/badge/Judge%20Score-9.1%2F10-gold)

Ante un contracargo, el agente reúne todo lo que se sabe del caso —la transacción, sus logs, las políticas que aplican, qué se resolvió en casos parecidos, el riesgo del comercio y el historial del cliente—, propone una resolución justificada y se autoevalúa. Los casos de riesgo alto frenan y esperan a un analista.

---

## Cómo usarlo

Hay **cuatro formas de usar el sistema**, y todas hacen lo mismo por dentro. Cambia por dónde entra el caso.

| | Forma | Para qué sirve | Qué necesitás |
|---|---|---|---|
| **1** | [Ver el circuito](#1-ver-el-circuito-sin-ejecutar-nada) | Entender qué hace el sistema sin ejecutarlo | Un navegador |
| **2** | [El panel web](#2-el-panel-web) | Verlo funcionar en 30 segundos, paso a paso | Un navegador |
| **3** | [El workflow de n8n](#3-el-workflow-de-n8n) | Ver la orquestación real, que es el entregable | Una instancia de n8n |
| **4** | [La API directa](#4-la-api-directa) | Integrarlo con otro sistema, o probar una pieza suelta | `curl` |

Ninguna requiere instalar nada ni configurar claves. Si preferís correr todo en tu máquina, está en [Todo local con Docker](#todo-local-con-docker).

### Dos modos, con un toggle en el panel

Investigar un caso cuesta dinero real —dos modelos, varias llamadas—. Evaluar una entrega no debería consumir la cuenta de nadie, así que el panel arranca en **modo demo** y el toggle cambia al otro:

| | **Modo demo** (por defecto) | **Modo producción** |
|---|---|---|
| **Qué casos** | Los 3 de ejemplo | Cualquier transacción del dataset |
| **Llama al modelo** | **No.** No es que intente y falle: no gasta | Sí, el pipeline completo |
| **Hace falta clave** | No | Sí — la del panel, o la del servidor si tiene |
| **Qué devuelve** | El informe ya generado, al instante | El análisis recién hecho |

**Si cargás tu API key, se usa la tuya y no la del servidor.** Esa es la forma de ver el sistema trabajando de verdad sobre cualquier caso, gastando de tu cuenta.

**Un caso sin análisis guardado recibe el más cercano en riesgo.** Si pedís `TXN-00004` y el modelo no está disponible, se responde con el ejemplo cuyo score antifraude está más cerca, y el cartel nombra las dos transacciones: *"pediste TXN-00004, esto es TXN-00051"*. El informe es entero del caso prestado — nunca los datos de una transacción con la resolución de otra.

Un informe prearmado nunca se hace pasar por uno recién hecho. Se declara en cuatro lugares: el cartel **DEMO (Caso prearmado)** que abre el HTML, la cabecera `X-Modo-Demo`, el uso que informa `cost_usd: 0.0`, y un warning en el log del servidor.

Esto vale también para el workflow de n8n: corre entero en modo demo, con las siete consultas de contexto reales y el informe generado de verdad. Lo único pregrabado es lo que hubiera contestado el modelo. El porqué y los trade-offs, en [`docs/decisions.md`](docs/decisions.md), decisión 14.

Lo que no cuesta nada funciona igual en los dos modos: transacciones, logs, búsqueda semántica de políticas y precedentes, riesgo del comercio, SLA e informes. El default del servidor se cambia con `CB_DEMO_MODE=false`.

> **Sobre la primera llamada:** la API está en el free tier de Render, que duerme tras 15 minutos sin uso. La primera petición puede tardar ~50 segundos en despertarla; las siguientes responden en ~12. El workflow de n8n ya contempla esto con un nodo que la despierta antes de empezar.

---

### 1. Ver el circuito, sin ejecutar nada

Abrí **[`docs/diagrams/n8n_workflow_analysis.html`](docs/diagrams/n8n_workflow_analysis.html)** en cualquier navegador.

Es el workflow entero en una página: los 29 pasos en orden de ejecución más las 3 salidas de error, con el endpoint que llama cada uno. Al tocar un paso se abre una ficha con qué hace, de dónde recibe y hacia dónde sigue. No necesita conexión ni instalar nada, y se imprime a PDF.

### 2. El panel web

**[ciri-chargeback-agent.onrender.com/panel](https://ciri-chargeback-agent.onrender.com/panel)**

Elegís un caso del dataset, apretás **Analizar** y ves el pipeline ejecutarse en vivo: cada consulta que hace, cuántas políticas recuperó, qué resolvió y qué puntaje le puso el Juez. Termina con el informe HTML completo.

Es la forma más rápida de ver el sistema funcionando de punta a punta. No es un entregable de la consigna: es una herramienta para poder probarlo sin montar nada.

El panel puede ejecutar de dos maneras: **Directo**, que corre el pipeline dentro de la API, o **a través de tu n8n**. Si elegís n8n hay que pegar la URL de tu instancia —este servidor no puede adivinar dónde corre—, y **si no responde te lo dice en vez de correr el pipeline directo por lo bajo**: un informe idéntico al real haciéndose pasar por una ejecución de la orquestación sería peor que un error.

Y al revés: cuando tu n8n llama a esta API, el panel lo confirma —*"tu n8n llegó hasta esta API hace 40 segundos"*—. Es la única forma de saber, desde tu lado, que el workflow importado llega. No guarda de dónde vino: la API es pública y compartida.

Tres casos que muestran comportamientos distintos:

| Caso | Qué tiene de particular | Cómo termina |
|---|---|---|
| `TXN-00051` | Cripto, score antifraude 8 | **BLOCKER** — rechazo automático, la operación es irreversible |
| `TXN-00042` | Tarjeta, score 4, cliente VIP | **HIGH** — frena y espera a un analista |
| `TXN-00089` | Comercio fuera de LATAM | **MEDIUM** — SLA extendido a 15 días hábiles |

### 3. El workflow de n8n

El entregable principal. Importá los tres archivos de [`n8n/`](n8n/) en cualquier instancia de n8n —Cloud, self-hosted o Desktop— y disparalo:

```bash
curl -X POST https://<tu-n8n>/webhook/chargeback-agent \
  -H "Content-Type: application/json" \
  -d '{"transaction_id": "TXN-00051", "motivo": "No reconoce la compra"}' \
  -o reporte.html
```

Devuelve el informe HTML listo. **No hay que configurar variables, credenciales ni API keys**: los nodos apuntan por defecto a la API pública, que es quien habla con Claude y con Qdrant.

| Archivo | Qué es |
|---|---|
| `workflow_ciri_agent.json` | El orquestador: 38 nodos, 32 ejecutables |
| `workflow_ciri_form.json` | Un formulario como segunda vía de entrada. Tiene su propio trigger y, al recibir un caso, **llama al webhook del orquestador**: por eso corre los 29 pasos igual |
| `workflow_ciri_errors.json` | Recibe los fallos de los otros dos y los registra |

Tres pasos manuales al importar, inevitables porque n8n reasigna identificadores al recibir un workflow:

1. **Activar los workflows.** n8n los importa desactivados siempre.
2. En el orquestador **y** en el del formulario: **Settings → Error Workflow → `workflow_ciri_errors`**. Sin eso, los fallos quedan sólo en la ejecución y no llegan al log de alertas.
3. **En el formulario, poner el Form Path.** Al importar desde la interfaz, n8n reemplaza el path del archivo por un identificador propio. Abrí el nodo **Form Trigger**, escribí `chargeback-form` en el campo **Form Path** y guardá. Ahí el formulario queda en `/form/chargeback-form`; si preferís el que generó n8n, la URL también está a la vista en ese mismo nodo.

Para apuntarlo a otra API, en orden de prioridad:

| | Cómo | Cuándo |
|---|---|---|
| 1 | `api_base_url` en el body del webhook | Override por request, funciona en cualquier n8n |
| 2 | Settings → Variables → `API_BASE_URL` | Requiere n8n con licencia: Variables es una feature paga |
| 3 | Por defecto: la API pública | Si no configurás nada |

### 4. La API directa

Todo lo que hace el workflow está disponible como endpoints. La referencia completa, con ejemplos que se pueden pegar en una terminal, está en **[`docs/api.md`](docs/api.md)**. Documentación interactiva en **[/docs](https://ciri-chargeback-agent.onrender.com/docs)**.

El camino más corto, una sola llamada que corre el pipeline completo y devuelve el informe:

```bash
curl -X POST "https://ciri-chargeback-agent.onrender.com/api/panel/analyze?direct=true" \
  -H "Content-Type: application/json" \
  -d '{"transaction_id": "TXN-00051", "motivo": "No reconoce la compra"}' \
  -o reporte.html
```

### Todo local con Docker

```bash
git clone https://github.com/federicomoroz/ciri-chargeback-agent.git
cd ciri-chargeback-agent
cp .env.example .env          # ver más abajo qué dos claves poner
docker-compose up -d
```

Hacen falta dos claves, ambas con free tier: [Anthropic](https://console.anthropic.com/settings/keys) para Claude y [Voyage AI](https://dash.voyageai.com/) para los embeddings. Langfuse es opcional, se activa con `CB_LANGFUSE_ENABLED=true`.

Levanta Qdrant, la API y n8n. Se inicializa solo: SQLite se carga desde el Excel y Qdrant se indexa en el primer arranque, sin paso de seed manual. El panel queda en `http://localhost:8000/panel` y n8n en `http://localhost:5678`.

Para que el workflow use tu API local en vez de la pública, mandá `api_base_url` en el body:

```bash
curl -X POST http://localhost:5678/webhook/chargeback-agent \
  -H "Content-Type: application/json" \
  -d '{"transaction_id": "TXN-00051", "motivo": "No reconoce la compra", "api_base_url": "http://api:8000"}' \
  -o reporte.html
```

---

---

## La API

Cada endpoint es una herramienta que el orquestador llama por su nombre. La referencia
completa, agrupada por para qué sirve cada uno y con ejemplos que se pegan en una terminal,
está en **[`docs/api.md`](docs/api.md)**.

Documentación interactiva generada por FastAPI: **[/docs](https://ciri-chargeback-agent.onrender.com/docs)**.

---

## Dónde está cada cosa

| Documento | Qué responde |
|---|---|
| [`docs/ejes.md`](docs/ejes.md) | Los 7 ejes de la consigna, uno por uno, con evidencia y cómo verificarla |
| [`docs/architecture.md`](docs/architecture.md) | Cómo está armado: el flujo de n8n, las capas, la estructura del repo y la suite de tests |
| [`docs/decisions.md`](docs/decisions.md) | 14 decisiones técnicas, cada una con su razonamiento y sus trade-offs |
| [`docs/prompts.md`](docs/prompts.md) | Los prompts, versionados, y por qué cambiaron |
| [`docs/rag_explanation.md`](docs/rag_explanation.md) | La estrategia RAG: qué se indexa, qué no, y cómo se arma cada consulta |
| [`docs/mejora_continua.md`](docs/mejora_continua.md) | El circuito de mejora: Juez, guardrails, feedback, auto-indexado |
| [`docs/demo_scenarios.md`](docs/demo_scenarios.md) | Los tres escenarios, paso a paso, con los comandos |
| [`docs/api.md`](docs/api.md) | Los 28 endpoints, agrupados por para qué sirven |
| [`docs/examples/`](docs/examples/) | Informes HTML ya generados, uno por escenario |

**Configuración:** todo se lee de `.env` con prefijo `CB_`. Las variables, con sus valores por
defecto y para qué sirve cada una, están comentadas en
[`.env.example`](.env.example).

**Tests:** `pytest tests/ -v`. Son 463 en 26 archivos; los de `unit/` e `integration/` corren
sin n8n ni Qdrant levantados. El desglose está en
[`docs/architecture.md`](docs/architecture.md#la-suite-de-tests).

---

## Autor

**Federico Palatnik Moroz**

Construido con **Claude Opus 4.6** (Anthropic) como asistente de desarrollo.
