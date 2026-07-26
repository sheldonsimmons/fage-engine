public with sharing class CostPilotAgentforceAction {
    // Non-secret workspace identifier. The API key belongs in the CostPilot
    // Named Credential as an X-CostPilot-Key custom header.
    @TestVisible
    private static String workspaceId = '4BE43240A6674314';

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
        List<CostPilot_Project__c> projectUpdates = new List<CostPilot_Project__c>();
        for (Integer index = 0; index < requests.size(); index++) {
            CostPilot_Project__c projectUpdate =
                projectUpdateFor(requests[index], results[index]);
            if (projectUpdate != null) {
                projectUpdates.add(projectUpdate);
            }
        }
        if (!projectUpdates.isEmpty()) {
            update projectUpdates;
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

        hydrateProjectRequest(request);
        MembershipContext membership = membershipFor(request.recordId);
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
            'salesforce_user_id'    => UserInfo.getUserId(),
            'salesforce_user_name'  => UserInfo.getName(),
            'salesforce_user_email' => UserInfo.getUserEmail(),
            'project_member_role'   => membership.role,
            'project_member_status' => membership.status,
            'project_member_can_use_ai' => membership.canUseAi,
            'context_type'          => 'project',
            'context_template'      => 'salesforce_project',
            'source_record_type'    => recordTypeName(request.recordId),
            'customer_external_id'  => membership.accountId,
            'customer_name'         => membership.accountName
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
            output.aiResponse = (String) payload.get('ai_response');
            output.executionMode = (String) payload.get('execution_mode');
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

    private static void hydrateProjectRequest(GovernanceRequest request) {
        try {
            Id typedRecordId = Id.valueOf(request.recordId);
            if (typedRecordId.getSObjectType() != CostPilot_Project__c.SObjectType) {
                return;
            }
            CostPilot_Project__c project = [
                SELECT Name, Project_ID__c, Project_Owner__c, Status__c,
                       Monthly_AI_Budget__c, Department__c, Selected_Model__c
                FROM CostPilot_Project__c
                WHERE Id = :typedRecordId
                LIMIT 1
            ];
            if (String.isBlank(request.projectExternalId)) {
                request.projectExternalId = project.Project_ID__c;
            }
            if (String.isBlank(request.projectName)) {
                request.projectName = project.Name;
            }
            if (String.isBlank(request.projectOwner)) {
                request.projectOwner = project.Project_Owner__c;
            }
            if (String.isBlank(request.projectStatus)) {
                request.projectStatus = project.Status__c;
            }
            if (request.monthlyAiBudget == null) {
                request.monthlyAiBudget = project.Monthly_AI_Budget__c;
            }
            if (String.isBlank(request.department)) {
                request.department = project.Department__c;
            }
            if (String.isBlank(request.requestedModel)) {
                request.requestedModel = project.Selected_Model__c;
            }
        } catch (Exception ignored) {
            // Non-project records can continue using values supplied by the
            // Agentforce action until a metadata mapping is configured.
        }
    }

    private static CostPilot_Project__c projectUpdateFor(
        GovernanceRequest request,
        GovernanceResult result
    ) {
        if (request == null || result == null || String.isBlank(request.recordId)) {
            return null;
        }
        try {
            Id typedRecordId = Id.valueOf(request.recordId);
            if (typedRecordId.getSObjectType() != CostPilot_Project__c.SObjectType) {
                return null;
            }
            CostPilot_Project__c projectUpdate = new CostPilot_Project__c(
                Id = typedRecordId,
                CostPilot_Decision__c = result.decision,
                CostPilot_Tracking_ID__c = result.trackingId,
                Estimated_AI_Cost__c = result.estimatedCostUsd,
                Last_AI_Request__c = result.aiResponse
            );
            if (!String.isBlank(result.selectedModel)) {
                projectUpdate.Selected_Model__c = result.selectedModel;
            }
            return projectUpdate;
        } catch (Exception ignored) {
            return null;
        }
    }

    private static MembershipContext membershipFor(String recordId) {
        MembershipContext context = new MembershipContext();
        context.role = 'Member';
        context.status = 'active';
        context.canUseAi = true;
        try {
            Id typedRecordId = Id.valueOf(recordId);
            if (typedRecordId.getSObjectType() != CostPilot_Project__c.SObjectType) {
                return context;
            }
            List<CostPilot_Project_Member__c> memberships = [
                SELECT Role__c, Status__c, Can_Use_AI__c,
                       Project__r.Account__c, Project__r.Account__r.Name
                FROM CostPilot_Project_Member__c
                WHERE Project__c = :typedRecordId
                  AND Salesforce_User__c = :UserInfo.getUserId()
                LIMIT 1
            ];
            if (memberships.isEmpty()) {
                context.role = 'Not assigned';
                context.status = 'inactive';
                context.canUseAi = false;
                return context;
            }
            CostPilot_Project_Member__c membership = memberships[0];
            context.role = membership.Role__c;
            context.status = String.isBlank(membership.Status__c)
                ? 'active' : membership.Status__c.toLowerCase();
            context.canUseAi = membership.Can_Use_AI__c;
            context.accountId = membership.Project__r.Account__c;
            context.accountName = membership.Project__r.Account__r == null
                ? null : membership.Project__r.Account__r.Name;
        } catch (Exception ignored) {
            // Non-project actions remain backward compatible. CostPilot still
            // receives and records the current Salesforce user identity.
        }
        return context;
    }

    private static String recordTypeName(String recordId) {
        try {
            return Id.valueOf(recordId).getSObjectType().getDescribe().getName();
        } catch (Exception ignored) {
            return 'CostPilot_Project__c';
        }
    }

    private class MembershipContext {
        String role;
        String status;
        Boolean canUseAi;
        String accountId;
        String accountName;
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

        @InvocableVariable(label='AI Response')
        public String aiResponse;

        @InvocableVariable(label='Execution Mode')
        public String executionMode;
    }
}
