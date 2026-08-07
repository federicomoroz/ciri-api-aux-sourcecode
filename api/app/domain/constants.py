"""
Business constants — single source of truth for every threshold and limit.

All magic numbers that were previously scattered across 8+ files live here.
To change a business rule, change one line in this file.
"""

from .enums import LATAM_COUNTRIES  # re-export: canonical source is enums.py

__all__ = [
    "LATAM_COUNTRIES",
    # SLA
    "SLA_VIP_DAYS",
    "SLA_STANDARD_DAYS",
    "SLA_EXTENDED_DAYS",
    "SLA_COMPENSATION_MAX_USD",
    # Client risk
    "CLIENT_RECIDIVIST_THRESHOLD",
    "CLIENT_GEO_ANOMALY_THRESHOLD",
    # Merchant risk
    "POLICY_SEED_BLOQUEANTES",
    "POLICY_SEED_SLA_DIAS",
    "MERCHANT_SUSPENDED_VS_BASELINE",
    "MERCHANT_HIGH_VS_BASELINE",
    "MERCHANT_STRATEGIC_VOLUME",
    # Modelos por paso
    "PASO_POLITICAS",
    "PASO_RESOLUCION",
    "PASO_JUEZ",
    "PASOS_DEL_PIPELINE",
    "PASOS_DESCRIPCION",
    "PROVEEDORES_SUGERIDOS",
    # Guardrails
    "GUARDRAIL_MAX_COMPENSATION_RATIO",
    "GUARDRAIL_MAX_CONFIDENCE",
    "GUARDRAIL_MIN_FAILS_FOR_WARNING",
    # RAG
    "SIMILAR_CASES_SCORE_THRESHOLD",
    "SIMILAR_CASES_TOP_K",
    "SIMILAR_CASES_CANDIDATOS",
    "POLICIES_TOP_K_FALLBACK",
    "POLICIES_SCORE_THRESHOLD",
    "EMBEDDING_DIM",
    # LLM
    "LLM_TRUNCATION_LENGTH",
    "LLM_BAD_KEY_MARKER",
    "LLM_CREDIT_EXHAUSTED_MARKER",
    "LLM_DEFAULT_MAX_TOKENS",
    "LLM_DEFAULT_MAX_RETRIES",
    "LLM_DEFAULT_TEMPERATURE",
    # Judge
    "JUDGE_APPROVAL_THRESHOLD",
    "JUDGE_NEEDS_REVIEW_THRESHOLD",
    "JUDGE_AUTO_INDEX_THRESHOLD",
    # RAG / Retrieval
    "FRAUD_SCORE_DEFAULT",
    "FRAUD_SCORE_HIGH_RISK_THRESHOLD",
    # Pattern detection
    "MERCHANT_TIMEOUT_PATTERN_MIN_COUNT",
    # LLM context limits
    "LLM_MAX_CRITICAL_LOGS",
    # Feedback / auto-index
    "FEEDBACK_MOTIVO_DESCONOCIDO",
    "FEEDBACK_MOTIVO_MAX_CHARS",
    "FEEDBACK_AUTO_RESOLUTION_DAYS",
    "FEEDBACK_AUTO_ANALYST_TAG",
    # n8n integration
    "N8N_WEBHOOK_PATH",
    "N8N_WEBHOOK_TEST_PATH",
    "N8N_HEALTHZ_PATH",
    "N8N_ORIGIN_HEADER",
    "LLM_TIMEOUT_S",
    "N8N_TIMEOUT_S",
    "N8N_PING_TIMEOUT_S",
    # Trace / observability names
    "TRACE_RESOLVE",
    "TRACE_JUDGE",
    "TRACE_LLM_CALL",
    "TRACE_FEEDBACK",
    "TRACE_FEEDBACK_SCORE",
    # Feedback response
    "FEEDBACK_STATUS_RECORDED",
    "FEEDBACK_CASE_ID_PREFIX",
    # Fallback values
    "FALLBACK_TX_ID",
    # SLA types
    "SLA_TYPE_VIP",
    "SLA_TYPE_EXTENDED",
    "SLA_TYPE_STANDARD",
    "SLA_TYPE_DIAS_POR_DEFECTO",
    # Health status
    "HEALTH_OK",
    "HEALTH_HEALTHY",
    "HEALTH_DEGRADED",
    # LLM Pricing
    "LLM_PRICING",
    "LLM_PRICING_PER_MTOK",
    # Reranking
    "RERANK_PAYMENT_METHOD_BOOST",
    "RERANK_COUNTRY_BOOST",
    "EMBEDDING_CACHE_MAX",
    "EMBEDDING_RATE_LIMIT_RETRIES",
    "EMBEDDING_RATE_LIMIT_WAIT_S",
    "RERANK_MAX_SCORE",
    # Dashboard
    "DASHBOARD_TOP_N",
    # Fallback
    "FALLBACK_REQUEST_ID",
    # Alerts
    "ALERTS_DEFAULT_LIMIT",
    # Langfuse stats
    "LANGFUSE_STATS_CACHE_TTL_S",
    "LANGFUSE_STATS_TRACE_LIMIT",
    "LANGFUSE_STATS_DISPLAY_LIMIT",
    "LANGFUSE_STATS_FETCH_LIMIT",
    "LANGFUSE_OBSERVATION_TYPE",
    # SQLite
    "SQLITE_TIMEOUT_S",
    # Display
    "DISPLAY_FALLBACK",
    # LLM Pricing
    "LLM_PRICING_FALLBACK_KEY",
    # Conversion
    "SECONDS_TO_MS",
    # Pipeline
    "PIPELINE_MAX_WORKERS",
    "PIPELINE_THREAD_TIMEOUT_S",
    # Alert events
    "ALERT_EVENT_BLOCKER_REJECT",
    "ALERT_EVENT_HITL_REQUIRED",
    "ALERT_SOURCE_RESOLVE",
    # Guardrail strings
    "GUARDRAIL_AUTO_CORRECTED_PREFIX",
    "GUARDRAIL_HITL_REASON_GENERIC",
    # Report template
    "REPORT_TEMPLATE_NAME",
]

