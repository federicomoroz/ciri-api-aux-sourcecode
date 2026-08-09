# Mejora Continua — Agente de Contracargos CIRI

Este documento describe el sistema de auto-mejora del agente: como las decisiones de los analistas retroalimentan la base de conocimiento, como el Judge controla la calidad de lo que se aprende, como se detectan y corrigen las alucinaciones, y como se observa todo en produccion.

---

## El camino de mejora: de 8.2 a 9.1

Cuando arrancamos, el promedio del Judge era **8.2/10**. Funcionaba, pero habia margen para mejorar. La evolucion fue asi:

| Etapa | Score promedio | Que cambio |
|-------|---------------|------------|
| v1.0 — Haiku para todo | 8.2 | Un solo modelo (Haiku) manejaba evaluacion, sintesis y juicio. Los prompts eran instruccionales basicos. |
| v1.1 — Rubricas en el Judge | 8.6 | Se agregaron rubricas de 5 niveles por criterio al prompt del Judge. Mejoro la consistencia de las evaluaciones, pero el techo de Haiku limito el avance. |
| Techo de Haiku | 8.6 | Por mas que afinamos los prompts, Haiku no podia generar el razonamiento analitico que necesitabamos en la sintesis. Techo claro. |
| v2.0 — Sonnet para sintesis y juicio | 8.9 | Se introdujo modelo dual: Haiku para evaluacion de politicas (tarea mecanica), Sonnet para sintesis y Judge (tareas que requieren razonamiento). Salto inmediato. |
| v3.0 — Ajustes dirigidos | **9.1** | Prompt v3.0 de resolucion desbloqueado para razonamiento analitico de Sonnet. Overrides deterministicos para 6/11 campos. Precedent summary deterministico con analisis de patron. |
| v3.1 — SLA determinista | *sin medir* | El resultado de `/api/sla/check` entra al prompt y `compensation_applicable` / `compensation_amount_usd` pasan a calcularse por codigo. Overrides deterministicos para 8/11 campos. Ver «Lo que no esta medido». |
| v2.1 — El Juez califica al modelo | *sin medir* | `policy_consistency` y `risk_assessment` pasan a evaluar la propuesta original en vez de la version ya corregida. **Se espera que baje**: hasta v2.0 esos dos criterios no podian fallar por construccion. Ver `decisions.md` 20. |
| v2.2 — El umbral sale del dominio | *sin medir* | `approved = overall_score >= 7.0` estaba escrito en el prompt **y** en `constants.py`. El codigo solo aplica la constante cuando el modelo omite el campo, asi que gobernaba la copia del prompt: mover la constante cambiaba el color del informe pero no el flag. Ahora se inyecta. |

> **v2.1 no llegaba por n8n, y esto invalida una afirmacion anterior de este documento.**
> El cambio de v2.1 depende de un campo —la propuesta del modelo antes del override— que
> `resolve` producia bajo una clave que el modelo de respuesta no declaraba: FastAPI la
> descartaba al serializar. El nodo `Juez de Calidad` reenvia a `/api/analyze/judge` **lo que
> devolvio `resolve`**, asi que por el orquestador el Juez seguia calificando la resolucion ya
> corregida y sus dos criterios seguian sin poder bajar de 10. El pipeline directo del panel no
> pasa por el serializador y si lo recibia: **el mismo caso sacaba distinta nota segun el
> camino, y la del orquestador era la mas alta**. Corregido; hay un test que serializa la
> respuesta y comprueba que la propuesta sobreviva.
>
> Consecuencia sobre los numeros de mas abajo: los informes guardados que salieron por n8n
> tienen notas de Juez v2.0 aunque el prompt dijera v2.1.

### Como se midio el 9.1

**Que es:** el promedio del `overall_score` del Juez sobre las corridas manuales de desarrollo
con la configuracion v3.0 — modelo dual (Haiku para evaluacion de politicas, Sonnet para sintesis
y juicio), prompts v1.2 / v3.0 / v2.0. Cada corrida es un caso del dataset atravesando el pipeline
completo, y el Juez la puntua sobre los cinco criterios de la rubrica.

**Como se tomo la muestra, con precision:** casos elegidos **al azar y a mano** durante el
desarrollo, corridos de a uno desde el panel. **No se registro cuantos fueron.** Es un promedio
observado, no una evaluacion sistematica: sirve para ver que la configuracion v3.0 rendia mejor
que las anteriores —que es para lo que se uso— y no alcanza para publicarlo como metrica de
calidad con intervalo de confianza. Decirlo asi es preferible a presentar como sistematico algo
que no lo fue.

**Que no es:** el promedio de los tres informes que viajan en este paquete. Esos tres promedian
**8.67**, y la diferencia no es ruido: son los tres casos **mas contenciosos** del dataset,
elegidos para la demo porque cubren los tres caminos del enrutador —BLOCKER, revision humana y
comercio fuera de LATAM— y no porque puntuaran bien. Un caso con cuatro veredictos FAIL y un
BLOCKER le da al Juez mucha mas superficie para descontar que uno que pasa limpio. Elegir los
casos dificiles para mostrar y despues promediar sobre ellos mide otra cosa.

| | Promedio | Sobre que |
|---|---|---|
| Corridas de desarrollo (v3.0) | **9.1** | El conjunto de casos que se corrio durante el desarrollo |
| Los tres escenarios del paquete | **8.67** | `data/informes_demo/analisis_*.json` — 8.6 / 8.7 / 8.7 |

