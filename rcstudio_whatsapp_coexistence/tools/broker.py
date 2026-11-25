# Client for RC Studio's coexistence relay ("broker").
#
# In licence mode the Embedded Signup runs on a page hosted by the broker with RC
# Studio's Tech Provider app, so the App Secret never reaches the customer's server.
# Odoo only talks to the broker under its licence key: it opens a signup session, polls
# it until the broker has the business token, and asks the broker to register the
# webhook (Meta signs deliveries with the App Secret; the broker validates that and
# forwards each delivery to Odoo re-signed with a per-licence session secret).
#
# Endpoints, all under {base_url}/v1/, authenticated with "Authorization: Bearer <licence>":
#   GET  app-config                 -> {app_id, config_id, graph_version}
#   POST signup/session             -> {url, token, expires_in}
#   GET  signup/session/<token>     -> {status: pending|done|expired, last_error,
#                                       access_token, waba_id, phone_number_id, business_id}
#   POST exchange                   -> {access_token}
#   POST subscribe                  -> {webhook_secret}
#   GET  number/<phone_number_id>   -> phone details as Meta returns them
#   POST sync                       -> Meta's answer to smb_app_data

import logging

import requests

_logger = logging.getLogger(__name__)

DEFAULT_BROKER_URL = 'https://coexistence.rcstudio.dev'
TIMEOUT = 60


class BrokerError(Exception):
    """A non-2xx answer from the broker, or a transport failure talking to it."""

    def __init__(self, message, http_status=None):
        super().__init__(message)
        self.message = message
        self.http_status = http_status

    def __str__(self):
        return self.message


class BrokerClient:
    """One instance per business number: holds the licence key that scopes every call."""

    def __init__(self, base_url, licence_key, timeout=TIMEOUT):
        self.base_url = (base_url or DEFAULT_BROKER_URL).rstrip('/')
        self.licence_key = licence_key
        self.timeout = timeout

    # ------------------------------------------------------------------ transport
    def _call(self, method, path, json=None, params=None):
        if not self.licence_key:
            raise BrokerError('The RC Studio licence key is missing on this number.')
        url = f"{self.base_url}/v1/{path.lstrip('/')}"
        headers = {'Authorization': f'Bearer {self.licence_key}'}
        try:
            response = requests.request(method, url, headers=headers, params=params,
                                        json=json, timeout=self.timeout)
        except requests.RequestException as exc:
            raise BrokerError(f'Could not reach the RC Studio service: {exc}') from exc
        try:
            payload = response.json()
        except ValueError:
            payload = {'error': (response.text or '').strip()[:300]}
        if not response.ok:
            message = payload.get('error') if isinstance(payload, dict) else None
            if response.status_code in (401, 403) and not message:
                message = 'the licence key was rejected'
            _logger.warning('Broker %s %s -> %s: %s', method, path, response.status_code, message or payload)
            raise BrokerError(f'RC Studio service: {message or f"HTTP {response.status_code}"}',
                              http_status=response.status_code)
        return payload if isinstance(payload, dict) else {}

    # ------------------------------------------------------------------ endpoints
    def app_config(self):
        """App ID / Login Configuration of RC Studio's Tech Provider app."""
        return self._call('GET', 'app-config')

    def signup_session(self, origin, callback_url=None, verify_token=None, lang='en'):
        """Open an Embedded Signup session for the Odoo that will show the popup.

        ``origin`` is the ``scheme://host[:port]`` of the page that opens the popup: the
        hosted page only reports back to that origin. Returns ``url`` (the hosted page to
        open), ``token`` (to poll with) and ``expires_in`` (seconds).
        """
        payload = {'origin': origin, 'lang': lang}
        if callback_url:
            payload['callback_url'] = callback_url
        if verify_token:
            payload['verify_token'] = verify_token
        data = self._call('POST', 'signup/session', json=payload)
        if not (data.get('url') and data.get('token')):
            raise BrokerError(f'The RC Studio service did not return a signup session: {data}')
        return data

    def signup_status(self, token):
        """Poll a session: ``status`` is pending, done or expired. When done, the business
        token and the ids the user picked in Meta's dialog come with it."""
        return self._call('GET', f'signup/session/{token}')

    def exchange(self, code, waba_id=None, phone_number_id=None, business_id=None):
        """Turn an Embedded Signup code into a business token, on the broker's app."""
        data = self._call('POST', 'exchange', json={
            'code': code, 'waba_id': waba_id or '', 'phone_number_id': phone_number_id or '',
            'business_id': business_id or '',
        })
        if not data.get('access_token'):
            raise BrokerError(f'The RC Studio service did not return an access token: {data}')
        return data['access_token']

    def subscribe(self, waba_id, callback_url, verify_token, mode=None):
        """Register the webhook for this WABA through the service.

        ``mode='relay'`` (the default on the service) keeps Meta delivering to RC Studio,
        which checks the signature and forwards the delivery signed with ``webhook_secret``.
        ``mode='direct'`` registers this Odoo's URL with Meta instead, so no message ever
        passes through RC Studio; the service first calls the URL itself to check the
        handshake answers, and refuses the change if it does not.
        """
        payload = {'waba_id': waba_id, 'callback_url': callback_url, 'verify_token': verify_token}
        if mode:
            payload['mode'] = mode
        return self._call('POST', 'subscribe', json=payload)

    def status(self):
        """What the service knows about this licence: numbers, quota and, per number,
        whether its webhooks go through the relay or straight from Meta."""
        return self._call('GET', 'status')

    def number_info(self, phone_number_id):
        return self._call('GET', f'number/{phone_number_id}')

    def sync(self, phone_number_id, sync_type):
        """Ask Meta for the one-time coexistence sync; the broker enforces the
        one-per-onboarding rule and the cooldown so a retry does not burn app quota."""
        return self._call('POST', 'sync', json={
            'phone_number_id': phone_number_id, 'sync_type': sync_type,
        })
