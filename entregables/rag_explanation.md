# Estrategia RAG — Agente de Contracargos CIRI

Este documento explica la estrategia de Retrieval-Augmented Generation (RAG) del agente: que se indexa en Qdrant, que no se indexa y por que, como funciona la recuperacion semantica, y como el vector store se mantiene actualizado mientras el sistema opera.

---

## Restricciones de infraestructura

| Componente | Tier | Limite |
|---|---|---|
| **Qdrant Cloud** | Free tier | 1 GB de almacenamiento vectorial |
| **Voyage AI** | Free tier | Embeddings sin costo (con rate limits) |
| **SQLite** | Local / en contenedor | Sin limite practico para el volumen del dataset |

Estas restricciones guiaron decisiones clave: corpus pequenos (17 politicas, ~60 casos), embeddings de 1024 dimensiones (no 1536), y cache semantico para reducir llamadas a la API de embeddings.

---

## Que se indexa (y por que)

### 1. Politicas — coleccion `policies`

**Por que:** Las politicas son documentos en lenguaje natural. Determinar si una politica aplica a una transaccion requiere comprension semantica, no coincidencia exacta de strings. "Este metodo de pago es irreversible" es semanticamente relevante para una transaccion Cripto aunque ni el registro de la transaccion ni la consulta contengan la palabra exacta "irreversible".

**Que:** 17+ documentos de politicas (4 FRAUDE, 5 CHARGEBACK, 4 SLA, 4 EXCEPCION) convertidos a formato Markdown antes de embeber:

```markdown
# POL-EXC-003
**Categoria:** EXCEPCION
**Politica:** Exclusion de criptomonedas
## Descripcion
Las transacciones realizadas con criptomonedas son irreversibles por naturaleza...
**Referencia:** Reg. Fintech 2024/03
```

El formato Markdown preserva jerarquia y enfasis, que el modelo de embeddings codifica en la representacion vectorial.

**Estrategia de Point ID:** UUID5 deterministico: `uuid5(NAMESPACE_DNS, policy_code)`. El mismo codigo siempre produce el mismo ID de punto. Esto hace que `upsert` sea idempotente: re-indexar una politica editada no crea duplicados.

### 2. Casos historicos — coleccion `historical_cases`

**Por que:** Los casos resueltos en el pasado proporcionan precedentes. Cuando llega un nuevo contracargo con tarjeta Visa en Argentina y un fraud score medio, encontrar 5 casos con comercio, metodo de pago y perfil de fraude similares ayuda al LLM a razonar sobre resultados probables sin alucinar.

**Que:** 60+ casos historicos, cada uno codificado como texto de busqueda que combina atributos del caso y contexto de la transaccion:

```
Caso CASO-00042: contracargo por cargo no reconocido
Resolucion: APPROVED
en TechStore AR, Credito Visa, ARG,
USD 234.50, score antifraude 4
Observaciones: Cliente VIP, primer contracargo en 18 meses
```

El contexto transaccional (comercio, monto, metodo de pago, pais, fraud_score) se embebe junto con el resultado del caso. Esto permite al retriever encontrar casos similares no solo en descripcion sino en perfil financiero.

**Crecimiento automatico:** Se indexan nuevos casos cada vez que `POST /api/feedback/` recibe un `judge_score >= 8.0`. La coleccion comienza con ~60 casos y crece con el tiempo a medida que el agente procesa mas contracargos.

### 3. Cache semantico — coleccion `_semantic_cache`

**Por que:** Dos transacciones con perfiles casi identicos (mismo comercio, monto similar, mismo metodo de pago, mismo pais, fraud score similar) produciran solicitudes de resolucion casi identicas. Ejecutar el pipeline LLM completo dos veces desperdicia tokens y agrega latencia.

**Que:** Respuestas de resolucion cacheadas, indexadas por el query string de la solicitud de resolucion. Umbral para cache hit: similitud coseno >= 0.92 (solo consultas casi identicas, no simplemente relacionadas semanticamente).

