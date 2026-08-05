# Prompt de corrección — pegar en ChatGPT (mismo chat)

---

La infografía está muy bien. Necesito que corrijas estas cosas y la regeneres:

## Correcciones de texto

1. En la etapa 04, donde dice "segúnnivel de riesgo" falta un espacio → debe decir "según nivel de riesgo"
2. En el feedback loop, donde dice "auto-indexado como, precedente" sobra una coma → debe decir "auto-indexado como precedente en Qdrant"
3. Revisá que no haya otros errores de tipeo o palabras pegadas en toda la imagen

## Tipografía más grande

Los textos descriptivos dentro de cada etapa del pipeline son demasiado chicos para leer en el feed de LinkedIn (se ve en celular). Agranda un ~20% la tipografía de:
- Las descripciones de cada etapa (el texto gris debajo del título de cada paso)
- Los bullets/items dentro de cada etapa
- Los nombres de las 6 fuentes en la etapa 02
- Los campos de la sección "El código decide, la IA explica"

Los títulos y stats ya están bien de tamaño.

## Agregar: mini-preview del panel interactivo

Entre la sección "El código decide, la IA explica" y el stack tecnológico, agregá una sección nueva:

**Título de sección:** "Panel interactivo — resultado real"

Mostrá un rectángulo estilo browser/app con fondo claro (#e8e4df) que simule la pantalla del panel con estos datos exactos:

```
Caso: TXN-00051
Tipo: Cripto · fraud score 8

Resultado: RECHAZADO AUTOMÁTICO (en rojo)

Riesgo: BLOCKER
Acción: RECHAZAR
Confianza: 95%
Judge: 9.4 / 10

Pasos completados: ✓ Transacción · ✓ Logs · ✓ Políticas RAG · ✓ Casos RAG · ✓ Comercio · ✓ Resolución (Sonnet) · ✓ Reporte

Costo: $0.03 · Tiempo: 11.8s · BYOK
```

Debajo del mockup, una línea: "SSE streaming en tiempo real · 3 modos de pipeline · BYOK (tu propia API key)"

## NO cambies

- Los datos/números — siguen siendo los mismos
- La estructura general — las 4 etapas, la sidebar, el insight Python vs LLM
- Los colores y la estética dark
- El tamaño: 1080×1350px
- El idioma: todo en español
