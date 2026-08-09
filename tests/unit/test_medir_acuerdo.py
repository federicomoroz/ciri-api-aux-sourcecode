"""La medicion contra las resoluciones humanas: lo que se puede probar sin gastar.

Correr el pipeline sobre 34 casos cuesta llamadas al modelo, asi que eso no se
testea aca. Lo que si se fija es lo que decide si el numero significa algo: que
el mapeo entre el vocabulario de los analistas y el del agente sea explicito,
que ninguna resolucion quede fuera en silencio, que la muestra estratificada
cubra las tres clases, y que el porcentaje se calcule solo sobre lo comparable.

Un porcentaje de acuerdo mal construido es peor que no medir: parece evidencia.
"""

import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))

from api.app.domain.enums import ResolutionOutcome  # noqa: E402
from scripts.medir_acuerdo import (  # noqa: E402
    COMPARABLES,
    SIN_EQUIVALENTE,
    _acuerdo,
    _elegir,
    _exigir_que_toda_resolucion_este_clasificada,
    _matriz,
)


def _caso(tx: str, resolucion: str, accion: str | None = None) -> dict:
    fila = {"case_id": f"CB-{tx[-4:]}", "transaction_id": tx, "resolution": resolucion}
    if accion is not None:
        fila["accion"] = accion
    return fila


class TestElMapeoEsExplicito:
    """Traducir «A favor del cliente» a APPROVE es una decision, no un detalle."""

    def test_cada_clase_comparable_apunta_a_una_accion_real(self):
        validas = set(ResolutionOutcome)
        for humana, accion in COMPARABLES.items():
            assert accion in validas, f"{humana} mapea a {accion!r}, que no es una accion del agente"

    def test_ninguna_clase_esta_en_las_dos_listas(self):
        assert not set(COMPARABLES) & set(SIN_EQUIVALENTE)

    def test_las_excluidas_dicen_por_que(self):
        """Excluir sin motivo escrito es elegir el resultado."""
        for etiqueta, razon in SIN_EQUIVALENTE.items():
            assert len(razon) > 30, f"{etiqueta} se excluye sin explicar por que"

    def test_dos_acciones_distintas_no_comparten_etiqueta(self):
        assert len(set(COMPARABLES.values())) == len(COMPARABLES)


class TestNingunaResolucionSeIgnoraEnSilencio:
    """El filtro de comparables y el de excluidas son dos listas separadas."""

    def test_una_resolucion_desconocida_corta_la_corrida(self):
        casos = [_caso("TXN-1", "A favor del cliente"),
                 _caso("TXN-2", "Resolución que nadie previó")]
        with pytest.raises(SystemExit) as e:
            _exigir_que_toda_resolucion_este_clasificada(casos)
        assert "Resolución que nadie previó" in str(e.value)

    def test_con_todo_clasificado_no_molesta(self):
        casos = [_caso("TXN-1", h) for h in (*COMPARABLES, *SIN_EQUIVALENTE)]
        _exigir_que_toda_resolucion_este_clasificada(casos)

    def test_un_caso_sin_resolucion_no_rompe(self):
        _exigir_que_toda_resolucion_este_clasificada([{"resolution": None}])


class TestLaMuestraCubreLasTresClases:
    """Una matriz de confusion con una fila vacia no dice nada de esa clase."""

    @staticmethod
    def _corpus():
        casos = []
        for i, humana in enumerate(COMPARABLES):
            casos += [_caso(f"TXN-{i}{j:03d}", humana) for j in range(8)]
        casos += [_caso("TXN-900", "Reembolso parcial")]
        return casos

    def test_estratifica_por_clase(self):
        elegidos = _elegir(self._corpus(), por_clase=3)
        from collections import Counter
        reparto = Counter(c["resolution"] for c in elegidos)
        assert set(reparto) == set(COMPARABLES)
        assert set(reparto.values()) == {3}

    def test_sin_tope_toma_todos_los_comparables(self):
        elegidos = _elegir(self._corpus(), por_clase=None)
        assert len(elegidos) == 8 * len(COMPARABLES)

    def test_nunca_incluye_las_no_comparables(self):
        for tope in (None, 2):
            assert not [c for c in _elegir(self._corpus(), tope)
                        if c["resolution"] in SIN_EQUIVALENTE]

    def test_la_muestra_es_reproducible(self):
        a = [c["transaction_id"] for c in _elegir(self._corpus(), 3)]
        b = [c["transaction_id"] for c in _elegir(self._corpus(), 3)]
        assert a == b, "la muestra tiene semilla fija: dos corridas deben elegir lo mismo"

    def test_pedir_mas_de_los_que_hay_no_explota(self):
        assert len(_elegir(self._corpus(), 500)) == 8 * len(COMPARABLES)