**Lo que no esta medido, y por que.** El 9.1 corresponde a la configuracion v3.0. Desde entonces
cambiaron el prompt de resolucion (v3.1), el del Juez (v2.1) y dos umbrales de negocio. **No hay
una medicion posterior**, y volver a hacerla requiere saldo de API que hoy no hay. Del cambio del
Juez ademas se espera que el numero *baje*: hasta v2.0 evaluaba la resolucion ya corregida por el
override, asi que `policy_consistency` y `risk_assessment` no podian bajar de 10 por construccion.
Bajar por dejar de medir mal es mejora, no regresion — pero hasta correrlo es una expectativa, no
un dato.

**Lo que quedo sin guardar.** Las corridas que produjeron el 9.1 no dejaron artefacto: no habia
harness de evaluacion que volcara los resultados a disco, Langfuse estaba desactivado y la SQLite
de Render es efimera. El numero es real y la configuracion esta documentada, pero **no es
reproducible desde este paquete**.

**El instrumento si viaja.** `scripts/evaluar.py` es lo que faltaba: corre N casos por el pipeline
completo, junta los scores del Juez y escribe un JSON versionable con el detalle caso por caso, el
promedio por criterio, la configuracion usada —modelos, temperatura, versiones de prompt leidas de
sus cabeceras— y el costo real.

```bash
python scripts/evaluar.py --n 20                 # 20 casos al azar, semilla fija
python scripts/evaluar.py --todos --tope-usd 4   # el dataset entero, con tope de gasto
python scripts/evaluar.py --casos TXN-00051,TXN-00042
```

El muestreo es reproducible: «al azar» usa una semilla fija, asi que la misma invocacion devuelve
la misma muestra y el resultado se puede volver a obtener. El script aborta antes de gastar si
falta la clave, corta si se pasa del tope, e imprime el costo acumulado mientras corre — unos
USD 0.037 por caso segun la estimacion de mas abajo.

**Se corrio dos veces, y dejo sus archivos.** Ambos con el free tier, costo USD 0.00,
mismos prompts y misma semilla:

| Corrida | Modelo | Medidos | Promedio | σ |
|---|---|---|---|---|
| [`2026-08-08-gemini-flash.json`](evaluaciones/2026-08-08-gemini-flash.json) | `gemini-flash-latest` | 3 de 20 (17 contra la cuota diaria) | 8.4 | 1.28 |
| [`2026-08-09-gemini-flash-lite.json`](evaluaciones/2026-08-09-gemini-flash-lite.json) | `gemini-flash-lite-latest` (`--modo demo`) | **7 de 7** | 8.97 | 0.42 |

**El badge sigue en 9.1 y eso es deliberado.** Ninguno de esos numeros es el score del sistema
entregado: la configuracion documentada es Haiku para politicas y Sonnet para sintesis y juez, y
en estas corridas el juez corre en el mismo modelo gratuito que resolvio — cada free tier se
puntua a si mismo con su propia vara. La prueba esta en los tres casos que ambas corridas
comparten: los dos modelos les dan hasta ±1.8 de diferencia, en las dos direcciones. Lo que
cambio no es el numero sino su condicion: antes no habia **ningun** archivo; ahora hay dos, con
su muestra, su semilla, sus versiones de prompt y sus fracasos anotados. El detalle caso por
caso y la comparacion entre corridas estan en
[`docs/evaluaciones/README.md`](evaluaciones/README.md).

Correrlo en la configuracion documentada cuesta unos USD 0.75 para 20 casos y sigue pendiente.

**Y hay una segunda medicion, que no depende de un juez.** El Juez es un modelo puntuando a
otro modelo; el dataset trae 60 resoluciones humanas etiquetadas que el RAG ya usa como
precedentes y que nadie estaba comparando. `python scripts/medir_acuerdo.py` arma la matriz
de confusion contra ellas: **12 de 34 casos comparables, 35% de acuerdo** — y toda la
coincidencia esta en la clase «En escalación». De los 21 casos donde una persona resolvio,
el agente no reprodujo ninguno, porque por politica no puede resolver. El detalle, el mapeo
entre los dos vocabularios y lo que la medicion encontro sin buscarlo estan en
[`docs/acuerdo_con_analistas.md`](acuerdo_con_analistas.md).

---

**Score por criterio** (rango en los tres escenarios que viajan en el paquete):

| Criterio | Rango actual | Que mide |
|----------|-------------|----------|
| `policy_consistency` | 8.5 – 9.1 | La resolucion respeta todos los BLOCKER y FAIL |
| `justification_quality` | 8.8 – 9.1 | La justificacion cita evidencia especifica y verificable |
| `precedent_usage` | 8.8 – 9.0 | La resolucion aprovecha los casos historicos similares |
| `risk_assessment` | 8.7 – 9.0 | El nivel de riesgo coincide con las evidencias |
| `actionability` | 8.0 – 8.3 | Los next_steps son concretos, realizables y sin contradicciones |

El criterio mas bajo (`risk_assessment` y `precedent_usage`) refleja la complejidad inherente de esas tareas: distinguir riesgo de fraude vs. riesgo de politica requiere matiz, y conectar precedentes con el caso actual requiere razonamiento causal.

