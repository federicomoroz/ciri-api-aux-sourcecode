"""
Deterministic analysis functions. No LLM calls here.

These produce structured data used by the LLM resolution prompt.
"""

import logging
from datetime import UTC, date, datetime, timedelta

from ..data.db import Database
from ..domain.constants import (
    CLIENT_GEO_ANOMALY_THRESHOLD,
    CLIENT_RECIDIVIST_THRESHOLD,
    LATAM_COUNTRIES,
    MERCHANT_HIGH_VS_BASELINE,
    MERCHANT_STRATEGIC_VOLUME,
    MERCHANT_SUSPENDED_VS_BASELINE,
    MERCHANT_TIMEOUT_PATTERN_MIN_COUNT,
    SLA_TYPE_DIAS_POR_DEFECTO,
    SLA_TYPE_EXTENDED,
    SLA_TYPE_STANDARD,
    SLA_TYPE_VIP,
)
from ..domain.enums import ClientFlag, ErrorPattern, LogEventType, MerchantFlag, Severity, TransactionStatus

logger = logging.getLogger(__name__)


class Analyzer:
    def __init__(self, db: Database):
        self.db = db

    def linea_base_cb(self) -> float:
        """Ratio de contracargos de todo el corpus. Es contra esto que se compara.

        Un comercio no es problematico por tener contracargos: lo es por tener
        mas de los que corresponden al conjunto en el que se lo mira. Este
        dataset es una muestra de disputas —casi la mitad de sus transacciones
        terminaron en contracargo—, asi que compararlo contra el 2% clasico de
        la industria da quince comercios suspendidos de quince y un flag que no
        distingue nada.
        """
        return self.db.get_corpus_cb_ratio()

    def merchant_risk_profile(self, merchant: str) -> dict:
        """Compute CB ratio, volume, and risk flags for a merchant."""
        stats = self.db.get_merchant_stats(merchant)
        cb_ratio = stats["cb_ratio"]
        base = self.linea_base_cb()
        flags = []
        if cb_ratio > base * MERCHANT_SUSPENDED_VS_BASELINE:
            flags.append(MerchantFlag.SUSPENDED_MERCHANT)
        elif cb_ratio > base * MERCHANT_HIGH_VS_BASELINE:
            flags.append(MerchantFlag.HIGH_CB_RATIO)
        return {
            **stats,
            # Va en la respuesta porque el ratio solo se lee bien al lado de su
            # referencia: 0.75 no dice nada; 0.75 contra una base de 0.47, si.
            "cb_ratio_baseline": round(base, 4),
            "flags": flags,
            "is_strategic": stats["total_volume_usd"] > MERCHANT_STRATEGIC_VOLUME,
        }

    def client_flags(self, client_id: str) -> dict:
        """Compute client history aggregations and risk flags."""
        raw = self.db.get_client_history(client_id)
        txns = raw["transactions"]
        cases = raw["cases"]

        total_transactions = len(txns)
        total_chargebacks = len(cases)
        rejected = sum(1 for t in txns if t.get("status") == TransactionStatus.RECHAZADA)
        countries = list({t["country"] for t in txns})
        methods = list({t["payment_method"] for t in txns})

        flags = []
        if total_chargebacks > CLIENT_RECIDIVIST_THRESHOLD:
            flags.append(ClientFlag.RECIDIVIST)
        if len(countries) > CLIENT_GEO_ANOMALY_THRESHOLD:
            flags.append(ClientFlag.GEO_ANOMALY)

        return {
            "client_id": client_id,
            "total_transactions": total_transactions,
            "total_chargebacks": total_chargebacks,
            "rejected_transactions": rejected,
            "countries_used": countries,
            "payment_methods_used": methods,
            "flags": flags,
        }

    @staticmethod
    def count_severities(logs: list[dict]) -> dict[str, int]:
        """Count log entries by severity level."""
        counts: dict[str, int] = {
            Severity.ERROR: 0,
            Severity.WARN: 0,
            Severity.INFO: 0,
        }
        for log in logs:
            sev = log.get("severity", Severity.INFO)
            counts[sev] = counts.get(sev, 0) + 1
        return counts

    @staticmethod
    def detect_error_patterns(logs: list[dict]) -> dict:
        """Deterministic pattern detection from log events.

        Returns:
            patterns: named patterns detected
            severity_counts: by severity level
            event_summary: event type counts
            critical_events: ERROR/WARN events for LLM context
        """
        if not logs:
            return {
                "patterns": [],
                "severity_counts": {Severity.ERROR: 0, Severity.WARN: 0, Severity.INFO: 0},
                "event_summary": {},
                "critical_events": [],
            }

        severity_counts = Analyzer.count_severities(logs)
        event_counts: dict[str, int] = {}

        for log in logs:
            event = log.get("event", "")
            event_counts[event] = event_counts.get(event, 0) + 1

        patterns = []
        events_set = set(event_counts)

        if event_counts.get(LogEventType.MERCHANT_NO_RESPONSE, 0) >= MERCHANT_TIMEOUT_PATTERN_MIN_COUNT:
            patterns.append(ErrorPattern.SYSTEMATIC_MERCHANT_TIMEOUT)
        if LogEventType.TIMEOUT_RETRY in events_set:
            patterns.append(ErrorPattern.CONNECTIVITY_ISSUE)
        if LogEventType.FRAUD_ALERT in events_set and LogEventType.AUTH_DECLINED in events_set:
            patterns.append(ErrorPattern.BLOCKED_FOR_FRAUD)
        if LogEventType.DOUBLE_CHARGE_DETECT in events_set:
            patterns.append(ErrorPattern.DUPLICATE_CHARGE)
        if LogEventType.SLA_BREACH in events_set:
            patterns.append(ErrorPattern.SLA_VIOLATION)
        if LogEventType.WEBHOOK_FAILED in events_set:
            patterns.append(ErrorPattern.INTEGRATION_FAILURE)
        if LogEventType.SESSION_EXPIRED in events_set and LogEventType.PAYMENT_INITIATED in events_set:
            patterns.append(ErrorPattern.SESSION_INTERRUPTED_PAYMENT)
        if LogEventType.GEO_ANOMALY in events_set:
            patterns.append(ErrorPattern.GEOGRAPHIC_ANOMALY)

        critical_events = [
            {
                "timestamp": log["timestamp"],
                "event": log["event"],
                "severity": log["severity"],
                "detail": log["detail"],
                "code": log.get("code", ""),
            }
            for log in logs
            if log.get("severity") in (Severity.ERROR, Severity.WARN)
        ]

        return {
            "patterns": patterns,
            "severity_counts": severity_counts,
            "event_summary": event_counts,
            "critical_events": critical_events,
        }

    @staticmethod
    def _business_days_between(start: date, end: date) -> int:
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

        If NOT within SLA -> compensation_applicable = True (POL-SLA-004: max USD 15)
        """
        today = today or datetime.now(UTC).date()

        def _fecha(valor: str | None, defecto: date, campo: str) -> date:
            try:
                return datetime.strptime(valor[:10], "%Y-%m-%d").date()
            except (ValueError, TypeError, IndexError):
                if valor:
                    logger.warning("Fecha invalida en %s: %r", campo, valor)
                return defecto

        open_date = _fecha(case_open_date, today, "case_open_date")
        corte = _fecha(case_close_date, today, "case_close_date") if case_close_date else today
        cerrado = case_close_date is not None and corte != today

        days_elapsed = self._business_days_between(open_date, corte)

        # Que politica aplica es una regla —depende del cliente y del pais—, y las
        # reglas viven en codigo. Cuantos dias concede esa politica es un dato
        # suyo, y sale de SQLite: editar POL-SLA-002 por la API cambia el plazo
        # sin deploy. Antes se editaba el texto y el numero seguia siendo 10.
        # Un pais vacio es un dato que falta, no un pais fuera de LATAM. Caia en
        # la rama internacional y se llevaba el plazo extendido de quince dias:
        # el sistema le concedia mas tiempo al caso por no saber de donde era.
        # Ante la duda se aplica el limite estandar, que es el mas exigente de
        # los dos, y queda registrado que el pais no se conocia.
        pais_desconocido = not (country or "").strip()
        if cliente_vip:
            codigo, sla_type, quien = "POL-EXC-002", SLA_TYPE_VIP, "clientes VIP"
        elif not pais_desconocido and country not in LATAM_COUNTRIES:
            codigo, sla_type, quien = "POL-EXC-004", SLA_TYPE_EXTENDED, "comercios internacionales"
        else:
            codigo, sla_type, quien = "POL-SLA-002", SLA_TYPE_STANDARD, "resolucion estandar"

        sla_limit = self._dias_de(codigo, SLA_TYPE_DIAS_POR_DEFECTO[sla_type])
        policy_reference = f"{codigo} ({quien}: {sla_limit} dias habiles)"

        within_sla = days_elapsed <= sla_limit
        compensation_applicable = not within_sla

        return {
            "within_sla": within_sla,
            "days_elapsed": days_elapsed,
            "sla_limit_days": sla_limit,
            "sla_type": sla_type,
            "policy_reference": policy_reference,
            "compensation_applicable": compensation_applicable,
            # Contra que se conto: sin esto, «12 dias habiles» no se puede
            # reproducir ni auditar.
            "medido_desde": open_date.isoformat(),
            "medido_hasta": corte.isoformat(),
            "caso_cerrado": cerrado,
            "pais_desconocido": pais_desconocido,
        }
