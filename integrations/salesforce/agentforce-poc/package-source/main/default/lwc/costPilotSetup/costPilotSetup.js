import { LightningElement, wire } from 'lwc';
import getSetupState from '@salesforce/apex/CostPilotSetupController.getSetupState';

export default class CostPilotSetup extends LightningElement {
    state;
    errorMessage;
    loading = true;

    @wire(getSetupState)
    loadState({ data, error }) {
        this.loading = false;
        if (data) {
            this.state = data;
            this.errorMessage = undefined;
        } else if (error) {
            this.errorMessage =
                error?.body?.message || 'Salesforce did not return the organization details.';
        }
    }

    get environmentLabel() {
        return this.state?.sandbox ? 'Sandbox' : 'Production';
    }

    connect() {
        window.open(this.state.connectUrl, '_blank', 'noopener,noreferrer');
    }
}