# ── SLA Limits ──────────────────────────────────────────────────────────────
# Sources: POL-EXC-002 (VIP), POL-SLA-002 (standard LATAM), POL-EXC-004 (non-LATAM)
SLA_VIP_DAYS: int = 5
SLA_STANDARD_DAYS: int = 10
SLA_EXTENDED_DAYS: int = 15
SLA_COMPENSATION_MAX_USD: float = 15.0   # POL-SLA-004: tope por incumplir el plazo

# ── Client Risk Thresholds ──────────────────────────────────────────────────
CLIENT_RECIDIVIST_THRESHOLD: int = 3       # chargebacks > N → "recidivist"
CLIENT_GEO_ANOMALY_THRESHOLD: int = 3      # distinct countries > N → "geo_anomaly"

# ── Merchant Risk Thresholds ────────────────────────────────────────────────
# Un ratio de contracargos solo significa algo contra una linea base, y la linea
# base la fija el corpus que se esta mirando. Estos umbrales eran absolutos —0.02
# y 0.01, los de la industria sobre el libro de ventas completo de un comercio— y
# se aplicaban a un dataset que es una muestra de disputas: 47 de 100
# transacciones terminaron en contracargo. Contra ese 2%, los quince comercios
# quedaban suspendidos, el flag daba positivo siempre y por lo tanto no informaba
# nada; peor, arrastraba cada caso a riesgo HIGH y dejaba muertas las ramas MEDIUM
# y LOW del enrutador.
#
# Ahora el umbral es relativo: se compara cada comercio contra el ratio del propio
# corpus. Sobre el dataset de la prueba eso deja 5 comercios suspendidos, 4 con
# ratio alto y 6 limpios. Sobre un libro de ventas real, donde la linea base es
# del orden del 1%, los mismos multiplicadores reproducen los umbrales clasicos.
MERCHANT_SUSPENDED_VS_BASELINE: float = 1.5   # cb_ratio > linea_base × N → "suspended_merchant"
MERCHANT_HIGH_VS_BASELINE: float = 1.15       # cb_ratio > linea_base × N → "high_cb_ratio"
MERCHANT_STRATEGIC_VOLUME: float = 1_000_000.0  # total_volume_usd > N → is_strategic


