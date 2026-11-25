// The client action behind the "Inbox" menu: the conversation list on the left, the open
// conversation on the right. Pure OWL over the inbox store; nothing from Discuss.
import { Component, onMounted, onWillStart, onWillUnmount, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

import { ConversationList } from "./conversation_list";
import { Thread } from "./thread";

export class InboxAction extends Component {
    static template = "rcstudio_whatsapp_coexistence.Inbox";
    static components = { ConversationList, Thread };
    static props = { "*": true };

    setup() {
        this.store = useState(useService("rcs_whatsapp.inbox"));
        onWillStart(async () => {
            await this.store.loadList();
            const action = this.props.action || {};
            const requested = action.params?.conversation_id || action.context?.conversation_id;
            if (requested) {
                await this.store.open(requested);
            } else if (this.store.activeId) {
                await this.store.refreshActive();
            }
        });
        onMounted(() => {
            this.store.inboxMounted = true;
            this.store.requestNotificationPermission();
        });
        onWillUnmount(() => {
            this.store.inboxMounted = false;
        });
    }
}

registry.category("actions").add("rcs_whatsapp.inbox", InboxAction);
