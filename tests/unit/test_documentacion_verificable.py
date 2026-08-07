"""Los numeros que afirma la documentacion tienen que estar respaldados.

El badge del Judge anuncia 9.1: el promedio sobre las corridas de desarrollo. Los
tres informes que viajan en el paquete promedian 8.67, porque son los tres casos
mas contenciosos del dataset y no una muestra al azar.

La regla que se verifica NO es que los dos numeros coincidan —serian dos medidas
de cosas distintas, y forzar la igualdad falsearia una de las dos—, sino que la
diferencia este DECLARADA: si el badge se aparta del subconjunto que viaja, la
documentacion tiene que decir de donde sale cada numero. Un numero sin
procedencia se desmiente con un comando; uno con procedencia se discute.
"""

import json
import re
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
INFORMES = RAIZ / "data" / "informes_demo"
DOCS = RAIZ / "docs"
# El build renombra README.md a "1 — LEEME.md" para que quede primero al abrir
# la carpeta. Buscarlo por un solo nombre hacia que estos tests fallaran justo
# sobre el paquete entregado, que es donde mas importaba que corrieran.
LEEME = next(
    (f for f in (RAIZ / "README.md", RAIZ / "1 — LEEME.md") if f.is_file()),
    RAIZ / "README.md",
)
DOCUMENTOS = [LEEME, *sorted(DOCS.glob("*.md"))]


def scores_reales() -> list[float]:
    return [
        json.loads(f.read_text(encoding="utf-8"))["judge"]["overall_score"]
        for f in sorted(INFORMES.glob("analisis_*.json"))
    ]


@pytest.fixture(scope="module")
def promedio() -> float:
    scores = scores_reales()
    assert scores, "no hay informes precomputados contra los cuales verificar"
    return round(sum(scores) / len(scores), 1)


def _menciona_el_promedio(texto: str, promedio: float) -> bool:
    """Acepta el promedio redondeado o con dos decimales.

    Escribir 8.67 es mas honesto que 8.7 y no deberia hacer fallar al test que
    pide justamente que el numero este escrito.
    """
    exacto = round(sum(scores_reales()) / len(scores_reales()), 2)
    return str(promedio) in texto or str(exacto) in texto


def badge_del_judge() -> float:
    m = re.search(r"Judge%20Score-([0-9.]+)%2F10", LEEME.read_text(encoding="utf-8"))
    assert m, f"{LEEME.name} perdio el badge del Judge score"
    return float(m.group(1))


def test_si_el_badge_se_aparta_del_paquete_la_diferencia_esta_declarada(promedio):
    """El badge puede medir otra cosa; lo que no puede es no decirlo."""
    badge = badge_del_judge()
    if badge == pytest.approx(promedio, abs=0.05):
        return
    portada = LEEME.read_text(encoding="utf-8")
    assert str(badge) in portada, f"la portada no menciona el {badge} del badge"
    assert _menciona_el_promedio(portada, promedio), (
        f"el badge dice {badge}, los informes del paquete promedian {promedio}, y la "
        f"portada no menciona el segundo: la diferencia se descubre antes de explicarse"
    )
    assert "mejora_continua" in portada, "la portada no apunta a la metodologia"


def test_la_metodologia_del_badge_esta_escrita(promedio):
    doc = (DOCS / "mejora_continua.md").read_text(encoding="utf-8")
    assert "Como se midio" in doc, "falta la seccion de metodologia"
    seccion = doc.split("Como se midio")[1]
    assert str(badge_del_judge()) in seccion, "la metodologia no menciona el numero del badge"
    assert _menciona_el_promedio(seccion, promedio), (
        f"la metodologia no contrasta contra el {promedio} de los informes que viajan"
    )


def test_lo_que_no_esta_medido_esta_declarado_como_tal():
    """Cambiar prompts sin volver a medir es legitimo; presentarlo como medido no."""
    doc = (DOCS / "mejora_continua.md").read_text(encoding="utf-8")
    assert "sin medir" in doc, (
        "hay versiones de prompt posteriores a la ultima medicion: la tabla tiene que decirlo"
    )


