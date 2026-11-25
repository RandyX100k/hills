/** @odoo-module **/
// WebM/Opus -> OGG/Opus, in the browser, without re-encoding.
//
// Chrome and Edge record voice with MediaRecorder as Opus packets inside a WebM
// (Matroska) container, which Meta does not accept. WhatsApp plays OGG/Opus as a
// voice note, so the packets are moved, untouched, into Ogg pages: the container
// changes, the audio does not. No library, no server-side tool.
//
// What is read from the WebM: the Opus track (its number and its OpusHead, stored
// as CodecPrivate) and every SimpleBlock/Block of that track, in order. Chrome writes
// clusters and the segment with an unknown size, so the file is walked linearly:
// master elements are entered, everything else is skipped by its size.

const ID_EBML = 0x1a45dfa3;
const ID_SEGMENT = 0x18538067;
const ID_INFO = 0x1549a966;
const ID_TRACKS = 0x1654ae6b;
const ID_TRACK_ENTRY = 0xae;
const ID_TRACK_NUMBER = 0xd7;
const ID_CODEC_ID = 0x86;
const ID_CODEC_PRIVATE = 0x63a2;
const ID_AUDIO = 0xe1;
const ID_SAMPLING_FREQUENCY = 0xb5;
const ID_CHANNELS = 0x9f;
const ID_CLUSTER = 0x1f43b675;
const ID_BLOCK_GROUP = 0xa0;
const ID_SIMPLE_BLOCK = 0xa3;
const ID_BLOCK = 0xa1;
const MASTER_IDS = new Set([ID_EBML, ID_SEGMENT, ID_INFO, ID_TRACKS, ID_TRACK_ENTRY, ID_AUDIO, ID_CLUSTER, ID_BLOCK_GROUP]);

const OGG_CRC_TABLE = (() => {
    const table = new Uint32Array(256);
    for (let i = 0; i < 256; i++) {
        let r = i << 24;
        for (let j = 0; j < 8; j++) {
            r = r & 0x80000000 ? ((r << 1) ^ 0x04c11db7) >>> 0 : (r << 1) >>> 0;
        }
        table[i] = r >>> 0;
    }
    return table;
})();

function oggCrc(bytes) {
    let crc = 0;
    for (let i = 0; i < bytes.length; i++) {
        crc = ((crc << 8) ^ OGG_CRC_TABLE[((crc >>> 24) & 0xff) ^ bytes[i]]) >>> 0;
    }
    return crc >>> 0;
}

class EbmlReader {
    constructor(buffer) {
        this.u8 = new Uint8Array(buffer);
        this.pos = 0;
    }

    get done() {
        return this.pos >= this.u8.length;
    }

    /** Element ids keep their length marker (1 to 4 bytes). */
    readId() {
        const first = this.u8[this.pos];
        let length = 1;
        let mask = 0x80;
        while (length <= 4 && !(first & mask)) {
            mask >>= 1;
            length++;
        }
        if (length > 4 || this.pos + length > this.u8.length) {
            throw new Error("Bad EBML element id");
        }
        let id = 0;
        for (let i = 0; i < length; i++) {
            id = id * 256 + this.u8[this.pos + i];
        }
        this.pos += length;
        return id;
    }

    /** Sizes drop their marker; all ones means "unknown" (-1), which Chrome uses for live writing. */
    readVint() {
        const first = this.u8[this.pos];
        let length = 1;
        let mask = 0x80;
        while (length <= 8 && !(first & mask)) {
            mask >>= 1;
            length++;
        }
        if (length > 8 || this.pos + length > this.u8.length) {
            throw new Error("Bad EBML size");
        }
        let value = first & (mask - 1);
        let allOnes = value === mask - 1;
        for (let i = 1; i < length; i++) {
            const byte = this.u8[this.pos + i];
            value = value * 256 + byte;
            allOnes = allOnes && byte === 0xff;
        }
        this.pos += length;
        return allOnes ? -1 : value;
    }
}