class TestElPorcentajeSoloCuentaLoComparable:

    def test_cuenta_las_coincidencias(self):
        filas = [
            _caso("TXN-1", "A favor del cliente", ResolutionOutcome.APPROVE),      # =
            _caso("TXN-2", "A favor del comercio", ResolutionOutcome.REJECT),      # =
            _caso("TXN-3", "En escalación", ResolutionOutcome.APPROVE),            # no
        ]
        assert _acuerdo(filas) == (2, 3)

    def test_un_caso_que_fallo_no_cuenta_como_desacuerdo(self):
        """Sin accion no hubo comparacion: contarlo como error inventa un dato."""
        filas = [
            _caso("TXN-1", "A favor del cliente", ResolutionOutcome.APPROVE),
            {"transaction_id": "TXN-2", "resolution": "En escalación", "error": "429"},
        ]
        assert _acuerdo(filas) == (1, 1)

    def test_sin_nada_medido_no_divide_por_cero(self):
        assert _acuerdo([]) == (0, 0)


class TestLaMatrizDeConfusion:

    def test_agrupa_por_resolucion_humana_y_accion(self):
        filas = [
            _caso("TXN-1", "A favor del cliente", ResolutionOutcome.PENDING_HITL),
            _caso("TXN-2", "A favor del cliente", ResolutionOutcome.PENDING_HITL),
            _caso("TXN-3", "A favor del cliente", ResolutionOutcome.APPROVE),
        ]
        assert _matriz(filas) == {
            "A favor del cliente": {ResolutionOutcome.PENDING_HITL: 2,
                                    ResolutionOutcome.APPROVE: 1},
        }

    def test_los_casos_con_error_quedan_fuera(self):
        filas = [{"transaction_id": "TXN-1", "resolution": "En escalación", "error": "429"}]
        assert _matriz(filas) == {}


class TestElDocumentoDiceLoQueElArtefactoMidio:
    """Los numeros publicados salen del JSON de la corrida, no de la memoria."""

    ARTEFACTO = RAIZ / "docs" / "evaluaciones" / "acuerdo_con_analistas.json"
    DOC = RAIZ / "docs" / "acuerdo_con_analistas.md"

    @pytest.fixture(scope="class")
    def medicion(self):
        import json
        if not self.ARTEFACTO.exists():
            pytest.skip("todavia no se corrio scripts/medir_acuerdo.py")
        return json.loads(self.ARTEFACTO.read_text(encoding="utf-8"))

    @pytest.fixture(scope="class")
    def doc(self):
        return self.DOC.read_text(encoding="utf-8")

    def test_el_acuerdo_declarado_es_el_medido(self, medicion, doc):
        r = medicion["resumen"]
        assert f"{r['coincidencias']} de {r['medidos']}" in doc, (
            f"el artefacto dice {r['coincidencias']}/{r['medidos']} y el documento no lo declara"
        )
        assert f"{r['acuerdo']:.0%}" in doc

    def test_la_matriz_del_documento_es_la_medida(self, medicion, doc):
        """Cada fila de la matriz tiene que estar, con su total."""
        for humana, cuentas in medicion["resumen"]["matriz_de_confusion"].items():
            assert humana in doc, f"la fila {humana!r} no esta en el documento"
            assert f"({sum(cuentas.values())})" in doc, (
                f"el documento no declara cuantos casos tiene la clase {humana!r}"
            )

    def test_declara_que_clases_quedaron_afuera(self, medicion, doc):
        for etiqueta, info in medicion["resumen"]["fuera_del_porcentaje"].items():
            assert etiqueta in doc, f"{etiqueta} quedo fuera del porcentaje sin declararse"
            assert f"({info['casos']} casos)" in doc

    def test_no_presenta_el_numero_como_tasa_de_acierto(self, doc):
        """Es la lectura equivocada mas facil, y la que un evaluador va a probar.

        Una matriz con una sola columna poblada no mide acierto: mide que en esos
        casos derivar era lo correcto. El documento tiene que decirlo.
        """
        assert "No es un 35% de acierto" in doc or "no es un 35% de acierto" in doc.lower()
