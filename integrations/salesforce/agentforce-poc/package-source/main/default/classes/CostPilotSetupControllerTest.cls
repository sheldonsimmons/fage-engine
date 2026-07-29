@IsTest
private class CostPilotSetupControllerTest {
    private class SetupCalloutMock implements HttpCalloutMock {
        public HttpResponse respond(HttpRequest requestValue) {
            HttpResponse responseValue = new HttpResponse();
            responseValue.setStatusCode(200);
            responseValue.setHeader('Content-Type', 'application/json');

            if (requestValue.getEndpoint().contains('/package/status')) {
                responseValue.setBody(JSON.serialize(new Map<String, Object>{
                    'status' => 'connected',
                    'connected' => true,
                    'workspace_id' => 'WORKSPACE-TEST',
                    'connection_id' => 42,
                    'last_error' => null
                }));
            } else if (
                requestValue.getMethod() == 'GET' &&
                requestValue.getEndpoint().endsWith('/ai-entry-points')
            ) {
                responseValue.setBody(JSON.serialize(new Map<String, Object>{
                    'agents' => new List<Object>{
                        new Map<String, Object>{
                            'id' => '0Xx000000000001',
                            'name' => 'Sales_Assistant',
                            'label' => 'Sales Assistant',
                            'status' => 'Active'
                        }
                    },
                    'flows' => new List<Object>{
                        new Map<String, Object>{
                            'id' => '300000000000001',
                            'name' => 'Draft_Follow_Up',
                            'label' => 'Draft Follow Up',
                            'status' => 'Active',
                            'process_type' => 'AutoLaunchedFlow'
                        }
                    },
                    'selected' => new List<Object>{
                        new Map<String, Object>{
                            'kind' => 'agent',
                            'id' => '0Xx000000000001'
                        }
                    },
                    'warnings' => new List<Object>()
                }));
            } else if (
                requestValue.getMethod() == 'POST' &&
                requestValue.getEndpoint().endsWith('/ai-entry-points/selection')
            ) {
                responseValue.setBody('{"count":1}');
            } else {
                responseValue.setStatusCode(404);
                responseValue.setBody('{"detail":"Not found"}');
            }
            return responseValue;
        }
    }

    @IsTest
    static void returnsSubscriberOrgConnectionDetails() {
        Test.setMock(HttpCalloutMock.class, new SetupCalloutMock());

        Test.startTest();
        CostPilotSetupController.SetupState state =
            CostPilotSetupController.getSetupState();
        Test.stopTest();

        System.assertEquals(UserInfo.getOrganizationId(), state.orgId);
        System.assertNotEquals(null, state.orgName);
        System.assert(state.instanceUrl.startsWith('https://'));
        System.assert(state.connectUrl.contains('/salesforce/package/start'));
        System.assert(state.connectUrl.contains('org_id='));
        System.assertEquals(true, state.connected);
        System.assertEquals('WORKSPACE-TEST', state.workspaceId);
        System.assertEquals(42, state.connectionId);
        System.assertEquals(1, state.agents.size());
        System.assertEquals('Sales Assistant', state.agents[0].label);
        System.assertEquals(true, state.agents[0].selected);
        System.assertEquals(1, state.flows.size());
        System.assertEquals('Draft Follow Up', state.flows[0].label);
        System.assertEquals(false, state.flows[0].selected);
    }

    @IsTest
    static void savesApprovedEntryPoints() {
        Test.setMock(HttpCalloutMock.class, new SetupCalloutMock());

        Test.startTest();
        CostPilotSetupController.saveSelections(
            42,
            '[{"kind":"agent","id":"0Xx000000000001",' +
            '"name":"Sales_Assistant","label":"Sales Assistant"}]'
        );
        Test.stopTest();
    }
}
