public with sharing class CostPilotSetupController {
    public class SetupState {
        @AuraEnabled public String orgId;
        @AuraEnabled public String orgName;
        @AuraEnabled public String instanceUrl;
        @AuraEnabled public String connectUrl;
        @AuraEnabled public Boolean sandbox;
        @AuraEnabled public Boolean connected;
        @AuraEnabled public String status;
        @AuraEnabled public String workspaceId;
        @AuraEnabled public Integer connectionId;
        @AuraEnabled public String connectionError;
        @AuraEnabled public List<EntryPoint> agents = new List<EntryPoint>();
        @AuraEnabled public List<EntryPoint> flows = new List<EntryPoint>();
        @AuraEnabled public List<String> warnings = new List<String>();
    }

    public class EntryPoint {
        @AuraEnabled public String id;
        @AuraEnabled public String name;
        @AuraEnabled public String label;
        @AuraEnabled public String kind;
        @AuraEnabled public String status;
        @AuraEnabled public String processType;
        @AuraEnabled public Boolean selected;
    }

    @AuraEnabled
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
        state.connected = false;
        state.status = 'not_started';
        state.connectUrl =
            'https://fage-engine-21cb49fe4806.herokuapp.com' +
            '/api/integrations/connections/salesforce/package/start' +
            '?org_id=' + EncodingUtil.urlEncode(state.orgId, 'UTF-8') +
            '&instance_url=' + EncodingUtil.urlEncode(instanceUrl, 'UTF-8');

        try {
            HttpResponse statusResponse = sendGet(
                '/api/integrations/connections/salesforce/package/status' +
                '?org_id=' + EncodingUtil.urlEncode(state.orgId, 'UTF-8')
            );
            if (!isSuccess(statusResponse)) {
                state.connectionError =
                    'CostPilot returned HTTP ' + statusResponse.getStatusCode() +
                    ' while checking this connection.';
                return state;
            }
            Map<String, Object> statusPayload =
                (Map<String, Object>) JSON.deserializeUntyped(statusResponse.getBody());
            state.status = stringValue(statusPayload.get('status'), 'not_started');
            state.connected =
                statusPayload.get('connected') == null
                    ? false
                    : (Boolean) statusPayload.get('connected');
            state.workspaceId = stringValue(statusPayload.get('workspace_id'), null);
            state.connectionId = integerValue(statusPayload.get('connection_id'));
            state.connectionError = stringValue(statusPayload.get('last_error'), null);

            if (state.connected && state.connectionId != null) {
                populateEntryPoints(state);
            }
        } catch (Exception exceptionValue) {
            // Before the first authorization the packaged named principal is
            // intentionally empty. The Connect button remains the valid next step.
            state.connected = false;
            state.status = 'not_started';
            state.connectionError = null;
        }
        return state;
    }

    @AuraEnabled
    public static void saveSelections(Integer connectionId, String entriesJson) {
        if (connectionId == null) {
            throw new AuraHandledException('Connect Salesforce before selecting AI entry points.');
        }
        Object entries;
        try {
            entries = JSON.deserializeUntyped(entriesJson);
        } catch (Exception ignored) {
            throw new AuraHandledException('The selected agents and Flows could not be read.');
        }
        HttpRequest requestValue = new HttpRequest();
        requestValue.setEndpoint(
            'callout:CostPilot/api/integrations/connections/' +
            connectionId + '/ai-entry-points/selection'
        );
        requestValue.setMethod('POST');
        requestValue.setHeader('Content-Type', 'application/json');
        requestValue.setTimeout(30000);
        requestValue.setBody(JSON.serialize(new Map<String, Object>{
            'entries' => entries
        }));
        HttpResponse responseValue;
        try {
            responseValue = new Http().send(requestValue);
        } catch (Exception exceptionValue) {
            throw new AuraHandledException(
                'CostPilot could not save the selection: ' + exceptionValue.getMessage()
            );
        }
        if (!isSuccess(responseValue)) {
            throw new AuraHandledException(
                'CostPilot could not save the selection (HTTP ' +
                responseValue.getStatusCode() + ').'
            );
        }
    }

    private static void populateEntryPoints(SetupState state) {
        HttpResponse responseValue = sendGet(
            '/api/integrations/connections/' + state.connectionId + '/ai-entry-points'
        );
        if (!isSuccess(responseValue)) {
            state.warnings.add(
                'CostPilot could not list this organization\'s agents and Flows (HTTP ' +
                responseValue.getStatusCode() + ').'
            );
            return;
        }
        Map<String, Object> payload =
            (Map<String, Object>) JSON.deserializeUntyped(responseValue.getBody());
        Set<String> selectedKeys = new Set<String>();
        List<Object> selectedValues = (List<Object>) payload.get('selected');
        if (selectedValues != null) {
            for (Object selectedValue : selectedValues) {
                Map<String, Object> selectedEntry = (Map<String, Object>) selectedValue;
                selectedKeys.add(
                    stringValue(selectedEntry.get('kind'), '') + ':' +
                    stringValue(selectedEntry.get('id'), '')
                );
            }
        }
        state.agents = toEntryPoints(
            (List<Object>) payload.get('agents'), 'agent', selectedKeys
        );
        state.flows = toEntryPoints(
            (List<Object>) payload.get('flows'), 'flow', selectedKeys
        );
        List<Object> warningValues = (List<Object>) payload.get('warnings');
        if (warningValues != null) {
            for (Object warningValue : warningValues) {
                state.warnings.add(String.valueOf(warningValue));
            }
        }
    }

    private static List<EntryPoint> toEntryPoints(
        List<Object> values,
        String kind,
        Set<String> selectedKeys
    ) {
        List<EntryPoint> result = new List<EntryPoint>();
        if (values == null) {
            return result;
        }
        for (Object value : values) {
            Map<String, Object> source = (Map<String, Object>) value;
            EntryPoint item = new EntryPoint();
            item.id = stringValue(source.get('id'), '');
            item.name = stringValue(source.get('name'), item.id);
            item.label = stringValue(source.get('label'), item.name);
            item.kind = kind;
            item.status = stringValue(source.get('status'), 'discovered');
            item.processType = stringValue(source.get('process_type'), null);
            item.selected = selectedKeys.contains(kind + ':' + item.id);
            result.add(item);
        }
        return result;
    }

    private static HttpResponse sendGet(String path) {
        HttpRequest requestValue = new HttpRequest();
        requestValue.setEndpoint('callout:CostPilot' + path);
        requestValue.setMethod('GET');
        requestValue.setHeader('Accept', 'application/json');
        requestValue.setTimeout(30000);
        return new Http().send(requestValue);
    }

    private static Boolean isSuccess(HttpResponse responseValue) {
        return responseValue.getStatusCode() >= 200 &&
            responseValue.getStatusCode() < 300;
    }

    private static String stringValue(Object value, String fallbackValue) {
        return value == null ? fallbackValue : String.valueOf(value);
    }

    private static Integer integerValue(Object value) {
        if (value == null) {
            return null;
        }
        return Decimal.valueOf(String.valueOf(value)).intValue();
    }
}
