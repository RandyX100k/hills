// The inbox store: one reactive object shared by the inbox action and the systray item.
// It owns the conversation list, the open conversation with its messages, and keeps both
// in step with the bus notifications the server sends on the user's partner channel.
// Everything talks to the models directly (inbox_* methods); there is no controller.
import { markRaw, reactive } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { user } from "@web/core/user";
import { debounce } from "@web/core/utils/timing";

const CONVERSATION_MODEL = "rcs.whatsapp.conversation";
const MESSAGE_MODEL = "rcs.whatsapp.message";
const LIST_LIMIT = 40;
const THREAD_LIMIT = 60;

export class InboxStore {
    constructor(env, services) {
        // services are not state: keep them out of the reactive proxy
        this.services = markRaw({ env, ...services });

        this.isOperator = false;
        this.chatterModels = [];
        this.capabilities = { voice_conversion: false };
        this.unreadTotal = 0;
        this.inboxMounted = false;
        // sound and desktop notifications for inbound messages
        this.muted = false;
        try {
            this.muted = window.localStorage.getItem("rcs_whatsapp.muted") === "1";
        } catch {
            // storage unavailable: stay unmuted
        }
        this._notified = new Set();
        this._lastSoundAt = 0;

        // conversation list
        this.numbers = [];
        this.numberId = false;
        this.filter = "all";
        this.search = "";
        this.conversations = [];
        this.listHasMore = false;
        this.listLoading = false;
        this._listOffset = 0;
        this._listRequest = 0;

        // open conversation
        this.activeId = false;
        this.active = null;
        this.messages = [];
        this.threadHasMore = false;
        this.threadLoading = false;
        this.olderLoading = false;
        this.sending = false;
        this.replyTo = null;
        this._openRequest = 0;
        // what the open conversation can be anchored to; loaded when the menu opens
        this.anchorOptions = { options: [], creatable: [], current: false };
        this.anchorBusy = false;
    }

    get orm() {
        return this.services.orm;
    }

    /** Called on the reactive proxy, so the closures below mutate observable state. */
    setup() {
        this.refreshUnreadTotal = debounce(() => this._fetchUnreadTotal(), 400);
    }

    async start() {
        this.isOperator = await user.hasGroup("rcstudio_whatsapp_coexistence.group_user");
        if (!this.isOperator) {
            return;
        }
        const bus = this.services.bus_service;
        bus.subscribe("rcs_whatsapp/conversation", (payload) => this.onConversationUpdate(payload));
        bus.subscribe("rcs_whatsapp/message", (payload) => this.onMessageUpdate(payload));
        bus.start();
        this.refreshUnreadTotal();
        this.chatterModels = await this.orm.call("rcs.whatsapp.send", "chatter_models", []);
        this.capabilities = await this.orm.call(CONVERSATION_MODEL, "inbox_capabilities", []);
    }

    // ------------------------------------------------------------------ list
    async loadList({ reset = true } = {}) {
        const request = ++this._listRequest;
        this.listLoading = true;
        const offset = reset ? 0 : this._listOffset;
        try {
            const result = await this.orm.call(CONVERSATION_MODEL, "inbox_list", [], {
                number_id: this.numberId || false,
                filter_: this.filter,
                search: this.search,
                limit: LIST_LIMIT,
                offset,
            });
            if (request !== this._listRequest) {
                return;
            }
            this.numbers = result.numbers;
            this.unreadTotal = result.unread_total;
            const fresh = result.conversations;
            if (reset) {
                this.conversations = fresh;
            } else {
                const known = new Set(this.conversations.map((c) => c.id));
                this.conversations.push(...fresh.filter((c) => !known.has(c.id)));
            }
            this._listOffset = offset + fresh.length;
            this.listHasMore = fresh.length === LIST_LIMIT;
            this.sortConversations();
        } finally {
            if (request === this._listRequest) {
                this.listLoading = false;
            }
        }
    }

    loadMoreList() {
        if (this.listHasMore && !this.listLoading) {
            return this.loadList({ reset: false });
        }
    }