function readUint(bytes) {
    let value = 0;
    for (let i = 0; i < bytes.length; i++) {
        value = value * 256 + bytes[i];
    }
    return value;
}

function readFloat(bytes) {
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    return bytes.length === 8 ? view.getFloat64(0) : view.getFloat32(0);
}

/** Track number, then the Opus frame, of a SimpleBlock or Block payload. */
function readBlock(payload) {
    const reader = new EbmlReader(payload.buffer.slice(payload.byteOffset, payload.byteOffset + payload.byteLength));
    const track = reader.readVint();
    const flags = payload[reader.pos + 2];
    const lacing = (flags & 0x06) >> 1;
    if (lacing !== 0) {
        // MediaRecorder never laces audio; a laced file goes to the server instead
        throw new Error("Laced WebM blocks are not supported");
    }
    return { track, frame: payload.subarray(reader.pos + 3) };
}

/** Samples (at 48 kHz) a packet covers, from its TOC byte: RFC 6716, section 3.1. */
function opusPacketSamples(packet) {
    const toc = packet[0];
    const config = toc >> 3;
    let frameSize;
    if (config < 12) {
        frameSize = [480, 960, 1920, 2880][config & 3]; // SILK 10/20/40/60 ms
    } else if (config < 16) {
        frameSize = config & 1 ? 960 : 480; // hybrid 20/10 ms
    } else {
        frameSize = [120, 240, 480, 960][config & 3]; // CELT 2.5/5/10/20 ms
    }
    let frames;
    switch (toc & 3) {
        case 0:
            frames = 1;
            break;
        case 1:
        case 2:
            frames = 2;
            break;
        default:
            frames = packet.length > 1 ? packet[1] & 0x3f : 1;
    }
    return frameSize * frames;
}

function parseWebm(buffer) {
    const reader = new EbmlReader(buffer);
    const entries = [];
    let current = null;
    const blocks = [];
    while (!reader.done) {
        const id = reader.readId();
        const size = reader.readVint();
        if (MASTER_IDS.has(id)) {
            if (id === ID_TRACK_ENTRY) {
                current = {};
                entries.push(current);
            }
            continue; // walk into it; unknown sizes cost nothing this way
        }
        if (size < 0 || reader.pos + size > reader.u8.length) {
            throw new Error("Truncated WebM element");
        }
        const payload = reader.u8.subarray(reader.pos, reader.pos + size);
        reader.pos += size;
        switch (id) {
            case ID_TRACK_NUMBER:
                if (current) {
                    current.number = readUint(payload);
                }
                break;
            case ID_CODEC_ID:
                if (current) {
                    current.codec = String.fromCharCode(...payload);
                }
                break;
            case ID_CODEC_PRIVATE:
                if (current) {
                    current.codecPrivate = payload.slice();
                }
                break;
            case ID_SAMPLING_FREQUENCY:
                if (current) {
                    current.sampleRate = readFloat(payload);
                }
                break;
            case ID_CHANNELS:
                if (current) {
                    current.channels = readUint(payload);
                }
                break;
            case ID_SIMPLE_BLOCK:
            case ID_BLOCK:
                blocks.push(readBlock(payload));
                break;
            default:
                break;
        }
    }
    const track = entries.find((entry) => entry.codec === "A_OPUS");
    if (!track) {
        throw new Error("No Opus track in the recording");
    }
    const packets = blocks.filter((block) => block.track === track.number).map((block) => block.frame);
    if (!packets.length) {
        throw new Error("The recording has no audio packets");
    }
    return { track, packets };
}

