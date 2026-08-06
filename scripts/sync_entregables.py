"""Regenera la carpeta `entregables/` a partir de las fuentes del repo.

`entregables/` es solo un empaquetado para el evaluador: copias de archivos que
viven en `docs/`, `n8n/` y la raiz. Este script existe para que esas copias no
se desincronicen de la fuente. Correr despues de tocar docs o workflows:

    python scripts/sync_entregables.py
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / "entregables"

# (origen, destino relativo dentro de entregables/)
FILES: list[tuple[Path, str]] = [
    (ROOT / "README.md", "Documentacion/README.md"),
    (ROOT / "docs/architecture.md", "Documentacion/architecture.md"),
    (ROOT / "docs/decisions.md", "Documentacion/decisions.md"),
    (ROOT / "docs/demo_scenarios.md", "Documentacion/demo_scenarios.md"),
    (ROOT / "docs/mejora_continua.md", "Documentacion/mejora_continua.md"),
    (ROOT / "docs/prompts.md", "Documentacion/prompts.md"),
    (ROOT / "docs/rag_explanation.md", "Documentacion/rag_explanation.md"),
    (ROOT / "docs/workflow_diagram.html", "Documentacion/workflow_diagram.html"),
    (ROOT / "n8n/workflow_ciri_agent.json", "n8n/workflow_ciri_agent.json"),
    (ROOT / "n8n/workflow_ciri_errors.json", "n8n/workflow_ciri_errors.json"),
    (ROOT / "n8n/workflow_ciri_form.json", "n8n/workflow_ciri_form.json"),
    (
        ROOT / "docs/examples/report_blocker_TXN-00051.html",
        "Reports Examples/report_blocker_TXN-00051.html",
    ),
    (
        ROOT / "docs/examples/report_high_TXN-00042.html",
        "Reports Examples/report_high_TXN-00042.html",
    ),
    (
        ROOT / "docs/examples/report_medium_TXN-00089.html",
        "Reports Examples/report_medium_TXN-00089.html",
    ),
]


def main() -> int:
    missing = [src for src, _ in FILES if not src.exists()]
    if missing:
        for src in missing:
            print(f"FALTA: {src.relative_to(ROOT)}", file=sys.stderr)
        return 1

    for src, rel in FILES:
        dst = DEST / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        print(f"{src.relative_to(ROOT)} -> entregables/{rel}")

    print(f"\n{len(FILES)} archivos sincronizados.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
