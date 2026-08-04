public with sharing class CostPilotGateway {
    public class Request {
        @InvocableVariable(label='Agent Name')
        public String agentName;

        @InvocableVariable(label='Department')
        public String department;

        @InvocableVariable(label='Business Unit')
        public String businessUnit;

        @InvocableVariable(label='Record Id')
        public String recordId;

        @InvocableVariable(label='Object Name')
        public String objectName;

        @InvocableVariable(label='User Prompt' required=true)
        public String prompt;

        @InvocableVariable(label='Context JSON')
        public String contextJson;
    }

    public class Response {
        @InvocableVariable(label='CostPilot Response')
        public String costPilotResponse;

        @InvocableVariable(label='Model Tier')
        public String modelTier;

        @InvocableVariable(label='Risk Level')
        public String riskLevel;

        @InvocableVariable(label='Outcome')
        public String outcome;

        @InvocableVariable(label='Blocked')
        public Boolean blocked;

        @InvocableVariable(label='Audit Id')
        public String auditId;
    }

    @InvocableMethod(
        label='Route Through CostPilot'
        description='Govern and execute any Agentforce request through CostPilot.'
        callout=true
    )
    public static List<Response> send(List<Request> requests) {
        List<CostPilotAgentforceAction.GovernanceRequest> governanceRequests =
            new List<CostPilotAgentforceAction.GovernanceRequest>();

        for (Request requestValue : requests) {
            CostPilotAgentforceAction.GovernanceRequest governanceRequest =
                new CostPilotAgentforceAction.GovernanceRequest();
            String resolvedAgentName = String.isBlank(requestValue.agentName)
                ? 'Salesforce Agentforce'
                : requestValue.agentName;
            governanceRequest.recordId = requestValue.recordId;
            RecordIdentity identity = recordIdentity(requestValue.recordId);
            if (identity != null) {
                governanceRequest.projectName = identity.displayName;
                governanceRequest.sourceType = identity.objectName;
                governanceRequest.sourceSystem = 'Salesforce';
                if (identity.objectName != 'CostPilot_Project__c') {
                    governanceRequest.projectExternalId = requestValue.recordId;
                }
            } else if (String.isBlank(requestValue.recordId)) {
                String agentContextId = 'AGENT-' + UserInfo.getOrganizationId() +
                    '-' + EncodingUtil.convertToHex(
                        Crypto.generateDigest(
                            'SHA-256',
                            Blob.valueOf(resolvedAgentName)
                        )
                    ).substring(0, 12);
                governanceRequest.recordId = agentContextId;
                governanceRequest.projectExternalId = agentContextId;
                governanceRequest.projectName = resolvedAgentName;
                governanceRequest.sourceType = 'AgentforceAgent';
                governanceRequest.sourceSystem = 'Salesforce';
            }
            String groundedContext = recordContext(requestValue.recordId);
            governanceRequest.taskDescription = String.isBlank(groundedContext)
                ? requestValue.prompt
                : requestValue.prompt + '\n\nSalesforce record context:\n' +
                    groundedContext;
            governanceRequest.agentName = resolvedAgentName;
            governanceRequest.department = requestValue.department;
            governanceRequests.add(governanceRequest);
        }

        List<CostPilotAgentforceAction.GovernanceResult> governanceResults =
            CostPilotAgentforceAction.govern(governanceRequests);
        List<Response> responses = new List<Response>();

        for (CostPilotAgentforceAction.GovernanceResult governanceResult :
             governanceResults) {
            Response responseValue = new Response();
            responseValue.costPilotResponse = governanceResult.allowed &&
                !String.isBlank(governanceResult.aiResponse)
                ? governanceResult.aiResponse
                : governanceResult.reason;
            responseValue.modelTier = governanceResult.selectedTier;
            responseValue.riskLevel = governanceResult.decision;
            responseValue.outcome = governanceResult.decision;
            responseValue.blocked = !governanceResult.allowed;
            responseValue.auditId = governanceResult.trackingId;
            responses.add(responseValue);
        }
        return responses;
    }

    private static RecordIdentity recordIdentity(String recordId) {
        if (String.isBlank(recordId)) {
            return null;
        }
        try {
            Id typedRecordId = Id.valueOf(recordId);
            Schema.DescribeSObjectResult objectDescribe =
                typedRecordId.getSObjectType().getDescribe();
            Map<String, Schema.SObjectField> fieldMap =
                objectDescribe.fields.getMap();
            String labelField;
            if (fieldMap.containsKey('Name') &&
                fieldMap.get('Name').getDescribe().isAccessible()) {
                labelField = 'Name';
            } else if (fieldMap.containsKey('Subject') &&
                       fieldMap.get('Subject').getDescribe().isAccessible()) {
                labelField = 'Subject';
            }
            String displayName;
            if (labelField != null) {
                SObject recordValue = Database.query(
                    'SELECT ' + labelField +
                    ' FROM ' + objectDescribe.getName() +
                    ' WHERE Id = :typedRecordId LIMIT 1'
                );
                displayName = String.valueOf(recordValue.get(labelField));
            }
            if (String.isBlank(displayName)) {
                displayName = objectDescribe.getLabel() + ' ' + recordId;
            }
            RecordIdentity identity = new RecordIdentity();
            identity.objectName = objectDescribe.getName();
            identity.displayName = displayName;
            return identity;
        } catch (Exception ignored) {
            return null;
        }
    }

    private static String recordContext(String recordId) {
        if (String.isBlank(recordId)) {
            return null;
        }
        try {
            Id typedRecordId = Id.valueOf(recordId);
            Schema.SObjectType objectType = typedRecordId.getSObjectType();
            Schema.DescribeSObjectResult objectDescribe = objectType.getDescribe();
            Map<String, Schema.SObjectField> fieldMap =
                objectDescribe.fields.getMap();
            List<String> preferredFields = new List<String>{
                'Name', 'Subject', 'Status', 'StageName', 'Amount', 'CloseDate',
                'NextStep', 'Description', 'Type', 'Priority', 'Origin',
                'AccountId', 'ContactId', 'OpportunityId', 'OwnerId'
            };
            List<String> selectedFields = new List<String>{ 'Id' };
            for (String fieldName : preferredFields) {
                if (fieldMap.containsKey(fieldName) &&
                    fieldMap.get(fieldName).getDescribe().isAccessible()) {
                    selectedFields.add(fieldName);
                }
            }
            SObject recordValue = Database.query(
                'SELECT ' + String.join(selectedFields, ',') +
                ' FROM ' + objectDescribe.getName() +
                ' WHERE Id = :typedRecordId LIMIT 1'
            );
            Map<String, Object> context = new Map<String, Object>{
                'object' => objectDescribe.getName(),
                'fields' => recordValue.getPopulatedFieldsAsMap()
            };
            if (objectDescribe.getName() == 'Opportunity') {
                context.put('products', [
                    SELECT Product2.Name, Quantity, TotalPrice, Description
                    FROM OpportunityLineItem
                    WHERE OpportunityId = :typedRecordId
                    LIMIT 20
                ]);
            }
            return JSON.serialize(context);
        } catch (Exception ignored) {
            return null;
        }
    }

    private class RecordIdentity {
        String objectName;
        String displayName;
    }
}