**Formato:**
```json
{
  "query": "Contracargo en TechStore AR por USD 234.50, Credito Visa, ARG, score 4",
  "response": { "/* JSON de Resolution completo */" }
}
```

---

## Que NO se indexa (y por que)

### Transacciones

**No estan en Qdrant.** Las transacciones se recuperan por ID exacto (`GET /api/transactions/{id}`) o se filtran por campos estructurados (rango de monto, fecha, metodo de pago). Son consultas estructuradas sin ambiguedad semantica. SQLite las maneja eficientemente con columnas indexadas.

Indexar 100 transacciones en Qdrant seria un desperdicio: "TXN-00051" no es semanticamente similar a nada — se busca por su ID exacto.

### Logs de eventos

**No estan en Qdrant.** Los logs son registros estructurados con `{timestamp, severity, event, service, code, detail}`. Se recuperan via `GET /api/logs/{tx_id}` (match exacto por transaction_id). El analisis de logs es **deterministico** — `Analyzer.detect_error_patterns()` extrae patrones nombrados (SYSTEMATIC_MERCHANT_TIMEOUT, CONNECTIVITY_ISSUE, etc.) y `Analyzer.count_severities()` produce conteos de severidad. Un resumen de los logs criticos se pasa al prompt `v1_resolution` como contexto. No hay necesidad de pre-indexar logs para busqueda por similitud.

Indexar logs en Qdrant complicaria el pipeline sin beneficio: siempre se recuperan logs para una transaccion especifica, nunca se pregunta "encontrar logs similares a este log".

### Registros de feedback

**No estan en Qdrant.** El feedback del analista es dato estructurado (transaction_id, decision, notas, judge_score, timestamp). Se almacena en SQLite para analitica y auditoria. El _resultado_ del feedback de alta calidad (judge_score >= 8.0) genera un nuevo caso indexado en `historical_cases`, pero el registro de feedback en si no se indexa.

---

## Especificacion detallada de colecciones Qdrant

### Coleccion: `policies`

| Parametro | Valor |
|---|---|
| Tamano del vector | 1024 |
| Metrica de distancia | Coseno |
| Puntos totales | 17 (estatico, crece solo via POST /api/policies/) |
| Recuperacion `top_k` | 17 |
| Recuperacion `score_threshold` | 0.0 |
| Proposito | Retornar TODAS las politicas; el LLM filtra relevancia |

**Esquema del payload por punto:**

```json
{
  "code": "POL-EXC-003",
  "name": "Exclusion de criptomonedas",
  "category": "EXCEPCION",
  "description": "Las transacciones con criptomonedas...",
  "reference": "Reg. Fintech 2024/03",
  "markdown": "# POL-EXC-003\n**Categoria:** ..."
}
```

**Por que threshold=0.0:** El corpus es pequeno (17 documentos). Filtrar por score de similitud arriesga excluir una politica altamente relevante que usa vocabulario diferente. El LLM es mas confiable que un umbral de coseno para determinar la aplicabilidad de una politica — puede leer la descripcion y decidir "NOT_APPLICABLE" con razonamiento.

### Coleccion: `historical_cases`

| Parametro | Valor |
|---|---|
| Tamano del vector | 1024 |
| Metrica de distancia | Coseno |
| Puntos iniciales | 60+ |
| Recuperacion `top_k` | 5 |
| Recuperacion `score_threshold` | 0.40 |
| Proposito | Recuperacion de precedentes para contexto de resolucion |

**Esquema del payload por punto:**

```json
{
  "case_id": "CASO-00042",
  "transaction_id": "TXN-00042",
  "motivo": "Cargo no reconocido",
  "resolution": "APPROVED",
  "resolution_days": 7,
  "analyst": "ana.garcia",
  "observations": "Cliente VIP primer contracargo",
  "merchant": "TechStore AR",
  "amount_usd": 234.50,
  "payment_method": "Credito Visa",
  "country": "ARG",
  "fraud_score": 4,
  "_text": "Caso CASO-00042: contracargo por cargo no reconocido..."
}
```

