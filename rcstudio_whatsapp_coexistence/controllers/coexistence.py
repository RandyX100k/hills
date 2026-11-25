"""Coexistence onboarding pages and endpoints.

Routes (all under /rcs_whatsapp/coexistence/, all for logged-in WhatsApp managers, all
guarded by the number's token):

* ``GET  onboard/<number_id>/<token>`` - the onboarding window. With an own Tech
  Provider app it runs the Facebook SDK here; in licence mode it opens the page hosted
  by RC Studio's relay in a popup and collects the result.
* ``POST session`` (jsonrpc)  - licence mode: open a fresh signup session on the relay.
* ``POST collect`` (jsonrpc)  - poll the onboarding; finishes it in licence mode.
* ``POST finish``  (jsonrpc)  - own app: the page posts the code and the ids here.

The jsonrpc endpoints never raise for expected failures: they answer
``{'status': 'error', 'error': message}`` / ``{'ok': False, 'error': message}`` so the
page and the form poller can show something useful.
"""
import json
import logging
from string import Template

from markupsafe import escape

from odoo import http
from odoo.exceptions import AccessError, UserError
from odoo.http import request
from odoo.tools import consteq

from ..tools.broker import BrokerError
from ..tools.graph import GraphError

_logger = logging.getLogger(__name__)


class WhatsAppCoexistence(http.Controller):

    # ------------------------------------------------------------------ helpers
    def _number(self, number_id, token):
        """The number behind the URL, or an error the caller turns into a message."""
        if not request.env.user.has_group('rcstudio_whatsapp_coexistence.group_manager'):
            raise AccessError('Only WhatsApp managers can connect a number.')
        number = request.env['rcs.whatsapp.number'].browse(int(number_id or 0)).exists()
        if not number:
            raise UserError('Business number not found.')
        real = number.sudo().webhook_token or ''
        if not (token and real and consteq(str(token), real)):
            raise AccessError('This onboarding link is not valid for the number.')
        return number

    @staticmethod
    def _error(exc):
        if isinstance(exc, (UserError, AccessError, BrokerError, GraphError)):
            return str(exc)
        _logger.exception('Coexistence onboarding failed')
        return f'Unexpected error: {exc.__class__.__name__}. Check the server log.'

    # --------------------------------------------------------------------- page
    @http.route('/rcs_whatsapp/coexistence/onboard/<int:number_id>/<string:token>',
                type='http', auth='user', methods=['GET'])
    def onboard(self, number_id, token, **kw):
        try:
            number = self._number(number_id, token)
        except (AccessError, UserError) as exc:
            return request.make_response(str(exc), status=403, headers=[('Content-Type', 'text/plain')])
        if number.connection_mode != 'coexistence':
            return request.make_response('This number is not a coexistence number.', status=400,
                                         headers=[('Content-Type', 'text/plain')])
        me = number.sudo()
        common = {
            'title': 'Connect WhatsApp via Coexistence',
            'number_name': escape(number.name),
            'number_id': json.dumps(number.id),
            'token': json.dumps(me.webhook_token),
            'lang': escape(number._fb_locale()[:2]),
        }
        if number.provider == 'rcstudio':
            broker = (number._param('broker_url') or '').rstrip('/')
            html = HOSTED_PAGE.substitute(
                common, broker_origin=json.dumps(broker), signup_url=json.dumps(me.signup_url or ''))
        else:
            if not (me.app_id and me.login_config_id):
                return request.make_response(
                    'The App ID and the Login Configuration ID are missing on this number. '
                    'Your Tech Provider issues them; fill them in and try again.',
                    status=400, headers=[('Content-Type', 'text/plain')])
            html = OWN_APP_PAGE.substitute(
                common, app_id=json.dumps(me.app_id), config_id=json.dumps(me.login_config_id),
                graph_version=json.dumps(number._param('graph_version') or 'v22.0'),
                fb_locale=escape(number._fb_locale()))
        return request.make_response(html, headers=[('Content-Type', 'text/html; charset=utf-8')])

    # ---------------------------------------------------------------- jsonrpc
    # 18.0: json-rpc routes are type='json' (19.0: 'jsonrpc')
    @http.route('/rcs_whatsapp/coexistence/session', type='json', auth='user', methods=['POST'])
    def session(self, number_id=None, token=None, **kw):
        """Licence mode: a fresh signup session (the page asks for one when the previous
        one expired, or when the form opened the window without one)."""
        try:
            number = self._number(number_id, token)
            if number.provider != 'rcstudio':
                raise UserError('This number uses its own Tech Provider app; no relay session is needed.')
            number._check_coexistence_ready()
            return dict(number._open_signup_session(), ok=True)
        except Exception as exc:  # noqa: BLE001 - the page needs a message, not a 500
            return {'ok': False, 'error': self._error(exc)}

    @http.route('/rcs_whatsapp/coexistence/collect', type='json', auth='user', methods=['POST'])
    def collect(self, number_id=None, token=None, **kw):
        try:
            number = self._number(number_id, token)
            return number.coexistence_collect()
        except Exception as exc:  # noqa: BLE001
            return {'status': 'error', 'error': self._error(exc)}

    @http.route('/rcs_whatsapp/coexistence/finish', type='json', auth='user', methods=['POST'])
    def finish(self, number_id=None, token=None, code=None, waba_id=None, phone_number_id=None,
               business_id=None, event=None, **kw):
        try:
            number = self._number(number_id, token)
            if event and event not in ('FINISH', 'FINISH_ONLY_WABA', 'FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING'):
                raise UserError(f"Meta's dialog ended with {event}; the number was not connected.")
            result = number.coexistence_finish_with_code(code, waba_id, phone_number_id, business_id)
            me = number.sudo()
            return {'ok': True, 'name': number.name, 'phone': me.display_phone or '',
                    'warnings': result.get('warnings') or []}
        except Exception as exc:  # noqa: BLE001
            return {'ok': False, 'error': self._error(exc)}


