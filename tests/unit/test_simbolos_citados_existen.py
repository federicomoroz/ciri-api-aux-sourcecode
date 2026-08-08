"""Los nombres de codigo que la documentacion cita tienen que existir.

Habia tests que atrapaban un badge de tests viejo, un conteo de endpoints viejo
y una version de prompt vieja. Ninguno miraba los **simbolos**, y despues del
refactor cinco funciones citadas en seis documentos habian dejado de existir:
`_determine_outcome`, `_detect_divergence`, `_validate_resolution`,
`_summarize_logs` y `_build_precedent_summary`. La doc explicaba el sistema con
el vocabulario de la version anterior, que es peor que no explicarlo: quien
busca esos nombres en el repo no encuentra nada y concluye que la doc miente.

El test recorre `docs/` buscando identificadores en backticks con forma de
funcion o de constante, y verifica que existan en `api/app/`. Solo mira los que
puede resolver sin ambiguedad — lo demas es prosa, no una promesa verificable.
"""

import re
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
DOCS = sorted(
    f for f in RAIZ.joinpath("docs").rglob("*")
    if f.suffix in (".md", ".html") and f.is_file()
)

# Lo que el codigo define hoy: nombres de def/class y constantes en mayusculas.
def _vocabulario_del_codigo() -> set[str]:
    nombres: set[str] = set()
    for py in RAIZ.joinpath("api/app").rglob("*.py"):
        texto = py.read_text(encoding="utf-8")
        nombres |= set(re.findall(r"^\s*(?:async\s+)?def\s+(\w+)", texto, re.MULTILINE))
        nombres |= set(re.findall(r"^\s*class\s+(\w+)", texto, re.MULTILINE))
        # El guion bajo inicial cuenta: `_PUBLIC_PREFIXES` es una constante de
        # modulo tan real como las publicas, y sin el se la daba por muerta.
        nombres |= set(re.findall(r"^(_?[A-Z][A-Z0-9_]{3,})\s*[:=]", texto, re.MULTILINE))
    return nombres


# Nombres que aparecen en backticks pero no son simbolos de este repo: los trae
# una libreria, son de n8n, o son campos de un JSON. Lista corta y explicita:
# preferimos ignorar de mas antes que hacer fallar el test por prosa.
AJENOS = frozenset({
    "model_dump", "model_validate", "dict", "list", "frozenset", "str", "int",
    "float", "bool", "compare_digest", "getattr", "hasattr", "isinstance",
    "json", "loads", "dumps", "search", "upsert", "count", "get", "post", "put",
    "delete", "append", "items", "keys", "values", "format", "join", "split",
    "strip", "replace", "lower", "upper", "sorted", "len", "range", "print",
    "as_completed", "now",          # concurrent.futures y datetime
    "_query",                       # clave que el retriever agrega al resultado, no un simbolo
})


@pytest.fixture(scope="module")
def vocabulario() -> set[str]:
    v = _vocabulario_del_codigo()
    assert len(v) > 200, f"solo se leyeron {len(v)} simbolos de api/app — cambio la estructura?"
    return v


@pytest.mark.parametrize("doc", DOCS, ids=lambda f: f.name)
def test_ninguna_funcion_citada_murio_en_un_refactor(doc, vocabulario):
    texto = doc.read_text(encoding="utf-8")

    # `nombre()` o `modulo.nombre()` entre backticks: una promesa de que existe.
    citados = {
        m.group(1) for m in re.finditer(r"`(?:[\w./]+\.)?(\w+)\(\)`", texto)
    } | {
        m.group(1) for m in re.finditer(r"`(_\w+)`", texto)   # privados, siempre nuestros
    }
    citados = {c for c in citados if c not in AJENOS}

    muertos = sorted(c for c in citados if c not in vocabulario)
    assert not muertos, (
        f"{doc.relative_to(RAIZ).as_posix()} cita simbolos que no existen en "
        f"api/app: {muertos}"
    )
