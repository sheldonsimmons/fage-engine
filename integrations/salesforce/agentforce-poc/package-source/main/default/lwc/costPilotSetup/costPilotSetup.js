import { LightningElement } from 'lwc';
import getSetupState from '@salesforce/apex/CostPilotSetupController.getSetupState';
import saveSelections from '@salesforce/apex/CostPilotSetupController.saveSelections';
import discoverRelationships from '@salesforce/apex/CostPilotSetupController.discoverRelationships';
import approveRelationships from '@salesforce/apex/CostPilotSetupController.approveRelationships';
import verifyConnection from '@salesforce/apex/CostPilotSetupController.verifyConnection';
import activateConnection from '@salesforce/apex/CostPilotSetupController.activateConnection';

export default class CostPilotSetup extends LightningElement {
    state;
    errorMessage;
    loading = true;
    working = false;
    activeStep = 1;
    manualAgentName = '';
    manualFlowName = '';
    parentObject = 'Account';
    relationshipChildren = [];

    relationshipBehaviorOptions = [
        { label: 'Track and roll up to parent', value: 'track_and_rollup' },
        { label: 'Roll up to parent only', value: 'rollup_only' },
        { label: 'Track as separate work', value: 'separate' },
        { label: 'Ignore', value: 'ignore' }
    ];

    connectedCallback() {
        this.loadSetup();
    }

    async loadSetup() {
        this.loading = true;
        this.errorMessage = undefined;
        try {
            this.applyState(await getSetupState());
            this.activeStep = this.initialStep;
        } catch (error) {
            this.state = undefined;
            this.errorMessage = this.errorText(error, 'Salesforce did not return the setup details.');
        } finally {
            this.loading = false;
        }
    }

    applyState(result) {
        this.state = {
            ...result,
            agents: (result.agents || []).map(item => ({ ...item })),
            flows: (result.flows || []).map(item => ({ ...item })),
            warnings: result.warnings || []
        };
        this.parentObject = result.parentObject || this.parentObject || 'Account';
        this.relationshipChildren = (result.relationshipChildren || []).map(item => ({
            ...item,
            key: this.relationshipKey(item),
            selected: item.selected !== false,
            behavior: item.behavior || 'track_and_rollup'
        }));
    }

    get initialStep() {
        if (!this.state?.connected) return 1;
        if (this.state.activated || this.state.verificationPassed) return 5;
        if (this.state.relationshipsApproved) return 4;
        if (this.state.selectionsSaved) return 3;
        return 2;
    }

    get ready() { return Boolean(this.state); }
    get connected() { return Boolean(this.state?.connected); }
    get environmentLabel() { return this.state?.sandbox ? 'Sandbox' : 'Production'; }
    get showConnect() { return this.activeStep === 1; }
    get showEntryPoints() { return this.activeStep === 2 && this.connected; }
    get showRelationships() { return this.activeStep === 3 && this.connected; }
    get showVerification() { return this.activeStep === 4 && this.connected; }
    get showGoLive() { return this.activeStep === 5 && this.connected; }
    get agents() { return this.state?.agents || []; }
    get flows() { return this.state?.flows || []; }
    get warnings() { return this.state?.warnings || []; }
    get hasAgents() { return this.agents.length > 0; }
    get hasFlows() { return this.flows.length > 0; }
    get hasRelationships() { return this.relationshipChildren.length > 0; }
    get selectedEntries() { return [...this.agents, ...this.flows].filter(item => item.selected); }
    get selectedAgents() { return this.agents.filter(item => item.selected); }
    get selectedFlows() { return this.flows.filter(item => item.selected); }
    get hasSelectedAgents() { return this.selectedAgents.length > 0; }
    get hasSelectedFlows() { return this.selectedFlows.length > 0; }
    get selectedCount() { return this.selectedEntries.length; }
    get selectionSummary() { return `${this.selectedCount} selected`; }
    get continueDisabled() { return this.selectedCount === 0 || this.working; }
    get relationshipDisabled() {
        return !this.parentObject || !this.relationshipChildren.some(item => item.selected) || this.working;
    }
    get relationshipsStepDisabled() { return !this.state?.selectionsSaved || this.working; }
    get verificationStepDisabled() { return !this.state?.relationshipsApproved || this.working; }
    get goLiveStepDisabled() { return !this.verificationPassed || this.working; }
    get verificationPassed() { return Boolean(this.state?.verificationPassed); }
    get activated() { return Boolean(this.state?.activated); }
    get actionLabel() { return this.working ? 'Working…' : 'Run verification'; }

    stepClass(number, completed) {
        if (this.activeStep === number) return 'step active';
        return completed ? 'step complete' : 'step';
    }
    get stepOneClass() { return this.stepClass(1, this.connected); }
    get stepTwoClass() { return this.stepClass(2, this.state?.selectionsSaved); }
    get stepThreeClass() { return this.stepClass(3, this.state?.relationshipsApproved); }
    get stepFourClass() { return this.stepClass(4, this.verificationPassed); }
    get stepFiveClass() { return this.stepClass(5, this.activated); }

    errorText(error, fallback) {
        return error?.body?.message || error?.message || fallback;
    }

