@IsTest
private class CostPilotSetupControllerTest {
    @IsTest
    static void returnsSubscriberOrgConnectionDetails() {
        Test.startTest();
        CostPilotSetupController.SetupState state =
            CostPilotSetupController.getSetupState();
        Test.stopTest();

        System.assertEquals(UserInfo.getOrganizationId(), state.orgId);
        System.assertNotEquals(null, state.orgName);
        System.assert(state.instanceUrl.startsWith('https://'));
        System.assert(state.connectUrl.contains('/salesforce/package/start'));
        System.assert(state.connectUrl.contains('org_id='));
    }
}
