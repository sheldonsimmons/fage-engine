public with sharing class CostPilotGateway {
    public class Request {
        @InvocableVariable(
            label='Agent Name'
            description='Name of the Agentforce agent making this governed AI request.'
        )
        public String agentName;

        @InvocableVariable(
            label='Department'
            description='Department responsible for this AI request, used for attribution and budget controls.'
        )
        public String department;

        @InvocableVariable(
            label='Business Unit'
            description='Business unit responsible for this AI request, used for reporting and attribution.'
        )
        public String businessUnit;

        @InvocableVariable(
            label='Record Id'
            description='Salesforce record ID that provides business context and permanent attribution for this request.'
        )
        public String recordId;

        @InvocableVariable(
            label='Object Name'
            description='Salesforce object API name for the related record, such as Account or Opportunity.'
        )
        public String objectName;

        @InvocableVariable(
            label='User Prompt'
            description='The user request that CostPilot must govern and send to the selected AI model.'
            required=true
        )
        public String prompt;

        @InvocableVariable(
            label='Context JSON'
            description='Optional additional business context formatted as JSON.'
        )
        public String contextJson;
    }

    public class Response {
        @InvocableVariable(
            label='CostPilot Response'
            description='The governed AI response returned by CostPilot, or the reason the request was not allowed.'
        )
        public String costPilotResponse;

        @InvocableVariable(
            label='Model Tier'
            description='CostPilot model tier selected for the request, such as Scout, Analyst, Advisor, or Strategist.'
        )
        public String modelTier;

        @InvocableVariable(
            label='Risk Level'
            description='Governance risk classification assigned to the request by CostPilot.'
        )
        public String riskLevel;

        @InvocableVariable(
            label='Outcome'
            description='Final CostPilot routing or governance outcome for the request.'
        )
        public String outcome;

        @InvocableVariable(
            label='Blocked'
            description='True when CostPilot blocked the request from reaching an AI model.'
        )
        public Boolean blocked;

        @InvocableVariable(
            label='Audit Id'
            description='Unique CostPilot audit identifier for tracing this governed request.'
        )
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
            governanceRequest.recordId = requestValue.recordId;
            RecordIdentity identity = recordIdentity(requestValue.recordId);
            if (identity != null) {
                governanceRequest.projectName = identity.displayName;
                governanceRequest.sourceType = identity.objectName;
                governanceRequest.sourceSystem = 'Salesforce';
                if (identity.objectName != 'CostPilot_Project__c') {
                    governanceRequest.projectExternalId = requestValue.recordId;
                }
            }
            String groundedContext = recordContext(requestValue.recordId);
            governanceRequest.taskDescription = String.isBlank(groundedContext)
                ? requestValue.prompt
                : requestValue.prompt + '\n\nSalesforce record context:\n' +
                    groundedContext;
            governanceRequest.agentName = String.isBlank(requestValue.agentName)
                ? 'Salesforce Agentforce'
                : requestValue.agentName;
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
