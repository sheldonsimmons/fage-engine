public with sharing class CostPilotAgentforceAction {
    // Non-secret workspace identifier. The API key belongs in the CostPilot
    // Named Credential as an X-CostPilot-Key custom header.
    @TestVisible
    private static String workspaceId = 'REPLACE_WITH_COSTPILOT_WORKSPACE_ID';

    @InvocableMethod(
        label='Govern AI Work with CostPilot'
        description='Attributes Agentforce work to a project and returns the CostPilot governance decision.'
        category='CostPilot'
        callout=true
    )
    public static List<GovernanceResult> govern(List<GovernanceRequest> requests) {
        List<GovernanceResult> results = new List<GovernanceResult>();
        for (GovernanceRequest request : requests) {
            results.add(governOne(request));
        }
        return results;
    }

    private static GovernanceResult governOne(GovernanceRequest request) {
        GovernanceResult output = new GovernanceResult();
        if (request == null || String.isBlank(request.recordId) ||
            String.isBlank(request.taskDescription)) {
            output.allowed = false;
            output.decision = 'INVALID_REQUEST';
            output.reason = 'A Salesforce record ID and task description are required.';
            return output;
        }

        HttpRequest httpRequest = new HttpRequest();
        httpRequest.setEndpoint(
            'callout:CostPilot/api/integrations/salesforce/agentforce/' +
            EncodingUtil.urlEncode(workspaceId, 'UTF-8') + '/govern'
        );
        httpRequest.setMethod('POST');
        httpRequest.setHeader('Content-Type', 'application/json');
        httpRequest.setTimeout(30000);
        httpRequest.setBody(JSON.serialize(new Map<String, Object>{
            'record_id'             => request.recordId,
            'task_description'      => request.taskDescription,
            'project_external_id'   => request.projectExternalId,
            'project_name'          => request.projectName,
            'project_owner'         => request.projectOwner,
            'project_status'        => String.isBlank(request.projectStatus)
                ? 'active' : request.projectStatus,
            'monthly_ai_budget'     => request.monthlyAiBudget,
            'department'            => String.isBlank(request.department)
                ? 'Sales' : request.department,
            'agent_name'            => String.isBlank(request.agentName)
                ? 'Salesforce Agentforce' : request.agentName,
            'requested_model'       => request.requestedModel,
            'salesforce_user_id'    => UserInfo.getUserId()
        }));

        try {
            HttpResponse response = new Http().send(httpRequest);
            if (response.getStatusCode() < 200 || response.getStatusCode() >= 300) {
                output.allowed = false;
                output.decision = 'INTEGRATION_ERROR';
                output.reason = 'CostPilot returned HTTP ' + response.getStatusCode() + '.';
                return output;
            }

            Map<String, Object> payload =
                (Map<String, Object>) JSON.deserializeUntyped(response.getBody());
            output.allowed = (Boolean) payload.get('allowed');
            output.decision = (String) payload.get('decision');
            output.reason = (String) payload.get('reason');
            output.projectId = (String) payload.get('project_id');
            output.projectName = (String) payload.get('project_name');
            output.selectedModel = (String) payload.get('selected_model');
            output.selectedTier = (String) payload.get('selected_tier');
            output.trackingId = (String) payload.get('tracking_id');
            output.estimatedCostUsd = decimalValue(payload.get('estimated_cost_usd'));
            output.projectBudgetRemainingUsd =
                decimalValue(payload.get('project_budget_remaining_usd'));
        } catch (Exception exceptionValue) {
            output.allowed = false;
            output.decision = 'INTEGRATION_ERROR';
            output.reason = 'CostPilot could not be reached: ' + exceptionValue.getMessage();
        }
        return output;
    }

    private static Decimal decimalValue(Object value) {
        return value == null ? null : Decimal.valueOf(String.valueOf(value));
    }

    public class GovernanceRequest {
        @InvocableVariable(required=true label='Salesforce Record ID')
        public String recordId;

        @InvocableVariable(required=true label='Task Description')
        public String taskDescription;

        @InvocableVariable(label='Project External ID')
        public String projectExternalId;

        @InvocableVariable(label='Project Name')
        public String projectName;

        @InvocableVariable(label='Project Owner')
        public String projectOwner;

        @InvocableVariable(label='Project Status')
        public String projectStatus;

        @InvocableVariable(label='Monthly AI Budget')
        public Decimal monthlyAiBudget;

        @InvocableVariable(label='Department')
        public String department;

        @InvocableVariable(label='Agent Name')
        public String agentName;

        @InvocableVariable(label='Requested Model')
        public String requestedModel;
    }

    public class GovernanceResult {
        @InvocableVariable(label='Allowed')
        public Boolean allowed;

        @InvocableVariable(label='Decision')
        public String decision;

        @InvocableVariable(label='Reason')
        public String reason;

        @InvocableVariable(label='Project ID')
        public String projectId;

        @InvocableVariable(label='Project Name')
        public String projectName;

        @InvocableVariable(label='Selected Model')
        public String selectedModel;

        @InvocableVariable(label='Selected Tier')
        public String selectedTier;

        @InvocableVariable(label='Estimated Cost USD')
        public Decimal estimatedCostUsd;

        @InvocableVariable(label='Project Budget Remaining USD')
        public Decimal projectBudgetRemainingUsd;

        @InvocableVariable(label='CostPilot Tracking ID')
        public String trackingId;
    }
}
