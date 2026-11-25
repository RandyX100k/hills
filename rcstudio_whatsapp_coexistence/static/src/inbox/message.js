// One message bubble. Renders every kind the server can send back: text, media (inline
// image, audio and video players, document download), location, quoted reply, template
// label, reactions folded in by the thread, and the delivery ticks with the failure reason.
import { Component } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";

import { fullStamp, timeLabel, toDateTime } from "./inbox_dates";

const MEDIA_KINDS = ["image", "video", "audio", "document", "sticker"];

export class Message extends Component {
    static template = "rcstudio_whatsapp_coexistence.Message";
    static props = {
        message: Object,
        reactions: { type: Array, optional: true },
        onReply: Function,
        onImageLoad: { type: Function, optional: true },
    };
    static defaultProps = { reactions: [] };

    get message() {
        return this.props.message;
    }

    get isOut() {
        return this.message.direction === "out";
    }

    get isMedia() {
        return MEDIA_KINDS.includes(this.message.kind);
    }

    get bodyText() {
        return this.isMedia ? this.message.caption : this.message.body;
    }

    get time() {
        return timeLabel(toDateTime(this.message.date));
    }

    get fullDate() {
        return fullStamp(this.message.date);
    }

    /** Players read the raw content; the download link keeps the attachment disposition. */
    get mediaUrl() {
        const attachment = this.message.attachment;
        return attachment ? `/web/content/${attachment.id}` : "";
    }

    get mapUrl() {
        const { latitude, longitude } = this.message.location;
        return `https://www.google.com/maps?q=${latitude},${longitude}`;
    }

    get locationSubtitle() {
        const { address, latitude, longitude } = this.message.location;
        return address || `${latitude}, ${longitude}`;
    }

    get replyLabel() {
        return this.message.reply_to.direction === "out" ? _t("You") : _t("Customer");
    }

    get documentIcon() {
        const mimetype = this.message.attachment?.mimetype || "";
        if (mimetype.includes("pdf")) {
            return "fa-file-pdf-o";
        }
        if (mimetype.includes("word") || mimetype.includes("opendocument.text")) {
            return "fa-file-word-o";
        }
        if (mimetype.includes("sheet") || mimetype.includes("excel") || mimetype.includes("csv")) {
            return "fa-file-excel-o";
        }
        if (mimetype.includes("presentation") || mimetype.includes("powerpoint")) {
            return "fa-file-powerpoint-o";
        }
        if (mimetype.includes("zip") || mimetype.includes("compressed")) {
            return "fa-file-archive-o";
        }
        if (mimetype.startsWith("text/")) {
            return "fa-file-text-o";
        }
        return "fa-file-o";
    }

    /** The tick shown after the time on outbound messages. */
    get status() {
        switch (this.message.status) {
            case "queued":
                return { icon: "fa-clock-o", tooltip: _t("Queued") };
            case "sending":
                return { icon: "fa-clock-o", tooltip: _t("Sending") };
            case "sent":
                return { ticks: "✓", tooltip: _t("Sent") };
            case "delivered":
                return { ticks: "✓✓", tooltip: _t("Delivered") };
            case "read":
                return { ticks: "✓✓", cls: "o_read", tooltip: _t("Read") };
            case "failed":
                return {
                    icon: "fa-exclamation-circle text-danger",
                    tooltip: this.message.error_message || _t("Sending failed"),
                };
            default:
                return null;
        }
    }

    onReply() {
        this.props.onReply(this.message);
    }
}
