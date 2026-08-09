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
import subprocess
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]


def _objeto_js(html: str, declaracion: str) -> str:
    """El objeto literal que sigue a `declaracion`, balanceando llaves.

    Los diagramas embeben sus datos como un objeto JS dentro de un `<script>`.
    Es JSON válido, pero no se puede recortar con un regex: las descripciones
    traen llaves y comillas.
    """
    i = html.index(declaracion) + len(declaracion)
    profundidad, k = 0, i
    while k < len(html):
        if html[k] == "{":
            profundidad += 1
        elif html[k] == "}":
            profundidad -= 1
            if profundidad == 0:
                return html[i : k + 1]
        k += 1
    raise AssertionError(f"no se pudo cerrar el objeto de `{declaracion}`")
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
    """Un badge de tests que nadie recalcula envejece en la primera semana.

    Se cuentan los casos que pytest recolecta, no las funciones `def test_`. Antes
    se comparaba contra las funciones con un margen de +/-40 para absorber lo que
    `parametrize` multiplica; con el uso que tiene ahora, esa diferencia paso de
    28 a 104 y el margen habria que ensancharlo hasta dejar de medir nada. Un
    numero exacto cuesta tres segundos de recoleccion y no tiene fudge factor.
    """
    readme = LEEME.read_text(encoding="utf-8")
    m = re.search(r"tests-(\d+)%20passed", readme)
    assert m, f"{LEEME.name} perdio el badge de tests"
    declarados = int(m.group(1))

    salida = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "--collect-only", "-q", "-p", "no:cacheprovider"],
        capture_output=True, text=True, cwd=RAIZ, timeout=300,
    ).stdout
    encontrado = re.search(r"(\d+) tests? collected", salida)
    assert encontrado, f"no se pudo recolectar:\n{salida[-500:]}"
    reales = int(encontrado.group(1))

    assert declarados == reales, (
        f"el badge dice {declarados} y pytest recolecta {reales}"
    )