---

## Arquitectura dual de modelos

No todos los pasos de la pipeline necesitan el mismo nivel de razonamiento. La configuracion dual aprovecha las fortalezas de cada modelo:

```
CB_LLM_MODEL=claude-haiku-4-5-20251001          # llm — evaluacion de politicas
CB_LLM_MODEL_RESOLUTION=claude-sonnet-4-20250514 # llm_resolution — sintesis + judge
```

| Paso | Modelo | Justificacion |
|------|--------|---------------|
| `v1_policy_eval` — evaluacion de politicas | Haiku | Tarea mecanica: comparar datos contra umbrales. Haiku es rapido, barato y suficiente. |
| `v1_resolution` — sintesis de resolucion | Sonnet | Requiere razonamiento analitico: conectar precedentes, explicar riesgos, proponer acciones concretas. |
| `v1_judge` — evaluacion de calidad | Sonnet | Requiere juicio calibrado: aplicar rubricas de 5 niveles con granularidad. |

En `dependencies.py`, `ResolutionService` recibe ambos clientes:

```python
resolution_service = ResolutionService(llm, tracer, llm_resolution=llm_resolution)
```

El servicio usa `self.llm` para la evaluacion de politicas y `self.llm_resolution` para la sintesis y el juicio. Si `CB_LLM_MODEL_RESOLUTION` no esta configurado, ambos usan el mismo modelo.

---

## Overrides deterministicos — "El codigo decide, el LLM explica"

De los 11 campos principales de una resolucion, **6 son calculados por Python** y siempre sobreescriben lo que diga el LLM:

| Campo | Quien decide | Como |
|-------|-------------|------|
| `recommended_action` | Python (`decision.decidir`) | BLOCKER -> REJECT, FAIL -> PENDING_HITL, todo PASS -> APPROVE |
| `risk_level` | Python (`decision.decidir`) | BLOCKER en verdicts -> BLOCKER, 2+ FAILs o fraud_score < 15 -> HIGH, 1 FAIL -> MEDIUM, 0 FAILs -> LOW |
| `requires_hitl` | Python (`decision.decidir`) | true si hay FAILs sin BLOCKER, o `requires_human_review=true` en algun veredicto |
| `hitl_reason` | Python (`decision.decidir`) | Razon explicita con conteo de violaciones |
| `policy_verdicts` | LLM (Haiku) + whitelist | LLM evalua, pero BLOCKERs fuera de `puede_bloquear` se degradan a FAIL |
| `precedent_summary` | Python (`precedentes.resumir_precedentes`) | Deterministico con tags, match de sinonimos y analisis de patron |

Los 5 campos restantes (`justification`, `confidence`, `log_summary`, `next_steps`, `compensation_*`) los genera el LLM y se validan con guardrails post-generacion.

La razon es simple: si las politicas son editables (y lo son — via `PUT /api/policies/{code}`), la evaluacion individual puede ser LLM, pero la **decision final** no puede depender de que el LLM interprete correctamente la combinacion de todos los veredictos. Eso lo hace Python, siempre.

---

## Precedent summary deterministico

El campo `precedent_summary` no lo genera el LLM — lo construye `precedentes.resumir_precedentes()` en Python. El proceso:

1. **Match de sinonimos**: Cada motivo del caso actual se compara contra los motivos de los precedentes usando grupos de sinonimos definidos en `formatter.py`:

```python
_MOTIVO_SYNONYM_GROUPS = [
    ("cargo duplicado", {"duplicado", "duplicada", "doble", "doble cobro", ...}),
    ("fraude / no reconocido", {"no reconoce", "no reconocida", "no autorizado", "fraude"}),
    ("producto no recibido", {"no recibido", "no entregado", "no llego", ...}),
    ...
]
```

2. **Etiquetado**: Los precedentes que comparten grupo de sinonimos reciben el tag `[MOTIVO SIMILAR]`. Si el merchant coincide con el de la transaccion actual, reciben `[MISMO MERCHANT]`.

3. **Notas de implicacion**: Para cada precedente con `[MOTIVO SIMILAR]`, se agrega una nota deterministica basada en el outcome del caso:
   - "Sin resolucion" -> "caso similar permanece sin resolver — sugiere investigacion adicional"
   - "Aprobado" -> "patron favorable al cliente para este tipo de caso"
   - "Rechazado" -> "patron desfavorable al cliente"
   - "Parcial" -> "solucion intermedia para este tipo de caso"

4. **Analisis de patron global**: Al final, una linea resume la tendencia: "de 5 precedentes, 3 aprobados, 1 rechazado — tendencia favorable al cliente. Motivo similar: 2/5, 2 aprobados".

El LLM recibe este summary pre-construido en la seccion "DECISION DETERMINADA" y lo copia textualmente. Esto garantiza que las estadisticas de precedentes sean siempre correctas — sin riesgo de que el LLM cuente mal o invente un patron.

---

## Ciclo de retroalimentacion completo