# --------------------------------------------------------------------------- HTML
# Both pages are plain HTML with inline JS; placeholders are $name (string.Template),
# JSON-encoded where they land inside a script, HTML-escaped elsewhere.

_HEAD = """<!DOCTYPE html>
<html lang="$lang">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>$title</title>
  <style>
    body { font-family: system-ui, -apple-system, "Segoe UI", sans-serif; max-width: 680px; margin: 32px auto; padding: 0 16px; color: #222; background: #fff; }
    h1 { font-size: 1.35rem; margin-bottom: 4px; }
    .sub { color: #666; margin-top: 0; }
    h2 { font-size: 1rem; margin-top: 24px; }
    ul, ol { line-height: 1.6; padding-left: 22px; }
    button { background: #25D366; color: #fff; border: 0; padding: 12px 20px; font-size: 1rem; border-radius: 8px; cursor: pointer; }
    button:disabled { opacity: .5; cursor: not-allowed; }
    button.secondary { background: #555; margin-left: 8px; }
    #status { margin-top: 20px; padding: 12px; border-radius: 8px; background: #f5f5f5; white-space: pre-wrap; word-break: break-word; min-height: 1.5em; }
    #status.ok { background: #e8f7ee; }
    #status.err { background: #fdecec; }
    #status a { color: #0b7a3b; }
    .note { color: #666; font-size: .9rem; }
  </style>
</head>
<body>
  <div id="fb-root"></div>
  <h1>$title</h1>
  <p class="sub">Business number: <b>$number_name</b></p>
  <h2>Before you start</h2>
  <ul>
    <li>The number is active on the <b>WhatsApp Business app</b> and has been for <b>at least 7 days</b>.</li>
    <li>It is a <b>WhatsApp Business account</b>, not a personal WhatsApp account.</li>
    <li>The app is up to date: <b>Android 2.24.17</b> or newer, <b>iOS 24.17</b> or newer.</li>
    <li>You can scan a QR code with the phone that has the number.</li>
  </ul>
  <h2>What happens next</h2>
  <ol>
    <li>Press <b>Connect via Coexistence</b>.</li>
    <li>In Meta's dialog choose <b>"existing WhatsApp Business account"</b> and scan the <b>QR code</b> with the app. Do not choose "new number".</li>
    <li>Come back here: the number is connected automatically and you can close this window.</li>
  </ol>
"""