    setFilter(filter) {
        if (this.filter !== filter) {
            this.filter = filter;
            return this.loadList();
        }
    }

    setNumber(numberId) {
        if (this.numberId !== numberId) {
            this.numberId = numberId;
            return this.loadList();
        }
    }

    setSearch(search) {
        if (this.search !== search) {
            this.search = search;
            return this.loadList();
        }
    }

    sortConversations() {
        this.conversations.sort(
            (a, b) => (b.last_activity || "").localeCompare(a.last_activity || "") || b.id - a.id
        );
    }

    /** Whether a summary belongs in the list under the current number / filter. */
    matchesFilter(summary) {
        if (this.numberId && summary.number_id !== this.numberId) {
            return false;
        }
        if (this.filter === "closed") {
            return summary.state === "closed";
        }
        if (summary.state !== "open") {
            return false;
        }
        if (this.filter === "unread" && !summary.unread_count && summary.id !== this.activeId) {
            return false;
        }
        if (this.filter === "mine" && summary.assigned_user_id !== user.userId) {
            return false;
        }
        return true;
    }

    /** The search box only filters newcomers: the server already searched message bodies. */
    matchesSearch(summary) {
        const needle = this.search.trim().toLowerCase();
        if (!needle) {
            return true;
        }
        return [summary.name, summary.phone, summary.phone_display, summary.preview]
            .filter(Boolean)
            .some((text) => String(text).toLowerCase().includes(needle));
    }

    upsertConversation(summary) {
        const index = this.conversations.findIndex((c) => c.id === summary.id);
        if (index >= 0) {
            if (this.matchesFilter(summary)) {
                Object.assign(this.conversations[index], summary);
            } else {
                this.conversations.splice(index, 1);
                return;
            }
        } else if (this.matchesFilter(summary) && this.matchesSearch(summary)) {
            this.conversations.push({ ...summary });
        } else {
            return;
        }
        this.sortConversations();
    }

    // ---------------------------------------------------------------- thread
    async open(conversationId) {
        const request = ++this._openRequest;
        this.activeId = conversationId;
        this.replyTo = null;
        this.threadLoading = true;
        // optimistic: the server marks it read as part of inbox_open
        const known = this.conversations.find((c) => c.id === conversationId);
        if (known && known.unread_count) {
            this.unreadTotal = Math.max(0, this.unreadTotal - known.unread_count);
            known.unread_count = 0;
        }
        try {
            const result = await this.orm.call(CONVERSATION_MODEL, "inbox_open", [[conversationId]], {
                limit: THREAD_LIMIT,
            });
            if (request !== this._openRequest) {
                return;
            }
            this.active = result.conversation;
            this.messages = result.messages;
            this.threadHasMore = result.has_more;
            this.upsertConversation(result.conversation);
        } catch (error) {
            if (request === this._openRequest) {
                this.closeThread();
            }
            throw error;
        } finally {
            if (request === this._openRequest) {
                this.threadLoading = false;
            }
        }
    }

    closeThread() {
        this._openRequest++;
        this.activeId = false;
        this.active = null;
        this.messages = [];
        this.threadHasMore = false;
        this.replyTo = null;
        this.anchorOptions = { options: [], creatable: [], current: false };
    }

    // ------------------------------------------------- what it is all about
    /**
     * A WhatsApp message carries no thread reference the way an email does, so the
     * conversation remembers which document it is currently about and every message is
     * logged there. These are the ways to set, change or drop that choice.
     */
    async loadAnchorOptions() {
        if (!this.activeId) {
            return;
        }
        const conversationId = this.activeId;
        const result = await this.orm.call(CONVERSATION_MODEL, "inbox_anchor_options", [
            [conversationId],
        ]);
        if (conversationId === this.activeId) {
            this.anchorOptions = result;
        }
    }

    applyConversation(summary) {
        if (!summary) {
            return;
        }
        this.upsertConversation(summary);
        if (this.active?.id === summary.id) {
            Object.assign(this.active, summary);
        }
    }

