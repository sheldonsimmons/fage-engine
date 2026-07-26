@IsTest
private class CostPilotGatewayTest {
    private class CostPilotMock implements HttpCalloutMock {
        public HttpResponse respond(HttpRequest requestValue) {
            System.assert(requestValue.getEndpoint().startsWith('callout:CostPilot/'));
            HttpResponse responseValue = new HttpResponse();
            responseValue.setStatusCode(200);
            responseValue.setHeader('Content-Type', 'application/json');
            responseValue.setBody(JSON.serialize(new Map<String, Object>{
                'allowed' => true,
                'decision' => 'ROUTINE',
                'reason' => 'Approved',
                'project_id' => 'CP-TEST-1',
                'project_name' => 'Gateway Test',
                'selected_model' => 'claude-haiku-4-5-20251001',
                'selected_tier' => 'Scout',
                'estimated_cost_usd' => 0.0001,
                'project_budget_remaining_usd' => 9.99,
                'tracking_id' => 'CP-TX-GATEWAY',
                'ai_response' => 'This response came through CostPilot.',
                'execution_mode' => 'live'
            }));
            return responseValue;
        }
    }

    @IsTest
    static void adaptsTheExistingAgentforceActionToCostPilot() {
        CostPilot_Project__c project = new CostPilot_Project__c(
            Name = 'Gateway Test',
            Project_ID__c = 'CP-TEST-1',
            Status__c = 'Active',
            Department__c = 'Sales',
            Monthly_AI_Budget__c = 10
        );
        insert project;
        insert new CostPilot_Project_Member__c(
            Project__c = project.Id,
            Salesforce_User__c = UserInfo.getUserId(),
            Status__c = 'Active',
            Can_Use_AI__c = true
        );

        CostPilotGateway.Request requestValue = new CostPilotGateway.Request();
        requestValue.recordId = project.Id;
        requestValue.prompt = 'Draft a customer follow-up email.';
        requestValue.agentName = 'CostPilot Agent';
        requestValue.department = 'Sales';

        Test.setMock(HttpCalloutMock.class, new CostPilotMock());
        Test.startTest();
        List<CostPilotGateway.Response> responses = CostPilotGateway.send(
            new List<CostPilotGateway.Request>{ requestValue }
        );
        Test.stopTest();

        System.assertEquals(1, responses.size());
        System.assertEquals(false, responses[0].blocked);
        System.assertEquals('CP-TX-GATEWAY', responses[0].auditId);
        System.assertEquals(
            'This response came through CostPilot.',
            responses[0].costPilotResponse
        );
    }
}