El feedback loop cierra la brecha entre la resolucion automatizada y la experiencia humana. Cada decision de un analista es una señal de entrenamiento que hace mejores las resoluciones futuras.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                     CICLO DE RETROALIMENTACION                              │
│                                                                             │
│  1. LLEGA UN CASO                                                           │
│     Webhook -> n8n (orquestador explicito) -> pipeline FastAPI             │
│                                                                             │
│  2. EL AGENTE RESUELVE                                                      │
│     RAG (politicas + precedentes) -> LLM -> Resolution JSON                │
│     + 6 campos deterministicos sobreescritos por Python                    │
│                                                                             │
│  3. EL JUDGE EVALUA                                                         │
│     n8n HTTP Request -> FastAPI /api/analyze/judge -> score 1-10           │
│           │                                                                 │
│           ├── score >= 7.0 ──────────────────────────────────┐             │
│           │                                                   │             │
│           └── score < 7.0 -> HITL ──────────────────────────>│             │
│                              Analista lee reporte HTML        │             │
│                              APPROVE / REJECT / MODIFY        │             │
│                                        │                      │             │
│                                        v                      │             │
│                     POST /api/hitl/decide  <- traduce la      │             │
│                     decision y decide si la resolucion viaja  │             │
│                                                               v             │
│  4. FEEDBACK ENVIADO                                                        │
│     POST /api/feedback/ {transaction_id, analyst_decision,                 │
│                          final_outcome, judge_score, resolution}            │
│     `resolution` va en null salvo que el analista haya avalado la del       │
│     agente TAL CUAL: sin ella, el paso 6 no ocurre.                         │
│                                │                                            │
│                                v                                            │
│  5. REGISTRO EN SQLITE                                                      │
│     tabla feedback: decision, notas, judge_score, timestamp                │
│                                │                                            │
│               judge_score >= 8.0?                                           │
│                    │                                                         │
│          SI ───────┴────── NO                                               │
│           │                 └──> Guardado solo en SQLite (auditoria)        │
│           v                                                                 │
│  6. AUTO-INDEXACION EN QDRANT                                               │
│     QdrantIndexer.index_single_case(case, tx)                              │
│     Nuevo precedente en coleccion historical_cases                         │
│                                │                                            │
│           ┌────────────────────┘                                            │
│           v                                                                 │
│  7. PROXIMO CASO SIMILAR                                                    │
│     GET /api/cases/similar -> encuentra el nuevo precedente                │
│     (similitud >= 0.40)                                                    │
│     v1_resolution lo recibe como contexto -> mejor decision                │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Que ve el analista

El analista recibe un reporte HTML renderizado con Jinja2 (`POST /api/reports/html`) que contiene:
- Datos de la transaccion (monto, comercio, pais, metodo de pago, fraud_score)
- Veredictos de politica con razonamiento para cada politica evaluada
- Casos historicos similares con sus resoluciones
- La recomendacion del agente con su justificacion
- Scores del Judge por criterio, con fortalezas y debilidades
- Proximos pasos propuestos por el agente

El analista puede confirmar la recomendacion del agente o corregirla, y agregar notas en texto libre explicando por que. El formulario del nodo `Wait` ofrece **tres** opciones —`APPROVE`, `REJECT`, `MODIFY`— y las tres se traducen en `POST /api/hitl/decide`, no en el canvas.

**Que entra al corpus de precedentes, y que no.** Solo la resolucion que el analista avalo tal cual:

| Decision | Se registra el feedback | Se indexa como precedente |
|---|---|---|
| `APPROVE` | si | si, con `judge_score >= 8.0` |
| `REJECT` | si | **no** |
| `MODIFY` | si | **no** — la aprobo con cambios que el sistema no tiene |
| plazo vencido, sin respuesta | si, como `SIN_RESPUESTA` | **no** |

Las dos ultimas filas son la parte cara. Mientras el mapeo vivio en el canvas y cubrio dos de las tres opciones, un analista que eligio «modificar» quedaba asentado como si hubiera aprobado, y con `judge_score >= 8.0` el caso se indexaba como precedente aprobado — envenenando el corpus con el que se resuelven los siguientes. Y un plazo vencido caia en `APPROVE`, o sea que un contracargo de riesgo alto se aprobaba solo. Las reglas viven ahora en `domain/hitl.py` y `tests/unit/test_hitl.py` las prueba ejecutandolas.

### Payload de feedback

```bash
POST /api/feedback/
{
  "transaction_id": "TXN-00042",
  "analyst_decision": "APPROVED",
  "analyst_notes": "Cliente VIP con historial limpio de 18 meses. Riesgo aceptado.",
  "final_outcome": "APPROVED",
  "judge_score": 8.2,
  "resolution": { /* Resolution JSON completa del agente */ }
}
```

### Respuesta del feedback

```json
{
  "status": "recorded",
  "feedback_id": 15,
  "auto_indexed": true,
  "needs_review": false,
  "judge_score": 8.2
}
```

`auto_indexed: true` confirma que el caso fue agregado como precedente en Qdrant.

---

## Mecanismo del Judge (LLM-as-Judge)

El Judge (`v1_judge`, version 2.0) proporciona una evaluacion independiente de calidad para cada resolucion. Evalua 5 criterios con rubricas granulares de 5 niveles, cada uno con score de 1.0 a 10.0.

### 5 criterios con rubricas granulares

Cada criterio tiene una rubrica de 5 niveles. El Judge debe asignar el score que corresponda segun la descripcion del nivel, sin redondear sistematicamente a .0 o .5.

**1. policy_consistency** (8.5 – 9.1 promedio actual)

