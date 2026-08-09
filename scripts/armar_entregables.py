"""Arma la carpeta `entregables/` desde el estado actual del repositorio.

**Por que existe.** `entregables/` no esta versionada —es el paquete que se manda,
no codigo— y se habia armado a mano. El resultado fue que quedo congelada: seguia
teniendo el codigo, los informes y la documentacion de dos semanas antes, con los
bugs ya corregidos adentro y los documentos que ya se habian arreglado en el repo.
Una copia manual de un entregable envejece a la primera correccion, y nadie se
entera porque no falla nada.

Con esto, regenerarla es un comando y siempre refleja lo que hay.

**Que NO hace:** no decide que va adentro. La forma de la carpeta —los nombres en
castellano, que documento corresponde a cual, cual es el diagrama principal— la
eligio una persona pensando en quien la va a abrir, y esa decision se conserva
tal cual. Lo unico que cambia es de donde sale el contenido.

    python scripts/armar_entregables.py
    python scripts/armar_entregables.py --destino /tmp/paquete
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

# Los documentos, con el nombre que tienen para quien abre la carpeta. El orden
# es el de la tabla del LEEME.
DOCUMENTACION = {
    "README.md": "README.md",
    "docs/api.md": "api.md",
    "docs/architecture.md": "arquitectura.md",
    "docs/decisions.md": "decisiones-tecnicas.md",
    "docs/demo_scenarios.md": "escenarios-demo.md",
    "docs/ejes.md": "los-7-ejes.md",
    "docs/mejora_continua.md": "mejora-continua.md",
    "docs/prompts.md": "prompts.md",
    "docs/rag_explanation.md": "rag.md",
}

# Los tres informes que viajan, con el nombre que dice de que caso es cada uno.
INFORMES = {
    "docs/HTML_Output_Examples/report_blocker_TXN-00051.html":
        "TXN-00051 — bloqueante.html",
    "docs/HTML_Output_Examples/report_high_TXN-00042.html":
        "TXN-00042 — riesgo alto con aprobacion humana.html",
    "docs/HTML_Output_Examples/report_no_latam_TXN-00089.html":
        "TXN-00089 — riesgo medio.html",
}

DIAGRAMA_PRINCIPAL = "docs/diagrams/n8n_workflow_analysis.html"


def archivos_del_proyecto() -> list[Path]:
    """Que es «el codigo», segun git: lo versionado mas lo nuevo sin ignorar.

    Se le pregunta a git en vez de mantener una lista de exclusiones propia. El
    repositorio ya declara en `.gitignore` que no es parte del proyecto —los
    entornos, las caches, la base de n8n, los logs— y una segunda lista solo
    puede desincronizarse: la primera version de este script tenia la suya y se
    llevaba puestos 16 MB de `n8n/data/database.sqlite`, que es el estado de una
    instancia local, no un entregable.

    `--others --exclude-standard` es lo que agrega los archivos nuevos todavia
    sin commitear. Sin eso, un entregable armado antes del commit sale sin el
    codigo que se acaba de escribir — que es exactamente cuando se arma.

    **`-z` no es un detalle.** Sin el, git escapa los nombres que no son ASCII:
    el dataset sale como `"data/Similaci\\303\\263n_dataset_..."`, entre comillas
    y con los bytes en octal. `Path()` no lo resuelve, el archivo no existe, y se
    saltea **en silencio** — el paquete se armaba sin el Excel del que sale toda
    la base. Con `-z` los nombres viajan crudos, separados por NUL.
    """
    salida = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=RAIZ, capture_output=True, check=True,
    ).stdout.decode("utf-8")
    return [Path(nombre) for nombre in salida.split("\0") if nombre.strip()]


def pasos_del_diagrama() -> int:
    """Cuantos pasos numerados dibuja el circuito. El LEEME lo cita."""
    html = (RAIZ / DIAGRAMA_PRINCIPAL).read_text(encoding="utf-8")
    return len(re.findall(r'<span class="step">(\d+)</span>', html))


def leeme(pasos: int) -> str:
    """El indice de la carpeta. Vive aca para que no se desincronice solo."""
    return f"""# Agente Inteligente de Contracargos — CIRI

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

**`Diagrama del circuito.html`.** Es el workflow entero en una página: los {pasos} pasos
en orden de ejecución, cada conexión, y qué hace cada nodo al tocarlo. No necesita
conexión ni instalar nada.

Después, `Documentacion/los-7-ejes.md` recorre los siete ejes de la consigna uno por
uno, con el archivo o el nodo concreto que lo resuelve y un comando para comprobarlo.

## Probarlo de verdad

Importá los tres archivos de `Flujo n8n/` en cualquier instancia de n8n y disparalo:

```bash
curl -X POST https://<tu-n8n>/webhook/chargeback-agent \\
  -H "Content-Type: application/json" \\
  -d '{{"transaction_id": "TXN-00051", "motivo": "No reconoce la compra"}}' \\
  -o reporte.html
```

