@IsTest
private class CostPilotLoadGeneratorTest {
    private class CostPilotMock implements HttpCalloutMock {
        public HttpResponse respond(HttpRequest requestValue) {
            System.assert(requestValue.getEndpoint().contains('/agentforce/TEST-WORKSPACE/govern'));
            Map<String, Object> body =
                (Map<String, Object>) JSON.deserializeUntyped(requestValue.getBody());
            System.assertEquals(true, body.get('simulation_mode'));
            System.assertEquals(UserInfo.getUserId(), body.get('salesforce_user_id'));
            System.assert(String.valueOf(body.get('task_description')).startsWith('[scout]'));
            HttpResponse responseValue = new HttpResponse();
            responseValue.setStatusCode(200);
            responseValue.setHeader('Content-Type', 'application/json');
            responseValue.setBody(JSON.serialize(new Map<String, Object>{
                'allowed' => true,
                'decision' => 'ROUTINE',
                'reason' => 'Test route',
                'project_id' => 'PROJECT-TEST',
                'project_name' => 'Acme Test',
                'selected_model' => 'claude-haiku-4-5-20251001',
                'selected_tier' => 'Scout',
                'estimated_cost_usd' => 0.001,
                'tracking_id' => 'CP-TEST-1',
                'ai_response' => 'Simulated response',
                'execution_mode' => 'simulation'
            }));
            return responseValue;
        }
    }

    @IsTest
    static void startsAndCompletesBoundedSimulationRun() {
        Account accountValue = new Account(Name = 'Acme Test');
        insert accountValue;
        CostPilotAgentforceAction.workspaceId = 'TEST-WORKSPACE';
        Test.setMock(HttpCalloutMock.class, new CostPilotMock());

        Test.startTest();
        CostPilotLoadGeneratorController.RunStatus initial =
            CostPilotLoadGeneratorController.startRun(
                accountValue.Id,
                UserInfo.getUserId(),
                'CostPilot Salesforce Test Agent',
                1,
                'simulation',
                'balanced',
                'Sales'
            );
        Test.stopTest();

        CostPilot_Test_Run__c stored = [
            SELECT Status__c, Total_Calls__c, Completed_Calls__c, Failed_Calls__c,
                   Source_Record_Id__c, Routing_Mix__c
            FROM CostPilot_Test_Run__c
            WHERE Id = :initial.runId
        ];
        System.assertEquals('Completed', stored.Status__c);
        System.assertEquals(1, stored.Total_Calls__c);
        System.assertEquals(1, stored.Completed_Calls__c);
        System.assertEquals(0, stored.Failed_Calls__c);
        System.assertEquals(accountValue.Id, stored.Source_Record_Id__c);
        System.assertEquals('balanced', stored.Routing_Mix__c);
    }

    @IsTest
    static void exposesRecordsUsersAndBalancedPrompts() {
        insert new Account(Name = 'Selectable Account');
        System.assertEquals(3, CostPilotLoadGeneratorController.getSourceObjects().size());
        System.assertEquals(1, CostPilotLoadGeneratorController.getRecords('Account').size());
        System.assert(!CostPilotLoadGeneratorController.getUsers().isEmpty());

        CostPilot_Test_Run__c run = new CostPilot_Test_Run__c(
            Source_Record_Name__c = 'Acme',
            Routing_Mix__c = 'balanced'
        );
        System.assert(
            CostPilotLoadGeneratorWorker.promptFor(run, 3).startsWith('[strategist]')
        );
        run.Routing_Mix__c = 'economy';
        System.assert(
            CostPilotLoadGeneratorWorker.promptFor(run, 9).startsWith('[advisor]')
        );
    }
}
