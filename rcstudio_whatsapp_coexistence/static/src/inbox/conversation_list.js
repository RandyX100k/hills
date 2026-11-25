// The left pane: number selector, search, filter pills and the conversation list with
// load-more on scroll. All the data comes from the store; this component only renders it.
import { Component, useRef, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";
import { useDebounced } from "@web/core/utils/timing";

import { fullStamp, listStamp } from "./inbox_dates";

export class ConversationList extends Component {
    static template = "rcstudio_whatsapp_coexistence.ConversationList";
    static props = {};

    setup() {
        this.store = useState(useService("rcs_whatsapp.inbox"));
        this.state = useState({ search: this.store.search });
        this.listRef = useRef("list");
        this.applySearch = useDebounced(() => this.store.setSearch(this.state.search.trim()), 300);
    }

    get filters() {
        return [
            { id: "all", label: _t("All") },
            { id: "unread", label: _t("Unread") },
            { id: "mine", label: _t("Mine") },
            { id: "closed", label: _t("Closed") },
        ];
    }

    get emptyText() {
        if (this.state.search.trim()) {
            return _t('No conversation matches "%s".', this.state.search.trim());
        }
        switch (this.store.filter) {
            case "unread":
                return _t("No unread conversations.");
            case "mine":
                return _t("No conversation is assigned to you.");
            case "closed":
                return _t("No closed conversations.");
            default:
                return _t("No conversations yet.");
        }
    }

    stamp(conversation) {
        return listStamp(conversation.last_activity);
    }

    initials(conversation) {
        const name = (conversation.name || "").trim();
        return name ? name[0].toUpperCase() : "#";
    }

    numberInitial(conversation) {
        const name = (conversation.number_name || "").trim();
        return name ? name[0].toUpperCase() : "";
    }

    windowTooltip(conversation) {
        if (conversation.window_open) {
            return _t("Window open until %s", fullStamp(conversation.window_expires_at));
        }
        return _t("24-hour window closed: only templates can be sent");
    }

    onSearchInput() {
        this.applySearch();
    }

    clearSearch() {
        this.state.search = "";
        this.store.setSearch("");
    }

    onChangeNumber(ev) {
        const value = ev.target.value;
        this.store.setNumber(value ? Number(value) : false);
    }

    onScroll() {
        const el = this.listRef.el;
        if (el && el.scrollTop + el.clientHeight >= el.scrollHeight - 120) {
            this.store.loadMoreList();
        }
    }

    onClickConversation(conversation) {
        if (conversation.id !== this.store.activeId) {
            this.store.open(conversation.id);
        }
    }
}
