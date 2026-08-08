# La API

31 endpoints. Cada uno es una herramienta que el orquestador llama por su nombre.

Base pública: `https://ciri-chargeback-agent.onrender.com` · Local: `http://localhost:8000`

Todos los ejemplos de este documento se pegan tal cual en una terminal y funcionan contra la
API pública, sin claves ni configuración.

> La API está en el free tier de Render y duerme tras 15 minutos sin uso. La primera llamada
> puede tardar ~50 segundos en despertarla.

## Modo demo

La instancia publicada corre en modo demo: **no llama al modelo**, para que probarla no consuma
la cuenta de nadie. Sólo afecta a los dos endpoints que gastan (`resolve` y `judge`, y el
pipeline que los usa). Todo el resto responde normalmente.

El campo `demo_mode` del body elige el modo por petición; si no viene, decide el servidor.

| Petición | Qué pasa |
|---|---|
| `demo_mode: true` · caso de ejemplo (`TXN-00051`, `TXN-00042`, `TXN-00089`) | Devuelve su informe ya generado. `X-Modo-Demo: true`, costo cero, y el HTML abre con el cartel **DEMO (Caso prearmado)** |
| `demo_mode: true` · cualquier otro caso | Devuelve el ejemplo **más cercano en riesgo**, con el cartel nombrando las dos transacciones |
| `demo_mode: false` · con `api_key` | Corre el pipeline completo **con esa clave**, que reemplaza a la del servidor |
| `demo_mode: false` · sin saldo | Devuelve el informe demo marcado, en vez de un error |
| `demo_mode: false` · clave inválida | `500` diciendo que la clave no sirve y cómo es una válida |

`GET /api/panel/demo-status` dice en qué modo arranca el servidor y qué casos tienen informe.
`GET /api/panel/server-key-status` dice si el servidor tiene clave propia — de eso depende que
el panel pueda correr sin que el visitante traiga la suya.

### `GET /api/panel/n8n-status`

Estado de la integración con n8n, y una señal que no se puede obtener de otro modo:

```json
{"configured": true, "available": true, "url": "https://tu.app.n8n.cloud",
 "ultimo_contacto_hace_s": 40.2, "contactos": 3}
```

Acepta `?n8n_base_url=` y, cuando viene, **chequea esa** y no la del servidor. Tiene que ser
la misma que va a usar el análisis: mirar una y llamar a la otra daba un badge en verde seguido
de «tu n8n no respondió», y la página se contradecía sin forma de ver por qué.

El panel usa esta respuesta para habilitar o no el pipeline con orquestación. **Y quien prueba
es la API**, no el navegador: al webhook lo llama ella, que puede estar en otra red. Con la API
en un contenedor, `http://localhost:5678` abre el editor en tu browser y desde el contenedor es
la API misma.

`ultimo_contacto_hace_s` dice cuánto hace que **una orquestación de n8n llamó a esta API**. El workflow marca su primera petición con la cabecera `X-Origen-n8n`; quien importó el workflow y lo disparó no tiene forma, desde su lado, de saber si la llamada llegó, y esto se lo confirma.

No registra de dónde vino: la API es pública y compartida, así que anotar la instancia de quien la prueba y mostrársela a otro sería filtrarla. Vive en memoria y se pierde al reiniciar — es una señal de *"recién pasó esto"*, no un registro histórico.

### Ejecutar a través de tu n8n

`POST /api/panel/analyze` acepta `?n8n_base_url=` para ejecutar contra tu instancia en vez del pipeline directo.

| | |
|---|---|
| Sin URL | `400` — el servidor no puede adivinar dónde corre tu n8n |
| Tu n8n no responde | `502` diciendo a qué dirección se llamó y qué revisar. Si la URL es local y la API no, lo explica: la llamada la hace ella |
| El caso necesita una persona | `303` al formulario de aprobación, que así se abre solo |

En los dos casos **no se ejecuta el pipeline directo en su lugar**. El informe sería idéntico al de una corrida real, y quien evalúa creería que pasó por los 39 nodos de orquestación sin haber pasado.

