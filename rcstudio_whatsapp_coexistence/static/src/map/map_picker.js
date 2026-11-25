// A point picker on an OpenStreetMap map (Leaflet, loaded on demand from its CDN):
// click or drag the marker, or search an address, and the coordinates come back through
// onChange. Without internet the map is unavailable but the coordinates stay editable.
import { Component, onMounted, onWillUnmount, useRef, useState } from "@odoo/owl";
import { loadCSS, loadJS } from "@web/core/assets";
import { _t } from "@web/core/l10n/translation";

const LEAFLET_JS = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js";
const LEAFLET_CSS = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css";
const NOMINATIM = "https://nominatim.openstreetmap.org/search";

export class MapPicker extends Component {
    static template = "rcstudio_whatsapp_coexistence.MapPicker";
    static props = {
        latitude: { type: Number, optional: true },
        longitude: { type: Number, optional: true },
        onChange: Function, // ({ latitude, longitude, address? }) => void
        height: { type: String, optional: true },
    };
    static defaultProps = { height: "280px" };

    setup() {
        this.mapRef = useRef("map");
        this.state = useState({ query: "", searching: false, error: "", ready: false });
        onMounted(() => this.initMap());
        onWillUnmount(() => this.map?.remove());
    }

    get hasPoint() {
        return Number.isFinite(this.props.latitude) && Number.isFinite(this.props.longitude) &&
            (this.props.latitude !== 0 || this.props.longitude !== 0);
    }

    async initMap() {
        try {
            await Promise.all([loadJS(LEAFLET_JS), loadCSS(LEAFLET_CSS)]);
        } catch {
            this.state.error = _t("The map could not be loaded (no internet access?). You can still type the coordinates.");
            return;
        }
        const L = window.L;
        const start = this.hasPoint ? [this.props.latitude, this.props.longitude] : [4.711, -74.0721];
        this.map = L.map(this.mapRef.el, { zoomControl: true }).setView(start, this.hasPoint ? 16 : 5);
        L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
            maxZoom: 19,
            attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
        }).addTo(this.map);
        this.marker = L.marker(start, { draggable: true }).addTo(this.map);
        this.marker.on("dragend", () => this.emit(this.marker.getLatLng()));
        this.map.on("click", (ev) => {
            this.marker.setLatLng(ev.latlng);
            this.emit(ev.latlng);
        });
        this.state.ready = true;
        // Leaflet needs a second layout pass inside dialogs and notebooks
        setTimeout(() => this.map?.invalidateSize(), 250);
    }

    emit(latlng, extra = {}) {
        this.props.onChange({
            latitude: Math.round(latlng.lat * 1e7) / 1e7,
            longitude: Math.round(latlng.lng * 1e7) / 1e7,
            ...extra,
        });
    }

    onSearchKeydown(ev) {
        if (ev.key === "Enter") {
            ev.preventDefault();
            this.search();
        }
    }

    async search() {
        const query = this.state.query.trim();
        if (!query || this.state.searching) {
            return;
        }
        this.state.searching = true;
        this.state.error = "";
        try {
            const url = `${NOMINATIM}?format=json&limit=1&q=${encodeURIComponent(query)}`;
            const response = await fetch(url, { headers: { Accept: "application/json" } });
            const results = await response.json();
            if (!results.length) {
                this.state.error = _t("No place found for that address.");
                return;
            }
            const hit = results[0];
            const latlng = { lat: parseFloat(hit.lat), lng: parseFloat(hit.lon) };
            if (this.map) {
                this.marker.setLatLng(latlng);
                this.map.setView(latlng, 16);
            }
            this.emit(latlng, { address: hit.display_name });
        } catch {
            this.state.error = _t("The address search is not reachable right now.");
        } finally {
            this.state.searching = false;
        }
    }

    useMyPosition() {
        if (!navigator.geolocation) {
            return;
        }
        navigator.geolocation.getCurrentPosition((pos) => {
            const latlng = { lat: pos.coords.latitude, lng: pos.coords.longitude };
            if (this.map) {
                this.marker.setLatLng(latlng);
                this.map.setView(latlng, 16);
            }
            this.emit(latlng);
        });
    }
}
