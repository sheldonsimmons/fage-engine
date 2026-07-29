import { LightningElement, wire } from 'lwc';
import getSetupState from '@salesforce/apex/CostPilotSetupController.getSetupState';

export default class CostPilotSetup extends LightningElement {
    state;
    errorMessage;
    loading = true;

    @wire(getSetupState)
    loadState({ data, error }) {
        if (data) {
            this.state = data;
            this.errorMessage = undefined;
            this.loading = false;
        } else if (error) {
            this.state = undefined;
            this.errorMessage =
                error?.body?.message || 'Salesforce did not return the organization details.';
            this.loading = false;
        }
    }

    get ready() {
        return Boolean(this.state);
    }

    get environmentLabel() {
        return this.state?.sandbox ? 'Sandbox' : 'Production';
    }

    connect() {
        if (this.state?.connectUrl) {
            window.open(this.state.connectUrl, '_blank', 'noopener,noreferrer');
        }
    }
}
