"""Fix n8n workflow URLs to use $vars instead of hardcoded values."""
import json

with open("n8n/workflow_ciri_agent.json", encoding="utf-8") as f:
    wf = json.load(f)

URL_FIXES = {
    "Obtener Transacción": (
        "={{ $vars.API_BASE_URL + '/api/transactions/'"
        " + $('Validar Formato TXN').first().json.transaction_id }}"
    ),
    "Obtener Logs": (
        "={{ $vars.API_BASE_URL + '/api/logs/'"
        " + $('Validar Formato TXN').first().json.transaction_id }}"
    ),
    "Buscar Políticas": "={{ $vars.API_BASE_URL }}/api/policies/search",
    "Buscar Casos Similares": "={{ $vars.API_BASE_URL }}/api/cases/similar",
    "Riesgo del Comercio": (
        "={{ $vars.API_BASE_URL + '/api/merchants/'"
        " + encodeURIComponent($('Obtener Transacción').first().json.merchant)"
        " + '/risk' }}"
    ),
    "Historial del Cliente": (
        "={{ $vars.API_BASE_URL + '/api/clients/'"
        " + $('Obtener Transacción').first().json.client_id"
        " + '/history' }}"
    ),
    "Sintetizar Resolución": "={{ $vars.API_BASE_URL }}/api/analyze/resolve",
    "Juez de Calidad": "={{ $vars.API_BASE_URL }}/api/analyze/judge",
    "Generar Reporte — BLOCKER": "={{ $vars.API_BASE_URL }}/api/reports/html",
    "Generar Reporte — HIGH": "={{ $vars.API_BASE_URL }}/api/reports/html",
    "Generar Reporte — MEDIUM": "={{ $vars.API_BASE_URL }}/api/reports/html",
    "Generar Reporte — LOW": "={{ $vars.API_BASE_URL }}/api/reports/html",
    "Registrar Feedback HITL": "={{ $vars.API_BASE_URL }}/api/feedback",
    "Despertar API": "={{ $vars.API_BASE_URL }}/health",
    "Verificar Caché": (
        "={{ $vars.API_BASE_URL + '/api/cache/lookup?transaction_id='"
        " + encodeURIComponent($('Validar Formato TXN').first().json.transaction_id)"
        " + '&cliente_vip='"
        " + ($('Validar Formato TXN').first().json.cliente_vip || false) }}"
    ),
}

count = 0
for node in wf["nodes"]:
    name = node["name"]
    if name in URL_FIXES:
        node["parameters"]["url"] = URL_FIXES[name]
        count += 1
        print(f"  {name:40s} -> {URL_FIXES[name][:80]}")

with open("n8n/workflow_ciri_agent.json", "w", encoding="utf-8") as f:
    json.dump(wf, f, indent=2, ensure_ascii=False)

print(f"\n{count} URLs fixed with $vars.API_BASE_URL")
