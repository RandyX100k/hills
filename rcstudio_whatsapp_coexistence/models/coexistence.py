"""Coexistence onboarding (Meta Embedded Signup) for rcs.whatsapp.number.

Two ways to get there, one common tail:

* provider ``rcstudio``: the Facebook dialog runs on a page hosted by RC Studio's relay
  (the broker) with RC Studio's Tech Provider app. Odoo opens a signup session under
  its licence key, the user scans the QR code in the popup, and Odoo polls the
  session until the broker hands over the business token and the ids. The App Secret
  never leaves the broker; deliveries are relayed to our webhook signed with a
  per-licence session secret (``webhook_secret``).
* provider ``own``: our own page runs the Facebook SDK with the number's App ID and
  Login Configuration; the page posts the auth code and the ids back, and Odoo
  exchanges the code for a token with its App Secret.

``_finish_onboarding`` then stores the credentials, registers the webhook (with the
callback override pointing at this number's URL), reads the number's details and
fires the two one-time syncs.
"""
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.http import request

from ..tools.broker import BrokerClient, BrokerError, DEFAULT_BROKER_URL
from ..tools.graph import GraphError

_logger = logging.getLogger(__name__)

# Locales the Facebook JS SDK ships; anything else falls back on the language default.
FB_LOCALES = {
    'en_US', 'en_GB', 'es_ES', 'es_LA', 'pt_BR', 'pt_PT', 'fr_FR', 'fr_CA', 'de_DE',
    'it_IT', 'nl_NL', 'pl_PL', 'tr_TR', 'ru_RU', 'ja_JP', 'ko_KR', 'zh_CN', 'zh_TW',
    'ar_AR', 'he_IL', 'id_ID', 'th_TH', 'vi_VN', 'sv_SE', 'da_DK', 'fi_FI', 'nb_NO',
    'cs_CZ', 'hu_HU', 'ro_RO', 'el_GR', 'uk_UA', 'ca_ES', 'hi_IN', 'ms_MY',
}
FB_LOCALE_BY_LANGUAGE = {
    'en': 'en_US', 'es': 'es_LA', 'pt': 'pt_BR', 'fr': 'fr_FR', 'de': 'de_DE', 'it': 'it_IT',
    'nl': 'nl_NL', 'pl': 'pl_PL', 'tr': 'tr_TR', 'ru': 'ru_RU', 'ja': 'ja_JP', 'ko': 'ko_KR',
    'zh': 'zh_CN', 'ar': 'ar_AR', 'he': 'he_IL', 'id': 'id_ID', 'th': 'th_TH', 'vi': 'vi_VN',
    'sv': 'sv_SE', 'da': 'da_DK', 'fi': 'fi_FI', 'nb': 'nb_NO', 'cs': 'cs_CZ', 'hu': 'hu_HU',
    'ro': 'ro_RO', 'el': 'el_GR', 'uk': 'uk_UA', 'ca': 'ca_ES', 'hi': 'hi_IN', 'ms': 'ms_MY',
}

SYNCS = {
    # sync_type sent to Meta -> (timestamp field, label)
    'smb_app_state_sync': ('sync_contacts_done', 'contacts'),
    'history': ('sync_history_done', 'history'),
}