**Por que threshold=0.40:** Para precedentes, solo los matches semanticamente significativos deben incluirse. Un caso con 38% de similitud (ej. diferente pais, diferente metodo de pago) seria un precedente pobre y podria desviar al LLM. El umbral filtra coincidencias debiles mientras captura casos que comparten atributos clave del perfil financiero.

**Indice de payload:** Se crea un indice `KEYWORD` sobre el campo `payment_method` para acelerar los filtros `should` de Qdrant en busquedas de casos similares.

### Coleccion: `_semantic_cache`

| Parametro | Valor |
|---|---|
| Tamano del vector | 1024 |
| Metrica de distancia | Coseno |
| Puntos totales | Crece sin limite |
| Recuperacion `top_k` | 1 |
| Recuperacion `score_threshold` | 0.92 |
| Proposito | Evitar llamadas LLM redundantes para casos casi identicos |

**Por que threshold=0.92:** El cache solo debe devolver resultados cuando la nueva solicitud es casi identica a una cacheada. Un umbral menor retornaria resoluciones cacheadas para casos que difieren en monto, fraud score o pais — diferencias que cambian significativamente la resolucion. A 0.92, solo solicitudes con el mismo comercio, monto muy similar, mismo metodo de pago y mismo pais producen un cache hit.

---

## Modelo de embeddings

**Modelo:** `voyage-multilingual-2` (API de Voyage AI)

**Dimensiones:** 1024

**Por que este modelo:**

| Criterio | Justificacion |
|---|---|
| Multilingue | El dataset, las politicas y los logs estan en espanol. El modelo maneja espanol de forma nativa sin overhead de traduccion. Top-3 en benchmarks MTEB para espanol multilingue. |
| Alta calidad (1024 dims) | Similitud semantica superior para deteccion de parafrasis. "Cargo no reconocido" y "compra desconocida" producen embeddings altamente similares. |
| Entrenado para parafrasis | Entrenado explicitamente para similitud semantica, no solo coincidencia de palabras clave. |
| Free tier | Voyage AI ofrece un tier gratuito generoso — sin costo para desarrollo y testing. |
| Carga lazy | El cliente se inicializa en la primera llamada a `encode()` via double-checked locking. Cero RAM al arrancar. |
| Thread-safe | `FastEmbedder` usa `threading.Lock` para acceso concurrente seguro. |
| Configurable | API key via constructor (`api_key`) o variable de entorno `CB_VOYAGE_API_KEY`. |

**Configuracion:**
```env
CB_VOYAGE_API_KEY=pa-...          # Requerido — obtener clave gratuita en https://dash.voyageai.com/
CB_EMBEDDING_MODEL=voyage-multilingual-2
CB_EMBEDDING_DIM=1024
```

**Alternativas consideradas:**

- `paraphrase-multilingual-MiniLM-L12-v2` (sentence-transformers): 384 dims, corre localmente pero menor calidad y requiere ONNX runtime en Docker.
- `text-embedding-3-small` (OpenAI): requiere API de pago, vendor lock-in con el proveedor LLM.
- `all-MiniLM-L6-v2`: solo ingles, rendimiento pobre en espanol.

---

## Estrategia de enriquecimiento del QueryBuilder

La clase `QueryBuilder` en `api/app/rag/retriever.py` construye la consulta de busqueda para Qdrant **sin llamar a un LLM**. Parte de una consulta base y agrega terminos especificos del dominio segun los campos de la transaccion.

### Construccion de consultas para politicas

```python
base = f"contracargo {motivo}, {channel}, {payment_method}, score {fraud_score}/100, {country}"

# Regla 1: Transacciones Cripto → palabras clave de irreversibilidad
if payment_method == "Cripto":
    base += " criptomonedas no reversible blocker"

# Regla 2: Fraud score bajo (< 30) → palabras clave de alto riesgo
if fraud_score < 30:
    base += " transaccion de alto riesgo fraude score bajo"

# Regla 3: Pais fuera de LATAM → palabras clave de SLA extendido
if country not in {"ARG", "BRA", "CHL", "COL", "MEX", "PER", "URY"}:
    base += " internacional fuera LATAM plazo extendido"

# Regla 4: Canal IVR → palabras clave de limite de monto
if channel == "IVR":
    base += " limite monto IVR"
```

