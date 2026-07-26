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
            governanceRequest.recordId = requestValue.recordId;
            governanceRequest.taskDescription = requestValue.prompt;
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
}
