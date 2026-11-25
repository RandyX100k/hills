import { browser } from "@web/core/browser/browser";
import { _t } from "@web/core/l10n/translation";
import { rpc } from "@web/core/network/rpc";
import { registry } from "@web/core/registry";

const POLL_MS = 5000;
const MAX_POLLS = 240; // 20 minutes: longer than the relay's session lifetime

/**
 * Client action returned by "Connect via Coexistence".
 *
 * Opens the onboarding page in a popup straight from the button's action (still
 * inside the click's user activation, so browsers let it through), then polls the
 * number until the onboarding is done and reloads the form so the new status shows
 * without the user pressing anything.
 */
function coexistenceWindow(env, action) {
    const params = action.params || {};
    const notification = env.services.notification;
    const popup = browser.open(params.url, "rcs_whatsapp_coexistence", "width=780,height=880");
    if (!popup) {
        notification.add(
            _t("Your browser blocked the onboarding window. Allow pop-ups for this site, or open it from the button below."),
            {
                type: "warning",
                sticky: true,
                buttons: [
                    {
                        name: _t("Open the onboarding window"),
                        primary: true,
                        onClick: () => browser.open(params.url, "_blank"),
                    },
                ],
            }
        );
        return;
    }

    let polls = 0;
    let stopped = false;
    const reloadForm = () => env.services.action.doAction({ type: "ir.actions.client", tag: "soft_reload" });

    const poll = async () => {
        if (stopped) {
            return;
        }
        polls += 1;
        const closed = popup.closed;
        let result;
        try {
            result = await rpc("/rcs_whatsapp/coexistence/collect", {
                number_id: params.number_id,
                token: params.token,
            });
        } catch {
            result = { status: "error", error: _t("Odoo could not be reached.") };
        }
        if (result.status === "done") {
            stopped = true;
            notification.add(_t("WhatsApp number connected via Coexistence."), { type: "success" });
            await reloadForm();
            return;
        }
        if (result.status === "error") {
            // The window shows the same error with a "check again" button; stop hammering.
            stopped = true;
            notification.add(result.error || _t("The onboarding could not be checked."), {
                type: "warning",
                sticky: true,
            });
            return;
        }
        if (result.status === "expired") {
            stopped = true;
            await reloadForm();
            return;
        }
        if (closed || polls >= MAX_POLLS) {
            // One last look after the user closed the window; then reload to show the truth.
            stopped = true;
            await reloadForm();
            return;
        }
        browser.setTimeout(poll, POLL_MS);
    };
    browser.setTimeout(poll, POLL_MS);
}

registry.category("actions").add("rcs_whatsapp.coexistence_window", coexistenceWindow);
