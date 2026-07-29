public with sharing class CostPilotSetupController {
    public class SetupState {
        @AuraEnabled public String orgId;
        @AuraEnabled public String orgName;
        @AuraEnabled public String instanceUrl;
        @AuraEnabled public String connectUrl;
        @AuraEnabled public Boolean sandbox;
    }

    @AuraEnabled(cacheable=true)
    public static SetupState getSetupState() {
        Organization orgRecord = [
            SELECT Id, Name, IsSandbox
            FROM Organization
            LIMIT 1
        ];
        String instanceUrl = URL.getOrgDomainUrl().toExternalForm();
        SetupState state = new SetupState();
        state.orgId = UserInfo.getOrganizationId();
        state.orgName = orgRecord.Name;
        state.instanceUrl = instanceUrl;
        state.sandbox = orgRecord.IsSandbox;
        state.connectUrl =
            'https://fage-engine-21cb49fe4806.herokuapp.com' +
            '/api/integrations/connections/salesforce/package/start' +
            '?org_id=' + EncodingUtil.urlEncode(state.orgId, 'UTF-8') +
            '&instance_url=' + EncodingUtil.urlEncode(instanceUrl, 'UTF-8');
        return state;
    }
}