| Nivel | Score | Descripcion |
|-------|-------|-------------|
| Excelente | 10.0 | Accion perfecta, todos los veredictos respetados sin excepcion |
| Muy bueno | 9.0 | Accion correcta, veredictos citados correctamente, minimas inconsistencias |
| Bueno | 7.0–8.9 | Accion correcta pero algun veredicto no citado o razonamiento impreciso |
| Regular | 5.0–6.9 | Accion correcta pero inconsistencias claras |
| Critico | 1.0–4.9 | Accion incorrecta (APPROVE con BLOCKER, REJECT sin BLOCKER) |

**2. justification_quality** (8.8 – 9.1 promedio actual)

| Nivel | Score | Descripcion |
|-------|-------|-------------|
| Excelente | 10.0 | Cada afirmacion respaldada por datos verificables + explicacion de por que importan |
| Muy bueno | 9.0 | Cita datos correctos de todas las secciones relevantes con analisis |
| Bueno | 7.0–8.9 | Cita datos correctos pero sin conectarlos analiticamente |
| Regular | 5.0–6.9 | Justificacion vaga con pocos datos especificos |
| Critico | 1.0–4.9 | Alucinacion — datos inventados que no existen en la evidencia |

**3. precedent_usage** (8.8 – 9.0 promedio actual)

| Nivel | Score | Descripcion |
|-------|-------|-------------|
| Excelente | 10.0 | Analiza TODOS los precedentes, identifica patrones, conecta implicaciones |
| Muy bueno | 9.0 | Analiza precedentes [MOTIVO SIMILAR] con profundidad + patron general |
| Bueno | 7.0–8.9 | Menciona precedentes y cita outcomes pero sin analisis de implicaciones |
| Regular | 5.0–6.9 | Solo lista case_ids sin extraer aprendizajes |
| Critico | 1.0–4.9 | Ignora completamente los precedentes disponibles |

**4. risk_assessment** (8.7 – 9.0 promedio actual)

| Nivel | Score | Descripcion |
|-------|-------|-------------|
| Excelente | 10.0 | Risk level correcto + explica POR QUE + distingue riesgo de fraude vs politica |
| Muy bueno | 9.0 | Risk level correcto + explica fuente del riesgo |
| Bueno | 7.0–8.9 | Risk level correcto pero sin explicar la fuente |
| Regular | 5.0–6.9 | Risk level correcto pero justificacion contradictoria |
| Critico | 1.0–4.9 | Risk level incorrecto |

**5. actionability** (8.0 – 8.3 promedio actual)

| Nivel | Score | Descripcion |
|-------|-------|-------------|
| Excelente | 10.0 | Cada paso cita politica + dato + responsable + sin contradicciones |
| Muy bueno | 9.0 | Pasos concretos con datos de politicas + coherencia interna |
| Bueno | 7.0–8.9 | Pasos concretos pero con alguna contradiccion menor |
| Regular | 5.0–6.9 | Pasos vagos o inaplicables |
| Critico | 1.0–4.9 | Sin next_steps o completamente genericos |

### Umbrales de score y resultados

```
Rango de score   Resultado
───────────────────────────────────────────────────────────────────
>= 8.0           Aprobado + auto-indexado como nuevo precedente en Qdrant
7.0 – 7.9        Aprobado — entregado como resolucion final
5.0 – 6.9        No aprobado — redirigido a HITL (revision de analista)
< 5.0            No aprobado + marcado needs_review=true en SQLite
```

### Por que 7.0 como umbral de HITL

Un score de 7.0 significa que los 5 criterios promediaron al menos 7/10. Resoluciones por debajo de este umbral tipicamente tienen al menos uno de: razonamiento de politica incompleto, precedentes no utilizados, o next_steps vagos. Estos son exactamente los casos donde la revision de un analista agrega mas valor. Bajarlo a 6.0 dejaria pasar resoluciones mediocres sin revision.

### Por que 8.0 como umbral de auto-indexacion

Los casos auto-indexados se convierten en precedentes permanentes que influencian resoluciones futuras. Solo casos de alta calidad (8.0+ = "bueno" o "muy bueno" en los 5 criterios) deberian convertirse en datos de entrenamiento. Un caso indexado con score 6.5 podria tener justificacion debil o mal uso de precedentes — aprender de el podria degradar resoluciones futuras.

### El Judge como detector de alucinaciones

El criterio `policy_consistency` detecta la alucinacion mas peligrosa: recomendar `APPROVE` cuando hay un veredicto `BLOCKER`. Si el Judge detecta esto, asigna `policy_consistency = 1.0` y el `overall_score` cae por debajo de 7.0, redirigiendo a HITL. Pero el guardrail en `/api/analyze/resolve` atrapa esto antes de que el Judge siquiera se ejecute (ver seccion Guardrails).

**Ruta de llamada del Judge**: El Judge (prompt v1_judge v2.0) se invoca via FastAPI (`POST /api/analyze/judge`), donde el prompt esta versionado en `v1_judge.py` y se ejecuta a traves del mismo `AnthropicClient` que todas las llamadas LLM. Esto garantiza observabilidad consistente via Langfuse, manejo de errores unificado y versionado de prompts.

---

## Deteccion de alucinaciones — Guardrails

Cinco guardrails corren dentro de `ResolutionService.resolve()`, y estan en dos lugares
distintos a proposito.

