"""Que se puede decir de un comercio y de un cliente, mirando la base.

Las tres agregaciones que necesitan persistencia: el ratio de contracargos del
corpus —la referencia contra la que se juzga a cada comercio—, el perfil de riesgo
de un comercio y los flags de un cliente.

Lo que **no** esta aca, y por que:

- `analysis/patrones.py` — lo que dicen los logs. No toca la base: recibe una
  lista y cuenta.
- `analysis/sla.py` — el reloj de un reclamo. Si la toca, pero por una sola razon
  (los dias que concede cada politica), y separarlo importa porque el SLA decide
  compensacion.

Eran tres ejes de cambio en una clase de 305 lineas. Ahora cada uno tiene el suyo,
y `Analyzer` conserva el nombre porque es el que el resto del sistema conoce.
"""

import logging

from ..data.db import Database
from ..domain.constants import (
    CLIENT_GEO_ANOMALY_THRESHOLD,
    CLIENT_RECIDIVIST_THRESHOLD,
    MERCHANT_HIGH_VS_BASELINE,
    MERCHANT_STRATEGIC_VOLUME,
    MERCHANT_SUSPENDED_VS_BASELINE,
)
from ..domain.enums import ClientFlag, MerchantFlag, TransactionStatus

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
