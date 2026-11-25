// A WhatsApp shortcut next to every phone field: opens the "Send by WhatsApp" wizard for
// the record being viewed, with the phone already filled in.
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { user } from "@web/core/user";
import { PhoneField } from "@web/views/fields/phone/phone_field";
import { onWillStart } from "@odoo/owl";

patch(PhoneField.prototype, {
    setup() {
        super.setup(...arguments);
        this.rcsWaAction = useService("action");
        this.rcsWaEnabled = false;
        onWillStart(async () => {
            this.rcsWaEnabled = await user.hasGroup("rcstudio_whatsapp_coexistence.group_user");
        });
    },

    get rcsWaVisible() {
        const record = this.props.record;
        return this.rcsWaEnabled && !!record.resId && !!record.data[this.props.name];
    },

    onClickRcsWa(ev) {
        ev.preventDefault();
        ev.stopPropagation();
        const record = this.props.record;
        this.rcsWaAction.doAction("rcstudio_whatsapp_coexistence.action_send", {
            additionalContext: {
                active_model: record.resModel,
                active_id: record.resId,
                active_ids: [record.resId],
                default_phone: record.data[this.props.name],
            },
        });
    },
});