**Tres detectan que el modelo se contradijo con la evidencia** (`guardrails.antes_del_override`). Corren
**antes** del override determinista, porque despues la propuesta del modelo ya fue reemplazada y
la contradiccion deja de ser observable: el sistema seguiria estando protegido, pero la
alucinacion se corregiria en silencio y no quedaria registrada en ningun lado. Al correr antes,
el warning viaja en `guardrail_warnings` hasta el informe y hasta el audit trail.

**Dos validan campos que el codigo no sobrescribe** (`guardrails.despues_del_override`): la compensacion y
la confianza salen del modelo tal cual, asi que ahi si hay algo que comprobar sobre el valor
final.

### Guardrail 1: APPROVE con BLOCKER activo

**Condicion:** `recommended_action == "APPROVE"` Y al menos un veredicto de politica tiene `verdict == "BLOCKER"`

**Accion:** El override determinista ya fija `REJECT` + `BLOCKER` a partir de los veredictos. El guardrail no corrige: **registra** que el modelo propuso lo contrario.

**Warning agregado:**
```
"GUARDRAIL: el modelo propuso APPROVE con un veredicto BLOCKER activo — corregido a REJECT (posible alucinacion)"
```

**Por que importa:** Esta es la alucinacion mas peligrosa posible. Aprobar un contracargo para una transaccion de criptomonedas (irreversible por diseño) o un caso de fraude confirmado resultaria en perdida financiera. El guardrail lo atrapa incluso cuando el LLM produce output contradictorio.

### Guardrail 2: BLOCKER whitelist

**Condicion:** Un veredicto tiene `verdict == "BLOCKER"` pero `policy_code` no esta en `puede_bloquear`

**POLICY_SEED_BLOQUEANTES** actualmente contiene solo `{"POL-EXC-003"}` (criptomonedas — transaccion tecnicamente irreversible).

**Accion:** Degradar a `FAIL` + marcar `requires_human_review = true`

**Por que importa:** El LLM a veces sobre-escala situaciones como "comercio suspendido" a BLOCKER. Un comercio suspendido es una señal de riesgo, pero la transaccion sigue siendo tecnicamente reversible. Solo las transacciones genuinamente irreversibles (cripto) merecen BLOCKER. Esta whitelist evita que el LLM convierta FAILs en BLOCKERs falsos.

```python
POLICY_SEED_BLOQUEANTES: frozenset[str] = frozenset({"POL-EXC-003"})  # solo la semilla
```

### Guardrail 3: BLOCKER sin veredictos reales

**Condicion:** `risk_level == "BLOCKER"` pero no hay ningun veredicto BLOCKER en policy_verdicts

**Accion:** Auto-correccion — degradar a `risk_level = "HIGH"` + `recommended_action = "PENDING_HITL"` + `requires_hitl = true`

**Warning agregado:**
```
"GUARDRAIL: risk_level=BLOCKER sin veredictos BLOCKER reales — auto-corregido a HIGH + PENDING_HITL"
```

### Guardrail 4: Compensacion excede monto de transaccion

**Condicion:** `compensation_amount_usd > transaction.amount_usd * 1.10`

**Accion:** Agregar warning (sin auto-correccion — el humano debe decidir)

**Warning agregado:**
```
"GUARDRAIL: Compensacion USD 25.00 excede el monto original USD 18.50 en >10%"
```

**Por que importa:** POL-SLA-004 fija la compensacion maxima en USD 15. El LLM podria alucinar un monto de compensacion basado en el valor de la transaccion en vez del tope fijo. Este guardrail señala la anomalia sin forzar una correccion (el analista puede tener razones legitimas para un override).

### Guardrail 5: Confianza excesiva con multiples fallas de politica

**Condicion:** `confidence > 0.95` Y `fail_count >= 2` (veredictos FAIL o BLOCKER)

**Accion:** Agregar warning (sin auto-correccion)

**Warning agregado:**
```
"GUARDRAIL: Confianza excesiva (0.97) con 3 violaciones de politica"
```

**Por que importa:** Confianza alta (>0.95) es apropiada solo cuando la evidencia es inequivoca. Multiples fallas de politica indican un caso complejo donde la certeza deberia ser menor. Una resolucion que afirma 97% de confianza con 3 veredictos FAIL es probablemente sobreconfiada y amerita revision.

### Resumen de guardrails

| Guardrail | Auto-corrige | Trigger HITL | Severidad |
|-----------|-------------|-------------|-----------|
| APPROVE + BLOCKER | Si (forzar REJECT) | No (resuelto automaticamente) | Critica |
| BLOCKER fuera de whitelist | Si (degradar a FAIL) | Si (requires_human_review) | Critica |
| BLOCKER sin veredictos reales | Si (degradar a HIGH) | Si (PENDING_HITL) | Alta |
| Compensacion > 110% | No | Si (warning registrado) | Alta |
| Confianza > 95% con 2+ FAILs | No | Si (warning registrado) | Media |

---

## Auto-actualizacion del RAG — Triggers y comportamiento

### Trigger: `on_policy_created` (POST /api/policies/)

```
REST call -> db.upsert_policy() -> RAGUpdater.on_policy_created(policy)
                                          │
                                          └──> QdrantIndexer.index_single_policy(policy)
                                               [nuevo embedding Markdown en coleccion 'policies']
                                               [disponible inmediatamente para la proxima consulta]
```