    async setAnchor(model, resId) {
        return this._anchorCall("inbox_set_anchor", [model, resId]);
    }

    async clearAnchor() {
        return this._anchorCall("inbox_clear_anchor", []);
    }

    async _anchorCall(method, args) {
        if (!this.activeId || this.anchorBusy) {
            return;
        }
        const conversationId = this.activeId;
        this.anchorBusy = true;
        try {
            const summary = await this.orm.call(CONVERSATION_MODEL, method, [
                [conversationId],
                ...args,
            ]);
            this.applyConversation(summary);
        } catch (error) {
            if (error?.data?.message) {
                this.services.notification.add(error.data.message, { type: "danger" });
                return;
            }
            throw error;
        } finally {
            this.anchorBusy = false;
        }
    }

    /** Create the document a customer who wrote first has no record for, and anchor it. */
    async createDocument(model) {
        if (!this.activeId || this.anchorBusy) {
            return null;
        }
        const conversationId = this.activeId;
        this.anchorBusy = true;
        try {
            const result = await this.orm.call(CONVERSATION_MODEL, "inbox_create_document", [
                [conversationId],
                model,
            ]);
            this.applyConversation(result.conversation);
            this.services.notification.add(result.name, {
                title: _t("Created from this conversation"),
                type: "success",
                buttons: [
                    {
                        name: _t("Open"),
                        primary: true,
                        onClick: () => this.openRecord(result.model, result.res_id),
                    },
                ],
            });
            return result;
        } catch (error) {
            if (error?.data?.message) {
                this.services.notification.add(error.data.message, { type: "danger" });
                return null;
            }
            throw error;
        } finally {
            this.anchorBusy = false;
        }
    }

    refreshActive() {
        if (this.activeId) {
            return this.open(this.activeId);
        }
    }

    async loadOlder() {
        if (!this.activeId || !this.threadHasMore || this.olderLoading || !this.messages.length) {
            return;
        }
        const conversationId = this.activeId;
        this.olderLoading = true;
        try {
            const result = await this.orm.call(MESSAGE_MODEL, "inbox_fetch", [
                conversationId,
                this.messages[0].id,
                THREAD_LIMIT,
            ]);
            if (conversationId !== this.activeId) {
                return;
            }
            const known = new Set(this.messages.map((m) => m.id));
            this.messages.unshift(...result.messages.filter((m) => !known.has(m.id)));
            this.threadHasMore = result.has_more;
        } finally {
            this.olderLoading = false;
        }
    }

    upsertMessage(message) {
        const index = this.messages.findIndex((m) => m.id === message.id);
        if (index >= 0) {
            Object.assign(this.messages[index], message);
            return;
        }
        this.messages.push(message);
        this.messages.sort((a, b) => a.date.localeCompare(b.date) || a.id - b.id);
    }

    /**
     * Send a free-form message in the open conversation. Returns the message dict, or
     * null when the server refused it (closed window, disconnected number...): that case
     * is shown as a toast instead of the generic error dialog.
     */
    async send({ body = "", attachmentId = null } = {}) {
        if (!this.activeId || this.sending) {
            return null;
        }
        const conversationId = this.activeId;
        this.sending = true;
        try {
            const message = await this.orm.call(CONVERSATION_MODEL, "inbox_send", [[conversationId]], {
                body,
                reply_to_id: this.replyTo?.id || false,
                attachment_id: attachmentId || false,
            });
            this.replyTo = null;
            if (conversationId === this.activeId) {
                this.upsertMessage(message);
            }
            if (message.status === "failed") {
                this.services.notification.add(
                    message.error_message || _t("The message could not be sent."),
                    { title: _t("Message not sent"), type: "danger" }
                );
            }
            return message;
        } catch (error) {
            if (error?.data?.message) {
                this.services.notification.add(error.data.message, { type: "danger" });
                return null;
            }
            throw error;
        } finally {
            this.sending = false;
        }
    }

