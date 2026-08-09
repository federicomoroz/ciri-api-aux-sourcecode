# Qué políticas se pueden aplicar con los datos que hay

> Reproducible: `python scripts/auditar_politicas.py`. No usa LLM —es una auditoría de
> datos, no una inferencia— así que corre gratis y da el mismo resultado siempre. El
> detalle queda en [`docs/evaluaciones/politicas_vs_dataset.json`](evaluaciones/politicas_vs_dataset.json).

## El hallazgo

**El agente deriva a un analista el 100% de los casos, y no es un defecto del agente.**
De las 17 políticas del reglamento, aplicadas fielmente a este dataset:

| | |
|---|---|
| Se pueden evaluar con los datos disponibles | **9** |
| Se evalúan sólo en parte, o por aproximación | **3** |
| Piden un dato que el dataset no tiene | **5** |

Y una de esas cinco decide el comportamiento de todo el sistema.

## POL-CB-002: por qué ningún caso puede cerrarse solo

> *"Para procesar un CB, el analista debe reunir: (a) comprobante de la transacción,
> (b) comunicación con el comercio, (c) evidencia del cliente. **Sin los tres elementos,
> el caso no puede avanzar.**"*

El dataset no tiene ningún campo para ninguno de los tres. Ni en `transacciones`, ni en
`casos`, ni en `logs`. El único texto libre es `notes`, poblado en 40 de 100
transacciones, y sus dos únicos valores son `"Alto riesgo detectado"` y
`"Revisar manualmente"` — ninguno es un comprobante, una comunicación ni una evidencia.

La política es **insatisfacible por construcción**, y su propio texto dice que sin esos
elementos el caso no avanza. Así que la conducta correcta de un agente que respeta el
reglamento es exactamente la que se observa: no cerrar ningún caso por su cuenta.

Vale la pena decirlo al revés, porque es lo que importa para el producto: **un agente que
aprobara casos con este dataset estaría violando el reglamento que se le pidió aplicar.**

## POL-CB-004: el umbral correcto sobre la población equivocada

> *"Todo comercio que supere el 1% de CBs sobre el total de transacciones en un mes
> calendario queda sujeto a revisión. Si supera el 2%, se suspende preventivamente."*

El 1% es una cifra de industria razonable — sobre **todas** las transacciones de un
comercio. Este dataset es una **muestra de disputas**: 60 contracargos sobre 100
transacciones, un ratio de corpus del 60%.

El resultado es que **los 15 comercios superan los dos umbrales**. El más limpio tiene
11,1% y el más sucio 80%. Un flag que marca 15 de 15 no distingue nada.

Hay además un problema de sujeto, y es el más interesante: lo que la política pone bajo
revisión es **el comercio**, no el contracargo de este cliente. Que MercadoLibre tenga
mal ratio no dice nada sobre si a *este* cliente le corresponde su reembolso. Hoy el
sistema trata ese hallazgo como una razón para frenar el caso individual.

El código ya tiene media solución: `Analyzer.linea_base_cb()` re-basea el ratio contra el
corpus en vez de contra el 2% de industria, y su comentario explica por qué. Esa idea
nunca llegó al camino que decide.

## Las otras tres que nunca se disparan

**POL-FRD-002** pide *"más de 3 transacciones en países distintos en menos de 24h"*.
`transactions.date` es una fecha sin hora, así que la ventana de 24 horas no se puede
medir. Y no hace falta llegar tan lejos: **ningún cliente del dataset tiene siquiera dos
transacciones el mismo día**. La condición es imposible.

**POL-FRD-004** pide ubicar la transacción respecto de la denuncia de la tarjeta. No hay
campo de denuncia ni de su hora; los logs mencionan "robada" en 5 líneas, como texto
libre dentro del detalle.

**POL-SLA-001** pide la primera respuesta al cliente dentro de 48 horas hábiles. No hay
registro de primera respuesta en ninguna tabla: el plazo no se puede cumplir ni incumplir,
porque no hay contra qué medirlo.

**POL-EXC-001 y POL-EXC-002** tampoco se disparan nunca, pero son excepciones —conceden
beneficios— así que no aplicar no frena nada. La segunda sí tiene un costo: sin nivel de
fidelidad en los datos, un cliente VIP real no recibe su trato preferente salvo que quien
dispara el caso lo declare a mano.

## Qué se hace con esto

**Lo que no hay que hacer es cambiar las políticas.** Vienen con el enunciado y son parte
de la entrada; editarlas para que el agente apruebe sería falsificar el problema. Y en
POL-CB-002 el reglamento tiene razón: sin evidencia, un contracargo no debería resolverse
automáticamente. El que está incompleto es el sistema de datos, no la política.

Lo que corresponde es lo que haría un analista sénior al recibir este reglamento:

1. **Separar los motivos de derivación.** Hoy el informe dice *"2 violación(es) de
   política — requiere revisión de analista"* y mete en la misma bolsa tres cosas que para
   quien recibe el caso son distintas: *falta documentación* (acción: pedirla), *el
   comercio tiene mal ratio* (acción: revisar al comercio, no al caso) y *este caso es
   riesgoso* (acción: decidir). Sin esa distinción, el analista tiene que releer todo.

2. **Que un hallazgo sobre el comercio no frene el caso del cliente.** POL-CB-004 debería
   producir una alerta operativa sobre el comercio —el sistema ya tiene `POST /api/alerts/`
   y el eje de "comercios problemáticos" pide justamente eso— en lugar de retener la
   resolución individual.

3. **Devolverle esto al dueño del reglamento.** Cinco políticas de diecisiete no se pueden
   aplicar por falta de datos. Eso es un pedido concreto de instrumentación: timestamps en
   las transacciones, campos de evidencia en el caso, registro de la denuncia de tarjeta,
   marca de primera respuesta, nivel de fidelidad del cliente. Con esos cinco datos, doce
   de las diecisiete políticas pasan a ser verificables.

## Por qué esto está en la entrega

La consigna pide identificar *"errores operativos, oportunidades de mejora e
inconsistencias de política"*. Esto es exactamente eso, y no se puede encontrar mirando el
código del agente: aparece sólo cuando se cruza el reglamento contra los datos y se cuenta.

Que el número sea 100% de derivación no es una métrica de un agente que funciona mal. Es
la medición de una brecha entre lo que el reglamento exige y lo que el sistema registra —
que es el hallazgo que un equipo de *risk intelligence* quiere tener antes de automatizar
nada.
