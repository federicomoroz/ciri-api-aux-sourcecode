"""Que dicen los logs de una transaccion, sin LLM.

Ocho patrones con nombre, detectados contando eventos: «timeout sistematico del
comercio» dice bastante mas que seis lineas repetidas de MERCHANT_NO_RESPONSE, y
es lo que despues entra al contexto del modelo.

Son funciones libres. Vivian como metodos estaticos de `Analyzer`, que ademas
consulta la base — pero estas no la tocan: reciben una lista de logs y devuelven
lo que se puede contar de ella. Tenerlas aparte deja claro cuales necesitan
persistencia y cuales no.
"""

from ..domain.constants import MERCHANT_TIMEOUT_PATTERN_MIN_COUNT
from ..domain.enums import ErrorPattern, LogEventType, Severity


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

    severity_counts = count_severities(logs)
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
