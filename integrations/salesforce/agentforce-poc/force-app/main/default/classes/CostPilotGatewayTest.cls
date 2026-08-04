@IsTest
private class CostPilotGatewayTest {
    private class CostPilotMock implements HttpCalloutMock {
        public HttpResponse respond(HttpRequest requestValue) {
            System.assert(requestValue.getEndpoint().startsWith('callout:CostPilot/'));
            Map<String, Object> requestBody =
                (Map<String, Object>) JSON.deserializeUntyped(requestValue.getBody());
            String taskDescription = String.valueOf(
                requestBody.get('task_description')
            );
            System.assert(taskDescription.contains('Salesforce record context:'));
            System.assert(taskDescription.contains('Gateway Test'));
            System.assertEquals('CP-TEST-1', requestBody.get('project_external_id'));
            System.assertEquals('Gateway Test', requestBody.get('project_name'));
            System.assertEquals(
                'CostPilot_Project__c',
                requestBody.get('source_type')
            );
            System.assertEquals('Salesforce', requestBody.get('source_system'));
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

    private class GeneralAgentMock implements HttpCalloutMock {
        public HttpResponse respond(HttpRequest requestValue) {
            Map<String, Object> requestBody =
                (Map<String, Object>) JSON.deserializeUntyped(requestValue.getBody());
            String recordId = String.valueOf(requestBody.get('record_id'));
            System.assert(recordId.startsWith('AGENT-' + UserInfo.getOrganizationId()));
            System.assertEquals(recordId, requestBody.get('project_external_id'));
            System.assertEquals('Salesforce Agentforce', requestBody.get('project_name'));
            System.assertEquals('AgentforceAgent', requestBody.get('source_type'));
            HttpResponse responseValue = new HttpResponse();
            responseValue.setStatusCode(200);
            responseValue.setHeader('Content-Type', 'application/json');
            responseValue.setBody(JSON.serialize(new Map<String, Object>{
                'allowed' => true,
                'decision' => 'ROUTINE',
                'reason' => 'Approved',
                'project_id' => recordId,
                'project_name' => 'Salesforce Agentforce',
                'selected_model' => 'claude-haiku-4-5-20251001',
                'selected_tier' => 'Scout',
                'estimated_cost_usd' => 0.0001,
                'tracking_id' => 'CP-TX-GENERAL-AGENT',
                'ai_response' => 'General agent response through CostPilot.',
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

    @IsTest
    static void governsStandaloneAgentChatWithoutSalesforceRecordContext() {
        CostPilotAgentforceAction.workspaceId = 'TEST-WORKSPACE';
        CostPilotGateway.Request requestValue = new CostPilotGateway.Request();
        requestValue.prompt = 'Draft a customer follow-up email.';

        Test.setMock(HttpCalloutMock.class, new GeneralAgentMock());
        Test.startTest();
        List<CostPilotGateway.Response> responses = CostPilotGateway.send(
            new List<CostPilotGateway.Request>{ requestValue }
        );
        Test.stopTest();

        System.assertEquals(1, responses.size());
        System.assertEquals(false, responses[0].blocked);
        System.assertEquals('CP-TX-GENERAL-AGENT', responses[0].auditId);
    }
}