No hay que configurar variables ni credenciales: los nodos apuntan por defecto a la
API pública del proyecto. Si levantaste la API local, mandá `api_base_url` en el
cuerpo — sin eso el workflow le escribe a la instancia pública y tu panel local no
va a encontrar ni las alertas ni el feedback. El detalle, y las otras dos formas de
probarlo, están en `Documentacion/README.md`.

## El código

`Codigo/` tiene el proyecto completo: la API en FastAPI, el pipeline de RAG, los
prompts versionados y los tests. Es el mismo contenido del repositorio, sin los
entornos virtuales ni las cachés.

Los otros cuatro diagramas —la API por dentro, cómo se hablan n8n y la API, el RAG
y los tests— están en `Codigo/docs/diagrams/`, y también se abren solos.
"""


def armar(destino: Path) -> None:
    if destino.exists():
        shutil.rmtree(destino)
    destino.mkdir(parents=True)

    # 1. El código, entero y sin basura.
    for relativo in archivos_del_proyecto():
        origen = RAIZ / relativo
        if not origen.is_file():          # un archivo borrado que git todavia lista
            continue
        copia = destino / "Codigo" / relativo
        copia.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(origen, copia)

    # 2. Los workflows, que son EL entregable de la consigna.
    (destino / "Flujo n8n").mkdir()
    for wf in sorted((RAIZ / "n8n").glob("*.json")):
        shutil.copy2(wf, destino / "Flujo n8n" / wf.name)

    # 3. La documentación, con los nombres de quien la va a leer.
    (destino / "Documentacion").mkdir()
    for origen, nombre in DOCUMENTACION.items():
        shutil.copy2(RAIZ / origen, destino / "Documentacion" / nombre)

    # 4. Los informes de ejemplo.
    (destino / "Reportes de ejemplo").mkdir()
    for origen, nombre in INFORMES.items():
        shutil.copy2(RAIZ / origen, destino / "Reportes de ejemplo" / nombre)

    # 5. El diagrama principal, en la raíz: es por donde se empieza.
    shutil.copy2(RAIZ / DIAGRAMA_PRINCIPAL, destino / "Diagrama del circuito.html")

    # 6. El índice, con los números del estado actual.
    (destino / "LEEME.md").write_text(leeme(pasos_del_diagrama()), encoding="utf-8")


def verificar(destino: Path) -> list[str]:
    """Que esté todo lo que la consigna pide. Un paquete incompleto no avisa solo."""
    faltan = []
    for esperado in (
        "LEEME.md", "Diagrama del circuito.html", "Codigo/api/app/main.py",
        "Codigo/n8n/workflow_ciri_agent.json", "Flujo n8n/workflow_ciri_agent.json",
        # El Excel del que sale toda la base. Se perdia en silencio porque git
        # escapa el acento de «Similación» y el archivo escapado no existe: el
        # paquete quedaba sin dataset y nada avisaba.
        "Codigo/data/Similación_dataset_contracargos_.xlsx",
        # Sin estos, el modo demo no tiene con que responder.
        "Codigo/data/informes_demo/analisis_TXN-00051.json",
        # Los otros cuatro diagramas viajan adentro del codigo.
        "Codigo/docs/diagrams/api.html",
        "Codigo/scripts/seed_data.py",
        *(f"Documentacion/{n}" for n in DOCUMENTACION.values()),
        *(f"Reportes de ejemplo/{n}" for n in INFORMES.values()),
    ):
        if not (destino / esperado).exists():
            faltan.append(esperado)

    # Que el paquete no se quede corto sin que se note: si de golpe trae la mitad
    # de los archivos, algo dejo de matchear.
    copiados = sum(1 for _ in (destino / "Codigo").rglob("*") if _.is_file())
    if copiados < 150:
        faltan.append(f"Codigo/ trae solo {copiados} archivos: algo no se copio")

    for prohibido in ("Codigo/.git", "Codigo/api/.venv", "Codigo/entregables"):
        if (destino / prohibido).exists():
            faltan.append(f"NO deberia estar: {prohibido}")
    if list(destino.rglob("__pycache__")):
        faltan.append("NO deberia estar: __pycache__")
    if list(destino.rglob("*.db")):
        faltan.append("NO deberia estar: *.db")
    return faltan


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--destino", default=str(RAIZ / "entregables"))
    args = p.parse_args()

    destino = Path(args.destino)
    print(f"Armando {destino} desde {RAIZ}\n")
    armar(destino)

    if problemas := verificar(destino):
        print("El paquete quedo incompleto:")
        for x in problemas:
            print("  -", x)
        return 1

    archivos = sum(1 for _ in destino.rglob("*") if _.is_file())
    peso = sum(f.stat().st_size for f in destino.rglob("*") if f.is_file())
    print(f"  {archivos} archivos · {peso / 1_048_576:.1f} MB")
    for hijo in sorted(destino.iterdir()):
        marca = "/" if hijo.is_dir() else ""
        print(f"    {hijo.name}{marca}")
    print("\nListo. Verificado: están los seis entregables y no viajan entornos ni cachés.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