**Efecto:** La nueva politica es recuperable inmediatamente por busqueda semantica. No requiere reinicio.

### Trigger: `on_policy_updated` (PUT /api/policies/{code})

```
REST call -> db.upsert_policy() -> RAGUpdater.on_policy_updated(policy)
                                          │
                                          ├──> QdrantIndexer.delete_policy(code)
                                          │    [elimina punto antiguo por UUID deterministico]
                                          └──> QdrantIndexer.index_single_policy(policy)
                                               [inserta nuevo punto con embedding actualizado]
```

**Efecto:** El embedding viejo (basado en la descripcion anterior) se elimina y se reemplaza con el nuevo. Si la descripcion de la politica cambio (ej: umbral actualizado de 15 a 20), el nuevo significado semantico se refleja de inmediato.

### Trigger: `on_policy_deleted` (DELETE /api/policies/{code})

```
REST call -> db.delete_policy(code) -> RAGUpdater.on_policy_deleted(code)
                                              │
                                              └──> QdrantIndexer.delete_policy(code)
                                                   [punto eliminado por UUID deterministico]
```

**Efecto:** La politica eliminada ya no es recuperable. La proxima resolucion no la incluira en el contexto de evaluacion.

### Trigger: `on_case_resolved` (POST /api/feedback/, judge_score >= 8.0)

```
POST /api/feedback/ -> db.save_feedback() -> RAGUpdater.on_case_resolved(case, 8.5)
                                                    │
                                               8.5 >= 8.0?  SI
                                                    │
                                               db.get_transaction(transaction_id)
                                                    │
                                               QdrantIndexer.index_single_case(case, tx)
                                               [nuevo punto en 'historical_cases']
                                               [retorna True -> auto_indexed=true en respuesta]
```

**Efecto:** El caso resuelto de alta calidad se convierte en un precedente permanente. Todos los casos similares futuros lo recuperaran como ejemplo en el top-5.

---

## Versionado de prompts

### Convencion de nombres

```
api/app/llm/prompts/
  v1_policy_eval.py       <- v1.2 en produccion (mecanico, optimizado para Haiku)
  v1_resolution.py        <- v3.0 en produccion (analitico, optimizado para Sonnet)
  v1_judge.py             <- v2.0 en produccion (rubricas granulares)
```

Cada archivo comienza con un encabezado de version:
```python
# PROMPT VERSION: v3.0 | DATE: 2025-07 | CHANGES: Unlock analytical reasoning for Sonnet
```

### Historia de versiones

| Prompt | v1.0 | v2.0 | v3.0 |
|--------|------|------|------|
| `policy_eval` | Instrucciones basicas | Logica de umbrales estricta, conteo PREVIO vs actual, determinacion LATAM | v1.2: fix igualdad en umbrales, documentacion |
| `resolution` | Instruccional basico | Mecanico: formato rigido, restricciones de datos | Analitico: "codigo decide, LLM razona". Decision determinada pre-inyectada. Precedent analysis. |
| `judge` | Criterios sin rubrica | Rubricas granulares de 5 niveles por criterio. Reglas anti-penalizacion para PENDING_HITL correcto. | — (v2.0 sigue en produccion) |

### Flujo de promocion de versiones

Cuando un prompt necesita actualizarse:

1. **Crear nuevo archivo:** Copiar `v1_resolution.py` -> `v2_resolution.py`
2. **Editar el nuevo archivo:** Hacer los cambios, actualizar el encabezado
3. **Actualizar el import en `__init__.py`:** `from . import v2_resolution as resolution`
4. **Correr tests de regresion:** `pytest tests/unit/ -v`
5. **Modo shadow (opcional):** Llamar v1 y v2 en paralelo, comparar outputs, loguear a Langfuse
6. **Promover:** Actualizar import en `__init__.py`, remover llamada shadow
7. **Conservar archivo viejo:** No borrar — necesario para rollback y auditoria

### Por que archivos versionados y no strings inline

- **Git blame** muestra exactamente cuando y por que cambio cada linea del prompt
- **Rollback** es un cambio de una linea en el import, no una edicion de string multi-linea
- **A/B testing** es directo: llamar ambas versiones, comparar scores del Judge
- **Tests unitarios** importan la funcion render directamente: `from app.llm.prompts.v1_policy_eval import render`
- **Tooling del IDE** (linting, busqueda, refactor) funciona sobre archivos Python, no sobre string literals embebidos

---

## Observabilidad — Langfuse

Langfuse se configura via `CB_LANGFUSE_ENABLED=true` y las credenciales en `.env`. Todas las llamadas LLM se trazan via `LangfuseTracer` (inyectado como dependencia `tracer` en los servicios).

### Metricas rastreadas

| Metrica | Campo Langfuse | Donde se captura |
|---------|---------------|------------------|
| Tokens (input/output) | `usage.input_tokens`, `usage.output_tokens` | Cada llamada LLM |
| Latencia por llamada | Duracion del span | Cada llamada LLM |
| Costo estimado por tokens | `usage.total_cost` | Cada llamada LLM |
| Score del Judge | Score personalizado `judge_score` | POST /api/analyze/judge |
| Feedback del analista | Score personalizado `analyst_feedback_judge_score` | POST /api/feedback/ |
| Cache hit | Atributo del span `cache_hit: true/false` | Antes de llamadas LLM |
| Tasa de error | Spans fallidos | Cada llamada LLM |

