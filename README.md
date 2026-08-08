# Agente de Investigación de Contracargos

[![tests](https://github.com/federicomoroz/ciri-api-aux-sourcecode/actions/workflows/tests.yml/badge.svg)](https://github.com/federicomoroz/ciri-api-aux-sourcecode/actions/workflows/tests.yml)
![Python](https://img.shields.io/badge/python-3.11+-blue)
![Tests](https://img.shields.io/badge/tests-1039%20passed-brightgreen)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688)
![n8n](https://img.shields.io/badge/n8n-orchestrator-ff6d00)
![Claude](https://img.shields.io/badge/Claude-Haiku%20%2B%20Sonnet-blueviolet)
![Qdrant](https://img.shields.io/badge/Qdrant-vector%20DB-dc382c)
![Judge](https://img.shields.io/badge/Judge%20Score-9.1%2F10-gold)

Ante un contracargo, el agente reúne todo lo que se sabe del caso —la transacción, sus logs, las políticas que aplican, qué se resolvió en casos parecidos, el riesgo del comercio y el historial del cliente—, propone una resolución justificada y se autoevalúa. Los casos de riesgo alto frenan y esperan a un analista.

> **Sobre el 9.1:** es el promedio del Juez sobre las corridas de desarrollo con la configuración
> v3.0. Los tres informes que viajan en este paquete promedian 8.67 — son los tres casos más
> contenciosos del dataset, elegidos por cubrir los tres caminos del enrutador y no por su puntaje.
> Aquellas corridas no dejaron artefacto y hoy no son reproducibles sin saldo de API. El instrumento
> para volver a medir sí viaja: `python scripts/evaluar.py --n 20` corre la muestra, escribe el
> detalle caso por caso en `docs/evaluaciones/` y reporta el costo. El método completo está en
> [`docs/mejora_continua.md`](docs/mejora_continua.md#como-se-midio-el-91).

---

## Configuración

Para las cuatro formas de usarlo **no hay nada que configurar**. Lo demás sólo aparece
si vas más lejos:

| Hace falta si… | Qué | Dónde se pone |
|---|---|---|
| Sólo querés mirar el circuito, el panel o los informes | **Nada** | — |
| Querés correr en la configuración documentada (Claude) en vez del modelo gratuito | Una API key de Anthropic | Campo **API key** del panel, o `api_key` en el body |
| Querés ejecutar a través de tu propia instancia de n8n | Una URL **pública** de n8n — al webhook lo llama la API, no tu navegador | Campo **n8n URL** del panel. Si tu n8n es local, levantá el proyecto con Docker y usá su panel |
| Importaste el workflow del formulario | El path del formulario | Nodo **Form Trigger** → campo **Form Path** → `chargeback-form` |
| Importaste los workflows y querés que los fallos se registren | El error handler | **Settings → Error Workflow → `workflow_ciri_errors`** |
| Vas a correr todo en tu máquina con Docker | Dos claves con free tier | `.env`: `CB_ANTHROPIC_API_KEY` y `CB_VOYAGE_API_KEY` |
| Querés que el servidor ejecute siempre de verdad, sin modo demo | El interruptor del modo | `.env`: `CB_DEMO_MODE=false` |

Todas las variables, con sus valores por defecto y para qué sirve cada una, están
comentadas en [`.env.example`](.env.example).

> **Antes de la primera prueba:** la API está en el free tier de Render y duerme tras 15
> minutos sin uso. La primera llamada puede tardar ~50 segundos en despertarla; las
> siguientes responden en ~12.

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

**El sistema está hecho para Claude**: Haiku evalúa las políticas, Sonnet sintetiza y juzga, y los prompts están afinados para esa configuración. Pero investigar un caso cuesta dinero real, y evaluar una entrega no debería consumir la cuenta de nadie — así que el panel arranca en **modo demo**, que cae a un modelo con free tier, y el toggle cambia al otro:

| | **Modo demo** (por defecto) | **Modo producción** |
|---|---|---|
| **Qué modelo** | `gemini-flash-lite-latest` — free tier | **Claude**: Haiku para políticas, Sonnet para síntesis y juez |
| **Qué casos** | Cualquier transacción del dataset | Cualquier transacción del dataset |
| **Llama al modelo** | Sí: el pipeline corre entero, de verdad | Sí, el pipeline completo |
| **Hace falta clave** | No — usa la del servidor | Sí, la tuya de Anthropic, sólo mientras el panel esté abierto |
| **Qué devuelve** | Un análisis de ahora, con su desvío declarado | El análisis en la configuración documentada |

**El modo demo corre de verdad, no recita.** Cuesta lo mismo que no correr y devuelve un análisis de ahora en vez de una grabación de hace semanas.

**Lo que el modelo gratuito no da es la calidad de la configuración documentada, y el informe lo dice.** El juez corre en el mismo modelo que resolvió, así que uno más chico se penaliza dos veces: razona con menos profundidad y después se puntúa a sí mismo. Cada informe declara que la nota puede desviarse hasta **±2.5 puntos** y que para el mejor resultado va Anthropic en modo producción. El orden de magnitud es observado: el mismo `TXN-00051` que en desarrollo daba alrededor de 9 salió 6.8 con Flash Lite.

**Si cargás tu API key, se usa la tuya y no la del servidor.** Esa es la forma de ver el sistema en su configuración real.

**Si no hay free tier configurado, el modo demo recita en vez de correr**, y ahí sí sirve el análisis guardado de los tres casos de ejemplo. Un caso sin análisis guardado recibe el más cercano en riesgo: el cartel nombra las dos transacciones —*"pediste TXN-00004, esto es TXN-00051"*— y el informe es entero del caso prestado, nunca los datos de una transacción con la resolución de otra.

**Un informe siempre dice cómo se produjo**, y son dos carteles distintos:

| Cartel | Qué significa |
|---|---|
| **ANÁLISIS REAL (modelo gratuito)** | Corrió recién, con RAG, guardrails y juez. Nombra el modelo y declara el ±2.5 |
| **DEMO (Caso prearmado)** | Es el resultado guardado de una corrida anterior |

Además viaja en la cabecera (`X-Modelo-Gratuito` o `X-Modo-Demo`), en el uso (`cost_usd: 0.0`) y en un warning del log.

Esto vale también para el workflow de n8n: **el modo demo no cambia quién orquesta.** Es sobre plata, no sobre arquitectura — los nodos llaman a esta misma API, que resuelve el modelo del demo igual. El porqué y los trade-offs, en `docs/decisions.md`, decisión 14.

Lo que no cuesta nada funciona igual en los dos modos: transacciones, logs, búsqueda semántica de políticas y precedentes, riesgo del comercio, SLA e informes. El default del servidor se cambia con `CB_DEMO_MODE=false`.

---

### 1. Ver el circuito, sin ejecutar nada

Los cuatro archivos HTML numerados de esta carpeta. Se abren en cualquier navegador, sin conexión ni instalar nada. Se imprimen a PDF.

Están en orden de lectura: primero **qué** hace el circuito, después **cómo** se hablan las dos piezas.

**«el circuito completo»** — los 39 pasos en orden de ejecución más las 4 salidas de error, con el endpoint de cada uno. Al tocar un paso se abre una ficha con qué hace, de dónde recibe y hacia dónde sigue. Se genera del propio JSON del workflow, así que no puede quedar desfasado del flujo real.

**«n8n y la API»** — quién le pide qué a quién. Las catorce llamadas en orden, qué toca cada una (SQLite, Qdrant, el modelo) y las dos veces que la conversación va al revés. Es el resumen: se lee en un minuto.

**«el RAG»** — la cadena entera de recuperación, seguida con un caso real: qué se indexa y qué no, cómo el código arma la consulta, por qué las dos colecciones se buscan con criterios opuestos, cómo se formatea el contexto y por dónde el índice se escribe solo.

**«los tests»** — qué defecto concreto no puede volver. Las tres capas, la cobertura por paquete y los dieciséis errores reales que hoy tienen un test que los fija. Ninguno de los dieciséis rompía un import.

### 2. El panel web

**[ciri-chargeback-agent.onrender.com/panel](https://ciri-chargeback-agent.onrender.com/panel)**

Elegís un caso del dataset, apretás **Analizar** y ves el pipeline ejecutarse en vivo: cada consulta que hace, cuántas políticas recuperó, qué resolvió y qué puntaje le puso el Juez. Termina con el informe HTML completo.

Es la forma más rápida de ver el sistema funcionando de punta a punta. No es un entregable de la consigna: es una herramienta para poder probarlo sin montar nada.

El panel puede ejecutar de dos maneras: **Directo**, que corre el pipeline dentro de la API, o **a través de tu n8n**. El selector arranca con las opciones de n8n **deshabilitadas**, y el orden es este:

1. Pegás la URL de tu instancia en el campo **n8n URL**.
2. La API prueba si llega — no tu navegador, porque **al webhook lo llama ella**. Una URL local no le sirve aunque a vos te abra el editor.
3. Cuando responde, **recién ahí** se habilitan «n8n Test» y «n8n Production».

Si deja de responder mientras mirás, el modo se apaga solo y vuelve a Directo: el chequeo corre cada 30 segundos. **Nunca se cae al pipeline directo por lo bajo** — un informe idéntico al real haciéndose pasar por una ejecución de la orquestación sería peor que un error.

Y al revés: cuando tu n8n llama a esta API, el panel lo confirma —*"tu n8n llegó hasta esta API hace 40 segundos"*—. Es la única forma de saber, desde tu lado, que el workflow importado llega. No guarda de dónde vino: la API es pública y compartida.

Tres casos que muestran comportamientos distintos:

| Caso | Qué tiene de particular | Cómo termina |
|---|---|---|
| `TXN-00051` | Cripto, score antifraude 8 | **BLOCKER** — rechazo automático, la operación es irreversible |
| `TXN-00042` | Tarjeta, score 4, cliente VIP | **HIGH** — frena y espera a un analista |
| `TXN-00089` | Comercio fuera de LATAM, score 8 | **HIGH** — frena y espera a un analista; POL-EXC-004 le extiende el SLA a 15 días hábiles |

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
| `workflow_ciri_agent.json` | El orquestador: 45 nodos, 39 ejecutables |
| `workflow_ciri_form.json` | Un formulario como segunda vía de entrada. Tiene su propio trigger y, al recibir un caso, **llama al webhook del orquestador**: por eso corre los 39 pasos igual |
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

Todo lo que hace el workflow está disponible como endpoints. La referencia completa, con ejemplos que se pueden pegar en una terminal, está en **`docs/api.md`**. Documentación interactiva en **[/docs](https://ciri-chargeback-agent.onrender.com/docs)**.

El camino más corto, una sola llamada que corre el pipeline completo y devuelve el informe:

```bash
curl -X POST "https://ciri-chargeback-agent.onrender.com/api/panel/analyze?direct=true" \
  -H "Content-Type: application/json" \
  -d '{"transaction_id": "TXN-00051", "motivo": "No reconoce la compra"}' \
  -o reporte.html
```

### Todo local con Docker

```bash
git clone https://github.com/federicomoroz/ciri-api-aux-sourcecode.git
cd ciri-api-aux-sourcecode
cp .env.example .env          # dos claves, las de acá abajo
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

## La arquitectura, en corto

Cuatro piezas, con una división que se sostiene en todo el sistema:

```
n8n        el orquestador. Decide QUÉ se hace y CUÁNDO. Cada paso es un nodo
           visible; no hay nodo de agente eligiendo qué herramienta llamar.

FastAPI    la ejecución. CÓMO se hace. Cada endpoint es una herramienta con
           nombre; toda la lógica de negocio vive acá, ninguna en el canvas.

Qdrant     la verdad semántica. Políticas y casos históricos, para preguntar
           "¿qué aplica acá?" y "¿qué se hizo antes en algo parecido?".

SQLite     la verdad exacta. Transacciones, logs, historial del cliente, para
           preguntar "¿qué dice el registro de ESTA transacción?".
```

**Unir las dos fuentes es el punto.** Una investigación necesita los hechos exactos del caso *y* el contexto de lo que aplica: SQLite responde lo primero, el RAG sobre Qdrant lo segundo, y ambos entran juntos al contexto del modelo. Por eso la resolución puede citar la política concreta y el precedente concreto.

**El código decide, el modelo explica.** La acción recomendada, el nivel de riesgo y la necesidad de revisión humana los calcula Python a partir de los veredictos de política, y sobrescriben lo que proponga el modelo. El modelo escribe el razonamiento, no el veredicto. Si contradice a la evidencia, la contradicción queda registrada en vez de corregirse en silencio.

**Las políticas son datos, no código.** Viven como documentos en Qdrant y se editan por API: `PUT /api/policies/{code}` reindexa en el momento, sin deploy. Por eso quien las evalúa también es el modelo y no una función Python — si las reglas se pueden cambiar en caliente, su evaluación tiene que poder cambiar con ellas.

Y no sólo el texto: **lo que la política *hace* también es dato**. `puede_bloquear` decide si esa política puede frenar un caso sola, y `sla_dias` cuántos días hábiles concede. Cargar una política nueva que rechace automáticamente es un `POST`; cambiar el plazo de POL-SLA-002 es un `PUT`. Antes eso estaba en `constants.py` y editar la descripción no movía ni el plazo ni la capacidad de bloquear.

**Sin crédito de Anthropic también corre.** El modelo de cada paso —evaluación de políticas, síntesis, juez— se elige por separado desde el panel, en **Modelo por paso**, y se guarda sin reiniciar nada. Además de Anthropic hay cinco proveedores con free tier que hablan el protocolo de OpenAI: **Groq**, **Gemini**, **OpenRouter**, **Cerebras** y **GitHub Models**. Elegís uno, el campo de API key pasa a pedir *su* clave con el link a su consola, y el dataset entero se puede medir sin pagar.

> Un score medido con otro proveedor **no es** el score del sistema entregado: los prompts están afinados para Claude y la configuración documentada es Haiku + Sonnet. Sirve para verificar que el pipeline no depende del proveedor, y como punto de comparación.

> **La instancia pública tiene el CRUD abierto a propósito**, para que se pueda probar todo esto sin credenciales. No es el modo de producción: el middleware de autenticación está implementado (`api/app/main.py`, comparación en tiempo constante) y se activa con `CB_ADMIN_API_KEY`. En una fintech, un `DELETE /api/policies/POL-FRD-001` anónimo es un incidente; acá es una decisión de evaluación, y conviene que quede dicha.

El detalle —las capas, el flujo de datos, la escalabilidad y las decisiones— está en `docs/architecture.md`.

---

## La API

Cada endpoint es una herramienta que el orquestador llama por su nombre. La referencia
completa, agrupada por para qué sirve cada uno y con ejemplos que se pegan en una terminal,
está en **`docs/api.md`**.

Documentación interactiva generada por FastAPI: **[/docs](https://ciri-chargeback-agent.onrender.com/docs)**.

---

## Dónde está cada cosa

La documentación completa viaja en la entrega, en la carpeta `docs/` — este repositorio tiene
sólo el código que la API necesita para funcionar.

| Documento | Qué responde |
|---|---|
| `docs/ejes.md` | Los 7 ejes de la consigna, uno por uno, con evidencia y cómo verificarla |
| `docs/architecture.md` | Cómo está armado: el flujo de n8n, las capas, la estructura del repo y la suite de tests |
| `docs/decisions.md` | 22 decisiones técnicas, cada una con su razonamiento y sus trade-offs |
| `docs/prompts.md` | Los prompts, versionados, y por qué cambiaron |
| `docs/rag_explanation.md` | La estrategia RAG: qué se indexa, qué no, y cómo se arma cada consulta |
| `docs/mejora_continua.md` | El circuito de mejora: Juez, guardrails, feedback, auto-indexado |
| `docs/demo_scenarios.md` | Los tres escenarios, paso a paso, con los comandos |
| `docs/api.md` | Los 31 endpoints, agrupados por para qué sirven |
| `docs/HTML_Output_Examples/` | Informes HTML ya generados, uno por escenario |

### Tests y cobertura

```bash
pytest tests/unit tests/integration -q --cov=api/app --cov-fail-under=85
```

**1039 tests** en 38 archivos y **92% de cobertura** sobre `api/app` — el número que
reporta el CI sobre un checkout limpio, que es el reproducible: medido con un `.env` cargado
sube unas décimas, porque se ejecutan ramas que sin configuración no corren. Los de `unit/` e `integration/` corren sin n8n ni Qdrant levantados; los 33 de
`e2e/` llaman a la API publicada y al modelo, así que quedan fuera del CI a propósito —un test
que falla porque Render está dormido no informa nada.

Cada push corre tres pasos: **lint**, **tests con piso de cobertura del 85%**, y una
**verificación del cableado de los workflows de n8n** —que ningún Wait sea un sleep, que
ningún nodo HTTP prometa reintentos que no tiene, que los Stop and Error tengan a dónde
derivar—. Nada de eso rompe al importar el workflow, y todo eso rompe en producción.

Lo que distingue a esta suite es que **53 de esos tests no verifican código**: que los números
del README sean los reales, que los informes que viajan en el paquete se abran sin internet,
que el workflow de n8n esté cableado y que ningún camino conteste `200` vacío. El diagrama **«los tests»** los recorre uno
por uno, con el defecto concreto que cada uno fija.

---

## Autor

**Federico Palatnik Moroz**

Desarrollado con **Claude Opus 5** (Anthropic) como asistente de desarrollo.
