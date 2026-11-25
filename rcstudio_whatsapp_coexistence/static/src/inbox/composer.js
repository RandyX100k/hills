// The composer at the bottom of a conversation: textarea (Enter sends, Shift+Enter breaks
// the line), the web client's emoji picker, one attachment uploaded as an ir.attachment
// before inbox_send, and the reply-to strip when answering a specific message.
import { Component, onMounted, onPatched, onWillUnmount, useRef, useState } from "@odoo/owl";
import { useEmojiPicker } from "@web/core/emoji_picker/emoji_picker";
import { _t } from "@web/core/l10n/translation";
import { useAutofocus, useService } from "@web/core/utils/hooks";
import { LocationDialog } from "../map/location_dialog";
import { checkFileSize } from "@web/core/utils/files";
import { getDataURLFromFile } from "@web/core/utils/urls";
import { humanNumber } from "@web/core/utils/numbers";
import { opusRecordingType, webmToOgg } from "./webm_to_ogg";

const MAX_TEXTAREA_HEIGHT = 160;
const MAX_VOICE_SECONDS = 300;

export class Composer extends Component {
    static template = "rcstudio_whatsapp_coexistence.Composer";
    static props = {};

    setup() {
        this.store = useState(useService("rcs_whatsapp.inbox"));
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.state = useState({ body: "", file: null, recording: false, seconds: 0 });
        this.recorder = null;
        this.timer = null;
        this.textareaRef = useRef("textarea");
        this.fileInputRef = useRef("fileInput");
        useAutofocus({ refName: "textarea" });
        useEmojiPicker(useRef("emojiButton"), { onSelect: (emoji) => this.insertEmoji(emoji) });
        this.dialog = useService("dialog");
        onMounted(() => this.autoGrow());
        onPatched(() => this.autoGrow());
        onWillUnmount(() => this.discardRecorder());
    }

    /** The microphone takes the place of Send while nothing is typed or attached. */
    get canRecord() {
        return (
            !this.state.body.trim() &&
            !this.state.file &&
            Boolean(navigator.mediaDevices?.getUserMedia && window.MediaRecorder)
        );
    }

    get recordingTime() {
        const s = this.state.seconds;
        return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
    }

