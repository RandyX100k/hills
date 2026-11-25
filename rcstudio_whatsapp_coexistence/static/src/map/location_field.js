// Form widget for a latitude field that also drives the longitude (and optionally the
// name and address) fields of the same record through the map picker.
import { Component } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { _t } from "@web/core/l10n/translation";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

import { MapPicker } from "./map_picker";

export class LocationField extends Component {
    static template = "rcstudio_whatsapp_coexistence.LocationField";
    static components = { MapPicker };
    static props = {
        ...standardFieldProps,
        longitudeField: { type: String },
        nameField: { type: String, optional: true },
        addressField: { type: String, optional: true },
    };

    get latitude() {
        return this.props.record.data[this.props.name] || 0;
    }

    get longitude() {
        return this.props.record.data[this.props.longitudeField] || 0;
    }

    onChange({ latitude, longitude, address }) {
        const values = { [this.props.name]: latitude, [this.props.longitudeField]: longitude };
        if (address && this.props.addressField && !this.props.record.data[this.props.addressField]) {
            values[this.props.addressField] = address;
        }
        this.props.record.update(values);
    }

    onInput(field, ev) {
        const value = parseFloat(ev.target.value);
        if (Number.isFinite(value)) {
            this.props.record.update({ [field]: value });
        }
    }
}

export const locationField = {
    component: LocationField,
    displayName: _t("Location on a map"),
    supportedTypes: ["float"],
    extractProps: ({ options }) => ({
        longitudeField: options.longitude_field,
        nameField: options.name_field,
        addressField: options.address_field,
    }),
};

registry.category("fields").add("rcs_whatsapp_location", locationField);