    connect() {
        if (this.state?.connectUrl) window.open(this.state.connectUrl, '_blank', 'noopener,noreferrer');
    }
    refreshConnection() { this.loadSetup(); }

    openStep(event) {
        const requested = Number(event.currentTarget.dataset.step);
        const allowed = requested === 1 ||
            (requested === 2 && this.connected) ||
            (requested === 3 && this.state?.selectionsSaved) ||
            (requested === 4 && this.state?.relationshipsApproved) ||
            (requested === 5 && this.verificationPassed);
        if (allowed) this.activeStep = requested;
    }

    handleSelection(event) {
        const propertyName = event.target.dataset.kind === 'agent' ? 'agents' : 'flows';
        const id = event.target.dataset.id;
        this.state = {
            ...this.state,
            [propertyName]: this.state[propertyName].map(item =>
                item.id === id ? { ...item, selected: event.target.checked } : item)
        };
    }

    handleManualName(event) {
        if (event.target.dataset.kind === 'agent') this.manualAgentName = event.target.value;
        else this.manualFlowName = event.target.value;
    }

    addManualEntry(event) {
        const kind = event.currentTarget.dataset.kind;
        const value = (kind === 'agent' ? this.manualAgentName : this.manualFlowName).trim();
        if (!value) return;
        const propertyName = kind === 'agent' ? 'agents' : 'flows';
        const id = `manual:${kind}:${value.toLowerCase().replace(/\s+/g, '-')}`;
        if (!this.state[propertyName].some(item => item.id === id)) {
            this.state = {
                ...this.state,
                [propertyName]: [...this.state[propertyName], {
                    id, name: value, label: value, kind, status: 'manual', selected: true
                }]
            };
        }
        if (kind === 'agent') this.manualAgentName = '';
        else this.manualFlowName = '';
    }

    async saveEntryPoints() {
        this.working = true;
        this.errorMessage = undefined;
        try {
            await saveSelections({
                connectionId: this.state.connectionId,
                entriesJson: JSON.stringify(this.selectedEntries.map(item => ({
                    kind: item.kind,
                    id: item.id || '',
                    name: item.name || item.label,
                    label: item.label || item.name
                })))
            });
            this.state = { ...this.state, selectionsSaved: true };
            this.activeStep = 3;
        } catch (error) {
            this.errorMessage = this.errorText(error, 'CostPilot could not save the selected agents and Flows.');
        } finally {
            this.working = false;
        }
    }

    handleParentObject(event) { this.parentObject = event.target.value.trim(); }
    handleRelationshipSelection(event) {
        const key = event.target.dataset.key;
        this.relationshipChildren = this.relationshipChildren.map(item =>
            this.relationshipKey(item) === key ? { ...item, selected: event.target.checked } : item);
    }
    handleRelationshipBehavior(event) {
        const key = event.target.dataset.key;
        this.relationshipChildren = this.relationshipChildren.map(item =>
            this.relationshipKey(item) === key ? { ...item, behavior: event.detail.value } : item);
    }
    relationshipKey(item) { return `${item.objectName}:${item.parentField}`; }

    async findRelationships() {
        this.working = true;
        this.errorMessage = undefined;
        try {
            const discovered = await discoverRelationships({ parentObject: this.parentObject });
            const existing = new Map(this.relationshipChildren.map(item => [this.relationshipKey(item), item]));
            (discovered || []).forEach(item => {
                const key = this.relationshipKey(item);
                if (!existing.has(key)) existing.set(key, {
                    ...item,
                    key,
                    selected: true,
                    behavior: 'track_and_rollup'
                });
            });
            this.relationshipChildren = [...existing.values()];
        } catch (error) {
            this.errorMessage = this.errorText(error, 'Salesforce could not discover related objects.');
        } finally {
            this.working = false;
        }
    }

    async approveRelationshipMap() {
        this.working = true;
        this.errorMessage = undefined;
        try {
            const result = await approveRelationships({
                connectionId: this.state.connectionId,
                parentObject: this.parentObject,
                childrenJson: JSON.stringify(this.relationshipChildren)
            });
            this.applyState(result);
            this.activeStep = 4;
        } catch (error) {
            this.errorMessage = this.errorText(error, 'CostPilot could not approve the relationship map.');
        } finally {
            this.working = false;
        }
    }

    async runVerification() {
        this.working = true;
        this.errorMessage = undefined;
        try {
            const result = await verifyConnection({ connectionId: this.state.connectionId });
            this.applyState(result);
            if (this.verificationPassed) this.activeStep = 5;
        } catch (error) {
            this.errorMessage = this.errorText(error, 'CostPilot could not verify a live governed request.');
        } finally {
            this.working = false;
        }
    }

    async goLive() {
        this.working = true;
        this.errorMessage = undefined;
        try {
            this.applyState(await activateConnection({ connectionId: this.state.connectionId }));
        } catch (error) {
            this.errorMessage = this.errorText(error, 'CostPilot could not activate this connection.');
        } finally {
            this.working = false;
        }
    }

    manageRelationships() { this.activeStep = 3; }
    reviewEntryPoints() { this.activeStep = 2; }
}
