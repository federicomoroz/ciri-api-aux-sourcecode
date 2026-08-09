"""El reloj de un reclamo: cuanto lleva abierto y si eso incumple el plazo.

Ciento treinta lineas de calendario, politica y casos sin dato, que estaban
mezcladas con el analisis de comercios y clientes en la misma clase. Separarlo no
es orden: es que **el SLA decide plata**. Si da incumplimiento, POL-SLA-004
habilita compensacion, y un error aca se paga.

Las dos reglas que costaron mas de un arreglo:

- **Un caso cerrado se mide hasta su cierre, no hasta hoy.** Sin eso, todo caso
  del dataset —fechado en 2024— aparece incumplido por el solo hecho de que
  pasaron los meses, y un plazo que da positivo el 100% de las veces no mide nada.
- **Sin fecha de apertura no se mide nada.** No se cae a la fecha de la compra:
  entre las dos puede haber meses. `within_sla` queda en `None` y no se afirma un
  incumplimiento que nadie puede sostener. Son 53 de las 100 transacciones del
  dataset, incluida TXN-00051.

Que politica aplica es una regla y vive en codigo; **cuantos dias concede esa
politica es un dato suyo y sale de SQLite**, asi que editar POL-SLA-002 por la API
cambia el plazo sin deploy. Esa es la unica razon por la que esto necesita la base.
"""

import logging
from datetime import UTC, date, datetime, timedelta

from ..domain.constants import (
    LATAM_COUNTRIES,
    POL_SLA_ESTANDAR,
    POL_SLA_EXTENDIDO,
    POL_SLA_VIP,
    SLA_TYPE_DIAS_POR_DEFECTO,
    SLA_TYPE_EXTENDED,
    SLA_TYPE_STANDARD,
    SLA_TYPE_VIP,
)

logger = logging.getLogger(__name__)