@pytest.mark.parametrize("doc", DOCUMENTOS, ids=lambda p: p.name)
def test_ningun_documento_promete_un_score_que_el_paquete_no_alcanza(doc, promedio):
    """Solo se persigue la sobreventa.

    Citar un score mas bajo es historia legitima —`mejora_continua.md` cuenta que
    se arranco en 8.2— y no hay nada que verificar ahi. Lo que no puede pasar es
    afirmar un resultado mejor que el mejor que se midio: el badge, o el promedio
    de los informes que viajan, lo que sea mas alto.
    """
    techo = max(badge_del_judge(), promedio)
    patron = re.compile(r"(?:score|judge)[^.\n]{0,60}?\b(\d\.\d)\s*/\s*10", re.IGNORECASE)
    inflados = [
        (n, float(m.group(1)), linea.strip()[:110])
        for n, linea in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1)
        for m in [patron.search(linea)]
        if m and float(m.group(1)) - techo > 0.15
    ]
    assert not inflados, (
        f"{doc.name} promete scores por encima del mejor resultado medido ({techo}): {inflados}"
    )


def test_cada_escenario_documentado_tiene_su_informe():
    """Los escenarios de demo citan transacciones concretas: tienen que existir."""
    doc = (RAIZ / "docs" / "demo_scenarios.md").read_text(encoding="utf-8")
    guardados = {f.stem.removeprefix("analisis_") for f in INFORMES.glob("analisis_*.json")}
    for txn in guardados:
        assert txn in doc, f"{txn} tiene informe guardado pero no esta documentado"


def test_el_conteo_de_tests_del_readme_es_el_real():
    """Un badge de tests que nadie recalcula envejece en la primera semana."""
    readme = LEEME.read_text(encoding="utf-8")
    m = re.search(r"tests-(\d+)%20passed", readme)
    assert m, f"{LEEME.name} perdio el badge de tests"
    declarados = int(m.group(1))

    archivos = [
        *(RAIZ / "tests" / "unit").glob("test_*.py"),
        *(RAIZ / "tests" / "integration").glob("test_*.py"),
        *(RAIZ / "tests" / "e2e").glob("test_*.py"),
    ]
    reales = sum(
        len(re.findall(r"^\s*def test_", f.read_text(encoding="utf-8"), re.MULTILINE))
        for f in archivos
    )
    # Margen: `parametrize` multiplica casos sin agregar funciones.
    assert abs(declarados - reales) <= 40, (
        f"el badge dice {declarados} y hay {reales} funciones de test en {len(archivos)} archivos"
    )


NIVELES = {"blocker", "high", "medium", "low"}


def test_ningun_informe_se_llama_como_un_riesgo_que_no_tiene():
    """El nombre de archivo es documentación: no puede contradecir al contenido.

    Regresión: `report_medium_TXN-00089.html` contenía un caso HIGH, y el README
    lo anunciaba como MEDIUM. Se refuta abriendo el archivo.
    """
    riesgos = {
        f.stem.removeprefix("analisis_"): json.loads(f.read_text(encoding="utf-8"))["resolution"]["risk_level"]
        for f in INFORMES.glob("analisis_*.json")
    }
    desmentidos = []
    for f in INFORMES.glob("report_*.html"):
        etiqueta = f.stem.split("_")[1].lower()
        txn = "TXN-" + f.stem.split("TXN-")[1][:5]
        if etiqueta in NIVELES and riesgos.get(txn, "").lower() != etiqueta:
            desmentidos.append(f"{f.name} dice '{etiqueta}' y el análisis dice '{riesgos.get(txn)}'")
    assert not desmentidos, desmentidos


def test_el_readme_anuncia_el_riesgo_que_cada_caso_tiene():
    """Los escenarios de la portada son lo primero que alguien contrasta."""
    readme = LEEME.read_text(encoding="utf-8")
    for f in INFORMES.glob("analisis_*.json"):
        txn = f.stem.removeprefix("analisis_")
        riesgo = json.loads(f.read_text(encoding="utf-8"))["resolution"]["risk_level"]
        fila = next((ln for ln in readme.splitlines() if f"`{txn}`" in ln and "|" in ln), None)
        if fila is None:
            continue
        otros = {n for n in ("BLOCKER", "HIGH", "MEDIUM", "LOW") if n != riesgo}
        assert riesgo in fila, f"{txn} es {riesgo} y el README no lo dice: {fila.strip()}"
        assert not (otros & set(fila.split())), (
            f"{txn} es {riesgo} y el README anuncia otro nivel: {fila.strip()}"
        )
