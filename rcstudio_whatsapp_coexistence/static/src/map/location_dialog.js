// "Send a location" from the inbox composer: pick the point, name it, send.
import { Component, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { _t } from "@web/core/l10n/translation";

import { MapPicker } from "./map_picker";

export class LocationDialog extends Component {
    static template = "rcstudio_whatsapp_coexistence.LocationDialog";
    static components = { Dialog, MapPicker };
    static props = {
        close: Function,
        onSend: Function, // ({ latitude, longitude, name, address }) => Promise
    };

    setup() {
        this.state = useState({ latitude: 0, longitude: 0, name: "", address: "", sending: false });
        this.title = _t("Send a location");
    }

    get canSend() {
        return (this.state.latitude || this.state.longitude) && !this.state.sending;
    }

    onPick({ latitude, longitude, address }) {
        this.state.latitude = latitude;
        this.state.longitude = longitude;
        if (address && !this.state.address) {
            this.state.address = address;
        }
    }

    async send() {
        this.state.sending = true;
        try {
            await this.props.onSend({
                latitude: this.state.latitude,
                longitude: this.state.longitude,
                name: this.state.name,
                address: this.state.address,
            });
            this.props.close();
        } finally {
            this.state.sending = false;
        }
    }
}
