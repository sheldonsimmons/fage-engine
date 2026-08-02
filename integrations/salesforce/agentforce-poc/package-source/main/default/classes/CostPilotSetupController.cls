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
        @AuraEnabled public Boolean selectionsSaved = false;
        @AuraEnabled public Boolean relationshipsApproved = false;
        @AuraEnabled public String parentObject = 'Account';
        @AuraEnabled public List<RelationshipChild> relationshipChildren = new List<RelationshipChild>();
        @AuraEnabled public Boolean verificationPassed = false;
        @AuraEnabled public String verificationMessage;
        @AuraEnabled public Integer verificationAuditId;
        @AuraEnabled public Boolean activated = false;
        @AuraEnabled public String goLiveAt;
    }

    public class RelationshipChild {
        @AuraEnabled public String objectName;
        @AuraEnabled public String label;
        @AuraEnabled public String parentField;
        @AuraEnabled public String behavior;
        @AuraEnabled public Boolean selected;
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
                populatePackageSetup(state);
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

    @AuraEnabled
    public static SetupState approveRelationships(
        Integer connectionId,
        String parentObject,
        String childrenJson
    ) {
        if (connectionId == null) {
            throw new AuraHandledException('Connect Salesforce before approving relationships.');
        }
        Object children;
        try {
            children = JSON.deserializeUntyped(childrenJson);
        } catch (Exception ignored) {
            throw new AuraHandledException('The relationship suggestions could not be read.');
        }
        HttpResponse responseValue = sendPost(
            '/api/integrations/connections/' + connectionId + '/package-setup/relationships',
            new Map<String, Object>{
                'parent_object' => String.isBlank(parentObject) ? 'Account' : parentObject.trim(),
                'children' => children
            }
        );
        assertSuccess(responseValue, 'approve the relationship mapping');
        SetupState state = new SetupState();
        state.connectionId = connectionId;
        applyPackageSetup(state, responseValue.getBody());
        return state;
    }

    @AuraEnabled
    public static SetupState verifyConnection(Integer connectionId) {
        HttpResponse responseValue = sendPost(
            '/api/integrations/connections/' + connectionId + '/package-setup/verify',
            new Map<String, Object>()
        );
        assertSuccess(responseValue, 'verify the governed request');
        SetupState state = new SetupState();
        state.connectionId = connectionId;
        applyPackageSetup(state, responseValue.getBody());
        return state;
    }

    @AuraEnabled
    public static SetupState activateConnection(Integer connectionId) {
        HttpResponse responseValue = sendPost(
            '/api/integrations/connections/' + connectionId + '/package-setup/activate',
            new Map<String, Object>()
        );
        assertSuccess(responseValue, 'activate this connection');
        SetupState state = new SetupState();
        state.connectionId = connectionId;
        applyPackageSetup(state, responseValue.getBody());
        return state;
    }

    @AuraEnabled(cacheable=true)
    public static List<RelationshipChild> discoverRelationships(String parentObject) {
        String parentName = String.isBlank(parentObject) ? 'Account' : parentObject.trim();
        Map<String, Schema.SObjectType> globalDescribe = Schema.getGlobalDescribe();
        if (!globalDescribe.containsKey(parentName)) {
            throw new AuraHandledException('Salesforce object ' + parentName + ' was not found.');
        }
        List<RelationshipChild> candidates = new List<RelationshipChild>();
        for (String objectName : globalDescribe.keySet()) {
            if (objectName == parentName || objectName.endsWith('History') ||
                objectName.endsWith('Share') || objectName.endsWith('Feed') ||
                objectName.endsWith('ChangeEvent')) {
                continue;
            }
            Schema.DescribeSObjectResult objectDescribe = globalDescribe.get(objectName).getDescribe();
            if (!objectDescribe.isQueryable() || objectDescribe.isDeprecatedAndHidden()) {
                continue;
            }
            for (Schema.SObjectField fieldToken : objectDescribe.fields.getMap().values()) {
                Schema.DescribeFieldResult fieldDescribe = fieldToken.getDescribe();
                if (fieldDescribe.getType() != Schema.DisplayType.Reference) {
                    continue;
                }
                Boolean pointsToParent = false;
                for (Schema.SObjectType referenceType : fieldDescribe.getReferenceTo()) {
                    if (referenceType.getDescribe().getName() == parentName) {
                        pointsToParent = true;
                        break;
                    }
                }
                if (pointsToParent) {
                    RelationshipChild child = new RelationshipChild();
                    child.objectName = objectName;
                    child.label = objectDescribe.getLabel();
                    child.parentField = fieldDescribe.getName();
                    child.behavior = 'track_and_rollup';
                    child.selected = true;
                    candidates.add(child);
                    break;
                }
            }
            if (candidates.size() >= 75) break;
        }
        return candidates;
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

    private static void populatePackageSetup(SetupState state) {
        HttpResponse responseValue = sendGet(
            '/api/integrations/connections/' + state.connectionId + '/package-setup'
        );
        if (!isSuccess(responseValue)) {
            state.warnings.add('CostPilot could not load the saved onboarding progress.');
            return;
        }
        applyPackageSetup(state, responseValue.getBody());
    }

    private static void applyPackageSetup(SetupState state, String responseBody) {
        Map<String, Object> payload =
            (Map<String, Object>) JSON.deserializeUntyped(responseBody);
        List<Object> selected = (List<Object>) payload.get('selected');
        state.selectionsSaved = selected != null && !selected.isEmpty();
        Map<String, Object> relationships = (Map<String, Object>) payload.get('relationships');
        if (relationships != null) {
            state.relationshipsApproved = Boolean.valueOf(
                stringValue(relationships.get('approved'), 'false')
            );
            state.parentObject = stringValue(relationships.get('parent_object'), 'Account');
            List<Object> children = (List<Object>) relationships.get('children');
            if (children != null) {
                for (Object childValue : children) {
                    Map<String, Object> source = (Map<String, Object>) childValue;
                    RelationshipChild child = new RelationshipChild();
                    child.objectName = stringValue(source.get('object_name'), '');
                    child.label = stringValue(source.get('label'), child.objectName);
                    child.parentField = stringValue(source.get('parent_field'), '');
                    child.behavior = stringValue(source.get('behavior'), 'track_and_rollup');
                    child.selected = child.behavior != 'ignore';
                    state.relationshipChildren.add(child);
                }
            }
        }
        Map<String, Object> verification = (Map<String, Object>) payload.get('verification');
        if (verification != null) {
            state.verificationPassed = Boolean.valueOf(
                stringValue(verification.get('verified'), 'false')
            );
            state.verificationMessage = stringValue(verification.get('message'), null);
            state.verificationAuditId = integerValue(verification.get('audit_id'));
        }
        state.activated = Boolean.valueOf(stringValue(payload.get('active'), 'false'));
        state.goLiveAt = stringValue(payload.get('go_live_at'), null);
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

    private static HttpResponse sendPost(String path, Object bodyValue) {
        HttpRequest requestValue = new HttpRequest();
        requestValue.setEndpoint('callout:CostPilot' + path);
        requestValue.setMethod('POST');
        requestValue.setHeader('Content-Type', 'application/json');
        requestValue.setTimeout(30000);
        requestValue.setBody(JSON.serialize(bodyValue));
        try {
            return new Http().send(requestValue);
        } catch (Exception exceptionValue) {
            throw new AuraHandledException('CostPilot could not be reached: ' + exceptionValue.getMessage());
        }
    }

    private static void assertSuccess(HttpResponse responseValue, String actionLabel) {
        if (!isSuccess(responseValue)) {
            String detail = responseValue.getBody();
            throw new AuraHandledException(
                'CostPilot could not ' + actionLabel + ' (HTTP ' +
                responseValue.getStatusCode() + '). ' + detail
            );
        }
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