function opusHead(track) {
    const priv = track.codecPrivate;
    if (priv && priv.length >= 19 && String.fromCharCode(...priv.subarray(0, 8)) === "OpusHead") {
        return priv;
    }
    // built by hand when the recorder left it out
    const head = new Uint8Array(19);
    const view = new DataView(head.buffer);
    head.set([0x4f, 0x70, 0x75, 0x73, 0x48, 0x65, 0x61, 0x64]); // "OpusHead"
    head[8] = 1; // version
    head[9] = track.channels || 1;
    view.setUint16(10, 312, true); // pre-skip, the libopus default
    view.setUint32(12, Math.round(track.sampleRate || 48000), true);
    view.setInt16(16, 0, true); // output gain
    head[18] = 0; // channel mapping family
    return head;
}

function opusTags() {
    const vendor = new TextEncoder().encode("rcstudio_whatsapp_coexistence");
    const tags = new Uint8Array(8 + 4 + vendor.length + 4);
    const view = new DataView(tags.buffer);
    tags.set([0x4f, 0x70, 0x75, 0x73, 0x54, 0x61, 0x67, 0x73]); // "OpusTags"
    view.setUint32(8, vendor.length, true);
    tags.set(vendor, 12);
    view.setUint32(12 + vendor.length, 0, true); // no user comments
    return tags;
}

class OggWriter {
    constructor(serial) {
        this.serial = serial;
        this.sequence = 0;
        this.pages = [];
    }

    /** One page holding these packets; granule is the sample count up to the last of them. */
    page(packets, granule, flags) {
        const segments = [];
        let bodyLength = 0;
        for (const packet of packets) {
            let remaining = packet.length;
            while (remaining >= 255) {
                segments.push(255);
                remaining -= 255;
            }
            segments.push(remaining);
            bodyLength += packet.length;
        }
        const page = new Uint8Array(27 + segments.length + bodyLength);
        const view = new DataView(page.buffer);
        page.set([0x4f, 0x67, 0x67, 0x53]); // "OggS"
        page[4] = 0; // version
        page[5] = flags;
        view.setBigInt64(6, BigInt(granule), true);
        view.setUint32(14, this.serial, true);
        view.setUint32(18, this.sequence++, true);
        view.setUint32(22, 0, true); // crc, filled below
        page[26] = segments.length;
        page.set(segments, 27);
        let offset = 27 + segments.length;
        for (const packet of packets) {
            page.set(packet, offset);
            offset += packet.length;
        }
        view.setUint32(22, oggCrc(page), true);
        this.pages.push(page);
    }

    blob(type) {
        return new Blob(this.pages, { type });
    }
}

/**
 * Turn a WebM/Opus recording into an OGG/Opus file. Throws when the input is not
 * WebM with an Opus track; the caller then falls back to the server, if it has ffmpeg.
 * @param {ArrayBuffer} buffer
 * @returns {Blob} audio/ogg;codecs=opus
 */
export function webmToOgg(buffer) {
    const { track, packets } = parseWebm(buffer);
    const writer = new OggWriter((Math.random() * 0xffffffff) >>> 0);
    writer.page([opusHead(track)], 0, 0x02); // beginning of stream
    writer.page([opusTags()], 0, 0);
    let granule = 0;
    let pending = [];
    let pendingSegments = 0;
    packets.forEach((packet, index) => {
        const segments = Math.floor(packet.length / 255) + 1;
        if (pending.length && (pendingSegments + segments > 255 || pending.length >= 50)) {
            writer.page(pending, granule, 0);
            pending = [];
            pendingSegments = 0;
        }
        pending.push(packet);
        pendingSegments += segments;
        granule += opusPacketSamples(packet);
        if (index === packets.length - 1) {
            writer.page(pending, granule, 0x04); // end of stream
        }
    });
    return writer.blob("audio/ogg;codecs=opus");
}

/** Whether this browser can produce Opus at all (Chrome, Edge, Firefox do; Safari does not). */
export function opusRecordingType() {
    const Recorder = window.MediaRecorder;
    if (!Recorder) {
        return "";
    }
    return ["audio/ogg;codecs=opus", "audio/webm;codecs=opus"].find((type) => Recorder.isTypeSupported(type)) || "";
}