### Construccion de consultas para casos similares

```python
query = f"Contracargo en {merchant} por USD {amount:.2f}, {payment_method}, {country}, score {fraud_score}"
if motivo:
    query += f", motivo: {motivo}"
```

### Logica de cada regla de enriquecimiento

| Regla | Condicion | Terminos agregados | Politicas objetivo |
|---|---|---|---|
| Criptomonedas | `payment_method == "Cripto"` | `"criptomonedas no reversible blocker"` | POL-EXC-003 (exclusion cripto) |
| Alto riesgo | `fraud_score < 30` | `"transaccion de alto riesgo fraude score bajo"` | POL-FRD-001..004 (politicas de fraude) |
| Internacional | `country not in LATAM_COUNTRIES` | `"internacional fuera LATAM plazo extendido"` | POL-EXC-004 (SLA extendido non-LATAM) |
| Canal IVR | `channel == "IVR"` | `"limite monto IVR"` | POL-EXC-001 (limites canal IVR) |

### Ejemplos de consultas enriquecidas

| Transaccion | Consulta enriquecida |
|---|---|
| Cripto, score=8, ARG | `"contracargo Cargo no reconocido, Web, Cripto, score 8/100, ARG criptomonedas no reversible blocker transaccion de alto riesgo fraude score bajo"` |
| Visa, score=4, USA | `"contracargo Cargo no reconocido, Web, Credito Visa, score 4/100, USA transaccion de alto riesgo fraude score bajo internacional fuera LATAM plazo extendido"` |
| Debito, score=80, ARG | `"contracargo Cargo no reconocido, Web, Debito Visa, score 80/100, ARG"` (sin enriquecimiento — no aplica ninguna regla) |

### Por que enriquecimiento deterministico supera a consultas generadas por LLM

1. **Reproducibilidad:** La misma transaccion siempre produce la misma consulta. Los resultados de tests son estables.
2. **Costo cero:** No consume tokens en el momento de la recuperacion.
3. **Debuggabilidad:** La consulta enriquecida se registra en la respuesta (campo `_query`) y puede inspeccionarse directamente.
4. **Alineacion de vocabulario:** Los terminos agregados se copian de las descripciones reales de las politicas, maximizando la similitud coseno con los documentos objetivo.

---

## Reranking de resultados

Despues de la busqueda vectorial en `historical_cases`, se aplica un reranking deterministico que bonifica resultados que comparten atributos clave con la transaccion consultada.

### Mecanismo

```python
@staticmethod
def _rerank(results, payment_method, country):
    for r in results:
        boost = 0.0
        if r.payload.get("payment_method") == payment_method:
            boost += 0.05   # RERANK_PAYMENT_METHOD_BOOST
        if r.payload.get("country") == country:
            boost += 0.03   # RERANK_COUNTRY_BOOST
        r.score = min(r.score + boost, 1.0)  # RERANK_MAX_SCORE
    return sorted(results, key=lambda r: r.score, reverse=True)
```

### Boosts configurados

| Factor | Boost | Justificacion |
|---|---|---|
| Mismo metodo de pago | +0.05 | Casos con el mismo metodo de pago (ej. "Credito Visa") son precedentes mas relevantes porque las reglas de contracargo varian por tipo de instrumento de pago. |
| Mismo pais | +0.03 | Casos del mismo pais comparten regulaciones y SLAs similares (LATAM vs. non-LATAM). |
| Techo maximo | 1.0 | El score nunca excede 1.0 para evitar distorsion en el ranking. |

Ademas, Qdrant recibe un filtro `should` por `payment_method` que actua como soft filter (bonifica pero no excluye). Combinado con el reranking post-busqueda, los casos con el mismo perfil financiero tienden a aparecer primero.

