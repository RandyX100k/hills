// A WhatsApp icon in the systray with the number of unread conversations. It reads the
// shared inbox store, so it stays in step with the bus without a request of its own.
import { Component, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

export class WhatsAppSystray extends Component {
    static template = "rcstudio_whatsapp_coexistence.Systray";
    static props = {};

    setup() {
        // useState: the icon must repaint when isOperator and the unread count resolve
        this.store = useState(useService("rcs_whatsapp.inbox"));
    }

    onClick() {
        this.store.openInbox();
    }
}

registry.category("systray").add(
    "rcstudio_whatsapp_coexistence.systray",
    { Component: WhatsAppSystray },
    { sequence: 26 }
);