### Estructura de traza por resolucion

```
Trace: POST /api/analyze/resolve [TXN-00051]
  ├── Span: qdrant_cache_check [hit=false, latency=12ms]
  ├── Span: v1_policy_eval [model=haiku, tokens=1847, latency=890ms]
  ├── Span: v1_resolution [model=sonnet, tokens=3204, latency=1340ms]
  └── Span: guardrails [warnings=1: APPROVE+BLOCKER corregido]

Trace: POST /api/analyze/judge [TXN-00051]
  ├── Span: v1_judge [model=sonnet, tokens=2400, latency=1100ms]
  ├── Score: judge_score = 8.7
  └── Score: judge_score -> adjunto a traza resolve
```

### Casos de uso del dashboard

**Tracking de costos:**
Langfuse agrega el uso de tokens por modelo, ruta y periodo. El dashboard revela si algun prompt esta consumiendo tokens desproporcionados (ej: un log summary muy largo pasado a v1_resolution).

**Tendencia de scores del Judge:**
Rastrear `judge_score` en el tiempo muestra si la calidad de las resoluciones esta mejorando a medida que se indexan nuevos precedentes. Una tendencia descendente indica que los casos auto-indexados recientes podrian ser de baja calidad.

**Tasa de cache hit:**
Una tasa alta (> 20%) indica que el sistema maneja muchos casos similares eficientemente. Una tasa baja puede indicar que el corpus es muy diverso o el umbral (0.92) es muy estricto.

**Desglose de latencia:**
Langfuse traza cada llamada de prompt por separado (policy_eval, resolution, judge). Si la latencia se dispara, la traza identifica cual prompt es el cuello de botella.

**Tasa de error:**
Parse de JSON fallido (a pesar de `validate_llm_output`), errores de conexion a Qdrant, o rate limits de la API de Anthropic se capturan como spans fallidos con mensajes de error.

### Alertas operativas

Complementando la observabilidad de Langfuse, el sistema emite alertas operativas a traves de `POST /api/alerts/`:

| Evento | Severidad | Fuente |
|--------|-----------|--------|
| Caso BLOCKER (rechazo automatico) | ERROR | Pipeline de resolucion (`analyze.py`) |
| Caso requiere HITL | WARNING | Pipeline de resolucion (`analyze.py`) |
| Error no manejado en n8n | ERROR | Error Handler (`workflow_ciri_errors.json`) |

Las alertas se almacenan en SQLite (tabla `alerts`) y se consultan via `GET /api/alerts/`. El panel de testing incluye un log de alertas con polling automatico cada 30 segundos, visible en la seccion "Log de alertas".

El Error Handler de n8n ademas envia un email a la direccion configurada en `$vars.ALERT_EMAIL`, asegurando notificacion inmediata para errores criticos.

### Habilitar observabilidad

```env
# .env
CB_LANGFUSE_ENABLED=true
CB_LANGFUSE_PUBLIC_KEY=pk-lf-...
CB_LANGFUSE_SECRET_KEY=sk-lf-...
CB_LANGFUSE_HOST=https://cloud.langfuse.com
```

Cuando `CB_LANGFUSE_ENABLED=false` (por defecto) **se sigue midiendo**: en lugar del
`NoOpTracer` corre `TrazadorLocal`, que implementa el mismo Protocol y anota trazas,
generaciones y puntajes en la SQLite del proyecto. Nadie aguas arriba sabe cual de los dos
esta corriendo.

La razon es que faltaba Langfuse no significa que no haya nada que medir: la latencia de cada
llamada existe igual, los tokens los informa el proveedor, y el costo de una corrida gratuita
es un dato —cero— y no una ausencia. Lo que Langfuse aporta y el registro local no es la traza
distribuida, el historico largo y su UI.

`GET /api/langfuse/stats` devuelve `fuente: "langfuse"` o `fuente: "local"`, y el panel lo
declara en pantalla: los numeros locales son de ese proceso y no sobreviven a un reinicio.

El `NoOpTracer` sigue existiendo para los tests, que no tienen por que escribir en disco.

---

## Estimacion de costos LLM

Por investigacion (1 caso de contracargo), con modelo dual:

| Llamada | Modelo | Tokens aprox. | Costo estimado |
|---------|--------|---------------|----------------|
| v1_policy_eval | Claude Haiku 4.5 | ~2K in + ~500 out | ~$0.004 |
| v1_resolution | Claude Sonnet 4.6 | ~3K in + ~800 out | ~$0.021 |
| v1_judge | Claude Sonnet 4.6 | ~2K in + ~400 out | ~$0.012 |
| **Total por caso** | | | **~$0.037** |

- El modelo dual reduce el costo ~21% vs. Sonnet para todo ($0.037 vs ~$0.047)
- El cache de idempotencia evita repetir un caso ya investigado: 113 segundos la primera
  vez, 2 la segunda. Es exact-match por transaccion, no semantico — el porque esta en
  `decisions.md`, decision 9
- Estimacion mensual para 1,000 casos: ~$30-37 USD con modelo dual
- Langfuse dashboard permite monitorear el costo real por traza y por prompt
- Para reducir costos aun mas: Haiku para los 3 pasos (~$0.012/caso), pero el score del Judge baja a ~8.6
