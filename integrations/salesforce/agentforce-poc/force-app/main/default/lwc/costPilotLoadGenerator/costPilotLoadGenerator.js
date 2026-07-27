import { LightningElement } from 'lwc';
import getSourceObjects from '@salesforce/apex/CostPilotLoadGeneratorController.getSourceObjects';
import getRecords from '@salesforce/apex/CostPilotLoadGeneratorController.getRecords';
import getUsers from '@salesforce/apex/CostPilotLoadGeneratorController.getUsers';
import startRun from '@salesforce/apex/CostPilotLoadGeneratorController.startRun';
import getRunStatus from '@salesforce/apex/CostPilotLoadGeneratorController.getRunStatus';
import stopRun from '@salesforce/apex/CostPilotLoadGeneratorController.stopRun';

const TERMINAL_STATUSES = new Set([
    'Completed',
    'Completed with errors',
    'Failed',
    'Stopped'
]);

export default class CostPilotLoadGenerator extends LightningElement {
    sourceObjectOptions = [];
    recordOptions = [];
    userOptions = [];
    sourceObject = 'Account';
    recordId;
    attributionUserId;
    agentName = 'CostPilot Salesforce Test Agent';
    department = 'Sales';
    mode = 'simulation';
    routingMix = 'balanced';
    callCount = '5';
    runStatus;
    errorMessage;
    loading = true;
    pollingTimer;

    modeOptions = [
        { label: 'Simulation — no paid LLM response', value: 'simulation' },
        { label: 'Live — call the selected LLM', value: 'live' }
    ];

    routingMixOptions = [
        { label: 'Balanced — even tier coverage', value: 'balanced' },
        { label: 'Natural — CostPilot decides from content', value: 'natural' },
        { label: 'Economy — mostly Scout and Analyst', value: 'economy' },
        { label: 'Complex — Advisor and Strategist', value: 'complex' }
    ];

    connectedCallback() {
        this.initialize();
    }

    disconnectedCallback() {
        this.stopPolling();
    }

    async initialize() {
        this.loading = true;
        try {
            const [objects, users] = await Promise.all([getSourceObjects(), getUsers()]);
            this.sourceObjectOptions = this.toOptions(objects);
            this.userOptions = this.toOptions(users);
            if (this.userOptions.length) {
                this.attributionUserId = this.userOptions[0].value;
            }
            await this.loadRecords();
        } catch (error) {
            this.showError(error);
        } finally {
            this.loading = false;
        }
    }

    async loadRecords() {
        this.recordId = null;
        this.recordOptions = [];
        try {
            const rows = await getRecords({ objectApiName: this.sourceObject });
            this.recordOptions = this.toOptions(rows);
            if (this.recordOptions.length) {
                this.recordId = this.recordOptions[0].value;
            }
        } catch (error) {
            this.showError(error);
        }
    }

    toOptions(rows) {
        return (rows || []).map((row) => ({
            label: row.description ? `${row.label} — ${row.description}` : row.label,
            value: row.value
        }));
    }

    async handleSourceObjectChange(event) {
        this.sourceObject = event.detail.value;
        await this.loadRecords();
    }

    handleRecordChange(event) {
        this.recordId = event.detail.value;
    }

    handleUserChange(event) {
        this.attributionUserId = event.detail.value;
    }

    handleAgentNameChange(event) {
        this.agentName = event.detail.value;
    }

    handleDepartmentChange(event) {
        this.department = event.detail.value;
    }

    handleModeChange(event) {
        this.mode = event.detail.value;
        if (this.mode === 'live' && Number(this.callCount) > 3) {
            this.callCount = '3';
        }
    }

    handleRoutingMixChange(event) {
        this.routingMix = event.detail.value;
    }

    handleCallCountChange(event) {
        this.callCount = event.detail.value;
    }

    async handleStart() {
        this.errorMessage = null;
        this.loading = true;
        try {
            this.runStatus = await startRun({
                recordId: this.recordId,
                attributionUserId: this.attributionUserId,
                agentName: this.agentName,
                callCount: Number(this.callCount),
                mode: this.mode,
                routingMix: this.routingMix,
                department: this.department
            });
            this.startPolling();
        } catch (error) {
            this.showError(error);
        } finally {
            this.loading = false;
        }
    }

    async handleStop() {
        try {
            this.runStatus = await stopRun({ runId: this.runStatus.runId });
        } catch (error) {
            this.showError(error);
        }
    }

    startPolling() {
        this.stopPolling();
        this.pollingTimer = window.setInterval(() => this.refreshRun(), 2000);
        this.refreshRun();
    }

    stopPolling() {
        if (this.pollingTimer) {
            window.clearInterval(this.pollingTimer);
            this.pollingTimer = null;
        }
    }

    async refreshRun() {
        if (!this.runStatus?.runId) {
            return;
        }
        try {
            this.runStatus = await getRunStatus({ runId: this.runStatus.runId });
            if (TERMINAL_STATUSES.has(this.runStatus.status)) {
                this.stopPolling();
            }
        } catch (error) {
            this.stopPolling();
            this.showError(error);
        }
    }

    showError(error) {
        this.errorMessage =
            error?.body?.message || error?.message || 'Salesforce could not complete the request.';
    }

    get callCountOptions() {
        const values = this.mode === 'live' ? [1, 2, 3] : [1, 5, 10, 15, 25];
        return values.map((value) => ({ label: String(value), value: String(value) }));
    }

    get disableRecordSelection() {
        return this.isRunning || !this.recordOptions.length;
    }

    get disableStart() {
        return (
            this.loading ||
            this.isRunning ||
            !this.recordId ||
            !this.attributionUserId ||
            !this.agentName?.trim()
        );
    }

    get isRunning() {
        return Boolean(this.runStatus) && !TERMINAL_STATUSES.has(this.runStatus.status);
    }

    get hasRun() {
        return Boolean(this.runStatus);
    }

    get progressPercent() {
        if (!this.runStatus?.totalCalls) {
            return 0;
        }
        const processed = this.runStatus.completedCalls + this.runStatus.failedCalls;
        return Math.round((processed / this.runStatus.totalCalls) * 100);
    }

    get remainingCalls() {
        if (!this.runStatus) {
            return 0;
        }
        return Math.max(
            this.runStatus.totalCalls -
                this.runStatus.completedCalls -
                this.runStatus.failedCalls,
            0
        );
    }

    get modeNoticeClass() {
        return this.mode === 'live' ? 'mode-notice live' : 'mode-notice simulation';
    }

    get modeNoticeTitle() {
        return this.mode === 'live' ? 'Live mode' : 'Simulation mode';
    }

    get modeNoticeText() {
        return this.mode === 'live'
            ? 'Up to 3 calls will pass through CostPilot to a live LLM and may incur provider cost.'
            : 'Up to 25 calls exercise attribution, governance, pruning, routing, budgets, and reporting without a paid LLM response.';
    }
}
