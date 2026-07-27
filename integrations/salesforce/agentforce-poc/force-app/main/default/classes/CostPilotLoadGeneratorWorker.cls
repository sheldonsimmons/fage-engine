public with sharing class CostPilotLoadGeneratorWorker implements Queueable, Database.AllowsCallouts {
    private final Id runId;

    public CostPilotLoadGeneratorWorker(Id runId) {
        this.runId = runId;
    }

    public void execute(QueueableContext context) {
        CostPilot_Test_Run__c run = [
            SELECT Id, Status__c, Total_Calls__c, Completed_Calls__c, Failed_Calls__c,
                   Mode__c, Routing_Mix__c, Source_Record_Id__c, Source_Record_Name__c,
                   Source_Object__c, Attribution_User__c, Agent_Name__c, Department__c,
                   Stop_Requested__c, Started_At__c
            FROM CostPilot_Test_Run__c
            WHERE Id = :runId
            LIMIT 1
        ];
        if (run.Stop_Requested__c) {
            run.Status__c = 'Stopped';
            run.Finished_At__c = System.now();
            update run;
            return;
        }

        Integer completed = run.Completed_Calls__c.intValue();
        Integer failed = run.Failed_Calls__c.intValue();
        Integer total = run.Total_Calls__c.intValue();
        Integer sequence = completed + failed;
        User attributionUser = [
            SELECT Id, Name, Email
            FROM User
            WHERE Id = :run.Attribution_User__c
            LIMIT 1
        ];

        CostPilotAgentforceAction.GovernanceRequest requestValue =
            new CostPilotAgentforceAction.GovernanceRequest();
        requestValue.recordId = run.Source_Record_Id__c;
        requestValue.projectExternalId = run.Source_Record_Id__c;
        requestValue.projectName = run.Source_Record_Name__c;
        requestValue.sourceType = run.Source_Object__c;
        requestValue.sourceSystem = 'Salesforce';
        requestValue.department = run.Department__c;
        requestValue.agentName = run.Agent_Name__c;
        requestValue.taskDescription = promptFor(run, sequence);
        requestValue.attributionUserId = attributionUser.Id;
        requestValue.attributionUserName = attributionUser.Name;
        requestValue.attributionUserEmail = attributionUser.Email;
        requestValue.simulationMode = run.Mode__c == 'simulation';

        Boolean succeeded = false;
        String errorMessage;
        try {
            CostPilotAgentforceAction.GovernanceResult result =
                CostPilotAgentforceAction.govern(
                    new List<CostPilotAgentforceAction.GovernanceRequest>{ requestValue }
                )[0];
            succeeded = result.decision != 'INTEGRATION_ERROR' &&
                result.decision != 'INVALID_REQUEST';
            if (!succeeded) {
                errorMessage = result.reason;
            }
        } catch (Exception exceptionValue) {
            errorMessage = exceptionValue.getMessage();
        }

        if (succeeded) {
            completed++;
        } else {
            failed++;
        }
        run.Completed_Calls__c = completed;
        run.Failed_Calls__c = failed;
        run.Last_Error__c = errorMessage;
        if (run.Started_At__c == null) {
            run.Started_At__c = System.now();
        }
        Boolean finished = completed + failed >= total;
        if (finished) {
            run.Status__c = failed > 0 ? 'Completed with errors' : 'Completed';
            run.Finished_At__c = System.now();
        } else {
            run.Status__c = 'Running';
        }
        update run;
        if (!finished) {
            System.enqueueJob(new CostPilotLoadGeneratorWorker(run.Id));
        }
    }

    @TestVisible
    private static String promptFor(CostPilot_Test_Run__c run, Integer sequence) {
        String tag = '';
        if (run.Routing_Mix__c == 'balanced') {
            List<String> tags = new List<String>{'[scout]', '[analyst]', '[advisor]', '[strategist]'};
            tag = tags[Math.mod(sequence, tags.size())] + ' ';
        } else if (run.Routing_Mix__c == 'economy') {
            Integer slot = Math.mod(sequence, 10);
            tag = slot < 7 ? '[scout] ' : (slot < 9 ? '[analyst] ' : '[advisor] ');
        } else if (run.Routing_Mix__c == 'complex') {
            tag = Math.mod(sequence, 2) == 0 ? '[advisor] ' : '[strategist] ';
        }
        return tag + 'CostPilot Salesforce test call ' + String.valueOf(sequence + 1) +
            ' for ' + run.Source_Record_Name__c +
            '. Summarize the record context and provide a concise internal working note.';
    }
}