class WhatsAppNumberCoexistence(models.Model):
    _inherit = 'rcs.whatsapp.number'

    signup_token = fields.Char(
        'Signup Session Token', readonly=True, copy=False, groups='rcstudio_whatsapp_coexistence.group_manager',
        help="Token of the Embedded Signup session opened on the RC Studio service. "
             "Short-lived; only needed until the connection completes.")
    signup_url = fields.Char(
        'Signup Session URL', readonly=True, copy=False, groups='rcstudio_whatsapp_coexistence.group_manager',
        help="Hosted page of the current signup session (RC Studio service).")
    coexistence_status = fields.Text(
        'Coexistence Log', readonly=True, copy=False,
        help="What happened during the last onboarding, sync or diagnosis of this number.")

    # ------------------------------------------------------------------- helpers
    def _broker(self):
        self.ensure_one()
        me = self.sudo()
        if not me.licence_key:
            raise UserError(_('Paste the RC Studio licence key on this number first.'))
        return BrokerClient(self._param('broker_url') or DEFAULT_BROKER_URL, me.licence_key)

    def _is_relayed(self):
        """True when webhooks reach us through RC Studio's relay (licence mode)."""
        self.ensure_one()
        return self.connection_mode == 'coexistence' and self.provider == 'rcstudio'

    def _onboarding_url(self, absolute=False):
        self.ensure_one()
        path = f'/rcs_whatsapp/coexistence/onboard/{self.id}/{self.sudo().webhook_token}'
        return f'{self.get_base_url()}{path}' if absolute else path

    def _request_origin(self):
        """``scheme://host[:port]`` of the browser that will open the popup: the hosted
        page only reports back to the origin declared on the session."""
        if request and request.httprequest:
            return request.httprequest.host_url.rstrip('/')
        return self.get_base_url().rstrip('/')

    @api.model
    def _fb_locale(self):
        """The Facebook SDK locale closest to the current user's language."""
        lang = (self.env.context.get('lang') or self.env.user.lang or 'en_US').replace('-', '_')
        if lang in FB_LOCALES:
            return lang
        return FB_LOCALE_BY_LANGUAGE.get(lang[:2].lower(), 'en_US')

    def _coexistence_log(self, line):
        """Prepend a timestamped line to the coexistence log (kept short)."""
        self.ensure_one()
        stamp = fields.Datetime.now().strftime('%Y-%m-%d %H:%M UTC')
        lines = [f'[{stamp}] {line}'] + (self.coexistence_status or '').splitlines()
        self.sudo().write({'coexistence_status': '\n'.join(lines[:40])})

    def _check_coexistence_ready(self):
        self.ensure_one()
        if self.connection_mode != 'coexistence':
            raise UserError(_('Set the connection mode to Coexistence first.'))
        me = self.sudo()
        if self.provider == 'rcstudio':
            if not me.licence_key:
                raise UserError(_('Paste the RC Studio licence key on this number first.'))
        elif self.provider == 'own':
            missing = [label for field, label in (
                ('app_id', 'App ID'), ('app_secret', 'App Secret'),
                ('login_config_id', 'Login Configuration ID')) if not me[field]]
            if missing:
                raise UserError(_('Your own Tech Provider app needs: %s. Your provider issues them.',
                                  ', '.join(missing)))
        else:
            raise UserError(_('Choose how the coexistence number is onboarded.'))

    # ------------------------------------------------------------- onboarding
    def action_connect_coexistence(self):
        """Open the onboarding window. In licence mode a signup session is opened on the
        broker first, so a wrong licence fails right here, in the form."""
        self.ensure_one()
        self._check_coexistence_ready()
        if self.provider == 'rcstudio':
            self._open_signup_session()
        return {
            'type': 'ir.actions.client',
            'tag': 'rcs_whatsapp.coexistence_window',
            'params': {
                'url': self._onboarding_url(),
                'number_id': self.id,
                'token': self.sudo().webhook_token,
                'provider': self.provider,
            },
        }

    def _open_signup_session(self):
        """Licence mode: open a fresh Embedded Signup session on the broker and remember
        its token and hosted URL. Returns the hosted URL (already carrying the language)."""
        self.ensure_one()
        try:
            data = self._broker().signup_session(
                origin=self._request_origin(), callback_url=self.webhook_url,
                verify_token=self.sudo().webhook_token, lang=self._fb_locale()[:2])
        except BrokerError as exc:
            raise UserError(_('Could not open the connection session: %s', exc))
        url = data['url']
        url += ('&' if '?' in url else '?') + 'lang=' + self._fb_locale()[:2]
        self.sudo().write({'signup_token': data['token'], 'signup_url': url})
        return {'url': url, 'expires_in': data.get('expires_in')}

    def coexistence_collect(self):
        """Poll the onboarding. In licence mode asks the broker for the session; once it
        is done, finishes the onboarding here. With an own app the page finishes the
        onboarding itself, so this only reports whether the number is connected.

        Returns ``{'status': pending|done|expired|none, ...}``; never raises for the
        normal "not yet" cases. Two pollers (the page and the form) can hit this at
        once: the row lock makes the second one wait and see the finished state.
        """
        self.ensure_one()
        self.env.cr.execute('SELECT id FROM rcs_whatsapp_number WHERE id = %s FOR UPDATE', (self.id,))
        self.invalidate_recordset()
        me = self.sudo()
        done = {'status': 'done', 'name': self.name, 'phone': me.display_phone or '',
                'warnings': []}
        if self.provider != 'rcstudio':
            return done if me.status == 'connected' else {'status': 'pending'}
        if not me.signup_token:
            return done if me.status == 'connected' else {'status': 'none'}
        try:
            data = self._broker().signup_status(me.signup_token)
        except BrokerError as exc:
            if exc.http_status == 404:
                me.write({'signup_token': False, 'signup_url': False})
                return {'status': 'expired'}
            raise UserError(str(exc))
        status = data.get('status')
        if status == 'expired':
            me.write({'signup_token': False, 'signup_url': False})
            return {'status': 'expired', 'last_error': data.get('last_error') or ''}
        if status != 'done':
            return {'status': 'pending', 'last_error': data.get('last_error') or ''}
        if not (data.get('access_token') and data.get('waba_id') and data.get('phone_number_id')):
            raise UserError(_('The RC Studio service returned an incomplete result: %s',
                              {k: v for k, v in data.items() if k != 'access_token'}))
        result = self._finish_onboarding(
            data['waba_id'], data['phone_number_id'], data['access_token'],
            business_id=data.get('business_id'), extra=data)
        me.write({'signup_token': False, 'signup_url': False})
        done.update(phone=me.display_phone or '', warnings=result.get('warnings') or [])
        return done

    def coexistence_finish_with_code(self, code, waba_id, phone_number_id, business_id=None):
        """Own app: exchange the Embedded Signup code with our App Secret, then finish."""
        self.ensure_one()
        if self.provider != 'own':
            raise UserError(_('This number is onboarded through the RC Studio service; '
                              'the code exchange does not apply.'))
        if not (code and waba_id and phone_number_id):
            raise UserError(_("Meta's dialog did not return the code, the WhatsApp Business "
                              "Account ID and the Phone Number ID. Try again and finish the dialog."))
        try:
            token = self._graph().exchange_code(code)
        except GraphError as exc:
            raise UserError(_('Meta refused to exchange the code for a token: %s', exc))
        if not token:
            raise UserError(_('Meta did not return an access token.'))
        return self._finish_onboarding(waba_id, phone_number_id, token, business_id=business_id)

    def _finish_onboarding(self, waba_id, phone_number_id, token, business_id=None, extra=None):
        """Common tail of both flows. Writes the credentials first, then does the
        best-effort steps; problems after that point are recorded on the number and
        returned as warnings, never raised (raising would roll the credentials back).
        """
        self.ensure_one()
        extra = extra or {}
        me = self.sudo()
        vals = {
            'waba_id': waba_id,
            'phone_number_id': phone_number_id,
            'access_token': token,
            'status': 'connected',
            'status_message': False,
            'webhook_subscribed': False,
            # A new onboarding gets a new pair of one-time syncs.
            'sync_contacts_done': False,
            'sync_history_done': False,
            'signup_token': False,
            'signup_url': False,
        }
        if self._is_relayed():
            # The token belongs to RC Studio's Tech Provider app: every later call that
            # names an app (sample uploads for templates, token debugging) must use that
            # id, whatever the record carried before, demo values included.
            app_id = extra.get('app_id')
            if not app_id:
                try:
                    config = self._broker().app_config() or {}
                    app_id = config.get('app_id') or config.get('appId')
                except BrokerError as exc:
                    _logger.warning('Coexistence: could not read the relay app id: %s', exc)
            if app_id:
                vals['app_id'] = str(app_id)
        elif extra.get('app_id') and not me.app_id:
            vals['app_id'] = extra['app_id']
        if self._is_relayed() and extra.get('webhook_secret'):
            vals['webhook_secret'] = extra['webhook_secret']
        me.write(vals)
        self._coexistence_log(f'Onboarding completed: WABA {waba_id}, phone number id {phone_number_id}'
                              + (f', business {business_id}' if business_id else '') + '.')
        warnings = []

        # 1. Webhook. Registering it makes Meta verify the callback URL within seconds, and
        # this request is holding an HTTP worker: on a small instance Meta's knock can queue
        # behind it and time out. So it is handed to the cron thread, which runs at once and
        # leaves every worker free to answer Meta.
        if self._queue_webhook_subscription():
            self._coexistence_log(f'Webhook registration queued for {self.webhook_url}.')
        else:
            try:
                self._coexistence_subscribe()
                self._coexistence_log(f'Webhook registered on {self.webhook_url}.')
            except (BrokerError, GraphError) as exc:
                message = (f'The webhook could not be registered: {exc}. '
                           'Press "Subscribe webhook" to retry.')
                warnings.append(message)
                me.write({'status': 'error', 'status_message': message})
                self._coexistence_log(message)

        # 2. What Meta says about the number.
        try:
            info = self._coexistence_fetch_info()
            if not info.get('is_on_biz_app'):
                warnings.append('Meta reports the number is NOT linked to the WhatsApp Business app '
                                f'(platform {info.get("platform_type") or "?"}); the syncs will fail. '
                                'Check that the Login Configuration is the coexistence one and connect again.')
        except GraphError as exc:
            warnings.append(f'Could not read the number details from Meta: {exc}')
            _logger.warning('Coexistence: phone_info failed for number %s: %s', self.id, exc)

        # 3. One-time syncs (24-hour window, once per onboarding). Non-fatal.
        for sync_type in SYNCS:
            try:
                self._coexistence_sync(sync_type)
            except (BrokerError, GraphError, UserError) as exc:
                label = SYNCS[sync_type][1]
                warnings.append(f'The {label} sync failed: {self._sync_error_hint(exc)} '
                                f'Once the cause is fixed, retry it from the number ("Sync {label}").')
        for line in warnings:
            self._coexistence_log(line)
        return {'ok': True, 'warnings': warnings}

    # ------------------------------------------------------------------ webhook
    def _subscribe_webhook_now(self):
        """What the scheduler runs. In licence mode it goes through the service and, when
        it works, clears the error the failed registration left on the number."""
        self.ensure_one()
        if not self._is_relayed():
            return super()._subscribe_webhook_now()
        self._coexistence_subscribe()
        if self.status == 'error':
            self._set_status('connected')
        self._coexistence_log(f'Webhook registered on {self.webhook_url}.')

    def _coexistence_subscribe(self):
        """Register this number's callback on the WABA. Licence mode goes through the
        relay, which also hands back the secret it will sign deliveries with."""
        self.ensure_one()
        me = self.sudo()
        if self._is_relayed():
            # Always direct: none of the customer's traffic should pass through RC Studio.
            # If the service cannot do it (an older service, or an administrator holding this
            # number on the relay), it answers with the mode it did register and the module
            # keeps checking the relay signature, which is the legacy path.
            data = self._broker().subscribe(me.waba_id, self.webhook_url, me.webhook_token, mode='direct')
            granted = data.get('mode') or 'relay'
            vals = {'webhook_subscribed': True, 'webhook_delivery': granted}
            if granted != 'direct':
                _logger.warning('Coexistence: asked for direct delivery on number %s, the service '
                                'registered %s; staying on the relay.', self.id, granted)
            if data.get('webhook_secret'):
                vals['webhook_secret'] = data['webhook_secret']
            elif not me.webhook_secret and granted != 'direct':
                _logger.warning('Coexistence: the relay returned no webhook secret for number %s', self.id)
            me.write(vals)
            self._coexistence_log(
                'Webhooks registered in %s mode on %s.'
                % ('direct' if granted == 'direct' else 'relay', self.webhook_url))
            return data
        self._graph().subscribe_app(self.webhook_url, me.webhook_token)
        me.write({'webhook_subscribed': True})
        return True

    def action_subscribe_webhook(self):
        """In licence mode the subscription goes through the RC Studio service, which is
        what registers the URL with Meta.

        It is handed to the scheduler rather than done here: registering makes Meta verify
        the callback URL within six seconds, and doing it inside this request would hold
        the worker serving the click while Meta knocks on the door. An Odoo with a single
        worker would then have nobody left to answer and the verification would always
        time out.
        """
        self.ensure_one()
        if not self._is_relayed():
            return super().action_subscribe_webhook()
        if self._queue_webhook_subscription():
            return self._toast(_('Registering the webhook. It takes a few seconds; the '
                                 'Webhook tab shows it as soon as Meta confirms.'))
        try:
            self._coexistence_subscribe()
        except BrokerError as exc:
            raise UserError(_('The webhook subscription failed: %s', exc))
        if self.status == 'error':
            self._set_status('connected')
        self._coexistence_log(f'Webhook registered on {self.webhook_url}.')
        return self._toast(_('Meta will deliver this number’s events to %s', self.webhook_url))

    # ------------------------------------------------------------------- number
    def _coexistence_fetch_info(self):
        """phone_info into the number fields. Returns Meta's raw answer."""
        self.ensure_one()
        info = self._graph().phone_info()
        self.sudo().write({
            'display_phone': info.get('display_phone_number') or self.sudo().display_phone,
            'verified_name': info.get('verified_name') or self.sudo().verified_name,
            'quality_rating': info.get('quality_rating'),
            'platform_type': info.get('platform_type'),
            'is_on_biz_app': bool(info.get('is_on_biz_app')),
            'last_check': fields.Datetime.now(),
        })
        return info

    # -------------------------------------------------------------------- syncs
    def _coexistence_sync(self, sync_type):
        """Meta allows each sync ONCE per onboarding: running it again needs a new
        Embedded Signup, so a repeat only burns app quota (error #4)."""
        self.ensure_one()
        field, label = SYNCS[sync_type]
        me = self.sudo()
        if me[field]:
            raise UserError(_(
                'The %(label)s sync already ran on %(date)s. Meta allows one per onboarding: '
                'to run it again, disconnect the number and connect it again.',
                label=label, date=fields.Datetime.to_string(me[field])))
        if self._is_relayed():
            self._broker().sync(me.phone_number_id, sync_type)
        else:
            self._graph().request_sync(sync_type)
        me.write({field: fields.Datetime.now()})
        self._coexistence_log(f'Requested the {label} sync from Meta.')

    def _action_sync(self, sync_type):
        self.ensure_one()
        label = SYNCS[sync_type][1]
        try:
            self._coexistence_sync(sync_type)
        except BrokerError as exc:
            self._coexistence_log(f'The {label} sync failed: {exc}')
            raise UserError(_('The RC Studio service did not run the %(label)s sync: %(error)s',
                              label=label, error=exc))
        except GraphError as exc:
            self._coexistence_log(f'The {label} sync failed: {exc}')
            raise UserError(_('Meta refused the %(label)s sync: %(error)s',
                              label=label, error=self._sync_error_hint(exc)))
        return self._toast(_('Meta will send the %s over the webhook during the next minutes.', label))

    def _sync_error_hint(self, exc):
        """The refusal of a sync, explained by Meta's error code. Error #135000 on this
        endpoint is not the phone: Meta grants the two syncs once per onboarding, and a
        number that was still connected when the Embedded Signup was run again gets no
        new pair. The relay explains its own refusals, so those pass through as they are."""
        code = getattr(exc, 'code', None)
        if code == 135000:
            return _(
                '%(error)s. Meta grants the contacts and history syncs once per onboarding, and a '
                'number that was still connected to the Cloud API when it was connected again does '
                'not get a new pair. To sync again: on the phone, open WhatsApp Business > Settings > '
                'Account > Business Platform > Disconnect Account, then connect the number again from '
                'Odoo and accept the confirmation pop-up the app shows (keep it open and online; if '
                'the pop-up was dismissed, restart the app). If the number had never been connected '
                'before, check the phone as described and that the history, smb_app_state_sync and '
                'smb_message_echoes webhooks are subscribed on the app.', error=exc)
        if code == 4:
            return _(
                "%(error)s. On the sync endpoint this is Meta's per-number throttle, not your app's "
                'call quota: it happens when the same number was connected again shortly after a '
                'previous onboarding. Wait a while (Meta suggests up to 24 hours) and retry from the '
                "number's form.", error=exc)
        return f'{exc}.'

    def action_sync_contacts(self):
        return self._action_sync('smb_app_state_sync')

    def action_sync_history(self):
        return self._action_sync('history')

    # ----------------------------------------------------------------- diagnose
    def action_diagnose(self):
        """One look that tells whether the number really is in coexistence and whether
        Meta (or the relay) delivers to this number's webhook."""
        self.ensure_one()
        me = self.sudo()
        try:
            info = self._coexistence_fetch_info()
        except GraphError as exc:
            self._coexistence_log(f'Diagnosis: Meta rejected the credentials: {exc}')
            raise UserError(_('Meta rejected the credentials of this number: %s', exc))
        parts = [
            f'Number {info.get("display_phone_number") or "?"} ({info.get("verified_name") or "?"}), '
            f'platform {info.get("platform_type") or "?"}, quality {info.get("quality_rating") or "?"}.',
        ]
        healthy = True
        if info.get('is_on_biz_app'):
            parts.append('Linked to the WhatsApp Business app: yes.')
        else:
            healthy = False
            parts.append('Linked to the WhatsApp Business app: NO. The Embedded Signup did not '
                         'link the phone; the syncs will fail. Connect again with the coexistence '
                         'Login Configuration.')
        try:
            apps = (self._graph().subscribed_apps() or {}).get('data') or []
        except GraphError as exc:
            apps = None
            healthy = False
            parts.append(f'Could not read the webhook subscriptions: {exc}')
        if apps is not None:
            if not apps:
                healthy = False
                parts.append('Webhook: no app is subscribed to this WhatsApp Business Account.')
            for app in apps:
                data = app.get('whatsapp_business_api_data') or {}
                override = app.get('override_callback_uri')
                line = f'Webhook: app {data.get("name") or "?"} ({data.get("id") or "?"}) subscribed'
                if override:
                    line += f', callback override {override}'
                    # In relay delivery the override is the service's own URL. In every
                    # other case it has to be this number's URL: if it is not, Meta is
                    # delivering somewhere else - typically another Odoo connected to the
                    # same WhatsApp Business Account, because Meta keeps one callback
                    # override per app and business account, and the last registration wins.
                    relayed_here = self._is_relayed() and me.webhook_delivery == 'relay'
                    if not relayed_here and override != self.webhook_url:
                        healthy = False
                        line += (' - which is NOT this Odoo. Meta keeps one callback per business '
                                 'account, so another instance registered it last and is receiving '
                                 'the messages. Press "Subscribe webhook" here to take it back')
                parts.append(line + '.')
        if self._is_relayed() and me.webhook_delivery == 'direct':
            parts.append('Deliveries come straight from Meta to this Odoo; nothing passes through '
                         'RC Studio. Meta signs with an App Secret this database does not hold, so '
                         'the signature is not checked: the token in the webhook URL is what guards it.')
            try:
                service = (self._broker().status() or {}).get('webhook_modes') or {}
            except BrokerError as exc:
                parts.append(f'Could not ask the RC Studio service which mode it registered: {exc}')
            else:
                registered = service.get(me.phone_number_id)
                if registered and registered != 'direct':
                    healthy = False
                    parts.append(f'The RC Studio service still has this number in {registered} mode: '
                                 'press "Subscribe webhook" to register the direct URL again.')
                elif registered:
                    parts.append('The RC Studio service confirms direct mode.')
        elif self._is_relayed():
            parts.append('Deliveries come through the RC Studio relay, signed with the session secret: '
                         + ('present.' if me.webhook_secret else 'MISSING (press "Subscribe webhook").'))
            healthy = healthy and bool(me.webhook_secret)
        if me.webhook_last_event:
            parts.append(f'Last webhook event received {fields.Datetime.to_string(me.webhook_last_event)} UTC.')
        else:
            parts.append('No webhook event received yet.')
        summary = ' '.join(parts)
        self._coexistence_log('Diagnosis: ' + summary)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Coexistence diagnosis'),
                'message': summary,
                'type': 'success' if healthy else 'warning',
                'sticky': True,
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }

    # --------------------------------------------------------------- disconnect
    def action_disconnect(self):
        """Unsubscribe the app from the WABA and forget the credentials. Meta's side keeps
        the number in coexistence until the user offboards it from the phone app; a new
        onboarding from here is a new Embedded Signup."""
        self.ensure_one()
        me = self.sudo()
        note = 'Disconnected.'
        if me.waba_id and me.access_token:
            try:
                self._graph().unsubscribe_app()
                note = 'Disconnected; the app was unsubscribed from the WhatsApp Business Account.'
            except GraphError as exc:
                note = f'Disconnected; Meta refused to unsubscribe the app ({exc}).'
                _logger.warning('Coexistence: unsubscribe failed for number %s: %s', self.id, exc)
        me.write({
            'access_token': False,
            'waba_id': False,
            'phone_number_id': False,
            'webhook_secret': False,
            'webhook_subscribed': False,
            'signup_token': False,
            'signup_url': False,
            'is_on_biz_app': False,
            'status': 'draft',
            'status_message': False,
        })
        self._coexistence_log(note)
        return self._toast(_('The number is disconnected. Connect it again whenever you want.'), 'info')