    async sendLocation({ latitude, longitude, name, address }) {
        if (!this.activeId) {
            return null;
        }
        const conversationId = this.activeId;
        try {
            const message = await this.orm.call(CONVERSATION_MODEL, "inbox_send_location",
                [[conversationId], latitude, longitude, name || "", address || ""]);
            if (conversationId === this.activeId) {
                this.upsertMessage(message);
            }
            if (message.status === "failed") {
                this.services.notification.add(message.error_message || _t("The location could not be sent."),
                    { title: _t("Message not sent"), type: "danger" });
            }
            return message;
        } catch (error) {
            if (error?.data?.message) {
                this.services.notification.add(error.data.message, { type: "danger" });
                return null;
            }
            throw error;
        }
    }

    /** A voice note recorded in the composer; the server converts and sends it. */
    async sendVoice({ data, mimetype = "", duration = 0 }) {
        if (!this.activeId || this.sending) {
            return null;
        }
        const conversationId = this.activeId;
        this.sending = true;
        try {
            const message = await this.orm.call(CONVERSATION_MODEL, "inbox_send_voice",
                [[conversationId], data, mimetype, duration]);
            if (conversationId === this.activeId) {
                this.upsertMessage(message);
            }
            if (message.status === "failed") {
                this.services.notification.add(message.error_message || _t("The voice note could not be sent."),
                    { title: _t("Message not sent"), type: "danger" });
            }
            return message;
        } catch (error) {
            if (error?.data?.message) {
                this.services.notification.add(error.data.message, { type: "danger" });
                return null;
            }
            throw error;
        } finally {
            this.sending = false;
        }
    }

    setReplyTo(message) {
        this.replyTo = message;
    }

    async markRead(conversationId) {
        const known = this.conversations.find((c) => c.id === conversationId);
        if (known) {
            known.unread_count = 0;
        }
        if (this.active?.id === conversationId) {
            this.active.unread_count = 0;
        }
        await this.orm.call(CONVERSATION_MODEL, "mark_read", [[conversationId]]);
        this.refreshUnreadTotal();
    }

    // --------------------------------------------------------------- actions
    async assignMe() {
        await this._activeAction("action_assign_me", {
            assigned_user_id: user.userId,
            assigned_user_name: user.name,
        });
    }

    async close() {
        await this._activeAction("action_close", { state: "closed" });
    }

    async reopen() {
        await this._activeAction("action_reopen", { state: "open" });
    }

    async removeFromBlacklist() {
        await this._activeAction("action_blacklist_remove", { opted_out: false });
    }

    async _activeAction(method, values) {
        if (!this.activeId) {
            return;
        }
        await this.orm.call(CONVERSATION_MODEL, method, [[this.activeId]]);
        if (this.active) {
            Object.assign(this.active, values);
            this.upsertConversation({ ...this.active });
        }
    }

    /** The "Send by WhatsApp" wizard in template mode, for a closed window. */
    openTemplateWizard() {
        const conversation = this.active;
        if (!conversation) {
            return;
        }
        return this.services.action.doAction("rcstudio_whatsapp_coexistence.action_send", {
            additionalContext: {
                active_model: false,
                active_id: false,
                active_ids: [],
                default_number_id: conversation.number_id,
                default_phone: conversation.phone_display || conversation.phone,
                default_partner_id: conversation.partner_id || false,
                default_mode: "template",
            },
            onClose: () => this.refreshActive(),
        });
    }

    /** Turn the stranger of the active thread into a contact, on request only. */
    async createPartner() {
        if (!this.activeId || this.anchorBusy) {
            return null;
        }
        const conversationId = this.activeId;
        this.anchorBusy = true;
        try {
            const result = await this.orm.call(CONVERSATION_MODEL, "inbox_create_partner", [
                [conversationId],
            ]);
            this.applyConversation(result.conversation);
            this.services.notification.add(result.name, {
                title: _t("Contact created from this conversation"),
                type: "success",
                buttons: [
                    {
                        name: _t("Open"),
                        primary: true,
                        onClick: () => this.openRecord("res.partner", result.partner_id),
                    },
                ],
            });
            return result;
        } catch (error) {
            if (error?.data?.message) {
                this.services.notification.add(error.data.message, { type: "danger" });
                return null;
            }
            throw error;
        } finally {
            this.anchorBusy = false;
        }
    }

