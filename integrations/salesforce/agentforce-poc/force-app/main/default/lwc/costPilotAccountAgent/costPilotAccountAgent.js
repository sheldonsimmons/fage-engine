import { LightningElement, api } from 'lwc';
import { open, execute } from 'lightning/accApi';

export default class CostPilotAccountAgent extends LightningElement {

    @api recordId;
    @api botId;

    errorMessage;

    async handleAnalyzeAccount() {
        this.errorMessage = null;
        console.log('[costPilotAccountAgent] click fired. recordId=', this.recordId, 'botId=', this.botId);

        try {
            console.log('[costPilotAccountAgent] calling open()...');
            const openResult = await open(this.botId);
            console.log('[costPilotAccountAgent] open() resolved:', openResult);

            const prompt =
                `Tell me about the Salesforce Account with Id ${this.recordId}.`;

            console.log('[costPilotAccountAgent] calling execute() with prompt:', prompt);
            const executeResult = await execute(prompt, this.botId);
            console.log('[costPilotAccountAgent] execute() resolved:', executeResult);

        } catch (error) {
            console.error('[costPilotAccountAgent] ERROR:', error);

            this.errorMessage =
                'Unable to open Agentforce for this Account.';
        }
    }

    async handleShowOpportunities() {
        this.errorMessage = null;

        try {
            await open(this.botId);

            const prompt =
                `What opportunities does the Salesforce Account with Id ${this.recordId} have?`;

            await execute(prompt, this.botId);

        } catch (error) {
            console.error(error);

            this.errorMessage =
                'Unable to retrieve Opportunity information.';
        }
    }
}
