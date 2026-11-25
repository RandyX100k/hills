// The right pane: conversation header, messages grouped by day, and the composer (or the
// closed-window notice). Scrolling is managed here: stick to the bottom on new messages,
// keep the viewport still when older messages are prepended.
import { Component, onMounted, onPatched, onWillRender, useRef, useState } from "@odoo/owl";
import { Dropdown } from "@web/core/dropdown/dropdown";
import { DropdownItem } from "@web/core/dropdown/dropdown_item";
import { _t } from "@web/core/l10n/translation";
import { user } from "@web/core/user";
import { useService } from "@web/core/utils/hooks";

import { Composer } from "./composer";
import { dayKey, dayLabel, fullStamp, toDateTime } from "./inbox_dates";
import { Message } from "./message";

const NEAR_BOTTOM_PX = 160;

export class Thread extends Component {
    static template = "rcstudio_whatsapp_coexistence.Thread";
    static components = { Composer, Dropdown, DropdownItem, Message };
    static props = {};

    setup() {
        this.store = useState(useService("rcs_whatsapp.inbox"));
        this.scrollerRef = useRef("scroller");
        this.scrollPlan = null;
        this.snapshot = { conversationId: null, firstId: null, lastId: null };
        onWillRender(() => this.planScroll());
        onMounted(() => this.applyScroll());
        onPatched(() => this.applyScroll());
    }

    get conversation() {
        return this.store.active;
    }

    get isMine() {
        return this.conversation?.assigned_user_id === user.userId;
    }

    /** The document this conversation is about; every message is logged in its chatter. */
    get anchor() {
        return this.conversation?.anchor || null;
    }

    /** Document types this user may create from here, known before any menu is opened. */
    get creatable() {
        return this.store.capabilities.documents || [];
    }

    get anchorLabel() {
        return this.anchor ? this.anchor.name : _t("Link to a document");
    }

    get anchorTitle() {
        return this.anchor
            ? _t("Logged in the chatter of %s", this.anchor.name)
            : _t("Not linked to any document yet");
    }

    loadAnchorOptions() {
        return this.store.loadAnchorOptions();
    }

    openAnchor() {
        if (this.anchor) {
            this.store.openRecord(this.anchor.model, this.anchor.res_id);
        }
    }

    get windowTooltip() {
        const conversation = this.conversation;
        if (!conversation) {
            return "";
        }
        return conversation.window_open
            ? _t("Window open until %s", fullStamp(conversation.window_expires_at))
            : _t("24-hour window closed");
    }

    /** Messages grouped by calendar day; reactions are folded into their target instead. */
    get groups() {
        const groups = [];
        let current = null;
        for (const message of this.store.messages) {
            if (message.kind === "reaction") {
                continue;
            }
            const dt = toDateTime(message.date);
            const key = dayKey(dt);
            if (!current || current.key !== key) {
                current = { key, label: dayLabel(dt), messages: [] };
                groups.push(current);
            }
            current.messages.push(message);
        }
        return groups;
    }

    /** The last reaction of each side on each message: an empty emoji removes it. */
    get reactions() {
        const byTarget = new Map();
        for (const message of this.store.messages) {
            const target = message.kind === "reaction" && message.reaction?.to_id;
            if (!target) {
                continue;
            }
            const sides = byTarget.get(target) || {};
            sides[message.direction] = message.reaction.emoji || "";
            byTarget.set(target, sides);
        }
        return byTarget;
    }

    reactionsFor(message) {
        const sides = this.reactions.get(message.id);
        return sides ? Object.values(sides).filter(Boolean) : [];
    }

    initials() {
        const name = (this.conversation?.name || "").trim();
        return name ? name[0].toUpperCase() : "#";
    }

    // ------------------------------------------------------------- scrolling
    isNearBottom() {
        const el = this.scrollerRef.el;
        return !el || el.scrollTop + el.clientHeight >= el.scrollHeight - NEAR_BOTTOM_PX;
    }

    planScroll() {
        const messages = this.store.messages;
        const next = {
            conversationId: this.store.activeId,
            firstId: messages[0]?.id ?? null,
            lastId: messages[messages.length - 1]?.id ?? null,
        };
        const previous = this.snapshot;
        const el = this.scrollerRef.el;
        if (next.conversationId !== previous.conversationId) {
            this.scrollPlan = { bottom: true };
        } else if (el && next.firstId !== previous.firstId && previous.firstId !== null) {
            this.scrollPlan = { keepHeight: el.scrollHeight, keepTop: el.scrollTop };
        } else if (next.lastId !== previous.lastId) {
            const last = messages[messages.length - 1];
            if (this.isNearBottom() || last?.direction === "out") {
                this.scrollPlan = { bottom: true };
            }
        }
        this.snapshot = next;
    }

    applyScroll() {
        const el = this.scrollerRef.el;
        const plan = this.scrollPlan;
        this.scrollPlan = null;
        if (!el || !plan) {
            return;
        }
        if (plan.bottom) {
            el.scrollTop = el.scrollHeight;
        } else if (plan.keepHeight !== undefined) {
            el.scrollTop = el.scrollHeight - plan.keepHeight + plan.keepTop;
        }
    }

    onScroll() {
        const el = this.scrollerRef.el;
        if (el && el.scrollTop < 40 && this.store.threadHasMore && !this.store.threadLoading) {
            this.store.loadOlder();
        }
    }

    /** Inline images change the height after the render: keep the end in view. */
    onImageLoad() {
        const el = this.scrollerRef.el;
        if (el && this.isNearBottom()) {
            el.scrollTop = el.scrollHeight;
        }
    }

    // --------------------------------------------------------------- actions
    setReply(message) {
        this.store.setReplyTo(message);
    }

    openPartner() {
        this.store.openRecord("res.partner", this.conversation.partner_id);
    }

    createPartner() {
        return this.store.createPartner();
    }
}
