@IsTest
private class CostPilotAgentforceActionTest {
    private class CostPilotMock implements HttpCalloutMock {
        public HttpResponse respond(HttpRequest request) {
            System.assertEquals('POST', request.getMethod());
            System.assert(request.getEndpoint().contains('/agentforce/TEST-WORKSPACE/govern'));

            Map<String, Object> body =
                (Map<String, Object>) JSON.deserializeUntyped(request.getBody());
            System.assertEquals('006000000000001AAA', body.get('record_id'));
            System.assertEquals('ACME-2026-041', body.get('project_external_id'));

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
                'tracking_id'                   => 'CP-TX-123'
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
}
