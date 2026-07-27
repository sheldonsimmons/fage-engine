public with sharing class CostPilotLoadGeneratorController {
    private static final Set<String> SUPPORTED_OBJECTS =
        new Set<String>{ 'Account', 'Opportunity', 'CostPilot_Project__c' };

    @AuraEnabled(cacheable=true)
    public static List<OptionValue> getSourceObjects() {
        return new List<OptionValue>{
            new OptionValue('Account', 'Accounts'),
            new OptionValue('Opportunity', 'Opportunities'),
            new OptionValue('CostPilot_Project__c', 'CostPilot Projects')
        };
    }

    @AuraEnabled(cacheable=true)
    public static List<OptionValue> getRecords(String objectApiName) {
        validateObject(objectApiName);
        List<OptionValue> output = new List<OptionValue>();
        for (SObject row : Database.query(
            'SELECT Id, Name FROM ' + objectApiName + ' ORDER BY Name LIMIT 200'
        )) {
            output.add(new OptionValue(String.valueOf(row.get('Id')), String.valueOf(row.get('Name'))));
        }
        return output;
    }

    @AuraEnabled(cacheable=true)
    public static List<OptionValue> getUsers() {
        List<OptionValue> output = new List<OptionValue>();
        for (User row : [
            SELECT Id, Name, Email
            FROM User
            WHERE IsActive = true
            ORDER BY Name
            LIMIT 200
        ]) {
            OptionValue optionValue = new OptionValue(row.Id, row.Name);
            optionValue.description = row.Email;
            output.add(optionValue);
        }
        return output;
    }

    @AuraEnabled
    public static RunStatus startRun(
        String recordId,
        String attributionUserId,
        String agentName,
        Integer callCount,
        String mode,
        String routingMix,
        String department
    ) {
        if (String.isBlank(recordId)) {
            throw new AuraHandledException('Select a Salesforce record.');
        }
        Id typedRecordId;
        try {
            typedRecordId = Id.valueOf(recordId);
        } catch (Exception exceptionValue) {
            throw new AuraHandledException('The selected Salesforce record ID is invalid.');
        }
        String objectApiName = typedRecordId.getSObjectType().getDescribe().getName();
        validateObject(objectApiName);
        Integer boundedCalls = callCount == null ? 5 : callCount;
        if (boundedCalls < 1 || boundedCalls > 25) {
            throw new AuraHandledException('Choose between 1 and 25 calls.');
        }
        String normalizedMode = String.isBlank(mode) ? 'simulation' : mode.toLowerCase();
        if (normalizedMode != 'simulation' && normalizedMode != 'live') {
            throw new AuraHandledException('Mode must be Simulation or Live.');
        }
        if (normalizedMode == 'live' && boundedCalls > 3) {
            throw new AuraHandledException('Live mode is limited to 3 calls in this proof org.');
        }
        String normalizedMix = String.isBlank(routingMix) ? 'balanced' : routingMix.toLowerCase();
        if (!new Set<String>{'balanced', 'natural', 'economy', 'complex'}.contains(normalizedMix)) {
            throw new AuraHandledException('Select a supported routing mix.');
        }
        Id selectedUserId = String.isBlank(attributionUserId)
            ? UserInfo.getUserId()
            : Id.valueOf(attributionUserId);
        User attributionUser = [
            SELECT Id
            FROM User
            WHERE Id = :selectedUserId AND IsActive = true
            LIMIT 1
        ];
        SObject sourceRecord = Database.query(
            'SELECT Id, Name FROM ' + objectApiName + ' WHERE Id = :typedRecordId LIMIT 1'
        );
        CostPilot_Test_Run__c run = new CostPilot_Test_Run__c(
            Status__c = 'Queued',
            Total_Calls__c = boundedCalls,
            Completed_Calls__c = 0,
            Failed_Calls__c = 0,
            Mode__c = normalizedMode,
            Routing_Mix__c = normalizedMix,
            Source_Record_Id__c = recordId,
            Source_Record_Name__c = String.valueOf(sourceRecord.get('Name')),
            Source_Object__c = objectApiName,
            Attribution_User__c = attributionUser.Id,
            Agent_Name__c = String.isBlank(agentName) ? 'CostPilot Salesforce Test Agent' : agentName.trim(),
            Department__c = String.isBlank(department) ? 'Sales' : department.trim(),
            Stop_Requested__c = false
        );
        insert run;
        System.enqueueJob(new CostPilotLoadGeneratorWorker(run.Id));
        return statusFor(run.Id);
    }

    @AuraEnabled
    public static RunStatus getRunStatus(String runId) {
        return statusFor(Id.valueOf(runId));
    }

    @AuraEnabled
    public static RunStatus stopRun(String runId) {
        CostPilot_Test_Run__c run = [
            SELECT Id, Status__c, Stop_Requested__c
            FROM CostPilot_Test_Run__c
            WHERE Id = :runId
            LIMIT 1
            FOR UPDATE
        ];
        if (run.Status__c == 'Queued' || run.Status__c == 'Running') {
            run.Stop_Requested__c = true;
            update run;
        }
        return statusFor(run.Id);
    }

    private static RunStatus statusFor(Id runId) {
        return new RunStatus([
            SELECT Id, Name, Status__c, Total_Calls__c, Completed_Calls__c,
                   Failed_Calls__c, Mode__c, Routing_Mix__c, Source_Record_Name__c,
                   Source_Object__c, Agent_Name__c, Last_Error__c,
                   Stop_Requested__c, Started_At__c, Finished_At__c
            FROM CostPilot_Test_Run__c
            WHERE Id = :runId
            LIMIT 1
        ]);
    }

    private static void validateObject(String objectApiName) {
        if (!SUPPORTED_OBJECTS.contains(objectApiName)) {
            throw new AuraHandledException('That Salesforce object is not enabled for this test generator.');
        }
    }

    public class OptionValue {
        @AuraEnabled public String value;
        @AuraEnabled public String label;
        @AuraEnabled public String description;

        public OptionValue(String value, String label) {
            this.value = value;
            this.label = label;
        }
    }

    public class RunStatus {
        @AuraEnabled public String runId;
        @AuraEnabled public String runNumber;
        @AuraEnabled public String status;
        @AuraEnabled public Integer totalCalls;
        @AuraEnabled public Integer completedCalls;
        @AuraEnabled public Integer failedCalls;
        @AuraEnabled public String mode;
        @AuraEnabled public String routingMix;
        @AuraEnabled public String sourceRecordName;
        @AuraEnabled public String sourceObject;
        @AuraEnabled public String agentName;
        @AuraEnabled public String lastError;
        @AuraEnabled public Boolean stopRequested;
        @AuraEnabled public Datetime startedAt;
        @AuraEnabled public Datetime finishedAt;

        public RunStatus(CostPilot_Test_Run__c row) {
            runId = row.Id;
            runNumber = row.Name;
            status = row.Status__c;
            totalCalls = row.Total_Calls__c.intValue();
            completedCalls = row.Completed_Calls__c.intValue();
            failedCalls = row.Failed_Calls__c.intValue();
            mode = row.Mode__c;
            routingMix = row.Routing_Mix__c;
            sourceRecordName = row.Source_Record_Name__c;
            sourceObject = row.Source_Object__c;
            agentName = row.Agent_Name__c;
            lastError = row.Last_Error__c;
            stopRequested = row.Stop_Requested__c;
            startedAt = row.Started_At__c;
            finishedAt = row.Finished_At__c;
        }
    }
}
