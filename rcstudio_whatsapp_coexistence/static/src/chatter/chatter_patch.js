// A "WhatsApp" button in the chatter of the records that carry a phone (contacts,
// quotations, invoices, deliveries...), next to "Send message" and "Log note". It opens
// the Send by WhatsApp wizard for the record being viewed.
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { Chatter } from "@mail/chatter/web_portal/chatter";

patch(Chatter.prototype, {
    setup() {
        super.setup(...arguments);
        this.rcsWaStore = useService("rcs_whatsapp.inbox");
        this.rcsWaAction = useService("action");
    },

    get rcsWaVisible() {
        const store = this.rcsWaStore;
        return (
            store.isOperator &&
            !!this.props.threadId &&
            store.chatterModels.includes(this.props.threadModel)
        );
    },

    onClickRcsWa() {
        this.rcsWaAction.doAction("rcstudio_whatsapp_coexistence.action_send", {
            additionalContext: {
                active_model: this.props.threadModel,
                active_id: this.props.threadId,
                active_ids: [this.props.threadId],
            },
        });
    },
});
