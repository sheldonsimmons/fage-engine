@IsTest
private class CostPilotAgentforceActionTest {
    private static Id projectRecordId;

    private class CostPilotMock implements HttpCalloutMock {
        public HttpResponse respond(HttpRequest request) {
            System.assertEquals('POST', request.getMethod());
            System.assert(request.getEndpoint().contains('/agentforce/TEST-WORKSPACE/govern'));

            Map<String, Object> body =
                (Map<String, Object>) JSON.deserializeUntyped(request.getBody());
            if (projectRecordId == null) {
                System.assertEquals('006000000000001AAA', body.get('record_id'));
                System.assertEquals('ACME-2026-041', body.get('project_external_id'));
                System.assertEquals('Member', body.get('project_member_role'));
            } else {
                System.assertEquals(String.valueOf(projectRecordId), body.get('record_id'));
                System.assertEquals('CP-AUTO-001', body.get('project_external_id'));
                System.assertEquals('Automatically Resolved Project', body.get('project_name'));
                System.assertEquals('Engineering', body.get('department'));
                System.assertEquals('Project Manager', body.get('project_member_role'));
                System.assertEquals(true, body.get('project_member_can_use_ai'));
            }
            System.assertEquals(UserInfo.getUserId(), body.get('salesforce_user_id'));
            System.assertEquals(UserInfo.getName(), body.get('salesforce_user_name'));
            System.assertEquals(UserInfo.getUserEmail(), body.get('salesforce_user_email'));
            System.assertEquals('active', body.get('project_member_status'));
            System.assertEquals('Salesforce', body.get('source_system'));

            HttpResponse response = new HttpResponse();
            response.setStatusCode(200);
            response.setHeader('Content-Type', 'application/json');
            response.setBody(JSON.serialize(new Map<String, Object>{
                'allowed'                       => true,
                'decision'                      => 'ROUTINE',
                'reason'                        => 'Routine request routed to the Scout tier.',
                'project_id'                    => 'ACME-2026-041',
                'project_name'                  => 'Acme Website Expansion',
                'selected_model'                => 'Claude Haiku 4.5',
                'selected_tier'                 => 'Scout',
                'estimated_cost_usd'            => 0.03,
                'project_budget_remaining_usd'  => 499.97,
                'tracking_id'                   => 'CP-TX-123',
                'ai_response'                   => 'ACME should schedule a customer follow-up.',
                'execution_mode'                => 'live'
            }));
            return response;
        }
    }

    @IsTest
    static void returnsStructuredGovernanceResult() {
        CostPilotAgentforceAction.workspaceId = 'TEST-WORKSPACE';
        Test.setMock(HttpCalloutMock.class, new CostPilotMock());

        CostPilotAgentforceAction.GovernanceRequest input =
            new CostPilotAgentforceAction.GovernanceRequest();
        input.recordId = '006000000000001AAA';
        input.taskDescription = 'Summarize this opportunity and recommend the next action.';
        input.projectExternalId = 'ACME-2026-041';
        input.projectName = 'Acme Website Expansion';
        input.projectStatus = 'Active';
        input.monthlyAiBudget = 500;
        input.department = 'Sales';
        input.agentName = 'Opportunity Assistant';

        Test.startTest();
        List<CostPilotAgentforceAction.GovernanceResult> results =
            CostPilotAgentforceAction.govern(
                new List<CostPilotAgentforceAction.GovernanceRequest>{ input }
            );
        Test.stopTest();

        System.assertEquals(1, results.size());
        System.assertEquals(true, results[0].allowed);
        System.assertEquals('ACME-2026-041', results[0].projectId);
        System.assertEquals('Claude Haiku 4.5', results[0].selectedModel);
        System.assertEquals(0.03, results[0].estimatedCostUsd);
        System.assertEquals('CP-TX-123', results[0].trackingId);
        System.assertEquals(
            'ACME should schedule a customer follow-up.',
            results[0].aiResponse
        );
        System.assertEquals('live', results[0].executionMode);
    }

    @IsTest
    static void rejectsMissingRequiredInputsWithoutCallout() {
        CostPilotAgentforceAction.GovernanceRequest input =
            new CostPilotAgentforceAction.GovernanceRequest();
        input.recordId = '006000000000001AAA';

        List<CostPilotAgentforceAction.GovernanceResult> results =
            CostPilotAgentforceAction.govern(
                new List<CostPilotAgentforceAction.GovernanceRequest>{ input }
            );

        System.assertEquals(false, results[0].allowed);
        System.assertEquals('INVALID_REQUEST', results[0].decision);
    }

    @IsTest
    static void resolvesProjectContextAndWritesResponseToProject() {
        CostPilot_Project__c project = new CostPilot_Project__c(
            Name = 'Automatically Resolved Project',
            Project_ID__c = 'CP-AUTO-001',
            Project_Owner__c = 'Test Owner',
            Status__c = 'Active',
            Monthly_AI_Budget__c = 250,
            Department__c = 'Engineering'
        );
        insert project;
        projectRecordId = project.Id;
        insert new CostPilot_Project_Member__c(
            Project__c = project.Id,
            Salesforce_User__c = UserInfo.getUserId(),
            Role__c = 'Project Manager',
            Status__c = 'Active',
            Can_Use_AI__c = true
        );

        CostPilotAgentforceAction.workspaceId = 'TEST-WORKSPACE';
        Test.setMock(HttpCalloutMock.class, new CostPilotMock());
        CostPilotAgentforceAction.GovernanceRequest input =
            new CostPilotAgentforceAction.GovernanceRequest();
        input.recordId = project.Id;
        input.taskDescription = 'Summarize the project status.';
        input.agentName = 'Project Status Agent';

        Test.startTest();
        List<CostPilotAgentforceAction.GovernanceResult> results =
            CostPilotAgentforceAction.govern(
                new List<CostPilotAgentforceAction.GovernanceRequest>{ input }
            );
        Test.stopTest();

        CostPilot_Project__c updated = [
            SELECT CostPilot_Decision__c, CostPilot_Tracking_ID__c,
                   Estimated_AI_Cost__c, Last_AI_Request__c, Selected_Model__c
            FROM CostPilot_Project__c
            WHERE Id = :project.Id
        ];
        System.assertEquals(true, results[0].allowed);
        System.assertEquals('ROUTINE', updated.CostPilot_Decision__c);
        System.assertEquals('CP-TX-123', updated.CostPilot_Tracking_ID__c);
        System.assertEquals(0.03, updated.Estimated_AI_Cost__c);
        System.assertEquals(
            'ACME should schedule a customer follow-up.',
            updated.Last_AI_Request__c
        );
        System.assertEquals('Claude Haiku 4.5', updated.Selected_Model__c);
    }
}
