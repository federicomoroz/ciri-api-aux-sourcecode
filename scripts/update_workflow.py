"""
Script to update the n8n workflow JSON with new native nodes:
1. Form Trigger (second trigger for axis 1 ingesta)
2. Guardrail visibility node (Code node post-resolution)
3. Judge score evaluation (IF node)
4. HITL Wait upgrade to form submission
5. Updated HITL code for form field names
6. New sticky notes
"""
import json
import uuid

INPUT = "n8n/workflow_ciri_agent.json"
OUTPUT = "n8n/workflow_ciri_agent.json"


def main():
    with open(INPUT, encoding="utf-8") as f:
        wf = json.load(f)

    # =================================================================
    # 1. ADD FORM TRIGGER NODE
    # =================================================================
    form_trigger = {
        "parameters": {
            "formTitle": "CIRI \u2014 Investigaci\u00f3n de Contracargo",
            "formDescription": "Ingresa los datos para iniciar una investigaci\u00f3n de contracargo.\\nEl an\u00e1lisis toma ~30 segundos (incluye evaluaci\u00f3n LLM + RAG).",
            "respondMode": "lastNode",
            "formFields": {
                "values": [
                    {
                        "fieldLabel": "Transaction ID",
                        "fieldType": "string",
                        "requiredField": True,
                        "placeholder": "TXN-00051",
                    },
                    {
                        "fieldLabel": "Motivo",
                        "fieldType": "textarea",
                        "requiredField": True,
                        "placeholder": "No reconoce la compra",
                    },
                    {
                        "fieldLabel": "Cliente VIP",
                        "fieldType": "options",
                        "requiredField": False,
                        "fieldOptions": {
                            "values": [{"option": "No"}, {"option": "S\u00ed"}]
                        },
                    },
                ]
            },
            "options": {"buttonLabel": "Iniciar Investigaci\u00f3n"},
        },
        "id": str(uuid.uuid4()),
        "name": "Form Trigger \u2014 Formulario",
        "type": "n8n-nodes-base.formTrigger",
        "typeVersion": 2.2,
        "position": [58976, 21776],
        "webhookId": "ciri-chargeback-form",
    }
    wf["nodes"].append(form_trigger)

    wf["connections"]["Form Trigger \u2014 Formulario"] = {
        "main": [
            [{"node": "Validar Formato \u2014 IF", "type": "main", "index": 0}]
        ]
    }

    # =================================================================
    # 2. UPDATE VALIDATION EXPRESSIONS for Form Trigger field names
    # =================================================================
    for node in wf["nodes"]:
        if node["name"] == "Validar Formato \u2014 IF":
            cond = node["parameters"]["conditions"]["conditions"][0]
            cond["leftValue"] = (
                "={{ ($input.first().json.body?.transaction_id "
                "|| $input.first().json.transaction_id "
                "|| $input.first().json['Transaction ID'] "
                "|| '').trim() }}"
            )

        if node["name"] == "Validar Formato TXN":
            for a in node["parameters"]["assignments"]["assignments"]:
                if a["name"] == "transaction_id":
                    a["value"] = (
                        "={{ ($input.first().json.body?.transaction_id "
                        "|| $input.first().json.transaction_id "
                        "|| $input.first().json['Transaction ID'] "
                        "|| '').trim() }}"
                    )
                elif a["name"] == "motivo":
                    a["value"] = (
                        "={{ ($input.first().json.body?.motivo "
                        "|| $input.first().json.motivo "
                        "|| $input.first().json['Motivo'] "
                        "|| '').trim() }}"
                    )
                elif a["name"] == "cliente_vip":
                    a["value"] = (
                        "={{ Boolean($input.first().json.body?.cliente_vip "
                        "|| $input.first().json.cliente_vip "
                        "|| $input.first().json['Cliente VIP'] === 'S\u00ed') }}"
                    )

    # =================================================================
    # 3. UPGRADE WAIT NODE to form submission mode
    # =================================================================
    for node in wf["nodes"]:
        if node["type"] == "n8n-nodes-base.wait" and "HITL" in node.get("name", ""):
            node["parameters"] = {
                "resume": "form",
                "formFields": {
                    "values": [
                        {
                            "fieldLabel": "Decisi\u00f3n",
                            "fieldType": "options",
                            "requiredField": True,
                            "fieldOptions": {
                                "values": [
                                    {"option": "APPROVE"},
                                    {"option": "REJECT"},
                                ]
                            },
                        },
                        {
                            "fieldLabel": "Notas del Analista",
                            "fieldType": "textarea",
                            "requiredField": False,
                            "placeholder": "Justificaci\u00f3n de la decisi\u00f3n...",
                        },
                    ]
                },
                "options": {
                    "formTitle": "HITL \u2014 Aprobaci\u00f3n de Contracargo",
                    "formDescription": "Revisa el caso y toma una decisi\u00f3n.",
                    "buttonLabel": "Enviar Decisi\u00f3n",
                },
            }

    # =================================================================
    # 4. UPDATE PROCESAR RESPUESTA HITL code
    # =================================================================
    new_hitl_code = (
        "// Compatible con resume via webhook y form submission nativo\n"
        "const resume     = $input.first().json;\n"
        "const ctx        = $('Compilar Contexto').first().json;\n"
        "const resolution = $('Sintetizar Resoluci\\u00f3n').first().json;\n"
        "const judge      = $('Extraer Evaluaci\\u00f3n \\u2014 Juez').first().json.judge_evaluation;\n"
        "\n"
        "// Form fields: 'Decisi\\u00f3n' / 'Notas del Analista'; webhook: 'decision' / 'notes'\n"
        "const rawDecision = resume['Decisi\\u00f3n'] || resume.decision || 'APPROVE';\n"
        "const rawNotes    = resume['Notas del Analista'] || resume.notes || '';\n"
        "\n"
        "const hitlDecision = {\n"
        "  analyst_decision: rawDecision.toUpperCase(),\n"
        "  analyst_notes:    rawNotes,\n"
        "  final_outcome:    rawDecision.toUpperCase() === 'REJECT' ? 'DENIED' : 'APPROVED',\n"
        "  timestamp:        new Date().toISOString(),\n"
        "};\n"
        "\n"
        "return [{\n"
        "  json: {\n"
        "    transaction:         ctx.tx_data,\n"
        "    resolution:          resolution,\n"
        "    judge_evaluation:    judge,\n"
        "    agent_analysis:      ctx.agent_analysis,\n"
        "    merchant_risk:       ctx.merchant_risk,\n"
        "    client_profile:      ctx.client_history,\n"
        "    logs:                ctx.logs,\n"
        "    policies_evaluated:  ctx.policies,\n"
        "    similar_cases:       ctx.similar_cases,\n"
        "    hitl_decision:       hitlDecision,\n"
        "    cache_hit:           false,\n"
        "    guardrail_warnings:  resolution.guardrail_warnings || [],\n"
        "    motivo:              ctx.motivo || '',\n"
        "    cliente_vip:         ctx.cliente_vip || false,\n"
        "    _feedback_payload: {\n"
        "      transaction_id:    ctx.transaction_id,\n"
        "      analyst_decision:  hitlDecision.analyst_decision,\n"
        "      analyst_notes:     hitlDecision.analyst_notes,\n"
        "      final_outcome:     hitlDecision.final_outcome,\n"
        "      judge_score:       judge.overall_score ?? 0,\n"
        "    }\n"
        "  }\n"
        "}];"
    )
    for node in wf["nodes"]:
        if node["name"] == "Procesar Respuesta HITL":
            node["parameters"]["jsCode"] = new_hitl_code

    # =================================================================
    # 5. ADD GUARDRAIL VISIBILITY NODE
    # =================================================================
    guardrail_code = (
        "// Verificar Guardrails post-LLM — visibilidad en el canvas\n"
        "const resolution = $input.first().json;\n"
        "const warnings = resolution.guardrail_warnings || [];\n"
        "const verdicts = resolution.policy_verdicts || [];\n"
        "\n"
        "const hasBLOCKER = verdicts.some(v => v.verdict === 'BLOCKER');\n"
        "const isAPPROVE = resolution.recommended_action === 'APPROVE';\n"
        "const compExcesiva = (resolution.compensation_amount_usd || 0) > "
        "(resolution.transaction_amount_usd || 99999) * 1.1;\n"
        "const failCount = verdicts.filter(v => ['FAIL','BLOCKER'].includes(v.verdict)).length;\n"
        "const overConfident = (resolution.confidence || 0) > 0.95 && failCount >= 2;\n"
        "\n"
        "const guardrailStatus = {\n"
        "  approve_with_blocker: isAPPROVE && hasBLOCKER,\n"
        "  compensation_excesiva: compExcesiva,\n"
        "  overconfident: overConfident,\n"
        "  total_warnings: warnings.length,\n"
        "  warnings: warnings,\n"
        "  status: warnings.length > 0 ? 'GUARDRAILS ACTIVADOS' : 'Sin alertas'\n"
        "};\n"
        "\n"
        "return [{ json: { ...resolution, _guardrail_check: guardrailStatus } }];"
    )
    guardrail_node = {
        "parameters": {"jsCode": guardrail_code},
        "id": str(uuid.uuid4()),
        "name": "Verificar Guardrails",
        "type": "n8n-nodes-base.code",
        "typeVersion": 2,
        "position": [61136, 21968],
    }
    wf["nodes"].append(guardrail_node)

    # Rewire: Sintetizar -> Guardrails -> Juez (instead of Sintetizar -> Juez)
    synth_key = "Sintetizar Resoluci\u00f3n"
    synth_conn = wf["connections"][synth_key]
    error_conn = synth_conn["main"][1] if len(synth_conn["main"]) > 1 else []
    wf["connections"][synth_key] = {
        "main": [
            [{"node": "Verificar Guardrails", "type": "main", "index": 0}],
            error_conn,
        ]
    }
    wf["connections"]["Verificar Guardrails"] = {
        "main": [[{"node": "Juez de Calidad", "type": "main", "index": 0}]]
    }

    # =================================================================
    # 6. ADD JUDGE SCORE EVALUATION NODE
    # =================================================================
    judge_eval = {
        "parameters": {
            "conditions": {
                "options": {
                    "caseSensitive": True,
                    "leftValue": "",
                    "typeValidation": "loose",
                    "version": 2,
                },
                "conditions": [
                    {
                        "id": "check-judge-score",
                        "leftValue": "={{ $json.judge_evaluation.overall_score }}",
                        "rightValue": 7,
                        "operator": {"type": "number", "operation": "gte"},
                    }
                ],
                "combinator": "and",
            },
            "options": {},
        },
        "id": str(uuid.uuid4()),
        "name": "\u00bfJuez Aprueba? (\u22657.0)",
        "type": "n8n-nodes-base.if",
        "typeVersion": 2,
        "position": [61624, 21968],
    }
    wf["nodes"].append(judge_eval)

    low_score = {
        "parameters": {
            "assignments": {
                "assignments": [
                    {
                        "id": "low-score-flag",
                        "name": "judge_evaluation.quality_flag",
                        "value": "LOW_QUALITY \u2014 Revisar resoluci\u00f3n manualmente",
                        "type": "string",
                    }
                ]
            },
            "includeOtherFields": True,
            "options": {},
        },
        "id": str(uuid.uuid4()),
        "name": "Marcar \u2014 Calidad Baja",
        "type": "n8n-nodes-base.set",
        "typeVersion": 3.4,
        "position": [61624, 22160],
    }
    wf["nodes"].append(low_score)

    # Rewire: Extraer Evaluación -> ¿Juez Aprueba? -> Preparar (true) / Marcar (false) -> Preparar
    eval_key = "Extraer Evaluaci\u00f3n \u2014 Juez"
    wf["connections"][eval_key] = {
        "main": [
            [
                {
                    "node": "\u00bfJuez Aprueba? (\u22657.0)",
                    "type": "main",
                    "index": 0,
                }
            ]
        ]
    }
    wf["connections"]["\u00bfJuez Aprueba? (\u22657.0)"] = {
        "main": [
            [{"node": "Preparar Informe", "type": "main", "index": 0}],
            [{"node": "Marcar \u2014 Calidad Baja", "type": "main", "index": 0}],
        ]
    }
    wf["connections"]["Marcar \u2014 Calidad Baja"] = {
        "main": [[{"node": "Preparar Informe", "type": "main", "index": 0}]]
    }

    # =================================================================
    # 7. ADD STICKY NOTES
    # =================================================================
    stickies = [
        {
            "parameters": {
                "content": "## \U0001f4cb FORM TRIGGER \u2014 Ingesta por Formulario\n\n"
                "Punto de entrada alternativo: formulario nativo de n8n.\n"
                "URL: `/form/chargeback-form`\n\n"
                "Demuestra Eje 1 (Ingesta) con formulario nativo.\n"
                "Soporta los mismos 3 campos que el Webhook.",
                "height": 200,
                "width": 400,
                "color": 5,
            },
            "id": str(uuid.uuid4()),
            "name": "Sticky Note \u2014 Form Trigger",
            "type": "n8n-nodes-base.stickyNote",
            "typeVersion": 1,
            "position": [58560, 21680],
        },
        {
            "parameters": {
                "content": "## \U0001f6e1\ufe0f GUARDRAILS POST-LLM\n\n"
                "Verificaci\u00f3n nativa en n8n (Code):\n"
                "1. APPROVE con BLOCKER \u2192 auto-REJECT\n"
                "2. Compensaci\u00f3n > 110% del monto\n"
                "3. Confianza > 0.95 con \u22652 fallas\n\n"
                "Defensa en profundidad: FastAPI + n8n.",
                "height": 220,
                "width": 340,
                "color": 7,
            },
            "id": str(uuid.uuid4()),
            "name": "Sticky Note \u2014 Guardrails",
            "type": "n8n-nodes-base.stickyNote",
            "typeVersion": 1,
            "position": [61040, 21700],
        },
        {
            "parameters": {
                "content": "## \u2696\ufe0f EVALUACI\u00d3N DEL JUEZ\n\n"
                "IF nativo: score \u2265 7.0 = aprobado.\n"
                "Score < 7.0 \u2192 flag 'LOW_QUALITY'\n"
                "visible en el reporte.",
                "height": 160,
                "width": 300,
                "color": 6,
            },
            "id": str(uuid.uuid4()),
            "name": "Sticky Note \u2014 Juez Score",
            "type": "n8n-nodes-base.stickyNote",
            "typeVersion": 1,
            "position": [61520, 21700],
        },
    ]
    wf["nodes"].extend(stickies)

    # =================================================================
    # WRITE BACK
    # =================================================================
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(wf, f, indent=2, ensure_ascii=False)

    # Summary
    total = len(wf["nodes"])
    sticky = sum(1 for n in wf["nodes"] if "stickyNote" in n["type"])
    triggers = sum(
        1
        for n in wf["nodes"]
        if "trigger" in n["type"].lower() or n["type"] == "n8n-nodes-base.webhook"
    )
    print(f"Total nodes: {total} ({total - sticky} executable, {sticky} sticky notes)")
    print(f"Triggers: {triggers}")
    print("New nodes: Form Trigger, Verificar Guardrails, Juez Aprueba IF, Marcar Calidad Baja, 3 Sticky Notes")


if __name__ == "__main__":
    main()