    async startRecording() {
        if (this.state.recording || this.store.sending) {
            return;
        }
        // Chrome, Edge and Firefox record Opus; Safari does not and needs ffmpeg on the server.
        const opusType = opusRecordingType();
        if (!opusType && !this.store.capabilities.voice_conversion) {
            this.notification.add(
                _t("This browser cannot record Opus audio, which WhatsApp needs for voice notes. Use Chrome, Edge or Firefox, or install ffmpeg on the Odoo server."),
                { type: "warning", sticky: true }
            );
            return;
        }
        if (!window.MediaRecorder) {
            this.notification.add(_t("This browser cannot record audio."), { type: "warning" });
            return;
        }
        let stream;
        try {
            stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: { ideal: 1 } } });
        } catch {
            this.notification.add(_t("The microphone is not available or access to it was refused."), {
                type: "warning",
            });
            return;
        }
        // WhatsApp wants mono Opus: the microphone goes through a one-channel destination,
        // whatever the device reports, and that is what gets recorded.
        const context = new window.AudioContext();
        const destination = context.createMediaStreamDestination();
        destination.channelCount = 1;
        destination.channelCountMode = "explicit";
        context.createMediaStreamSource(stream).connect(destination);
        const recorder = new window.MediaRecorder(destination.stream, opusType ? { mimeType: opusType } : undefined);
        const chunks = [];
        recorder.ondataavailable = (ev) => {
            if (ev.data?.size) {
                chunks.push(ev.data);
            }
        };
        this.recorder = {
            stream,
            stop: () =>
                new Promise((resolve) => {
                    const finish = (blob) => {
                        context.close().catch(() => {});
                        resolve(blob);
                    };
                    if (recorder.state === "inactive") {
                        finish(null);
                        return;
                    }
                    recorder.onstop = () =>
                        finish(chunks.length ? new Blob(chunks, { type: recorder.mimeType || opusType || "" }) : null);
                    recorder.stop();
                }),
        };
        recorder.start(250);
        this.state.recording = true;
        this.state.seconds = 0;
        this.timer = setInterval(() => {
            this.state.seconds += 1;
            if (this.state.seconds >= MAX_VOICE_SECONDS) {
                this.stopRecording(true);
            }
        }, 1000);
    }

    /** Stop the recorder; with send=true the voice note goes out. */
    async stopRecording(send) {
        const rec = this.recorder;
        if (!rec) {
            return;
        }
        this.recorder = null;
        clearInterval(this.timer);
        this.timer = null;
        this.state.recording = false;
        let blob = await rec.stop();
        rec.stream.getTracks().forEach((track) => track.stop());
        if (!send || !blob || this.state.seconds < 1) {
            return;
        }
        if (blob.type.startsWith("audio/webm")) {
            // Chrome/Edge: same Opus packets, OGG container, done here in the browser.
            try {
                blob = webmToOgg(await blob.arrayBuffer());
            } catch (error) {
                if (!this.store.capabilities.voice_conversion) {
                    this.notification.add(
                        _t("The recording could not be packaged as a voice note (%s).", error.message || error),
                        { type: "danger" }
                    );
                    return;
                }
                // an unusual WebM: let ffmpeg on the server deal with it
            }
        }
        const dataUrl = await getDataURLFromFile(blob);
        await this.store.sendVoice({
            data: dataUrl.split(",")[1] || "",
            mimetype: blob.type,
            duration: this.state.seconds,
        });
        this.textareaRef.el?.focus();
    }

    /** Leaving the conversation while recording: release the microphone, send nothing. */
    discardRecorder() {
        const rec = this.recorder;
        this.recorder = null;
        clearInterval(this.timer);
        this.timer = null;
        if (rec) {
            rec.stop().catch(() => {});
            rec.stream.getTracks().forEach((track) => track.stop());
        }
    }

    get canSend() {
        return !this.store.sending && Boolean(this.state.body.trim() || this.state.file);
    }

    get fileSize() {
        return this.state.file ? humanNumber(this.state.file.size) + "B" : "";
    }

    get replyAuthor() {
        return this.store.replyTo?.direction === "out" ? _t("You") : this.store.active?.name || "";
    }

    get replyPreview() {
        const message = this.store.replyTo;
        if (!message) {
            return "";
        }
        return message.body || message.caption || message.attachment?.name || message.kind;
    }

    autoGrow() {
        const el = this.textareaRef.el;
        if (el) {
            el.style.height = "auto";
            el.style.height = Math.min(el.scrollHeight, MAX_TEXTAREA_HEIGHT) + "px";
        }
    }

    onKeydown(ev) {
        if (ev.key === "Enter" && !ev.shiftKey && !ev.isComposing) {
            ev.preventDefault();
            this.send();
        }
    }

    insertEmoji(emoji) {
        const el = this.textareaRef.el;
        const body = this.state.body;
        const start = el?.selectionStart ?? body.length;
        const end = el?.selectionEnd ?? body.length;
        this.state.body = body.slice(0, start) + emoji + body.slice(end);
        if (el) {
            const cursor = start + emoji.length;
            // the value lands in the DOM on the next render
            Promise.resolve().then(() => {
                el.focus();
                el.setSelectionRange(cursor, cursor);
            });
        }
    }

    onClickLocation() {
        this.dialog.add(LocationDialog, { onSend: (location) => this.store.sendLocation(location) });
    }

    onClickAttach() {
        this.fileInputRef.el?.click();
    }

    onFileChange(ev) {
        const file = ev.target.files?.[0];
        ev.target.value = "";
        if (file && checkFileSize(file.size, this.notification)) {
            this.state.file = file;
        }
    }

    removeFile() {
        this.state.file = null;
    }

    cancelReply() {
        this.store.setReplyTo(null);
    }

    async send() {
        if (!this.canSend) {
            return;
        }
        const body = this.state.body.trim();
        const file = this.state.file;
        let attachmentId = null;
        if (file) {
            const dataUrl = await getDataURLFromFile(file);
            [attachmentId] = await this.orm.create("ir.attachment", [
                {
                    name: file.name,
                    datas: dataUrl.split(",")[1] || "",
                    mimetype: file.type || "application/octet-stream",
                    res_model: "rcs.whatsapp.message",
                    res_id: 0,
                },
            ]);
        }
        const message = await this.store.send({ body, attachmentId });
        if (message) {
            this.state.body = "";
            this.state.file = null;
        }
        this.textareaRef.el?.focus();
    }
}