# ── Modelos por paso ────────────────────────────────────────────────────────
# El pipeline hace tres llamadas y cada una es una tarea distinta: comparar
# datos contra reglas, redactar un analisis, y puntuar ese analisis con una
# rubrica. No tienen por que correr en el mismo modelo, y elegirlo no deberia
# requerir un deploy.
#
# Lo de siempre: el valor vigente vive en SQLite (`configuracion_modelos`, que
# el panel edita) y esto es el default con el que arranca. Las claves NO se
# guardan: viajan por peticion o salen del entorno.
PASO_POLITICAS = "policy_eval"
PASO_RESOLUCION = "resolution"
PASO_JUEZ = "judge"
PASOS_DEL_PIPELINE: tuple[str, ...] = (PASO_POLITICAS, PASO_RESOLUCION, PASO_JUEZ)

PASOS_DESCRIPCION: dict[str, dict[str, str]] = {
    PASO_POLITICAS: {
        "titulo": "Evaluacion de politicas",
        "detalle": "Compara la transaccion contra cada politica recuperada y emite un veredicto. "
                   "Tarea mecanica: un modelo rapido alcanza.",
        "prompt": "v1_policy_eval",
    },
    PASO_RESOLUCION: {
        "titulo": "Sintesis de la resolucion",
        "detalle": "Redacta la justificacion, razona sobre los precedentes y arma los next_steps. "
                   "Es donde el razonamiento se nota.",
        "prompt": "v1_resolution",
    },
    PASO_JUEZ: {
        "titulo": "Juez de calidad",
        "detalle": "Puntua la resolucion sobre cinco criterios con rubrica. "
                   "Evaluar bien es tan dificil como resolver bien.",
        "prompt": "v1_judge",
    },
}

# Proveedores conocidos y algunos modelos suyos, para que el panel sugiera en vez
# de exigir que se escriba de memoria. La lista no restringe: el campo acepta
# cualquier identificador que el proveedor entienda.
PROVEEDORES_SUGERIDOS: dict[str, dict] = {
    "anthropic": {
        "nombre": "Anthropic",
        "gratis": False,
        "modelos": ["claude-haiku-4-5-20251001", "claude-sonnet-4-6"],
        "consola": "https://console.anthropic.com/settings/keys",
        "formato_clave": "sk-ant-api03-...",
    },
    "groq": {
        "nombre": "Groq",
        "gratis": True,
        "modelos": ["llama-3.3-70b-versatile", "llama-3.1-8b-instant", "qwen/qwen3-32b"],
        "consola": "https://console.groq.com/keys",
        "formato_clave": "gsk_...",
    },
    "gemini": {
        "nombre": "Google Gemini",
        "gratis": True,
        # Los alias `-latest` y no una version fija: Google deja de habilitar
        # modelos a cuentas nuevas sin previo aviso. `gemini-2.5-flash` estaba
        # aca y ya devuelve «no longer available to new users» — el alias sigue
        # al modelo vigente y no se muere solo.
        "modelos": ["gemini-flash-latest", "gemini-flash-lite-latest", "gemini-3-flash-preview"],
        "consola": "https://aistudio.google.com/apikey",
        "formato_clave": "AIza...",
    },
    "openrouter": {
        "nombre": "OpenRouter",
        "gratis": True,
        # Solo los slugs terminados en `:free` no se cobran, y el catalogo rota:
        # un modelo que hoy es gratis puede dejar de serlo. La lista vigente esta
        # en `GET https://openrouter.ai/api/v1/models` filtrando por `:free`.
        # Estos se verificaron contra la API.
        "modelos": [
            "openai/gpt-oss-20b:free",
            "google/gemma-4-31b-it:free",
            "nvidia/nemotron-3-super-120b-a12b:free",
        ],
        "consola": "https://openrouter.ai/keys",
        "formato_clave": "sk-or-v1-...",
    },
    "cerebras": {
        "nombre": "Cerebras",
        "gratis": True,
        "modelos": ["llama-3.3-70b"],
        "consola": "https://cloud.cerebras.ai/platform",
        "formato_clave": "csk-...",
    },
    "github": {
        "nombre": "GitHub Models",
        "gratis": True,
        "modelos": ["gpt-4o-mini"],
        "consola": "https://github.com/settings/tokens",
        "formato_clave": "github_pat_... o ghp_...",
    },
    "openai": {
        "nombre": "OpenAI",
        "gratis": False,
        "modelos": ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini"],
        "consola": "https://platform.openai.com/api-keys",
        "formato_clave": "sk-proj-...",
    },
    # Compatibles con OpenAI y competitivos en precio. Se marcan como de pago a
    # proposito: varios tienen cuota gratuita, pero anunciar «gratis» sin estar
    # seguro haria que el modo demo gaste plata ajena creyendo que no gasta.
    "deepseek": {
        "nombre": "DeepSeek",
        "gratis": False,
        "modelos": ["deepseek-chat", "deepseek-reasoner"],
        "consola": "https://platform.deepseek.com/api_keys",
        "formato_clave": "sk-...",
    },
    "alibaba": {
        "nombre": "Qwen (Alibaba)",
        "gratis": False,
        "modelos": ["qwen-plus", "qwen-turbo", "qwen2.5-72b-instruct"],
        "consola": "https://bailian.console.alibabacloud.com/",
        "formato_clave": "sk-...",
    },
    "zhipu": {
        "nombre": "Zhipu GLM",
        "gratis": False,
        "modelos": ["glm-4-flash", "glm-4-plus"],
        "consola": "https://open.bigmodel.cn/usercenter/apikeys",
        "formato_clave": "...",
    },
    "moonshot": {
        "nombre": "Moonshot (Kimi)",
        "gratis": False,
        "modelos": ["moonshot-v1-8k", "moonshot-v1-32k"],
        "consola": "https://platform.moonshot.cn/console/api-keys",
        "formato_clave": "sk-...",
    },
}