OWN_APP_PAGE = Template(_HEAD + """
  <button id="btn" disabled>Loading Meta's SDK...</button>
  <div id="status"></div>
  <p class="note">This page uses your own Tech Provider app. Its App ID and Login Configuration were issued by your provider; the domain of this Odoo must be allowed in that app.</p>

  <script>
    var APP_ID = $app_id;
    var CONFIG_ID = $config_id;
    var GRAPH_VERSION = $graph_version;
    var NUMBER_ID = $number_id;
    var TOKEN = $token;
    var sessionData = {};
    var btn = document.getElementById("btn");
    var statusEl = document.getElementById("status");

    function setStatus(msg, cls) { statusEl.textContent = msg; statusEl.className = cls || ""; }

    /* Odoo JSON-RPC: errors come back as JSON, never as an HTML error page. */
    function rpc(path, params) {
      return fetch(path, {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ jsonrpc: "2.0", method: "call", id: Date.now(), params: params })
      }).then(function (r) { return r.json(); }).then(function (j) {
        if (j.error) { throw new Error((j.error.data && j.error.data.message) || j.error.message || "unknown error"); }
        return j.result;
      });
    }

    /* Meta posts the ids the user picked (and the outcome) to the page that opened the dialog. */
    window.addEventListener("message", function (event) {
      if (!/\\.facebook\\.com$$/.test(event.origin)) { return; }
      try {
        var data = JSON.parse(event.data);
        if (data.type === "WA_EMBEDDED_SIGNUP") { sessionData = data; }
      } catch (e) { /* not JSON: not for us */ }
    });

    window.fbAsyncInit = function () {
      FB.init({ appId: APP_ID, autoLogAppEvents: true, xfbml: false, version: GRAPH_VERSION });
      btn.disabled = false;
      btn.textContent = "Connect via Coexistence";
    };

    (function (d, s, id) {
      var js, fjs = d.getElementsByTagName(s)[0];
      if (d.getElementById(id)) { return; }
      js = d.createElement(s); js.id = id;
      js.src = "https://connect.facebook.net/$fb_locale/sdk.js";
      js.async = true; js.defer = true; js.crossOrigin = "anonymous";
      js.onerror = function () { setStatus("Meta's SDK could not be loaded. Check the network and reload this page.", "err"); };
      fjs.parentNode.insertBefore(js, fjs);
    })(document, "script", "facebook-jssdk");

    function finish(code) {
      var d = (sessionData && sessionData.data) || {};
      setStatus("Completing the onboarding in Odoo...");
      rpc("/rcs_whatsapp/coexistence/finish", {
        number_id: NUMBER_ID, token: TOKEN, code: code,
        waba_id: d.waba_id, phone_number_id: d.phone_number_id, business_id: d.business_id,
        event: sessionData && sessionData.event
      }).then(function (res) {
        if (res.ok) {
          var text = "Connected: " + (res.name || "") + " " + (res.phone || "") + ". You can close this window.";
          if (res.warnings && res.warnings.length) { text += "\\n\\nPlease note:\\n- " + res.warnings.join("\\n- "); }
          setStatus(text, res.warnings && res.warnings.length ? "" : "ok");
          btn.style.display = "none";
        } else {
          setStatus("Error: " + (res.error || "unknown"), "err");
          btn.disabled = false;
        }
      }).catch(function (e) {
        setStatus("Error: " + (e.message || e), "err");
        btn.disabled = false;
      });
    }

    btn.addEventListener("click", function () {
      btn.disabled = true;
      sessionData = {};
      setStatus("Opening Meta's dialog...");
      FB.login(function (response) {
        if (!response.authResponse || !response.authResponse.code) {
          var ev = sessionData && sessionData.event;
          setStatus(ev === "CANCEL" || !ev ? "Cancelled. Press the button to try again."
                                            : "Meta's dialog ended with " + ev + ". Press the button to try again.", "err");
          btn.disabled = false;
          return;
        }
        finish(response.authResponse.code);
      }, {
        config_id: CONFIG_ID,
        response_type: "code",
        override_default_response_type: true,
        extras: { setup: {}, featureType: "whatsapp_business_app_onboarding", sessionInfoVersion: "3" }
      });
    });
  </script>
</body>
</html>""")