def test_los_conteos_de_la_prosa_del_readme_tambien_son_reales():
    """El badge tenia test y la prosa de al lado no, asi que la prosa mintio.

    Decia «1089 tests en 44 archivos» y «los 33 de e2e» con el badge en 1170:
    los tres numeros del parrafo estaban viejos porque nadie los recalculaba,
    justo debajo del unico que si tenia quien lo vigilara. Es la tesis del
    proyecto —una invariante en prosa es una invariante sin test— demostrada
    por su propia portada.
    """
    readme = LEEME.read_text(encoding="utf-8")

    def _recolecta(*rutas: str) -> int:
        salida = subprocess.run(
            [sys.executable, "-m", "pytest", *rutas, "--collect-only", "-q",
             "-p", "no:cacheprovider"],
            capture_output=True, text=True, cwd=RAIZ, timeout=300,
        ).stdout
        m = re.search(r"(\d+) tests? collected", salida)
        assert m, f"no se pudo recolectar {rutas}:\n{salida[-400:]}"
        return int(m.group(1))

    for patron, real, que in (
        (r"\*\*(\d+) tests\*\* en \d+ archivos", _recolecta("tests/unit", "tests/integration"),
         "tests de unit+integration"),
        (r"\*\*\d+ tests\*\* en (\d+) archivos",
         len(list((RAIZ / "tests" / "unit").glob("test_*.py"))
             + list((RAIZ / "tests" / "integration").glob("test_*.py"))),
         "archivos de test"),
        (r"los (\d+) de\s*\n?`e2e/`", _recolecta("tests/e2e"), "tests de e2e"),
    ):
        m = re.search(patron, readme)
        assert m, f"{LEEME.name} dejo de declarar {que}"
        assert int(m.group(1)) == real, (
            f"el README dice {m.group(1)} {que} y hay {real}"
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


class TestLosInformesQueViajanSeAbrenSolos:
    """Los HTML del entregable se abren desde el disco, no desde un servidor.

    Traian los estilos del CDN de Tailwind. Quien reciba el ZIP puede abrirlos
    sin red, o en una corporativa que bloquee CDNs, y ver los datos apilados en
    texto plano. Son registros de corridas anteriores: se les cambio de donde
    salen los estilos, no lo que dicen.
    """

    # Los del ZIP son estos: `armar_entrega.py` copia lo que git trackea mas
    # `docs/` entero. La primera version de este test globeaba `docs/examples`
    # y `entregables/` —una ruta que no existe y otra que git no trackea— y
    # pasaba en verde sin abrir un solo archivo de los que viajan.
    ENTREGADOS = sorted(
        f for f in RAIZ.rglob("*.html")
        if f.is_relative_to(INFORMES) or f.is_relative_to(RAIZ / "docs" / "HTML_Output_Examples")
    )

    def test_hay_informes_que_revisar(self):
        """Sin esto, un glob que no matchea nada deja el archivo entero en verde."""
        assert len(self.ENTREGADOS) >= 6, (
            f"se esperaban los 3 de docs/ y los 3 del modo demo; hay {len(self.ENTREGADOS)}"
        )

    def test_los_que_viajan_estan_todos(self):
        """Que el ZIP los lleve no se deduce del glob: se comprueba contra git.

        Con un `skip` explicito y no en silencio. En el paquete entregado no hay
        `.git`, asi que `git ls-files` devuelve vacio y `set() <= revisados`
        pasaba de forma vacua: verde sin comparar nada, justo donde correr los
        tests importa mas. Un test que no puede probar lo que promete tiene que
        decirlo, no aprobar.
        """
        import subprocess

        if not (RAIZ / ".git").exists():
            pytest.skip("sin repo git al lado —el paquete entregado— no hay contra que comparar")

        rastreados = subprocess.run(
            ["git", "ls-files", "-z", "docs/HTML_Output_Examples"],
            cwd=RAIZ, capture_output=True,
        ).stdout.decode("utf-8").split(chr(0))
        publicados = {f for f in rastreados if f.endswith(".html")}
        assert publicados, "git no devolvio ningun informe: la comparacion seria vacua"
        revisados = {
            str(f.relative_to(RAIZ)).replace("\\", "/") for f in self.ENTREGADOS
        }
        assert publicados <= revisados, f"no se revisan: {publicados - revisados}"

    @pytest.mark.parametrize("informe", ENTREGADOS, ids=lambda p: p.name)
    def test_no_pide_nada_a_la_red(self, informe):
        t = informe.read_text(encoding="utf-8")
        externos = [
            u for u in re.findall(r'<(?:script|link|img|iframe)[^>]*\s(?:src|href)=["\']([^"\']+)', t)
            if u.startswith(("http://", "https://", "//"))
        ]
        assert not externos, f"{informe.name} no se ve sin internet: {externos}"

    @pytest.mark.parametrize("informe", ENTREGADOS, ids=lambda p: p.name)
    def test_trae_sus_estilos_adentro(self, informe):
        t = informe.read_text(encoding="utf-8")
        assert ".rounded-2xl" in t and ".shadow-lg" in t


class TestLasVersionesDePromptQueLaDocAfirma:
    """La doc nombra la version de cada prompt en varias tablas.

    Se desfasaron: el codigo iba por v1.3 y v2.2 mientras `architecture.md` y
    `ejes.md` seguian diciendo v1.2 y v2.1. Nada las ataba, asi que la unica
    forma de notarlo era leer las dos cosas al lado.

    Se compara contra `prompts.versiones()`, que lee el changelog del propio
    modulo — la misma fuente que el informe declara en su audit trail.
    """

    DOCS = ("docs/architecture.md", "docs/ejes.md", "docs/prompts.md")

    @pytest.fixture(scope="class")
    def vigentes(self) -> dict:
        from api.app.llm.prompts import versiones

        return versiones()

    @pytest.mark.parametrize("doc", DOCS)
    def test_ningun_documento_nombra_una_version_vieja(self, doc, vigentes):
        texto = (RAIZ / doc).read_text(encoding="utf-8")
        for modulo, version in vigentes.items():
            # Las menciones al archivo del prompt seguidas de una version. Los
            # separadores varian: `| v2.2 |` en una tabla, `# v2.2 —` en el
            # arbol de archivos. Sin el `#` el patron no matcheaba nada y el
            # test pasaba en verde por no mirar.
            nombradas = set(re.findall(rf"{modulo}\.py`?[\s|#]*(v\d+\.\d+)", texto))
            viejas = nombradas - {version}
            assert not viejas, (
                f"{doc} dice {viejas} para {modulo}, que va por {version}"
            )

    def test_prompts_md_encabeza_cada_prompt_con_su_version_vigente(self, vigentes):
        """El entregable que existe para declarar versiones, contra las reales.

        El patron de arriba pide `modulo.py` y `prompts.md` no escribe asi: pone
        `## Prompt 2: v1_resolution (v3.1)` y una fila por version en el
        changelog. Quedaba fuera del control justamente el documento cuya razon
        de ser es esta, y se desfaso en dos de tres.
        """
        texto = (RAIZ / "docs/prompts.md").read_text(encoding="utf-8")
        for modulo, version in vigentes.items():
            encabezado = re.search(rf"^##\s*Prompt \d+:\s*{modulo}\s*\((v[\d.]+)\)",
                                   texto, re.MULTILINE)
            assert encabezado, f"docs/prompts.md no tiene seccion para {modulo}"
            assert encabezado.group(1) == version, (
                f"docs/prompts.md encabeza {modulo} como {encabezado.group(1)}, "
                f"y el codigo va por {version}"
            )
            assert re.search(rf"\|\s*{modulo}\s*\|\s*{re.escape(version)}\s*\|", texto), (
                f"{version} de {modulo} no figura en el changelog de docs/prompts.md"
            )


class TestLaDocDeLaApiNoSeQuedaAtras:
    """`docs/api.md` y el diagrama del circuito describen la superficie real.

    Una lista de endpoints escrita a mano envejece en el primer commit que agrega
    uno, y envejece **en silencio**: nadie la mira hasta que alguien busca un
    endpoint que no está, o llama a uno que se documentó con el nombre de
    parámetro equivocado.

    Se compara contra el OpenAPI que la propia app genera, que es la única
    descripción de la API que no se puede desincronizar de la API.
    """

    @pytest.fixture(scope="class")
    def reales(self) -> set:
        import importlib

        spec = importlib.import_module("api.app.main").app.openapi()
        return {(m.upper(), ruta) for ruta, ms in spec["paths"].items() for m in ms}

    @staticmethod
    def _citadas(texto: str) -> set:
        return {
            (m.group(1), m.group(2).rstrip("`"))
            for m in re.finditer(r"\b(GET|POST|PUT|DELETE|PATCH)\s+`?(/[\w/{}.-]+)`?", texto)
        }

    def test_api_md_los_documenta_a_todos(self, reales):
        citadas = self._citadas((RAIZ / "docs" / "api.md").read_text(encoding="utf-8"))
        faltan = reales - citadas
        assert not faltan, f"sin documentar: {sorted(faltan)}"

    def test_api_md_no_inventa_ninguno(self, reales):
        """Un endpoint documentado que no existe manda a quien lo lea contra un 404."""
        citadas = self._citadas((RAIZ / "docs" / "api.md").read_text(encoding="utf-8"))
        sobran = citadas - reales
        assert not sobran, f"documentados pero inexistentes: {sorted(sobran)}"

    def test_el_conteo_que_anuncia_api_md_es_el_real(self, reales):
        texto = (RAIZ / "docs" / "api.md").read_text(encoding="utf-8")
        m = re.search(r"^(\d+) endpoints", texto, re.M)
        assert m, "docs/api.md perdio su conteo de endpoints"
        assert int(m.group(1)) == len(reales)

    def test_el_recorrido_del_diagrama_existe_de_verdad(self, reales):
        """Las llamadas que el circuito dibuja tienen que ser rutas reales."""
        html = (RAIZ / "docs" / "diagrams" / "api.html").read_text(encoding="utf-8")
        del_circuito = {(v, r) for v, r in re.findall(r'\{v:"(\w+)", r:"([^"]+)"', html)}
        assert del_circuito, "el circuito perdio sus pasos"
        assert del_circuito <= reales, f"el circuito dibuja rutas que no existen: {sorted(del_circuito - reales)}"

    def test_el_diagrama_dibuja_el_recorrido_COMPLETO(self):
        """Y al revés: que no le falte ninguna.

        El test de arriba sólo miraba una dirección —que no dibuje rutas
        inventadas—, así que agregar un paso al recorrido y no dibujarlo pasaba en
        verde. Es el mismo modo de falla que un glob que no matchea nada: verde
        porque no miró, no porque esté bien. Pasó exactamente eso al mover la
        lógica HITL a la API.

        La referencia es `scripts/seguir_el_flujo.py`, que recorre el circuito de
        verdad contra la API: si el script llama a un endpoint que el diagrama no
        dibuja, el dibujo dejó de ser el recorrido.
        """
        html = (RAIZ / "docs" / "diagrams" / "api.html").read_text(encoding="utf-8")
        dibujadas = {r for _, r in re.findall(r'\{v:"(\w+)", r:"([^"]+)"', html)}

        script = (RAIZ / "scripts" / "seguir_el_flujo.py").read_text(encoding="utf-8")
        # Las rutas que el recorrido llama de verdad. `f?"` cubre las dos formas
        # en que el script las escribe: literal (`"/api/sla/check"`) y f-string
        # (`f"/api/logs/{txn}"`), que es como arma las que llevan un id.
        del_script = {
            ruta.split("?")[0].split("{")[0].rstrip("/")
            for ruta in re.findall(
                r'llamar\(\s*(?:\n\s*)?"[^"]+",\s*"[A-Z]+",\s*f?"(/[^"]+)"', script,
            )
        }
        assert len(del_script) >= 10, (
            f"solo se leyeron {len(del_script)} llamadas del recorrido: el regex "
            f"dejo de matchear y este test estaria pasando sin mirar"
        )

        # Se comparan por forma: el script usa ids concretos donde el diagrama
        # usa el parámetro (`/api/logs/TXN-1` contra `/api/logs/{tx_id}`).
        def familia(r: str) -> str:
            return "/".join(p for p in r.split("/") if not p.startswith("{"))[:24]

        faltan = {r for r in del_script if not any(
            familia(r).startswith(familia(d)[:18]) or familia(d).startswith(familia(r)[:18])
            for d in dibujadas
        )}
        assert not faltan, (
            f"el recorrido llama a {sorted(faltan)} y el diagrama no lo dibuja: "
            f"el circuito que ve el evaluador no es el que corre"
        )


class TestElConteoDeNodosQueLaDocDeclara:
    """«45 nodos» estaba escrito en 12 lugares de 4 archivos distintos.

    Es el numero que mas se repite en la documentacion —es la tesis del proyecto:
    orquestacion explicita, cada paso un nodo visible— y por lo tanto el que peor
    queda desincronizado. Agregar UN nodo al workflow dejaba mintiendo a
    architecture.md (7 veces), decisions.md, README.md y CLAUDE.md a la vez, y
    nada avisaba: el conteo no estaba fijado por ningun test.

    Paso exactamente eso al mover la logica HITL fuera del canvas.
    """

    # (archivo, patron con el numero capturado, que cuenta)
    DECLARACIONES = (
        ("docs/architecture.md", r"(\d+) nodos \(\d+ ejecutables \+ \d+ sticky notes\)", "total"),
        ("docs/architecture.md", r"\((\d+) nodos: \d+ exec \+ \d+ sticky\)", "total"),
        ("docs/architecture.md", r"n8n \(explicito, (\d+) nodos\)", "total"),
        ("docs/decisions.md", r"(\d+) nodos explícitos en n8n", "total"),
        ("README.md", r"El orquestador: (\d+) nodos", "total"),
        ("CLAUDE.md", r"n8n \((\d+) nodes, explicit\)", "total"),
        ("CLAUDE.md", r"### n8n Flow \((\d+) nodes:", "total"),
        ("docs/architecture.md", r"\d+ nodos \((\d+) ejecutables \+ \d+ sticky notes\)", "ejecutables"),
        ("docs/decisions.md", r"\d+ nodos explícitos en n8n \((\d+) ejecutables", "ejecutables"),
        ("README.md", r"El orquestador: \d+ nodos, (\d+) ejecutables", "ejecutables"),
    )

    @staticmethod
    def _reales() -> dict[str, int]:
        wf = json.loads(
            (RAIZ / "n8n/workflow_ciri_agent.json").read_text(encoding="utf-8")
        )
        sticky = sum(1 for n in wf["nodes"] if n["type"] == "n8n-nodes-base.stickyNote")
        return {
            "total": len(wf["nodes"]),
            "ejecutables": len(wf["nodes"]) - sticky,
            "sticky": sticky,
        }

    @pytest.mark.parametrize(
        "archivo,patron,que", DECLARACIONES,
        ids=[f"{a.split('/')[-1]}-{q}-{i}" for i, (a, _, q) in enumerate(DECLARACIONES)],
    )
    def test_el_conteo_declarado_es_el_real(self, archivo, patron, que):
        ruta = RAIZ / archivo
        if not ruta.exists():
            # `CLAUDE.md` está en el .gitignore: es el archivo de instrucciones
            # del agente, no un entregable, y por eso no viaja en el paquete que
            # arma `tools/armar_entrega.py`. Sin esta guarda, quien recibe el
            # paquete y corre los tests se come un FileNotFoundError por un
            # archivo que nunca debió estar ahí.
            pytest.skip(f"{archivo} no existe en este árbol (no es un entregable)")
        doc = ruta.read_text(encoding="utf-8")
        encontrados = {int(m) for m in re.findall(patron, doc)}
        assert encontrados, f"{archivo} dejo de declarar el conteo de nodos ({que})"
        real = self._reales()[que]
        assert encontrados == {real}, (
            f"{archivo} dice {sorted(encontrados)} nodos {que} y el workflow tiene "
            f"{real}: la doc afirma un canvas que no es el que viaja"
        )

    def test_la_suma_de_los_tres_workflows_cierra(self):
        """El total que declara la memoria del proyecto, verificable."""
        total = sum(
            len(json.loads(f.read_text(encoding="utf-8"))["nodes"])
            for f in sorted((RAIZ / "n8n").glob("*.json"))
        )
        assert total == 60, f"los tres workflows suman {total} nodos, no 60"

    def test_el_diagrama_dibuja_el_canvas_que_viaja(self):
        """`n8n_workflow_analysis.html` es una réplica a mano del canvas.

        Es el entregable que alguien mira ANTES de importar el workflow, así que
        un diagrama que dibuja un circuito distinto del JSON contradice la tesis
        del proyecto —orquestación explícita y auditable— justo donde más se ve.

        Al mover la lógica HITL fuera del canvas, el diagrama se quedó sin el
        nodo nuevo y conservó una arista que ya no existía. Nada lo detectaba:
        se dibuja a mano y ningún test lo cruzaba contra el workflow.
        """
        diagrama = (RAIZ / "docs/diagrams/n8n_workflow_analysis.html").read_text(encoding="utf-8")
        wf = json.loads((RAIZ / "n8n/workflow_ciri_agent.json").read_text(encoding="utf-8"))

        reales = {n["name"] for n in wf["nodes"] if n["type"] != "n8n-nodes-base.stickyNote"}
        dibujados = set(re.findall(r'data-node="([^"]+)" type="button"', diagrama))
        assert dibujados == reales, (
            f"el diagrama no dibuja {sorted(reales - dibujados)} y dibuja de más "
            f"{sorted(dibujados - reales)}"
        )

        aristas_reales = {
            (origen, c["node"])
            for origen, salidas in wf["connections"].items()
            for rama in salidas.get("main", [])
            for c in (rama or [])
        }
        aristas = set(re.findall(r'data-from="([^"]+)" data-to="([^"]+)"', diagrama))
        assert aristas == aristas_reales, (
            f"al diagrama le faltan {sorted(aristas_reales - aristas)} y le sobran "
            f"{sorted(aristas - aristas_reales)}"
        )

        # El diagrama guarda las conexiones DOS veces: como paths SVG y como
        # `from`/`to` en el objeto que alimenta el panel lateral. Actualizar una
        # y olvidar la otra deja la ficha de un nodo apuntando a un vecino que ya
        # no tiene — que fue exactamente lo que pasó con el `Wait`.
        datos = json.loads(_objeto_js(diagrama, "const DATA = "))
        assert set(datos) == reales, "las fichas del panel no son los nodos del canvas"
        desde_fichas = {(n, t) for n, v in datos.items() for t in v.get("to", [])}
        hacia_fichas = {(f, n) for n, v in datos.items() for f in v.get("from", [])}
        assert desde_fichas == aristas_reales, (
            f"los `to` de las fichas no son las conexiones reales: "
            f"faltan {sorted(aristas_reales - desde_fichas)}, "
            f"sobran {sorted(desde_fichas - aristas_reales)}"
        )
        assert hacia_fichas == aristas_reales, (
            f"los `from` de las fichas no son las conexiones reales: "
            f"faltan {sorted(aristas_reales - hacia_fichas)}, "
            f"sobran {sorted(hacia_fichas - aristas_reales)}"
        )

    def test_la_prosa_del_hitl_no_contradice_la_invariante(self):
        """`architecture.md` afirmaba «Auto-aprueba tras 5s de timeout».

        Es exactamente lo contrario de lo que hace el sistema, y de lo que
        `domain/hitl.py` existe para garantizar: sin respuesta del analista el
        caso NO se aprueba. La línea sobrevivió a varias reescrituras de la
        sección de arriba, así que el documento afirmaba las dos cosas a la vez
        sobre la decisión más cara del dominio.

        No se puede verificar prosa contra código en general, pero sí se puede
        prohibir que el entregable diga la frase opuesta a la invariante.
        """
        for archivo in ("docs/architecture.md", "docs/decisions.md", "README.md"):
            texto = (RAIZ / archivo).read_text(encoding="utf-8").lower()
            for frase in ("auto-aprueba tras", "se auto-aprueba", "aprueba por timeout"):
                assert frase not in texto, (
                    f"{archivo} dice «{frase}»: el HITL falla CERRADO — sin decisión "
                    f"el caso sale PENDING_HITL (domain/hitl.py y test_hitl.py)"
                )

    def test_las_opciones_del_formulario_que_la_doc_describe_son_las_reales(self):
        """La doc decía «(APROBAR/RECHAZAR)» y el formulario ofrece tres.

        Con dos opciones descritas, `MODIFY` parece no existir — y es justamente
        la que causó el bug de precedentes envenenados.
        """
        wf = json.loads((RAIZ / "n8n/workflow_ciri_agent.json").read_text(encoding="utf-8"))
        wait = next(n for n in wf["nodes"] if n["type"] == "n8n-nodes-base.wait")
        opciones = wait["parameters"]["formFields"]["values"][0]["fieldOptions"]["values"]
        doc = (RAIZ / "docs/architecture.md").read_text(encoding="utf-8")
        assert "APROBAR / RECHAZAR / MODIFICAR" in doc or "APROBAR/RECHAZAR/MODIFICAR" in doc, (
            f"el formulario ofrece {len(opciones)} opciones "
            f"({[o['option'] for o in opciones]}) y architecture.md no las describe"
        )

    def test_las_etapas_suman_los_nodos_ejecutables(self):
        """`architecture.md` desglosa el canvas en 4 etapas con su conteo.

        Sumaban 37 sobre 39 ejecutables: los cuatro `Stop and Error` no estaban
        contados en ninguna etapa. Un desglose que no cierra invita a leerlo como
        si el canvas tuviera menos piezas de las que tiene, que es justo lo que un
        evaluador va a contar.
        """
        doc = (RAIZ / "docs/architecture.md").read_text(encoding="utf-8")
        etapas = [int(n) for n in re.findall(r"ETAPA \d -- [^(]+\((\d+) nodos\)", doc)]
        assert len(etapas) == 4, f"architecture.md declara {len(etapas)} etapas, no 4"
        real = self._reales()["ejecutables"]
        assert sum(etapas) == real, (
            f"las etapas suman {sum(etapas)} y hay {real} nodos ejecutables: "
            f"el desglose {etapas} no cierra"
        )


class TestLosTamanosQueLaDocDeclara:
    """Un conteo de lineas escrito a mano envejece con el primer commit.

    `decisions.md` justificaba dejar el panel sin partir con «tiene 3112
    lineas». El archivo tenia 4031: la deuda era un 30% mayor que la declarada,
    y el argumento se leia mas benigno de lo que era. Lo mismo con
    `routes/panel.py`, declarado en 961 cuando iba por 1015.
    """

    # (archivo, patron que lo nombra junto a su tamano en la doc)
    DECLARADOS = (
        ("api/app/reports/templates/test_panel.html",
         r"`api/app/reports/templates/test_panel\.html` tiene (\d+) lineas"),
        ("api/app/routes/panel.py",
         r"`routes/panel\.py`, (\d+) líneas"),
    )

    @pytest.mark.parametrize("archivo,patron", DECLARADOS, ids=lambda x: x.split("/")[-1])
    def test_el_tamano_declarado_es_el_real(self, archivo, patron):
        doc = (RAIZ / "docs/decisions.md").read_text(encoding="utf-8")
        m = re.search(patron, doc)
        assert m, f"docs/decisions.md dejo de declarar el tamano de {archivo}"
        real = len((RAIZ / archivo).read_text(encoding="utf-8").splitlines())
        declarado = int(m.group(1))
        assert declarado == real, (
            f"docs/decisions.md dice que {archivo} tiene {declarado} lineas y "
            f"tiene {real}: la deuda declarada no es la que hay"
        )

    def test_el_archivo_mas_grande_es_el_que_la_doc_señala(self):
        """Dos parrafos se declaraban «el archivo mas grande» al mismo tiempo."""
        pythons = {
            f: len(f.read_text(encoding="utf-8").splitlines())
            for f in (RAIZ / "api/app").rglob("*.py")
        }
        mayor = max(pythons, key=pythons.get)
        assert mayor.name == "panel.py", (
            f"el archivo Python mas grande hoy es {mayor.name}, y decisions.md "
            "sigue señalando a panel.py"
        )


def test_el_conteo_de_decisiones_es_el_real():
    """`decisions.md` se cita por cantidad en dos lugares; se agregan de a una."""
    doc = (DOCS / "decisions.md").read_text(encoding="utf-8")
    reales = len(re.findall(r"^## \d+\.", doc, re.MULTILINE))
    assert reales > 0, "decisions.md cambio de formato de encabezado"
    for f in (LEEME, DOCS / "architecture.md"):
        m = re.search(r"(\d+) decisiones", f.read_text(encoding="utf-8"))
        if m:
            assert int(m.group(1)) == reales, (
                f"{f.name} dice {m.group(1)} decisiones y decisions.md tiene {reales}"
            )