---

## Sistema de sinonimos de motivo

El formateador de casos (`api/app/rag/formatter.py`) implementa un sistema de coincidencia de motivos **puramente en Python**, sin usar LLM. Esto permite etiquetar deterministica y gratuitamente los precedentes cuyo motivo es semanticamente equivalente al caso actual.

### Grupos de sinonimos etiquetados

```python
_MOTIVO_SYNONYM_GROUPS = [
    ("cargo duplicado",       {"duplicado", "duplicada", "doble", "doble cobro", "doble cargo", "cargo doble"}),
    ("fraude / no reconocido", {"no reconoce", "no reconocida", "no autorizado", "no autorizada", "fraude"}),
    ("producto no recibido",  {"no recibido", "no entregado", "no entrega", "falta entrega", "no llego"}),
    ("producto defectuoso",   {"defecto", "defectuoso", "calidad", "dañado", "roto"}),
    ("monto incorrecto",      {"monto incorrecto", "monto erroneo", "cobro incorrecto"}),
    ("cancelacion",           {"cancelado", "cancelacion", "post-cancelacion", "post cancelacion"}),
]
```

### Como funciona

1. Para cada caso recuperado de Qdrant, se compara el `motivo` del caso con el `motivo` de la transaccion actual.
2. Ambos motivos se normalizan a minusculas y se buscan keywords de cada grupo de sinonimos.
3. Si ambos motivos contienen keywords del **mismo grupo**, el caso se etiqueta `[MOTIVO SIMILAR]` en el contexto del prompt.
4. Los casos etiquetados se ordenan **primero** en la lista de precedentes, dandoles prioridad visual para el LLM.

### Ejemplo

Si la transaccion actual tiene `motivo = "No reconoce la compra"` y un caso historico tiene `motivo = "Fraude con tarjeta"`, ambos contienen keywords del grupo `"fraude / no reconocido"` (`"no reconoce"` y `"fraude"` respectivamente). El caso se etiqueta:

```
### Precedente 1 [MOTIVO SIMILAR] (similitud: 72%)
- Caso: CASO-00042 | Motivo: Fraude con tarjeta
```

### Por que no usar el LLM para esto

- **Costo:** Comparar N motivos con un LLM consumiria tokens en cada busqueda.
- **Latencia:** Agrega una llamada LLM al flujo de recuperacion.
- **Determinismo:** Los mismos motivos siempre producen el mismo resultado. Testeable unitariamente.
- **Suficiencia:** Con 6 grupos de sinonimos cubrimos >95% de los motivos del dataset. Agregar un grupo nuevo es una linea de codigo.

---

## Busqueda por lotes (batch search)

El metodo `search_policies_and_cases()` optimiza el pipeline al realizar **una sola llamada a la API de Voyage AI** para obtener ambos vectores (politicas y casos), en lugar de dos llamadas separadas:

```python
# 1 llamada API en lugar de 2
policy_vec, case_vec = self._embed_batch([policy_query, case_query])
```

Esto ahorra un round-trip a Voyage AI por cada investigacion de contracargo, reduciendo latencia y consumo del free tier.

---

## Resumen de parametros de recuperacion

| Coleccion | top_k | score_threshold | Justificacion |
|---|---|---|---|
| `policies` | 17 | 0.0 | Corpus pequeno — recuperar todo, el LLM filtra |
| `historical_cases` | 5 | 0.40 | Solo precedentes semanticamente significativos |
| `_semantic_cache` | 1 | 0.92 | Solo coincidencia casi identica |

---

## Bucle de auto-actualizacion