def _fecha_o_none(valor: str | None) -> date | None:
    """La fecha que dice ese texto, o None si no dice ninguna.

    Acepta un ISO con hora («2024-08-08T05:17:58») porque asi vienen las fechas
    que escribe el feedback: se queda con los diez primeros caracteres.
    """
    try:
        return datetime.strptime(valor[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError, IndexError):
        return None


def dias_habiles_entre(start: date, end: date) -> int:
    """Dias habiles transcurridos, sin contar el dia de apertura.

    No modela feriados: no hay calendario de feriados en el dataset y
    inventarlo daria una precision falsa. Contar solo dias de semana ya
    acerca mucho mas a la regla que contar dias corridos.
    """
    if end <= start:
        return 0
    semanas, resto = divmod((end - start).days, 7)
    habiles = semanas * 5
    for i in range(1, resto + 1):
        if (start + timedelta(days=i)).weekday() < 5:
            habiles += 1
    return habiles


class CalculadoraDeSLA:
    """Mide el plazo de un reclamo contra la politica que le corresponde.

    Recibe la base porque los dias que concede cada politica salen de ahi. Es la
    unica razon: todo el resto del calculo es calendario.
    """

    def __init__(self, db):
        self.db = db

    def _dias_de(self, codigo: str, por_defecto: int) -> int:
        """Los dias habiles que concede esa politica, segun SQLite.

        Si la politica no esta cargada o no tiene plazo definido, se usa el
        valor por defecto: el sistema no puede quedarse sin poder medir un SLA
        porque alguien borro una fila.
        """
        try:
            politica = self.db.get_policy(codigo) or {}
        except Exception:
            logger.warning("No se pudo leer %s para el SLA", codigo, exc_info=True)
            return por_defecto
        dias = politica.get("sla_dias")
        return int(dias) if dias else por_defecto


    def check_sla(
        self,
        case_open_date: str,
        country: str,
        cliente_vip: bool = False,
        today: date | None = None,
        case_close_date: str | None = None,
        transaction_date: str = "",
    ) -> dict:
        """Check SLA compliance based on policy rules.

        SLA rules:
        - POL-EXC-002 (VIP clients): 5 business days
        - POL-SLA-002 (standard LATAM): 10 business days
        - POL-EXC-004 (non-LATAM merchants): 15 business days

        Los limites son en dias habiles, asi que se cuentan dias habiles.
        `today` existe para que los tests no dependan del dia en que corran.

        **Un caso cerrado se mide hasta su cierre, no hasta hoy.** Sin eso, todo
        caso del dataset —fechado en 2024— aparece incumplido por el solo hecho
        de que pasaron los meses, y la compensacion de POL-SLA-004 se dispara
        siempre: un plazo que da positivo el 100% de las veces no mide nada. El
        reloj de un reclamo corre mientras el reclamo esta abierto.

        **Y sin fecha de apertura no se mide nada.** Ese arreglo cubria los casos
        cerrados y dejaba afuera los que no tienen reclamo registrado —53 de las
        100 transacciones del dataset, incluida TXN-00051—: ahi la apertura caia
        a la fecha de la COMPRA y el plazo se contaba contra hoy. TXN-00051 daba
        489 dias habiles contra un limite de 10, y el informe afirmaba una
        compensacion de USD 15 al lado del veredicto de la misma politica
        diciendo que el caso recien empieza. Se contradecia en la misma pagina.

        La fecha de compra no es la de apertura del reclamo: entre las dos puede
        haber meses. Sin reclamo registrado no hay reloj que correr, asi que el
        plazo no se evalua —`within_sla` queda en None— y **no se afirma un
        incumplimiento que nadie puede sostener**. Que la compensacion no se
        aplique por falta de dato es lo conservador: se paga cuando consta que
        el plazo se incumplio, no cuando no consta nada.

        If NOT within SLA -> compensation_applicable = True (POL-SLA-004: max USD 15)
        """
        today = today or datetime.now(UTC).date()

        def _fecha(valor: str | None, defecto: date, campo: str) -> date:
            leida = _fecha_o_none(valor)
            if leida is None and valor:
                logger.warning("Fecha invalida en %s: %r", campo, valor)
            return defecto if leida is None else leida

        sin_apertura = not (case_open_date or "").strip()
        open_date = _fecha(case_open_date, today, "case_open_date")
        corte = _fecha(case_close_date, today, "case_close_date") if case_close_date else today
        cerrado = case_close_date is not None and corte != today

        days_elapsed = None if sin_apertura else dias_habiles_entre(open_date, corte)

        # Que politica aplica es una regla —depende del cliente y del pais—, y las
        # reglas viven en codigo. Cuantos dias concede esa politica es un dato
        # suyo, y sale de SQLite: editar POL-SLA-002 por la API cambia el plazo
        # sin deploy, texto y numero a la vez.
        #
        # Un pais vacio es un dato que falta, no un pais fuera de LATAM: tratarlo
        # como internacional le concederia quince dias en vez de diez, o sea mas
        # tiempo por no saber de donde es.
        # Ante la duda se aplica el limite estandar, que es el mas exigente de
        # los dos, y queda registrado que el pais no se conocia.
        pais_desconocido = not (country or "").strip()
        if cliente_vip:
            codigo, sla_type, quien = POL_SLA_VIP, SLA_TYPE_VIP, "clientes VIP"
        elif not pais_desconocido and country not in LATAM_COUNTRIES:
            codigo, sla_type, quien = (
                POL_SLA_EXTENDIDO, SLA_TYPE_EXTENDED, "comercios internacionales"
            )
        else:
            codigo, sla_type, quien = POL_SLA_ESTANDAR, SLA_TYPE_STANDARD, "resolucion estandar"

        sla_limit = self._dias_de(codigo, SLA_TYPE_DIAS_POR_DEFECTO[sla_type])
        policy_reference = f"{codigo} ({quien}: {sla_limit} dias habiles)"

        # Sin apertura no se afirma ni cumplimiento ni incumplimiento: el plazo
        # que concede la politica se informa igual, porque es un dato del caso.
        within_sla = None if sin_apertura else days_elapsed <= sla_limit
        compensation_applicable = within_sla is False

        return {
            "within_sla": within_sla,
            "days_elapsed": days_elapsed,
            "sla_limit_days": sla_limit,
            "sla_type": sla_type,
            "policy_reference": policy_reference,
            "compensation_applicable": compensation_applicable,
            # Contra que se conto: sin esto, «12 dias habiles» no se puede
            # reproducir ni auditar.
            "medido_desde": None if sin_apertura else open_date.isoformat(),
            "medido_hasta": None if sin_apertura else corte.isoformat(),
            "caso_cerrado": cerrado,
            "pais_desconocido": pais_desconocido,
            # Para que el informe y el modelo puedan decir POR QUE no se evaluo,
            # en vez de mostrar un plazo cumplido que nadie midio.
            "sin_reclamo_registrado": sin_apertura,
            **self._plazo_de_disputa(transaction_date, None if sin_apertura else open_date),
        }

    @staticmethod
    def _plazo_de_disputa(transaction_date: str, apertura: date | None) -> dict:
        """Dias corridos entre la compra y el reclamo — el otro plazo del caso.

        Son dos relojes distintos: el de RESOLUCION mide cuanto tarda la fintech
        desde que el reclamo se abre, y es el que da `days_elapsed`; el de
        DISPUTA mide cuanto tardo el cliente en reclamar desde que compro, y es
        contra el que se evalua POL-CB-001.

        **Lo cuenta el codigo y no el modelo.** Estuvo un rato pedido en el
        prompt como «restale la fecha de la transaccion a `medido_desde`», y era
        doblemente malo: una resta de fechas es trabajo determinista, y ademas
        en 24 de las 47 transacciones con caso el reclamo figura ANTES de la
        compra —ruido del dataset sintetico, el mismo que pone logs seis meses
        antes de su transaccion—, asi que la cuenta daba negativa la mitad de
        las veces y el modelo tenia que decidir solo que hacer con eso.

        Cuando las fechas no se ordenan, no se informa un numero sin sentido: se
        dice que son inconsistentes, que es lo unico cierto que se puede decir.
        """
        compra = _fecha_o_none(transaction_date)
        if compra is None or apertura is None:
            return {"dias_hasta_el_reclamo": None, "fechas_inconsistentes": False}
        dias = (apertura - compra).days
        if dias < 0:
            return {"dias_hasta_el_reclamo": None, "fechas_inconsistentes": True}
        return {"dias_hasta_el_reclamo": dias, "fechas_inconsistentes": False}
