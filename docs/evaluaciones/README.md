# Evaluaciones

Cada archivo de esta carpeta es la salida de `python scripts/evaluar.py`: una corrida
del pipeline completo sobre una muestra del dataset, con el detalle caso por caso, la
configuración usada y la fecha. Es lo que convierte un número en algo auditable.

El comando que generó cada uno está en su propio `configuracion.muestreo`, y la semilla
es fija: la misma muestra se puede volver a correr.

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