```
                      POST /api/policies/
                      PUT  /api/policies/{code}
                      DELETE /api/policies/{code}
                              |
                              v
                   RAGUpdater.on_policy_created/updated/deleted()
                              |
                              |--- SQLite upsert / delete
                              '--- Qdrant upsert / delete (inmediato)
                                   [la proxima busqueda usa la politica actualizada]


                      POST /api/feedback/
                       {judge_score: 8.5, resolution: {...}, ...}
                              |
                              v
                   RAGUpdater.on_case_resolved(case, judge_score)
                              |
                      judge_score >= 8.0?
                              |
                    SI -------+------- NO
                              |           '--- Guardar solo en SQLite
                              v
                   QdrantIndexer.index_single_case(case, tx)
                   [nuevo precedente disponible para el proximo caso similar]
```

### Flujo de actualizacion de politicas (efecto inmediato)

Cuando un analista llama a `PUT /api/policies/POL-EXC-003` para actualizar la descripcion de la politica de exclusion de criptomonedas:

1. La ruta FastAPI guarda el registro actualizado en SQLite (se actualiza el timestamp `updated_at`).
2. Se invoca `RAGUpdater.on_policy_updated()`.
3. Se elimina el punto antiguo de Qdrant (por UUID deterministico derivado de `POL-EXC-003`).
4. Se inserta el nuevo punto con el texto Markdown actualizado embebido.
5. La proxima llamada a `GET /api/policies/search` retorna el contenido actualizado de la politica.
6. **No requiere reinicio ni redeploy.**

### Flujo de auto-indexacion de casos

Cuando un analista envia feedback con `judge_score = 8.5`:

1. `POST /api/feedback/` guarda en la tabla SQLite `feedback`.
2. `RAGUpdater.on_case_resolved()` verifica `8.5 >= 8.0` (umbral configurable via `JUDGE_AUTO_INDEX_THRESHOLD`).
3. Se obtiene el registro completo de la transaccion desde SQLite via `db.get_transaction()`.
4. `QdrantIndexer.index_single_case(case, tx)` crea el embedding y hace upsert en `historical_cases`.
5. El Point ID es deterministico (`uuid5("FB-{feedback_id}")`) — seguro para llamar multiples veces.
6. Futuros casos con comercio + monto + metodo de pago + perfil de fraude similar recuperaran este caso como precedente.

Este bucle implementa el **Eje 6 (Mejora Continua)** de la prueba tecnica: el agente aprende de sus propias resoluciones de alta calidad.

---

## Estrategia de cache semantico

### Clave del cache

La clave es el query string de similitud de casos construido por `QueryBuilder.for_similar_cases()`:
```
"Contracargo en TechStore AR por USD 234.50, Credito Visa, ARG, score 4, motivo: Cargo no reconocido"
```

Este string se embebe y almacena en `_semantic_cache` junto con la respuesta de resolucion completa.

### Verificacion del cache (antes de llamadas LLM)

```python
cached = retriever.check_semantic_cache(query, threshold=0.92)
if cached:
    return cached  # se omiten todas las llamadas LLM
```

A similitud coseno 0.92, solo transacciones con el mismo comercio, monto muy similar, mismo metodo de pago y mismo pais producen un cache hit. Una transaccion por USD 235.00 en el mismo comercio puede o no producir hit dependiendo de como el embedding codifica la diferencia de monto.

### Escritura del cache (despues de resolucion exitosa)

Despues de que el pipeline completo se ejecuta exitosamente (policy_eval + resolution + judge), la respuesta se almacena en el cache:
```python
retriever.store_in_cache(query, resolution_response)
```

### Impacto en costos

En pruebas reales, el cache semantico redujo el tiempo de respuesta de **113 segundos** (primera ejecucion, pipeline LLM completo) a **2 segundos** (segunda ejecucion, cache hit). Esto representa un ahorro significativo tanto en latencia como en consumo de tokens del LLM.

### Invalidacion del cache

No existe un mecanismo de TTL o invalidacion explicita en v1. El cache es valido mientras el conjunto de politicas no cambie dramaticamente. Cuando ocurre una actualizacion de politica importante, el operador puede limpiar la coleccion `_semantic_cache` via la API de Qdrant:
```bash
curl -X DELETE http://localhost:6333/collections/_semantic_cache
```
La coleccion se recrea automaticamente en el proximo arranque de la API.
