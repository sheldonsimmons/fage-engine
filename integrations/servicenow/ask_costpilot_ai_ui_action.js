// ServiceNow UI Action: Ask CostPilot AI
// Table: change_request | Form button: true | Client: false
// Condition: current.canRead() && !current.isNewRecord()
(function runCostPilotAI() {
    var prompt =
        'Change Request: ' + current.getDisplayValue() + '\n' +
        'Short description: ' +
        (current.getValue('short_description') || '') + '\n\n' +
        'Description:\n' +
        (current.getValue('description') || '') + '\n\n' +
        'Summarize this change request in under 200 words and recommend the ' +
        'next step. Use plain text without Markdown.';

    var inputs = {
        prompt: prompt,
        record_table: current.getTableName(),
        record_sys_id: current.getUniqueValue(),
        task: 'Summarize change request',
        agent_name: String(gs.getProperty(
            'costpilot.servicenow.default_agent',
            'ServiceNow Change Agent'
        )),
        department: String(gs.getProperty(
            'costpilot.servicenow.default_department',
            'Operations'
        ))
    };

    try {
        var execution = sn_fd.FlowAPI.getRunner()
            .action('global.costpilot_governed_ai_request')
            .inForeground()
            .withInputs(inputs)
            .run();
        var outputs = execution.getOutputs();
        var response = outputs.ai_response || 'CostPilot returned no response.';

        gs.addInfoMessage(
            '<strong>CostPilot AI</strong><br/>' +
            GlideStringUtil.escapeHTML(response)
        );
    } catch (error) {
        gs.addErrorMessage('CostPilot AI request failed: ' + error.message);
    }

    action.setRedirectURL(current);
})();
