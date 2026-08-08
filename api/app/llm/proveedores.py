"""Un solo lugar donde vive «que proveedores de modelo conoce el sistema».

Estaba en tres tablas paralelas con la misma clave: la URL base en `client.py`,
el adaptador en `adaptadores.py` y los metadatos del panel en `constants.py`.
Agregar un proveedor eran tres ediciones sincronizadas, y **el modo de falla de
olvidarse una era silencioso**: sin URL base, `LLMManager._construir` caia al
cliente de Anthropic y mandaba el modelo de Groq con la credencial de Groq a la
API de Anthropic. El error que llegaba hablaba de autenticacion, no del
proveedor faltante.

Ahora es una entrada y el resto se deriva. Lo que cada campo decide:

- `base_url` — a donde se habla. Vacio significa SDK propio: solo Anthropic.
- `adaptador` — como se le habla (piso de tokens, reintentos, pedidos por
  minuto). El modelo puede afinarlo mas; ver `adaptadores.adaptador_de`.
- `nombre`, `gratis`, `modelos`, `consola`, `formato_clave` — lo que el panel
  muestra para que quien evalua no tenga que escribir de memoria.

La lista no restringe: `CB_LLM_BASE_URL` acepta cualquier endpoint que hable el
protocolo de OpenAI, y el campo de modelo acepta cualquier identificador.
"""

from .adaptadores import ABIERTO, CLAUDE, GEMINI, Adaptador

# El unico que no habla el protocolo de OpenAI: tiene SDK propio. Estaba como
# literal "anthropic" en cinco lugares de cuatro archivos, comparado con `==`
# para elegir cliente y credencial.
PROVEEDOR_ANTHROPIC: str = "anthropic"

# Proveedores conocidos y algunos modelos suyos, para que el panel sugiera en vez
# de exigir que se escriba de memoria. La lista no restringe: el campo acepta
# cualquier identificador que el proveedor entienda.
PROVEEDORES: dict[str, dict] = {
    "anthropic": {
        # "" = SDK propio, no compatible con el protocolo de OpenAI.
        "base_url": "",
        "adaptador": CLAUDE,
        "nombre": "Anthropic",
        "gratis": False,
        "modelos": ["claude-haiku-4-5-20251001", "claude-sonnet-4-6"],
        "consola": "https://console.anthropic.com/settings/keys",
        "formato_clave": "sk-ant-api03-...",
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "adaptador": ABIERTO,
        "nombre": "Groq",
        "gratis": True,
        "modelos": ["llama-3.3-70b-versatile", "llama-3.1-8b-instant", "qwen/qwen3-32b"],
        "consola": "https://console.groq.com/keys",
        "formato_clave": "gsk_...",
    },
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "adaptador": GEMINI,
        "nombre": "Google Gemini",
        "gratis": True,
        # Los alias `-latest` y no una version fija: Google deja de habilitar
        # modelos a cuentas nuevas sin previo aviso. `gemini-2.5-flash` estaba
        # aca y ya devuelve «no longer available to new users» — el alias sigue
        # al modelo vigente y no se muere solo.
        #
        # `flash-lite` va primero, y no por calidad: por cuota. En el free tier,
        # el flash grande da 20 pedidos por dia y el lite 500. Como cada analisis
        # son tres llamadas, eso es la diferencia entre 6 casos diarios y 166 —
        # o sea, entre un panel que se agota en cinco minutos y uno que aguanta
        # una evaluacion. Para prompts con estructura explicita como los de aca,
        # el modelo mas chico rinde parecido.
        "modelos": ["gemini-flash-lite-latest", "gemini-flash-latest", "gemini-3-flash-preview"],
        "consola": "https://aistudio.google.com/apikey",
        "formato_clave": "AIza...",
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "adaptador": ABIERTO,
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
        "base_url": "https://api.cerebras.ai/v1",
        "adaptador": ABIERTO,
        "nombre": "Cerebras",
        "gratis": True,
        "modelos": ["llama-3.3-70b"],
        "consola": "https://cloud.cerebras.ai/platform",
        "formato_clave": "csk-...",
    },
    "github": {
        "base_url": "https://models.inference.ai.azure.com",
        "adaptador": ABIERTO,
        "nombre": "GitHub Models",
        "gratis": True,
        "modelos": ["gpt-4o-mini"],
        "consola": "https://github.com/settings/tokens",
        "formato_clave": "github_pat_... o ghp_...",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "adaptador": ABIERTO,
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
        "base_url": "https://api.deepseek.com/v1",
        "adaptador": ABIERTO,
        "nombre": "DeepSeek",
        "gratis": False,
        "modelos": ["deepseek-chat", "deepseek-reasoner"],
        "consola": "https://platform.deepseek.com/api_keys",
        "formato_clave": "sk-...",
    },
    "alibaba": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "adaptador": ABIERTO,
        "nombre": "Qwen (Alibaba)",
        "gratis": False,
        "modelos": ["qwen-plus", "qwen-turbo", "qwen2.5-72b-instruct"],
        "consola": "https://bailian.console.alibabacloud.com/",
        "formato_clave": "sk-...",
    },
    "zhipu": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "adaptador": ABIERTO,
        "nombre": "Zhipu GLM",
        "gratis": False,
        "modelos": ["glm-4-flash", "glm-4-plus"],
        "consola": "https://open.bigmodel.cn/usercenter/apikeys",
        "formato_clave": "...",
    },
    "moonshot": {
        "base_url": "https://api.moonshot.cn/v1",
        "adaptador": ABIERTO,
        "nombre": "Moonshot (Kimi)",
        "gratis": False,
        "modelos": ["moonshot-v1-8k", "moonshot-v1-32k"],
        "consola": "https://platform.moonshot.cn/console/api-keys",
        "formato_clave": "sk-...",
    },
}


# Solo los que hablan el protocolo de OpenAI. Anthropic tiene su propio SDK y por
# eso queda afuera: su `base_url` es "".
BASES_OPENAI: dict[str, str] = {
    pid: p["base_url"] for pid, p in PROVEEDORES.items() if p["base_url"]
}

ADAPTADOR_POR_PROVEEDOR: dict[str, Adaptador] = {
    pid: p["adaptador"] for pid, p in PROVEEDORES.items()
}


def existe(proveedor: str) -> bool:
    return (proveedor or "").lower().strip() in PROVEEDORES


def base_url_de(proveedor: str, explicita: str = "") -> str:
    """A donde hablarle. La explicita gana: es la que puso quien configura."""
    if explicita:
        return explicita
    return BASES_OPENAI.get((proveedor or "").lower().strip(), "")


def catalogo() -> list[dict]:
    """Lo que el panel muestra para elegir proveedor y modelo.

    Sin `adaptador`: es una decision interna de como se le habla al proveedor, no
    algo que quien evalua tenga que ver ni pueda cambiar.
    """
    return [
        {
            "id": pid,
            "nombre": p["nombre"],
            "gratis": p["gratis"],
            "modelos": list(p["modelos"]),
            "consola": p["consola"],
            "formato_clave": p["formato_clave"],
            "base_url": p["base_url"],
        }
        for pid, p in PROVEEDORES.items()
    ]
