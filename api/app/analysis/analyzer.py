"""
Deterministic analysis functions. No LLM calls here.

These produce structured data used by the LLM resolution prompt.
"""

from datetime import date, datetime

from ..data.db import Database
from ..domain.constants import (
    LATAM_COUNTRIES,
    MERCHANT_TIMEOUT_PATTERN_MIN_COUNT,
    SLA_VIP_DAYS,
    SLA_STANDARD_DAYS,
    SLA_EXTENDED_DAYS,
    CLIENT_RECIDIVIST_THRESHOLD,
    CLIENT_GEO_ANOMALY_THRESHOLD,
    MERCHANT_SUSPENDED_CB_RATIO,
    MERCHANT_HIGH_CB_RATIO,
    MERCHANT_STRATEGIC_VOLUME,
)
from ..domain.enums import LogEventType


class Analyzer:
    def __init__(self, db: Database):
        self.db = db

    def merchant_risk_profile(self, merchant: str) -> dict:
        """Compute CB ratio, volume, and risk flags for a merchant."""
        stats = self.db.get_merchant_stats(merchant)
        cb_ratio = stats["cb_ratio"]
        flags = []
        if cb_ratio > MERCHANT_SUSPENDED_CB_RATIO:
            flags.append("suspended_merchant")
        elif cb_ratio > MERCHANT_HIGH_CB_RATIO:
            flags.append("high_cb_ratio")
        return {
            **stats,
            "flags": flags,
            "is_strategic": stats["total_volume_usd"] > MERCHANT_STRATEGIC_VOLUME,
        }

    def client_flags(self, client_id: str) -> dict:
        """Compute client history and risk flags."""
        history = self.db.get_client_history(client_id)
        flags = []
        if history["total_chargebacks"] > CLIENT_RECIDIVIST_THRESHOLD:
            flags.append("recidivist")
        if len(history["countries_used"]) > CLIENT_GEO_ANOMALY_THRESHOLD:
            flags.append("geo_anomaly")
        return {**history, "flags": flags}

    @staticmethod
    def count_severities(logs: list[dict]) -> dict[str, int]:
        """Count log entries by severity level."""
        counts: dict[str, int] = {"ERROR": 0, "WARN": 0, "INFO": 0}
        for log in logs:
            sev = log.get("severity", "INFO")
            counts[sev] = counts.get(sev, 0) + 1
        return counts

    def detect_error_patterns(self, logs: list[dict]) -> dict:
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
                "severity_counts": {"ERROR": 0, "WARN": 0, "INFO": 0},
                "event_summary": {},
                "critical_events": [],
            }

        severity_counts = self.count_severities(logs)
        event_counts: dict[str, int] = {}

        for log in logs:
            event = log.get("event", "")
            event_counts[event] = event_counts.get(event, 0) + 1

        patterns = []
        events_set = set(event_counts)

        if event_counts.get(LogEventType.MERCHANT_NO_RESPONSE, 0) >= MERCHANT_TIMEOUT_PATTERN_MIN_COUNT:
            patterns.append("systematic_merchant_timeout")
        if "TIMEOUT_RETRY" in events_set:
            patterns.append("connectivity_issue")
        if "FRAUD_ALERT" in events_set and "AUTH_DECLINED" in events_set:
            patterns.append("blocked_for_fraud")
        if "DOUBLE_CHARGE_DETECT" in events_set:
            patterns.append("duplicate_charge")
        if "SLA_BREACH" in events_set:
            patterns.append("sla_violation")
        if "WEBHOOK_FAILED" in events_set:
            patterns.append("integration_failure")
        if "SESSION_EXPIRED" in events_set and "PAYMENT_INITIATED" in events_set:
            patterns.append("session_interrupted_payment")
        if "GEO_ANOMALY" in events_set:
            patterns.append("geographic_anomaly")

        critical_events = [
            {
                "timestamp": log["timestamp"],
                "event": log["event"],
                "severity": log["severity"],
                "detail": log["detail"],
                "code": log.get("code", ""),
            }
            for log in logs
            if log.get("severity") in ("ERROR", "WARN")
        ]

        return {
            "patterns": patterns,
            "severity_counts": severity_counts,
            "event_summary": event_counts,
            "critical_events": critical_events,
        }

    def check_sla(
        self,
        case_open_date: str,
        country: str,
        cliente_vip: bool = False,
    ) -> dict:
        """Check SLA compliance based on policy rules.

        SLA rules:
        - POL-EXC-002 (VIP clients): 5 business days
        - POL-SLA-002 (standard LATAM): 10 business days
        - POL-EXC-004 (non-LATAM merchants): 15 business days

        If NOT within SLA → compensation_applicable = True (POL-SLA-004: max USD 15)
        """
        try:
            open_date = datetime.strptime(case_open_date[:10], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            open_date = date.today()

        days_elapsed = (date.today() - open_date).days

        if cliente_vip:
            sla_limit = SLA_VIP_DAYS
            sla_type = "vip"
            policy_reference = f"POL-EXC-002 (clientes VIP: {SLA_VIP_DAYS} dias habiles)"
        elif country not in LATAM_COUNTRIES:
            sla_limit = SLA_EXTENDED_DAYS
            sla_type = "extended"
            policy_reference = f"POL-EXC-004 (comercios internacionales: {SLA_EXTENDED_DAYS} dias habiles)"
        else:
            sla_limit = SLA_STANDARD_DAYS
            sla_type = "standard"
            policy_reference = f"POL-SLA-002 (resolucion estandar: {SLA_STANDARD_DAYS} dias habiles)"

        within_sla = days_elapsed <= sla_limit
        compensation_applicable = not within_sla

        return {
            "within_sla": within_sla,
            "days_elapsed": days_elapsed,
            "sla_limit_days": sla_limit,
            "sla_type": sla_type,
            "policy_reference": policy_reference,
            "compensation_applicable": compensation_applicable,
        }