# ── Guardrails ──────────────────────────────────────────────────────────────
GUARDRAIL_MAX_COMPENSATION_RATIO: float = 1.1   # comp > amount × N → warning
GUARDRAIL_MAX_CONFIDENCE: float = 0.95           # confidence > N with fails → warning
GUARDRAIL_MIN_FAILS_FOR_WARNING: int = 2         # min policy failures to trigger warning

# ── Deterministic Outcome ──────────────────────────────────────────────────
# Risk/action are computed from policy verdicts, not left to LLM discretion.
RISK_FRAUD_SEVERE: int = 15    # fraud_score < N → risk HIGH (even with 1 FAIL)
RISK_HIGH_MIN_FAILS: int = 2   # fail_count >= N → risk HIGH

# ── Semilla de la semantica de las politicas ────────────────────────────────
# Que politica puede bloquear y con que plazo son cosas que la politica HACE, y
# vivian aca mientras su texto se editaba por API: se podia cambiar la
# descripcion de POL-SLA-002 sin deploy, pero no sus diez dias. Ahora son
# columnas de la tabla `policies` y esto es solo el valor con el que arranca el
# dataset — a partir del primer arranque manda SQLite, y el CRUD las edita.
#
# POL-EXC-003 = cripto: metodo de pago irreversible, no se puede continuar. Es
# la unica que arranca pudiendo bloquear; cualquier otro BLOCKER que emita el
# modelo se degrada a FAIL + requires_human_review.
POLICY_SEED_BLOQUEANTES: frozenset[str] = frozenset({"POL-EXC-003"})
POLICY_SEED_SLA_DIAS: dict[str, int] = {
    "POL-EXC-002": SLA_VIP_DAYS,        # clientes VIP
    "POL-SLA-002": SLA_STANDARD_DAYS,   # resolucion estandar LATAM
    "POL-EXC-004": SLA_EXTENDED_DAYS,   # comercios internacionales
}

# ── RAG ─────────────────────────────────────────────────────────────────────
SIMILAR_CASES_SCORE_THRESHOLD: float = 0.40  # min cosine similarity for case results
SIMILAR_CASES_TOP_K: int = 5                 # cuantos precedentes ve el modelo
# El rerank se aplicaba despues del limit de Qdrant, asi que reordenaba el
# top-5 pero no podia cambiar quien entraba: un precedente del mismo metodo de
# pago que salia sexto por coseno se perdia aunque el boost lo hubiera puesto
# primero. Se traen mas candidatos y se recorta despues de reordenar.
SIMILAR_CASES_CANDIDATOS: int = 15
# El corpus de politicas se recupera entero: es chico y el LLM filtra relevancia.
# "Entero" se resuelve contando la coleccion en cada busqueda, no con un numero
# fijo — con el 17 del dataset escrito a mano, la politica 18 quedaba afuera del
# contexto en silencio. Este valor solo se usa si el conteo falla.
POLICIES_TOP_K_FALLBACK: int = 64
POLICIES_SCORE_THRESHOLD: float = 0.0        # no floor — return everything, rank later
EMBEDDING_DIM: int = 1024                    # voyage-multilingual-2 (Voyage AI API)

