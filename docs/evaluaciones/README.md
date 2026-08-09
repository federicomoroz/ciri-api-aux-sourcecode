# Evaluaciones

Cada archivo de esta carpeta es la salida de `python scripts/evaluar.py`: una corrida
del pipeline completo sobre una muestra del dataset, con el detalle caso por caso, la
configuración usada y la fecha. Es lo que convierte un número en algo auditable.

El comando que generó cada uno está en su propio `configuracion.muestreo`, y la semilla
es fija: la misma muestra se puede volver a correr.

---

## `2026-08-09-gemini-flash-lite.json`

**Qué dice:** promedio **8,97/10** sobre **7 casos, los 7 medidos**, con
`gemini-flash-lite-latest` en los tres pasos. Mediana 9,1 · σ 0,42 · rango 8,1–9,4 ·
costo **USD 0,00**. Corrida con `--modo demo`: el artefacto se autodescribe con
`"modo": "demo"` y `"es_free_tier": true`.

**Qué tiene de distinto:** es la primera corrida **completa** (la anterior perdió 17 de 20
casos contra la cuota diaria) y corre exactamente la configuración del modo demo del
deploy — el mismo modelo que ve un evaluador cuando aprieta «analizar» en el panel de
Render sin clave propia.

**Qué NO dice:** lo mismo que la anterior — no es el score del sistema entregado ni mueve
el badge. Y esta vez hay prueba concreta: tres casos se midieron en ambas corridas, con
los mismos prompts y la misma semilla, y los dos modelos gratuitos no se ponen de acuerdo:

| Caso | `flash` (08-08) | `flash-lite` (08-09) | Δ |
|---|---|---|---|
| TXN-00024 | 7,3 | 9,1 | +1,8 |
| TXN-00042 | 9,8 | 9,1 | −0,7 |
| TXN-00033 | 8,1 | 8,1 | 0,0 |

Hasta ±1,8 por caso en las dos direcciones, porque el juez corre en el mismo modelo que
resolvió: cada free tier se puntúa a sí mismo con su propia vara. Un score de estas
corridas mide al modelo gratuito de turno, no al sistema.

**El criterio más flojo es real y coincide entre corridas:** `actionability` (7,69 acá)
es el único promedio por debajo de 8 — los `next_steps` del modelo chico son más
genéricos. `policy_consistency` (9,93) y `risk_assessment` (9,71) son altos porque el
código decide esos campos y el juez califica la propuesta del modelo, que rara vez los
contradice.

**Una fe de erratas que aplica a ambos artefactos:** el campo `propuesta_del_modelo`
salió `{}` en todos los casos de las dos corridas. Es un defecto del harness, no del
pipeline: cuando el campo `propuesta_del_modelo` del servicio se renombró y perdió su
guion bajo inicial, el lector del harness quedó leyendo el nombre viejo, y `.get()` con
default no avisa. El Juez **sí** recibió la propuesta —viaja por dentro del servicio, los
scores no están afectados—; lo que falta es solo su copia en el registro. Corregido en
`scripts/evaluar.py`, con un test que ata los campos que lee a los de `ResolveResponse`.

**Para volver a generarlo:**

```bash
python scripts/evaluar.py --n 7 --modo demo
```

**Versiones de prompt de esta corrida:** `policy_eval` v1.4 · `resolution` v3.2 ·
`judge` v2.2 — las mismas de la corrida anterior, así que la diferencia entre ambas es
solo el modelo.

---

## `2026-08-08-gemini-flash.json`

**Qué dice:** promedio **8,4/10** sobre **3 casos**, con `gemini-flash-latest` en los tres
pasos. Mediana 8,1 · σ 1,28 · rango 7,3–9,8 · costo **USD 0,00**.

**Qué NO dice, y esto importa más que el número:**

**No es el score del sistema entregado.** Los prompts están afinados para Claude y la
configuración documentada es Haiku para políticas y Sonnet para síntesis y juez. Un
modelo más chico se penaliza dos veces: razona con menos profundidad y después se puntúa
a sí mismo, porque el juez corre en el mismo modelo que resolvió.

**No reemplaza al 9,1 del badge, ni lo refuta.** Con n=3 y σ=1,28 no hay muestra para
afirmar nada: los tres casos que sobrevivieron dan 7,3, 8,1 y 9,8. Lo que sí hace es
existir, que es lo que faltaba — el 9,1 salió de corridas manuales que no dejaron
artefacto, y eso está declarado en
[`mejora_continua.md`](../mejora_continua.md#como-se-midio-el-91).

**Por qué son 3 y no 20.** Los otros 17 casos murieron contra los límites del free tier,
y el archivo registra el motivo de cada uno:

| Casos | Motivo |
|---|---|
| 15 | `429` de Gemini — **cuota diaria** agotada (`generate_content_free_tier_requests`) |
| 1 | `EmbeddingRateLimit` de Voyage — 3 pedidos por minuto sin método de pago |
| 1 | `ReadTimeout` |

**Lo que esto reveló sobre el `RateLimiter`.** El limitador hizo lo suyo: espacia los
pedidos según los pedidos-por-minuto de cada proveedor (`perfiles.py` le da 5 a la familia
Gemini y 3 a Voyage) y por eso no hubo 429 por ráfaga. Lo que agotó la corrida fue el
**tope diario**, que el limitador no modela — y que además se comparte con el deploy de
Render, porque usa la misma clave. Espaciar por minuto no sirve contra una cuota por día:
lo único que la respeta es correr menos casos, o correr con una cuenta paga.

**Para volver a generarlo:**

```bash
# Con el free tier (costo cero, sujeto al tope diario del proveedor)
CB_LLM_PROVIDER=gemini CB_LLM_MODEL=gemini-flash-latest \
  python scripts/evaluar.py --n 20 --salida docs/evaluaciones/<fecha>-gemini-flash.json

# En la configuración documentada (Anthropic, ~USD 0.037 por caso)
python scripts/evaluar.py --n 20 --tope-usd 1.0
```

**Versiones de prompt de esta corrida:** `policy_eval` v1.4 · `resolution` v3.2 ·
`judge` v2.2. Van dentro del JSON, así que una corrida vieja nunca se confunde con la
configuración de hoy.