HOSTED_PAGE = Template(_HEAD + """
  <button id="btn">Connect via Coexistence</button>
  <button id="retry" class="secondary" style="display:none">Check the result again</button>
  <div id="status"></div>
  <p class="note">Meta's dialog opens on a secure page of RC Studio, which holds the Tech Provider app. The token issued for your number is handed to this Odoo and stays here.</p>

  <script>
    var NUMBER_ID = $number_id;
    var TOKEN = $token;
    var BROKER_ORIGIN = $broker_origin;
    var SIGNUP_URL = $signup_url;   /* session opened by the form; empty when it must be requested here */
    var btn = document.getElementById("btn");
    var retry = document.getElementById("retry");
    var statusEl = document.getElementById("status");
    var popup = null;
    var collecting = false;

    function setStatus(msg, cls, html) {
      if (html) { statusEl.innerHTML = msg; } else { statusEl.textContent = msg; }
      statusEl.className = cls || "";
    }
    function escapeHtml(s) {
      return String(s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; });
    }

    /* Odoo JSON-RPC: errors come back as JSON, never as an HTML error page. */
    function rpc(path, params) {
      return fetch(path, {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ jsonrpc: "2.0", method: "call", id: Date.now(), params: params })
      }).then(function (r) { return r.json(); }).then(function (j) {
        if (j.error) { throw new Error((j.error.data && j.error.data.message) || j.error.message || "unknown error"); }
        return j.result;
      });
    }

    function openPopup(url) {
      /* No "noopener": the hosted page reports back through window.opener. */
      popup = window.open(url, "rcs_whatsapp_signup", "width=720,height=800");
      if (!popup) {
        setStatus('Your browser blocked the window. Open it with this link: <a href="' + escapeHtml(url) + '" target="_blank" rel="opener">' + escapeHtml(url) + "</a>", "err", true);
      } else {
        setStatus("Complete the connection in the window that just opened. This page updates by itself when you are done.");
      }
      retry.style.display = "inline-block";
      btn.disabled = false;
    }

    function collect() {
      if (collecting) { return; }
      collecting = true;
      setStatus("Finishing the onboarding in Odoo...");
      retry.style.display = "none";
      rpc("/rcs_whatsapp/coexistence/collect", { number_id: NUMBER_ID, token: TOKEN }).then(function (res) {
        collecting = false;
        if (res.status === "done") {
          var text = "Connected: " + (res.name || "") + " " + (res.phone || "") + ". You can close this window.";
          if (res.warnings && res.warnings.length) { text += "\\n\\nPlease note:\\n- " + res.warnings.join("\\n- "); }
          setStatus(text, res.warnings && res.warnings.length ? "" : "ok");
          btn.style.display = "none";
          if (popup && !popup.closed) { try { popup.close(); } catch (e) { /* cross-origin: ignore */ } }
        } else if (res.status === "expired" || res.status === "none") {
          SIGNUP_URL = "";
          setStatus("The connection session expired. Press Connect via Coexistence to open a new one.", "err");
        } else if (res.status === "error") {
          setStatus("Error: " + (res.error || "unknown"), "err");
          retry.style.display = "inline-block";
        } else {
          setStatus((res.last_error ? "Error: " + res.last_error + "\\n" : "") +
                    "The connection is not finished yet. Complete it in the window and check again.");
          retry.style.display = "inline-block";
        }
      }).catch(function (e) {
        collecting = false;
        setStatus("Error: " + (e.message || e), "err");
        retry.style.display = "inline-block";
      });
    }

    window.addEventListener("message", function (event) {
      if (event.origin !== BROKER_ORIGIN) { return; }
      var data = event.data;
      if (!data || data.type !== "wacoex_signup") { return; }
      if (data.ok) {
        collect();
      } else {
        /* The popup stays open with its own "try again"; the session is still valid. */
        setStatus("Error: " + (data.error || "unknown"), "err");
      }
    });

    btn.addEventListener("click", function () {
      btn.disabled = true;
      if (SIGNUP_URL) {
        /* Opened straight from the click: no async work before window.open, so no blocker. */
        var url = SIGNUP_URL;
        SIGNUP_URL = "";
        openPopup(url);
        return;
      }
      setStatus("Opening the secure connection window...");
      rpc("/rcs_whatsapp/coexistence/session", { number_id: NUMBER_ID, token: TOKEN }).then(function (res) {
        if (!res.ok) { throw new Error(res.error || "unknown error"); }
        openPopup(res.url);
      }).catch(function (e) {
        setStatus("Error: " + (e.message || e), "err");
        btn.disabled = false;
      });
    });
    retry.addEventListener("click", collect);
  </script>
</body>
</html>""")
