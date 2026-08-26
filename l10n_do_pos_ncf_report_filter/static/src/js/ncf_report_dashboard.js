/** @odoo-module **/

import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Layout } from "@web/search/layout";
import { RecordSelector } from "@web/core/record_selectors/record_selector";
import { MultiRecordSelector } from "@web/core/record_selectors/multi_record_selector";

function today() {
    return new Date().toISOString().slice(0, 10);
}

export class NcfReportDashboard extends Component {
    static template = "l10n_do_pos_ncf_report_filter.NcfReportDashboard";
    static components = { Layout, RecordSelector, MultiRecordSelector };
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.actionService = useService("action");

        this.state = useState({
            dateFrom: today(),
            dateTo: today(),
            configId: false,
            ncfSequenceIds: [],
            includeNoNcf: false,
            activeTab: "ncf",
            data: null,
            loading: true,
            expanded: {},
        });

        onWillStart(() => this.loadData());
    }

    get display() {
        return { controlPanel: {} };
    }

    async loadData() {
        this.state.loading = true;
        this.state.data = await this.orm.call(
            "pos.reporte.ncf.wizard",
            "get_dashboard_data",
            [],
            {
                date_from: this.state.dateFrom,
                date_to: this.state.dateTo,
                config_id: this.state.configId || false,
                ncf_sequence_ids: this.state.ncfSequenceIds,
                include_no_ncf: this.state.includeNoNcf,
            }
        );
        this.state.loading = false;
    }

    onDateFromChange(ev) {
        this.state.dateFrom = ev.target.value;
        this.loadData();
    }

    onDateToChange(ev) {
        this.state.dateTo = ev.target.value;
        this.loadData();
    }

    onConfigChange(resId) {
        this.state.configId = resId;
        this.loadData();
    }

    onNcfSequencesChange(resIds) {
        this.state.ncfSequenceIds = resIds;
        this.loadData();
    }

    toggleIncludeNoNcf() {
        this.state.includeNoNcf = !this.state.includeNoNcf;
        this.loadData();
    }

    setTab(tab) {
        this.state.activeTab = tab;
    }

    toggleGroup(key) {
        this.state.expanded[key] = !this.state.expanded[key];
    }

    fmt(value) {
        const n = Number(value || 0);
        return "RD$ " + n.toLocaleString("es-DO", {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
        });
    }

    async printPdf() {
        const action = await this.orm.call(
            "pos.reporte.ncf.wizard",
            "get_report_action_pdf",
            [],
            {
                date_from: this.state.dateFrom,
                date_to: this.state.dateTo,
                config_id: this.state.configId || false,
                ncf_sequence_ids: this.state.ncfSequenceIds,
                include_no_ncf: this.state.includeNoNcf,
            }
        );
        this.actionService.doAction(action);
    }
}

registry.category("actions").add("l10n_do_pos_ncf_report_dashboard", NcfReportDashboard);
