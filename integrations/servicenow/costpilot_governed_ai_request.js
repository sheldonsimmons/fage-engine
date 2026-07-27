// ServiceNow Flow Designer Action: CostPilot Governed AI Request
// Script step. This code runs only when an AI workflow invokes the Action.
(function execute(inputs, outputs) {
    var tableName = String(inputs.record_table || '').trim();
    var recordSysId = String(inputs.record_sys_id || '').trim();
    var prompt = String(inputs.prompt || '').trim();
    if (!tableName || !recordSysId || !prompt) {
        throw new Error('record_table, record_sys_id, and prompt are required.');
    }

    var record = new GlideRecordSecure(tableName);
    if (!record.get(recordSysId)) {
        throw new Error('The requested ServiceNow record was not found or is not accessible.');
    }

    var rm = new sn_ws.RESTMessageV2();
    rm.setEndpoint('https://fage-engine-21cb49fe4806.herokuapp.com/api/route');
    rm.setHttpMethod('POST');
    rm.setRequestHeader('Content-Type', 'application/json');
    rm.setHttpTimeout(120000);
    rm.setRequestBody(JSON.stringify({
        contract_version: '2026-07-26',
        mode: 'control',
        source: {
            platform: 'ServiceNow',
            workspace_id: gs.getProperty('instance_name'),
            agent_name: String(inputs.agent_name || 'ServiceNow AI'),
            department: String(inputs.department || 'Operations')
        },
        actor: {
            external_id: gs.getUserID(),
            name: gs.getUserDisplayName(),
            email: gs.getUser().getEmail()
        },
        work: {
            external_id: record.getUniqueValue(),
            type: tableName,
            name: record.getDisplayValue() || tableName + ' ' + record.getUniqueValue(),
            sync_if_missing: true
        },
        request: {
            task: String(inputs.task || 'ServiceNow AI request'),
            content: prompt,
            payload_type: 'text',
            auto_prune: true
        }
    }));

    var response = rm.execute();
    var status = response.getStatusCode();
    if (status < 200 || status >= 300) {
        throw new Error('CostPilot request failed (' + status + '): ' + response.getBody());
    }

    var result = JSON.parse(response.getBody());
    outputs.ai_response = result.simulated_response || '';
    outputs.model_tier = result.model_tier || '';
    outputs.model_name = result.model_name || '';
    outputs.routing_decision = result.routing_decision || '';
    outputs.cost_usd = Number(result.cost_usd || 0);
    outputs.input_tokens = Number(result.input_tokens || 0);
    outputs.output_tokens = Number(result.output_tokens || 0);
    outputs.tokens_pruned = Number(result.tokens_saved_by_pruning || 0);
    outputs.work_item_id = result.work_item_id || '';
    outputs.work_item_name = result.work_item_name || '';
})(inputs, outputs);