# ── LLM ─────────────────────────────────────────────────────────────────────
LLM_TRUNCATION_LENGTH: int = 200    # chars to log to tracer (not to LLM)
LLM_CREDIT_EXHAUSTED_MARKER: str = "credit balance is too low"  # Anthropic, al agotarse el saldo
LLM_BAD_KEY_MARKER: str = "authentication_error"                # Anthropic, clave invalida
LLM_DEFAULT_MAX_TOKENS: int = 4096
LLM_DEFAULT_MAX_RETRIES: int = 2
LLM_DEFAULT_TEMPERATURE: float = 0.3

# ── LLM Pricing (USD per 1M tokens) ─────────────────────────────────────────
LLM_PRICING: dict[str, tuple[float, float]] = {
    # subcadena del modelo: (costo entrada, costo salida) por millon de tokens.
    # Se busca por subcadena porque los identificadores traen sufijos de fecha y
    # version. El orden importa: la primera que matchea gana, asi que las mas
    # especificas van antes ("gpt-4o-mini" antes que "gpt-4o").
    "haiku": (0.80, 4.00),
    "sonnet": (3.00, 15.00),
    "opus": (15.00, 75.00),
    # OpenAI. Sin estas entradas, `gpt-4o-mini` caia en la tarifa de referencia
    # —la de Sonnet— y el panel informaba un costo veinte veces mayor al real.
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4o": (2.50, 10.00),
    "o4-mini": (1.10, 4.40),
    # Los proveedores de free tier no tienen tarifa: `_costo` no los consulta.
}
LLM_PRICING_PER_MTOK: int = 1_000_000

# ── Judge ────────────────────────────────────────────────────────────────────
JUDGE_APPROVAL_THRESHOLD: float = 7.0    # overall_score >= N → approved
JUDGE_NEEDS_REVIEW_THRESHOLD: float = 5.0  # overall_score < N → needs_review flag
JUDGE_AUTO_INDEX_THRESHOLD: float = 8.0  # judge_score >= N → auto-index as precedent

# ── RAG / Retrieval ───────────────────────────────────────────────────────────
FRAUD_SCORE_DEFAULT: int = 50  # default fraud score when not provided
FRAUD_SCORE_HIGH_RISK_THRESHOLD: int = 30  # score < N → high risk query enrichment

# ── Pattern detection ─────────────────────────────────────────────────────────
MERCHANT_TIMEOUT_PATTERN_MIN_COUNT: int = 2  # MERCHANT_NO_RESPONSE events >= N → pattern

# ── LLM context limits ────────────────────────────────────────────────────────
LLM_MAX_CRITICAL_LOGS: int = 5  # max ERROR/WARN logs forwarded to LLM in summaries

# ── Feedback / auto-index ─────────────────────────────────────────────────────
FEEDBACK_MOTIVO_MAX_CHARS: int = 200     # max chars for motivo in auto-indexed cases
FEEDBACK_MOTIVO_DESCONOCIDO: str = "Motivo no informado"  # cuando el feedback no lo trae
FEEDBACK_AUTO_RESOLUTION_DAYS: int = 1  # default resolution_days for auto-indexed cases
FEEDBACK_AUTO_ANALYST_TAG: str = "auto-index"  # analyst field for auto-indexed cases

# ── n8n Integration ──────────────────────────────────────────────────────────
N8N_WEBHOOK_PATH: str = "/webhook/chargeback-agent"
N8N_WEBHOOK_TEST_PATH: str = "/webhook-test/chargeback-agent"
N8N_HEALTHZ_PATH: str = "/healthz"
# La pone el workflow en su primera llamada. Sirve para que el panel confirme
# "tu n8n llego hasta aca"; no lleva la URL de nadie.
N8N_ORIGIN_HEADER: str = "X-Origen-n8n"
LLM_TIMEOUT_S: float = 300.0                  # timeout de una llamada al modelo
N8N_TIMEOUT_S: float = 300.0
N8N_PING_TIMEOUT_S: float = 3.0

# ── Trace / Observability Names ──────────────────────────────────────────────
TRACE_RESOLVE: str = "resolve_chargeback"
TRACE_JUDGE: str = "judge_resolution"
TRACE_LLM_CALL: str = "llm_call"
TRACE_FEEDBACK: str = "analyst_feedback"
TRACE_FEEDBACK_SCORE: str = "analyst_feedback_judge_score"