    openInbox() {
        return this.services.action.doAction("rcstudio_whatsapp_coexistence.action_inbox");
    }

    openRecord(model, resId) {
        return this.services.action.doAction({
            type: "ir.actions.act_window",
            res_model: model,
            res_id: resId,
            views: [[false, "form"]],
            target: "current",
        });
    }

    // ----------------------------------------------------------- alerts
    toggleMute() {
        this.muted = !this.muted;
        try {
            window.localStorage.setItem("rcs_whatsapp.muted", this.muted ? "1" : "0");
        } catch {
            // storage unavailable: the choice lasts for this page
        }
    }

    requestNotificationPermission() {
        if (window.Notification && Notification.permission === "default") {
            Notification.requestPermission().catch(() => {});
        }
    }

    /** Ring once per inbound message (never for echoes), throttled to one ring per second. */
    _alertInbound(message, conversation) {
        if (message.direction !== "in" || message.is_echo || this._notified.has(message.id)) {
            return;
        }
        this._notified.add(message.id);
        if (this._notified.size > 500) {
            this._notified = new Set([...this._notified].slice(-200));
        }
        const now = Date.now();
        if (!this.muted && now - this._lastSoundAt > 1000) {
            this._lastSoundAt = now;
            this._playSound();
        }
        if (document.hidden) {
            this._desktopNotify(message, conversation);
        }
    }

    _playSound() {
        try {
            if (!this._audio) {
                this._audio = new Audio("/rcstudio_whatsapp_coexistence/static/audio/new_message.mp3");
                this._audio.volume = 0.6;
            }
            this._audio.currentTime = 0;
            this._audio.play().catch(() => {
                // browsers refuse to play before the first user gesture on the page
            });
        } catch {
            // no audio support: silence is acceptable
        }
    }

    _desktopNotify(message, conversation) {
        if (!window.Notification || Notification.permission !== "granted") {
            return;
        }
        const title = conversation?.name || _t("WhatsApp");
        const body = message.body || message.caption || conversation?.preview || _t("New message");
        try {
            const note = new Notification(title, { body: body.slice(0, 140), tag: `rcs-wa-${conversation?.id}` });
            note.onclick = () => {
                window.focus();
                note.close();
                if (conversation?.id) {
                    this.openInbox().then?.(() => this.open(conversation.id));
                }
            };
        } catch {
            // a notification that cannot be shown is not an error
        }
    }

    // ------------------------------------------------------------------- bus
    onConversationUpdate(summary) {
        this.upsertConversation(summary);
        if (summary.id === this.activeId && this.active) {
            Object.assign(this.active, summary);
        }
        this.refreshUnreadTotal();
    }

    onMessageUpdate({ conversation_id, message, conversation }) {
        if (conversation) {
            this.upsertConversation(conversation);
        }
        this._alertInbound(message, conversation);
        if (conversation_id === this.activeId) {
            this.upsertMessage(message);
            if (conversation && this.active) {
                Object.assign(this.active, conversation);
            }
            // the operator is looking at it: an incoming message is read on arrival
            if (message.direction === "in" && this.inboxMounted && conversation?.unread_count) {
                this.markRead(conversation_id);
                return;
            }
        }
        this.refreshUnreadTotal();
    }

    async _fetchUnreadTotal() {
        this.unreadTotal = await this.orm.call(CONVERSATION_MODEL, "inbox_unread_total", []);
    }
}

export const inboxService = {
    dependencies: ["orm", "bus_service", "action", "notification"],
    start(env, services) {
        const store = reactive(new InboxStore(env, services));
        store.setup();
        store.start();
        return store;
    },
};

registry.category("services").add("rcs_whatsapp.inbox", inboxService);
