// Date helpers for the inbox. The server sends UTC "YYYY-MM-DD HH:MM:SS" strings; these
// turn them into luxon DateTime objects in the user's timezone through the web client's
// own deserializer, and format them the way a messaging app does.
import { deserializeDateTime } from "@web/core/l10n/dates";
import { _t } from "@web/core/l10n/translation";

const { DateTime } = luxon;

/** @param {string|false} value */
export function toDateTime(value) {
    return value ? deserializeDateTime(value) : null;
}

/** The key that groups messages by calendar day in the user's timezone. */
export function dayKey(dt) {
    return dt ? dt.toISODate() : "";
}

/** "Today", "Yesterday" or the full date, for the day separators. */
export function dayLabel(dt) {
    if (!dt) {
        return "";
    }
    const now = DateTime.now();
    if (dt.hasSame(now, "day")) {
        return _t("Today");
    }
    if (dt.hasSame(now.minus({ days: 1 }), "day")) {
        return _t("Yesterday");
    }
    return dt.toLocaleString(DateTime.DATE_FULL);
}

/** The time of a message, e.g. "14:05" or "2:05 PM" depending on the locale. */
export function timeLabel(dt) {
    return dt ? dt.toLocaleString(DateTime.TIME_SIMPLE) : "";
}

/** Date and time together, for tooltips. */
export function fullStamp(value) {
    const dt = toDateTime(value);
    return dt ? dt.toLocaleString(DateTime.DATETIME_MED) : "";
}

/** Short stamp for the list: the time today, "Yesterday", the weekday within the week, else the date. */
export function listStamp(value) {
    const dt = toDateTime(value);
    if (!dt) {
        return "";
    }
    const now = DateTime.now();
    if (dt.hasSame(now, "day")) {
        return timeLabel(dt);
    }
    if (dt.hasSame(now.minus({ days: 1 }), "day")) {
        return _t("Yesterday");
    }
    if (now.diff(dt, "days").days < 6) {
        return dt.toFormat("cccc");
    }
    return dt.toLocaleString(DateTime.DATE_SHORT);
}