# ── Feedback Response ────────────────────────────────────────────────────────
FEEDBACK_STATUS_RECORDED: str = "recorded"
FEEDBACK_CASE_ID_PREFIX: str = "FB"

# ── Fallback Values ──────────────────────────────────────────────────────────
FALLBACK_TX_ID: str = "unknown"

# ── SLA Types ────────────────────────────────────────────────────────────────
SLA_TYPE_VIP: str = "vip"
SLA_TYPE_EXTENDED: str = "extended"
SLA_TYPE_STANDARD: str = "standard"

# Plazo de respaldo por tipo, para cuando la politica no esta cargada o no
# define `sla_dias`. El valor vigente es el de SQLite.
SLA_TYPE_DIAS_POR_DEFECTO: dict[str, int] = {
    SLA_TYPE_VIP: SLA_VIP_DAYS,
    SLA_TYPE_STANDARD: SLA_STANDARD_DAYS,
    SLA_TYPE_EXTENDED: SLA_EXTENDED_DAYS,
}

# ── Health Status ────────────────────────────────────────────────────────────
HEALTH_OK: str = "ok"
HEALTH_HEALTHY: str = "healthy"
HEALTH_DEGRADED: str = "degraded"

# ── Embeddings ────────────────────────────────────────
EMBEDDING_CACHE_MAX: int = 512             # textos cacheados en proceso
EMBEDDING_RATE_LIMIT_RETRIES: int = 1      # reintentos al topar el limite del proveedor
EMBEDDING_RATE_LIMIT_WAIT_S: int = 21      # espera entre reintentos (free tier: 3 RPM)

# ── Reranking ────────────────────────────────────────────────────────────
RERANK_PAYMENT_METHOD_BOOST: float = 0.05  # score boost for matching payment method
RERANK_COUNTRY_BOOST: float = 0.03         # score boost for matching country
RERANK_MAX_SCORE: float = 1.0              # ceiling for reranked scores

# ── Dashboard ───────────────────────────────────────────────────────────────
DASHBOARD_TOP_N: int = 5                   # top N merchants shown in dashboard

# ── Fallback ────────────────────────────────────────────────────────────────
FALLBACK_REQUEST_ID: str = "unknown"       # default request_id when not set

# ── Alerts ────────────────────────────────────────────────────────────────────
ALERTS_DEFAULT_LIMIT: int = 50

# ── Langfuse Stats ─────────────────────────────────────────────────────────
LANGFUSE_STATS_CACHE_TTL_S: int = 60
LANGFUSE_STATS_TRACE_LIMIT: int = 20
LANGFUSE_STATS_DISPLAY_LIMIT: int = 10
LANGFUSE_STATS_FETCH_LIMIT: int = 100
LANGFUSE_OBSERVATION_TYPE: str = "GENERATION"

# ── SQLite ────────────────────────────────────────────────────────────────────
SQLITE_TIMEOUT_S: int = 30

# ── Display ───────────────────────────────────────────────────────────────────
DISPLAY_FALLBACK: str = "N/A"

# ── LLM Pricing ──────────────────────────────────────────────────────────────
LLM_PRICING_FALLBACK_KEY: str = "sonnet"

# ── Conversion ───────────────────────────────────────────────────────────────
SECONDS_TO_MS: int = 1000

# ── Pipeline ────────────────────────────────────────────────────────────────
PIPELINE_MAX_WORKERS: int = 5   # logs, RAG, riesgo de comercio, historial, SLA
PIPELINE_THREAD_TIMEOUT_S: int = 60

# ── Alert Event Types ───────────────────────────────────────────────────────
ALERT_EVENT_BLOCKER_REJECT: str = "blocker_auto_reject"
ALERT_EVENT_HITL_REQUIRED: str = "hitl_required"
ALERT_SOURCE_RESOLVE: str = "resolve"

# ── Guardrail Strings ──────────────────────────────────────────────────────
GUARDRAIL_AUTO_CORRECTED_PREFIX: str = "Auto-corregido"
GUARDRAIL_HITL_REASON_GENERIC: str = "Requiere revision de analista antes de decision final"

# ── Report Template ─────────────────────────────────────────────────────────
REPORT_TEMPLATE_NAME: str = "case_report.html"
