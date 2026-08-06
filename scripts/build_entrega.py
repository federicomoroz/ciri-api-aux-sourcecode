"""Arma la entrega: la carpeta `entregables/` y el .zip listo para adjuntar.

La entrega se manda por mail como un unico archivo, asi que es una salida y no
una fuente: no se versiona, se construye. Esto garantiza que lo que recibe el
evaluador no pueda quedar desfasado del repositorio, que fue justo lo que paso
cuando las copias vivian en git.

El codigo se saca con `git archive`, asi entra solo lo versionado: nunca el
.env, ni la base, ni los __pycache__.

    python scripts/build_entrega.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / "entregables"
DIST = ROOT / "dist"
ZIP_NAME = "CIRI - Agente de Contracargos - Federico Moroz.zip"

# Lo que pide la consigna, en el orden en que lo pide.
ENTREGABLES: list[tuple[Path, str]] = [
    (ROOT / "n8n/workflow_ciri_agent.json", "Flujo n8n/workflow_ciri_agent.json"),
    (ROOT / "n8n/workflow_ciri_errors.json", "Flujo n8n/workflow_ciri_errors.json"),
    (ROOT / "n8n/workflow_ciri_form.json", "Flujo n8n/workflow_ciri_form.json"),
    (ROOT / "README.md", "Documentacion/README.md"),
    (ROOT / "docs/architecture.md", "Documentacion/arquitectura.md"),
    (ROOT / "docs/ejes.md", "Documentacion/los-7-ejes.md"),
    (ROOT / "docs/prompts.md", "Documentacion/prompts.md"),
    (ROOT / "docs/rag_explanation.md", "Documentacion/rag.md"),
    (ROOT / "docs/mejora_continua.md", "Documentacion/mejora-continua.md"),
    (ROOT / "docs/decisions.md", "Documentacion/decisiones-tecnicas.md"),
    (ROOT / "docs/demo_scenarios.md", "Documentacion/escenarios-demo.md"),
    (ROOT / "docs/diagrams/workflow.html", "Diagrama del circuito.html"),
    (
        ROOT / "docs/examples/report_blocker_TXN-00051.html",
        "Reportes de ejemplo/TXN-00051 — bloqueante.html",
    ),
    (
        ROOT / "docs/examples/report_high_TXN-00042.html",
        "Reportes de ejemplo/TXN-00042 — riesgo alto con aprobacion humana.html",
    ),
    (
        ROOT / "docs/examples/report_medium_TXN-00089.html",
        "Reportes de ejemplo/TXN-00089 — riesgo medio.html",
    ),
]

LEEME = """# Agente Inteligente de Contracargos — CIRI

Prueba técnica de AI Automation Engineer / AI Solutions Architect.
**Federico Palatnik Moroz**

## Qué pedía la consigna y dónde está

| Entregable | Archivo |
|---|---|
| Flujo exportado de n8n | `Flujo n8n/` — el principal más el manejador de errores y el formulario |
| README con explicación de arquitectura | `Documentacion/README.md` |
| Diagrama de la solución | `Diagrama del circuito.html` — abrilo en el navegador |
| Prompts documentados | `Documentacion/prompts.md` |
| Explicación del RAG | `Documentacion/rag.md` |
| Explicación del proceso de mejora continua | `Documentacion/mejora-continua.md` |
| HTML mostrando resultados | `Reportes de ejemplo/` — tres casos reales del dataset |

## Por dónde empezar

**`Diagrama del circuito.html`.** Es el workflow entero en una página: los 29 pasos
en orden de ejecución, cada conexión, y qué hace cada nodo al tocarlo. No necesita
conexión ni instalar nada.

Después, `Documentacion/los-7-ejes.md` recorre los siete ejes de la consigna uno por
uno, con el archivo o el nodo concreto que lo resuelve y un comando para comprobarlo.

## Probarlo de verdad

Importá los tres archivos de `Flujo n8n/` en cualquier instancia de n8n y disparalo:

```bash
curl -X POST https://<tu-n8n>/webhook/chargeback-agent \\
  -H "Content-Type: application/json" \\
  -d '{"transaction_id": "TXN-00051", "motivo": "No reconoce la compra"}' \\
  -o reporte.html
```

No hay que configurar variables ni credenciales: los nodos apuntan por defecto a la
API pública del proyecto. El detalle, y las otras dos formas de probarlo, están en
`Documentacion/README.md`.

## El código

`Codigo/` tiene el proyecto completo: la API en FastAPI, el pipeline de RAG, los
prompts versionados y los tests. Es el mismo contenido del repositorio.
"""


def run(*args: str) -> None:
    subprocess.run(args, cwd=ROOT, check=True, stdout=subprocess.DEVNULL)


def main() -> int:
    faltan = [src for src, _ in ENTREGABLES if not src.exists()]
    if faltan:
        for src in faltan:
            print(f"FALTA: {src.relative_to(ROOT)}", file=sys.stderr)
        return 1

    if DEST.exists():
        shutil.rmtree(DEST)
    DEST.mkdir(parents=True)

    for src, rel in ENTREGABLES:
        dst = DEST / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    (DEST / "LEEME.md").write_text(LEEME, encoding="utf-8")

    # El codigo, tal como esta versionado: nada de .env, base ni __pycache__.
    with tempfile.TemporaryDirectory() as tmp:
        tar = Path(tmp) / "codigo.tar"
        run("git", "archive", "--format=tar", "-o", str(tar), "HEAD")
        with tarfile.open(tar) as t:
            t.extractall(DEST / "Codigo", filter="data")

    DIST.mkdir(exist_ok=True)
    zip_path = DIST / ZIP_NAME
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(DEST.rglob("*")):
            if path.is_file():
                z.write(path, Path("CIRI — Agente de Contracargos") / path.relative_to(DEST))

    archivos = sum(1 for p in DEST.rglob("*") if p.is_file())
    print(f"{DEST.relative_to(ROOT)}/  ({archivos} archivos)")
    print(f"{zip_path.relative_to(ROOT)}  ({zip_path.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
