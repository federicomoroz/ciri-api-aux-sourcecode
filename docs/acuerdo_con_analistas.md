# Cuánto coincide el agente con los analistas que ya resolvieron estos casos

> Reproducible: `python scripts/medir_acuerdo.py --modo demo`. El detalle caso por caso
> queda en [`docs/evaluaciones/acuerdo_con_analistas.json`](evaluaciones/acuerdo_con_analistas.json).

## Por qué esta medición

La única medida de calidad del sistema era el Juez: un modelo puntuando a otro modelo en
cinco criterios subjetivos. Y al lado, sin usar, había **60 resoluciones humanas
etiquetadas** en `cases.resolution` — los mismos casos que el RAG ya recupera como
precedentes. Un número que sale de comparar contra decisiones reales vale más que uno que
sale de pedirle una nota a un LLM.

## El mapeo, que es la mitad del trabajo

Los analistas usan cinco resoluciones; el agente tiene tres acciones. Sólo tres se
corresponden:

| Resolución del analista | Acción equivalente | Casos |
|---|---|---|
| A favor del cliente | `APPROVE` | 9 |
| A favor del comercio | `REJECT` | 12 |
| En escalación | `PENDING_HITL` | 13 |

Las otras dos **quedan fuera del porcentaje**, y decirlo es parte del resultado:

- **Reembolso parcial** (6 casos): el agente no tiene una acción de reembolso parcial.
  Mapearla a `APPROVE` contaría como acuerdo una decisión que el agente no puede tomar.
- **Caso cerrado sin resolución** (7 casos): no es una decisión, es la ausencia de una.

Se mide sobre el caso **más reciente** de cada transacción, que es el que el sistema
considera en disputa. Eso deja 47 casos, de los cuales **34 son comparables**.

## El resultado

**Acuerdo: 12 de 34 — 35%.** Cero errores de ejecución.

| Resolución humana | Lo que dijo el agente | |
|---|---|---|
| A favor del cliente (9) | `PENDING_HITL` 8 · `REJECT` 1 | **0 aciertos** |
| A favor del comercio (12) | `PENDING_HITL` 12 | **0 aciertos** |
| En escalación (13) | `PENDING_HITL` 12 · `REJECT` 1 | **12 aciertos** |

La matriz tiene una sola columna poblada, y eso es todo el hallazgo:

**El agente coincide con el analista exactamente donde el analista tampoco resolvió.** De
los 21 casos en que una persona tomó una decisión —9 a favor del cliente, 12 a favor del
comercio— el agente no reprodujo **ninguna**.

No es un 35% de acierto. Es un sistema que deriva siempre, midiendo 35% porque en 12 de 34
casos derivar era lo correcto.

## Por qué deriva siempre

Ya estaba diagnosticado en [`politicas_vs_dataset.md`](politicas_vs_dataset.md), y esta
corrida lo cuantifica: **POL-CB-004 falla en 31 de los 34 casos**. Es la política que compara
un umbral de industria del 1% contra una muestra de disputas cuyo comercio más limpio tiene
11%. Una sola violación basta para derivar.

Detrás vienen POL-SLA-002 (26 casos) y POL-SLA-003 (16), que son incumplimientos de plazo
legítimos: son casos históricos que efectivamente tardaron. Y POL-CB-002 escalada por otro
camino —`WARNING` con pedido de revisión— porque el dataset no tiene los tres documentos
que exige.

Un solo caso derivó **sin ninguna política violada**: TXN-00019, donde el modelo gratuito
no devolvió un JSON parseable y el sistema cayó a su comportamiento seguro. El guardrail
funcionó, pero conviene no contarlo como decisión de política.

## Lo que la medición encontró sin buscarlo

Acá está lo que hace que valga la pena medir contra datos reales en vez de contra un juez.

**Las dos únicas veces que el agente resolvió solo, contradijo a la persona.** Son los dos
casos en cripto, donde POL-EXC-003 —la única política habilitada para bloquear— rechaza sin
intervención humana:

- **TXN-00071**: el analista escaló; el agente rechazó. La observación dice *"Cliente fue
  contactado, desistió del reclamo"*, o sea que el caso terminó por abandono, no por
  escalamiento.
- **TXN-00059**: el analista resolvió *a favor del cliente*… y su propia observación dice
  ***"Política de devolución vencida, se rechaza el CB"***. **La etiqueta se contradice con
  la nota del analista que la escribió.**

**Es la única contradicción evidente de las 60**, pero importa más de lo que su frecuencia
sugiere: es el dato contra el que se está midiendo. Un acuerdo del 35% se calcula sobre
etiquetas que en al menos un caso no dicen lo que pasó.

**Y una inconsistencia de política real:** en **TXN-00028** un analista concedió un
reembolso parcial sobre una transacción en cripto. POL-EXC-003 dice que las transacciones
en criptomonedas *"no son elegibles para chargeback **bajo ninguna circunstancia**"*. O la
práctica no sigue la política, o la política no describe la práctica — y ésa es una
pregunta para el dueño del reglamento, no para el agente.

## Qué NO prueba este número

- **n = 34**, sobre un dataset de 100 transacciones. Alcanza para ver una matriz degenerada;
  no alcanza para afirmar una tasa de acierto.
- Corrió con el **modelo gratuito** del modo demo, no con la configuración documentada.
  Para el resultado da casi igual —la acción la fija código determinista a partir de los
  veredictos, no el modelo— pero conviene decirlo.
- **Las etiquetas son ruidosas.** Al menos una contradice su propia observación.
- El mapeo deja 13 casos afuera. Están declarados, no escondidos.

## Para qué sirve

Sirve para responder la pregunta que un líder técnico va a hacer, con un número y una
matriz en vez de una opinión: *"¿tu agente decide como deciden tus analistas?"*

La respuesta honesta es **todavía no, y sé exactamente por qué**: mientras POL-CB-004
compare un umbral de industria contra una muestra de disputas y POL-CB-002 exija
documentos que el sistema no registra, la única salida disponible es derivar. El día que
esas dos se resuelvan, esta misma medición vuelve a correr con un comando y la matriz deja
de tener una sola columna.

Eso es lo que convierte un sistema en algo que se puede mejorar: no la nota, sino tener
contra qué compararla.