**El modo demo no cambia quién orquesta.** Es sobre plata, no sobre arquitectura: los nodos llaman a esta misma API, que resuelve el modelo del demo igual. Con un free tier configurado, `resolve` y `judge` corren de verdad con ese modelo y la resolución viaja con `demo_modelo` —el informe lo usa para declarar con qué se produjo—. Sin free tier responden con el análisis guardado, y cuando la resolución es la de otro caso, `/api/reports/html` responde con el informe completo de ese caso en vez de mezclar los dos.

```bash
# Modo demo: informe al instante, sin costo
curl -i -X POST "https://ciri-chargeback-agent.onrender.com/api/panel/analyze" \
  -H "Content-Type: application/json" \
  -d '{"transaction_id": "TXN-00051", "motivo": "No reconoce la compra"}' | grep -i x-modo-demo

# Con tu clave: el pipeline corre de verdad
curl -X POST "https://ciri-chargeback-agent.onrender.com/api/panel/analyze?direct=true" \
  -H "Content-Type: application/json" \
  -d '{"transaction_id": "TXN-00007", "motivo": "Cargo duplicado", "api_key": "sk-ant-..."}' \
  -o reporte.html
```

---

## Índice

| Quiero… | Ir a |
|---|---|
| Resolver un caso de punta a punta, en una llamada | [Investigar un caso](#investigar-un-caso) |
| Reproducir el pipeline paso a paso, como hace n8n | [Las siete herramientas](#las-siete-herramientas) |
| Que un modelo resuelva y otro lo evalúe | [Análisis con IA](#análisis-con-ia) |
| Editar una política sin tocar código ni redeployar | [Políticas](#políticas) |
| Convertir una resolución en un informe HTML | [Informes](#informes) |
| Cerrar el circuito con la decisión de un analista | [Feedback](#feedback) |
| Ver métricas, costos y alertas | [Observabilidad](#observabilidad) |

---

## Investigar un caso

Una sola llamada corre el pipeline completo —contexto, RAG, evaluación de políticas, síntesis,
guardrails, Juez— y devuelve el informe HTML.

### `POST /api/panel/analyze?direct=true`

```bash
curl -X POST "https://ciri-chargeback-agent.onrender.com/api/panel/analyze?direct=true" \
  -H "Content-Type: application/json" \
  -d '{"transaction_id": "TXN-00051", "motivo": "No reconoce la compra"}' \
  -o reporte.html
```

| Campo | Tipo | |
|---|---|---|
| `transaction_id` | string | Requerido. Formato `TXN-00000` |
| `motivo` | string | Requerido. Por qué el cliente desconoce el cargo |
| `cliente_vip` | boolean | Opcional. Cambia el SLA de 10 días a 5 |
| `api_key` | string | Opcional. Para usar tu propia clave de Anthropic en vez de la del servidor |

Responde `text/html`: el informe completo. El header `X-Usage-JSON` trae los tokens y el costo.

### `POST /api/panel/analyze-stream`

Lo mismo, pero emite eventos SSE con el progreso en tiempo real. Es lo que usa el panel para
mostrar cada paso mientras ocurre.

```bash
curl -N -X POST "https://ciri-chargeback-agent.onrender.com/api/panel/analyze-stream" \
  -H "Content-Type: application/json" \
  -d '{"transaction_id": "TXN-00051", "motivo": "No reconoce la compra"}'
```

Cada línea es `data: {"step": "...", ...}`. Los pasos, en orden: `start`, `cache_check`,
`transaction`, `logs`, `policies`, `cases`, `merchant_risk`, `client_flags`, `resolving`,
`resolved`, `judging`, `judged`, `done`. Un `error` puede reemplazar a cualquiera.

---

## Las siete herramientas

Lo que el orquestador consulta antes de pedirle una resolución al modelo. Cinco traen hechos
exactos de SQLite; dos hacen búsqueda semántica sobre Qdrant.

### `GET /api/transactions/{txn_id}` — la transacción

```bash
curl https://ciri-chargeback-agent.onrender.com/api/transactions/TXN-00051
```
```json
{"id": "TXN-00051", "client_id": "CLI-0003", "merchant": "Airbnb", "amount_usd": 2095.9,
 "date": "2024-09-23", "payment_method": "Cripto", "country": "COL", "channel": "POS",
 "fraud_score": 8, "status": "Contracargo iniciado"}
```

`404` si no existe. `GET /api/transactions` lista las 100 en formato compacto.

### `GET /api/logs/{tx_id}` — los eventos de esa transacción

Se traen completos: acá la similitud semántica no aporta nada. Incluye el conteo por severidad.

```bash
curl https://ciri-chargeback-agent.onrender.com/api/logs/TXN-00051
```

### `GET /api/policies/search` — qué políticas aplican · **RAG**

Búsqueda semántica sobre Qdrant. La consulta se arma de forma determinística y se **enriquece**
con reglas según el método de pago, el score y el país: una transacción con cripto suma
"irreversible", un país fuera de LATAM suma "plazo extendido".

```bash
curl "https://ciri-chargeback-agent.onrender.com/api/policies/search?motivo=fraude&payment_method=Cripto&fraud_score=8&country=COL"
```

La respuesta incluye `query_used`: **la consulta enriquecida que realmente se ejecutó**. Es lo
que hace auditable la búsqueda — se puede ver por qué recuperó lo que recuperó.

| Parámetro | |
|---|---|
| `motivo` | El motivo del contracargo |
| `channel` `payment_method` `country` | Contexto de la transacción |
| `fraud_score` | Entero 0–100 |

### `GET /api/cases/similar` — qué se hizo antes · **RAG**

Precedentes sobre los 60 casos históricos, con reordenamiento por método de pago y país.

```bash
curl "https://ciri-chargeback-agent.onrender.com/api/cases/similar?merchant=Airbnb&amount=2095.9&payment_method=Cripto&country=COL&fraud_score=8"
```

Devuelve el mismo sobre que la búsqueda de políticas: `query_used`, `results`,
`formatted_for_llm` y `count`.

### `GET /api/merchants/{name}/risk` — riesgo del comercio

Ratio de contracargos, volumen y señales. Los umbrales viven en `domain/constants.py`.

```bash
curl https://ciri-chargeback-agent.onrender.com/api/merchants/Airbnb/risk
```
```json
{"merchant": "Airbnb", "total_transactions": 4, "total_chargebacks": 3, "cb_ratio": 0.75,
 "total_volume_usd": 5521.08, "flags": ["suspended_merchant"], "is_strategic": false}
```

### `GET /api/clients/{client_id}/history` — historial del cliente

Reincidencia, países usados y métodos de pago, con las señales ya calculadas. `404` si el
cliente no existe.

### `POST /api/sla/check` — qué plazo aplica

Días **hábiles**, no corridos: 10 en LATAM, 15 fuera, 5 para clientes VIP.

```bash
curl -X POST https://ciri-chargeback-agent.onrender.com/api/sla/check \
  -H "Content-Type: application/json" \
  -d '{"case_open_date": "2024-09-23", "country": "COL", "cliente_vip": false}'
```
```json
{"within_sla": false, "days_elapsed": 682, "sla_limit_days": 10, "sla_type": "standard",
 "policy_reference": "POL-SLA-002 (resolucion estandar: 10 dias habiles)",
 "compensation_applicable": true}
```

**El reloj corre sobre el reclamo, no sobre la compra.** Con `transaction_id`, las fechas salen
del caso histórico: su apertura y, si está cerrado, su cierre. Sin caso registrado —53 de las 100
transacciones del dataset— **el plazo no se mide**:

```json
{"within_sla": null, "days_elapsed": null, "sla_limit_days": 10,
 "compensation_applicable": false, "sin_reclamo_registrado": true}
```

Entre la compra y el reclamo pueden pasar meses. Contarlos como plazo de resolución daba 489 días
de incumplimiento y USD 15 de compensación en un caso recién abierto, y el informe lo afirmaba al
lado del veredicto de la misma política diciendo que no correspondía. La compensación se paga
cuando **consta** que el plazo se incumplió, no cuando no consta nada.

---

## Análisis con IA

### `POST /api/analyze/resolve` — la resolución

Recibe todo el contexto reunido y devuelve la resolución. Por dentro hace dos llamadas: Haiku
evalúa cada política recuperada, Sonnet sintetiza.

**El código decide y el modelo explica.** La acción, el nivel de riesgo y la necesidad de
revisión humana los calcula Python a partir de los veredictos, y sobrescriben siempre lo que
proponga el modelo. Si el modelo contradice a la evidencia, la contradicción queda registrada
en `guardrail_warnings` en vez de corregirse en silencio.

Cuerpo: `transaction_id`, `tx_data`, `policies`, `similar_cases`, `logs`, `merchant_risk`,
`client_history`, `motivo`, `cliente_vip`.

```json
{
  "recommended_action": "REJECT",
  "risk_level": "BLOCKER",
  "confidence": 0.92,
  "justification": "…",
  "policy_verdicts": [{"policy_code": "POL-EXC-003", "verdict": "BLOCKER", "reasoning": "…"}],
  "precedent_summary": "CB-0008 [MOTIVO SIMILAR]: …",
  "requires_hitl": false,
  "guardrail_warnings": [],
  "trace_id": "…"
}
```

| Acción | Cuándo |
|---|---|
| `REJECT` | Hay un veredicto BLOCKER: la operación es irreversible |
| `PENDING_HITL` | Hay violaciones de política sin BLOCKER: decide un analista |
| `APPROVE` | Ninguna política se incumple |

### `POST /api/analyze/judge` — la autoevaluación

Un segundo modelo puntúa la resolución sobre cinco criterios, del 1 al 10. Por debajo de 7 el
caso se marca; a partir de 8 se convierte en precedente para los casos siguientes.

Cuerpo: `resolution` y `full_context`.

---

## Políticas

Las políticas son **datos, no código**: editarlas no requiere tocar el repositorio ni
redeployar. Cada cambio se reindexa en Qdrant al instante.

| | |
|---|---|
| `GET /api/policies/` | Las 17, desde SQLite |
| `GET /api/policies/{code}` | Una, por código |
| `POST /api/policies/` | Crear. El código debe matchear `POL-XXX-000` |
| `PUT /api/policies/{code}` | Actualizar. Acepta cambios parciales |
| `DELETE /api/policies/{code}` | Eliminar, también de Qdrant |

Cambiar un umbral y verlo aplicado en la próxima investigación:

```bash
curl -X PUT https://ciri-chargeback-agent.onrender.com/api/policies/POL-FRD-001 \
  -H "Content-Type: application/json" \
  -d '{"description": "El score mínimo aceptable pasa a ser 40."}'
```

---

## Configuración de modelos

El pipeline hace tres llamadas —evaluación de políticas, síntesis y juez— y cada una elige su
proveedor y su modelo por separado. La elección se guarda en SQLite y se aplica a la siguiente
investigación: no hay que reiniciar nada. El default vive en `constants.py`.

**Las claves no pasan por acá.** Se elige *qué* modelo, nunca *con qué credencial*: las claves
viajan por petición o salen del entorno.

### `GET /api/config/modelos`

Devuelve la configuración vigente por paso —con su descripción, el prompt que usa y si está
personalizado— más el catálogo de proveedores: cuáles tienen free tier, cuál tiene clave cargada,
y dónde se saca cada una.

```bash
curl -s https://ciri-chargeback-agent.onrender.com/api/config/modelos | jq '.pasos | keys'
# ["judge", "policy_eval", "resolution"]

curl -s .../api/config/modelos | jq '[.proveedores[] | select(.gratis) | .id]'
# ["groq", "gemini", "openrouter", "cerebras", "github"]
```

### `PUT /api/config/modelos/{paso}`

`paso` es `policy_eval`, `resolution` o `judge`. Cualquier otro da `422`, igual que un modelo vacío.

```bash
curl -X PUT .../api/config/modelos/judge   -H "Content-Type: application/json"   -d '{"proveedor": "groq", "modelo": "llama-3.3-70b-versatile"}'
```

Cambiar un paso no toca a los otros: se puede dejar la evaluación de políticas en Haiku y mover
sólo el juez.

### `POST /api/config/modelos/reset`

Borra lo guardado y vuelve al default. No lo pisa con valores: lo borra, así que el default sigue
siendo el de `constants.py` aunque cambie.

---

## Informes

### `POST /api/reports/html`

Renderiza el informe con Jinja2: nueve secciones y formulario de aprobación cuando el caso
requiere una persona. Devuelve `{"html": "..."}` y **guarda el resultado en caché**.

### `GET /api/cache/lookup`

Caché de idempotencia por `(transaction_id, cliente_vip)`. Si el caso ya se investigó,
devuelve el informe sin volver a pagar el modelo: **2 segundos en lugar de 113**.

```bash
curl "https://ciri-chargeback-agent.onrender.com/api/cache/lookup?transaction_id=TXN-00051"
```

Devuelve `{"cached": true, "html": "..."}` o `{"cached": false}`. Es exact-match a propósito;
el porqué está en [`decisions.md`](decisions.md), decisión 9.

---

## Feedback

### `POST /api/feedback/`

Cierra el circuito de mejora: registra qué decidió el analista y, cuando el caso salió
bien, lo convierte en precedente para las investigaciones siguientes.

Para que el caso se indexe en Qdrant hacen falta **las dos cosas**:

| | |
|---|---|
| `judge_score` ≥ 8.0 | El Juez aprobó la resolución |
| `resolution` presente | Sin el contenido de la resolución no hay precedente que indexar |

Registrar el feedback sin `resolution` es válido y se guarda igual — sólo que
`auto_indexed` vuelve en `false`.

```bash
curl -X POST https://ciri-chargeback-agent.onrender.com/api/feedback/ \
  -H "Content-Type: application/json" \
  -d '{"transaction_id": "TXN-00051", "analyst_decision": "APPROVE",
       "analyst_notes": "Confirmado con el comercio", "final_outcome": "Reembolso aprobado",
       "judge_score": 8.7, "motivo": "No reconoce la compra",
       "resolution": {"justification": "El comercio confirmó que no prestó el servicio."}}'
```

```json
{"status": "recorded", "feedback_id": 4, "auto_indexed": true,
 "needs_review": false, "judge_score": 8.7}
```

`auto_indexed: true` significa que `historical_cases` creció en uno y que el caso ya
puede aparecer como precedente. Se comprueba en `GET /health`.

---

## Observabilidad

| | |
|---|---|
| `GET /health` | Estado de SQLite y Qdrant, con el conteo de cada colección |
| `GET /api/analytics/dashboard` | Métricas agregadas del dataset y del feedback |
| `GET /api/langfuse/stats` | Trazas, tokens, costo y puntajes. Sin Langfuse devuelve lo medido en la API (`fuente: "local"`) en vez de nada |
| `POST /api/alerts/` | Registrar un evento operativo |
| `GET /api/alerts/` | Los más recientes, del más nuevo al más viejo |

Las alertas tienen dos severidades y tres orígenes. `ERROR`: fallas que llegan desde el workflow
de errores de n8n, y los rechazos automáticos por BLOCKER. `WARN`: entradas rechazadas en el
formulario, y los casos que quedan esperando a un analista. Separar las severidades es lo que
evita que un tipeo entierre una caída real.

**Las que nacen de una resolución se emiten donde la resolución nace**, no en la ruta que la
pidió: es el único punto por el que pasan los cuatro caminos —el webhook de n8n, el pipeline
directo, el panel y la llamada suelta—. Con la emisión en la capa de ruta, un caso resuelto por
el pipeline directo derivaba a una persona sin dejar rastro, y el mismo caso por n8n sí lo
dejaba. Que un HITL figure o no en el log según por dónde entró es un problema de auditoría.

```bash
curl https://ciri-chargeback-agent.onrender.com/health
```
```json
{"status": "healthy", "sqlite": "ok", "qdrant": "ok",
 "collections": {"policies": 17, "historical_cases": 60}}
```

---

## Autenticación

Sin configurar nada, la API es abierta: así se entrega, para que se pueda probar sin fricción.

Si se define `CB_ADMIN_API_KEY`, todos los `/api/*` pasan a exigir el header `X-API-Key`.
Quedan públicos `/health`, `/panel` y `/api/panel/*`, que son los que necesita un navegador.

## Errores

Toda respuesta lleva un header `X-Request-ID` que aparece también en los logs del servidor.

| Código | Qué pasó |
|---|---|
| `400` | Falta la clave de Anthropic y el servidor no tiene una propia |
| `401` | `X-API-Key` inválida o ausente, con autenticación activada |
| `404` | La transacción, el cliente o la política no existen |
| `422` | El cuerpo no valida contra el modelo. El detalle dice qué campo |
| `500` | Error interno. El `request_id` permite encontrarlo en los logs |
| `502` | Falló el proveedor del modelo — sin créditos, límite de rate |
