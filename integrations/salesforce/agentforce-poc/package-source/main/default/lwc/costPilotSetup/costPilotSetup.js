import { LightningElement } from 'lwc';
import getSetupState from '@salesforce/apex/CostPilotSetupController.getSetupState';
import saveSelections from '@salesforce/apex/CostPilotSetupController.saveSelections';

export default class CostPilotSetup extends LightningElement {
    state;
    errorMessage;
    loading = true;
    saving = false;
    activeStep = 1;
    manualAgentName = '';
    manualFlowName = '';

    connectedCallback() {
        this.loadSetup();
    }

    async loadSetup() {
        this.loading = true;
        this.errorMessage = undefined;
        try {
            const result = await getSetupState();
            this.state = {
                ...result,
                agents: (result.agents || []).map(item => ({ ...item })),
                flows: (result.flows || []).map(item => ({ ...item })),
                warnings: result.warnings || []
            };
            this.activeStep = this.state.connected ? 2 : 1;
        } catch (error) {
            this.state = undefined;
            this.errorMessage =
                error?.body?.message || 'Salesforce did not return the organization details.';
        } finally {
            this.loading = false;
        }
    }

    get ready() {
        return Boolean(this.state);
    }

    get connected() {
        return Boolean(this.state?.connected);
    }

    get environmentLabel() {
        return this.state?.sandbox ? 'Sandbox' : 'Production';
    }

    get showConnect() {
        return this.activeStep === 1;
    }

    get showEntryPoints() {
        return this.activeStep === 2 && this.connected;
    }

    get showActivation() {
        return this.activeStep === 3 && this.connected;
    }

    get stepOneClass() {
        return this.activeStep === 1 ? 'step active' : 'step complete';
    }

    get stepTwoClass() {
        return this.activeStep === 2 ? 'step active' : 'step';
    }

    get stepThreeClass() {
        return this.activeStep === 3 ? 'step active' : 'step';
    }

    get agents() {
        return this.state?.agents || [];
    }

    get flows() {
        return this.state?.flows || [];
    }

    get warnings() {
        return this.state?.warnings || [];
    }

    get hasAgents() {
        return this.agents.length > 0;
    }

    get hasFlows() {
        return this.flows.length > 0;
    }

    get selectedEntries() {
        return [...this.agents, ...this.flows].filter(item => item.selected);
    }

    get selectedAgents() {
        return this.agents.filter(item => item.selected);
    }

    get selectedFlows() {
        return this.flows.filter(item => item.selected);
    }

    get hasSelectedAgents() {
        return this.selectedAgents.length > 0;
    }

    get hasSelectedFlows() {
        return this.selectedFlows.length > 0;
    }

    get selectedCount() {
        return this.selectedEntries.length;
    }

    get selectionSummary() {
        return `${this.selectedCount} selected`;
    }

    get continueDisabled() {
        return this.selectedCount === 0 || this.saving;
    }

    connect() {
        if (this.state?.connectUrl) {
            window.open(this.state.connectUrl, '_blank', 'noopener,noreferrer');
        }
    }

    refreshConnection() {
        this.loadSetup();
    }

    openStep(event) {
        const requestedStep = Number(event.currentTarget.dataset.step);
        if (requestedStep === 1 || (requestedStep > 1 && this.connected)) {
            this.activeStep = requestedStep;
        }
    }

    handleSelection(event) {
        const kind = event.target.dataset.kind;
        const id = event.target.dataset.id;
        const propertyName = kind === 'agent' ? 'agents' : 'flows';
        this.state = {
            ...this.state,
            [propertyName]: (this.state[propertyName] || []).map(item =>
                item.id === id ? { ...item, selected: event.target.checked } : item
            )
        };
    }

    handleManualName(event) {
        if (event.target.dataset.kind === 'agent') {
            this.manualAgentName = event.target.value;
        } else {
            this.manualFlowName = event.target.value;
        }
    }

    addManualEntry(event) {
        const kind = event.currentTarget.dataset.kind;
        const value = (
            kind === 'agent' ? this.manualAgentName : this.manualFlowName
        ).trim();
        if (!value) {
            return;
        }
        const propertyName = kind === 'agent' ? 'agents' : 'flows';
        const id = `manual:${kind}:${value.toLowerCase().replace(/\s+/g, '-')}`;
        const existing = this.state[propertyName] || [];
        if (!existing.some(item => item.id === id)) {
            this.state = {
                ...this.state,
                [propertyName]: [
                    ...existing,
                    {
                        id,
                        name: value,
                        label: value,
                        kind,
                        status: 'manual',
                        selected: true
                    }
                ]
            };
        }
        if (kind === 'agent') {
            this.manualAgentName = '';
        } else {
            this.manualFlowName = '';
        }
    }

    async continueToActivation() {
        this.saving = true;
        this.errorMessage = undefined;
        const entries = this.selectedEntries.map(item => ({
            kind: item.kind,
            id: item.id || '',
            name: item.name || item.label,
            label: item.label || item.name
        }));
        try {
            await saveSelections({
                connectionId: this.state.connectionId,
                entriesJson: JSON.stringify(entries)
            });
            this.activeStep = 3;
        } catch (error) {
            this.errorMessage =
                error?.body?.message || 'CostPilot could not save the selected agents and Flows.';
        } finally {
            this.saving = false;
        }
    }

    backToEntryPoints() {
        this.activeStep = 2;
    }
}
