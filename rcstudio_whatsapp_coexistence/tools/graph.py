# Thin client for Meta's Graph API as used by the WhatsApp Cloud API.
#
# Written from Meta's public reference:
#   https://developers.facebook.com/docs/whatsapp/cloud-api/reference
#   https://developers.facebook.com/docs/graph-api/guides/error-handling
# Every method maps to one documented endpoint; the payload shapes are Meta's.

import logging

import requests

_logger = logging.getLogger(__name__)

DEFAULT_VERSION = 'v22.0'
BASE_URL = 'https://graph.facebook.com'
TIMEOUT = 30
MAX_DOWNLOAD = 100 * 1024 * 1024  # WhatsApp caps media at 100 MB

# Error codes Meta documents as transient (rate limits and throttling). A message that
# fails with one of these is worth sending again later; anything else is final.
#   4       application request limit reached
#   80007   WhatsApp Business Account rate limit
#   130429  cloud API throughput limit
#   131056  too many messages to the same recipient in a short time
#   133016  registration attempts limit
TRANSIENT_CODES = frozenset({4, 80007, 130429, 131056, 133016})


class GraphError(Exception):
    """A non-2xx answer from Graph, or a transport failure talking to it."""

    def __init__(self, message, code=None, subcode=None, http_status=None,
                 fbtrace_id=None, user_message=None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.subcode = subcode
        self.http_status = http_status
        self.fbtrace_id = fbtrace_id
        self.user_message = user_message

    @property
    def transient(self):
        return self.code in TRANSIENT_CODES or (self.http_status or 0) in (429, 500, 502, 503, 504)

    def __str__(self):
        if self.code is not None:
            return f'[{self.code}] {self.user_message or self.message}'
        return self.message


class GraphClient:
    """One instance per business number. Holds the token and the ids Meta needs."""

    def __init__(self, token, phone_number_id=None, waba_id=None, app_id=None,
                 app_secret=None, version=DEFAULT_VERSION, timeout=TIMEOUT):
        self.token = token
        self.phone_number_id = phone_number_id
        self.waba_id = waba_id
        self.app_id = app_id
        self.app_secret = app_secret
        self.version = version or DEFAULT_VERSION
        self.timeout = timeout

    # ------------------------------------------------------------------ transport
    def _url(self, path):
        return f"{BASE_URL}/{self.version}/{path.lstrip('/')}"

    def _call(self, method, path, params=None, json=None, data=None, files=None,
              token=None, raw=False):
        headers = {}
        bearer = self.token if token is None else token
        if bearer:
            headers['Authorization'] = f'Bearer {bearer}'
        # Meta localises error_user_msg to the token owner's language; the module is
        # English and translates its own strings, so ask for English explicitly.
        params = dict(params or {})
        params.setdefault('locale', 'en_US')
        try:
            response = requests.request(
                method, self._url(path), params=params, json=json, data=data,
                files=files, headers=headers, timeout=self.timeout, stream=raw)
        except requests.RequestException as exc:
            raise GraphError(f'Could not reach Meta: {exc}') from exc
        if raw:
            if not response.ok:
                raise GraphError(f'Download failed with HTTP {response.status_code}',
                                 http_status=response.status_code)
            return response
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if not response.ok or (isinstance(payload, dict) and 'error' in payload):
            err = (payload or {}).get('error') or {}
            raise GraphError(
                err.get('message') or f'HTTP {response.status_code}',
                code=err.get('code'), subcode=err.get('error_subcode'),
                http_status=response.status_code, fbtrace_id=err.get('fbtrace_id'),
                user_message=err.get('error_user_msg'))
        return payload

    def _need(self, attr):
        if not getattr(self, attr):
            raise GraphError(f'The business number has no {attr} configured')

    # ------------------------------------------------------------------ messages
    def send(self, payload):
        """POST /{phone_number_id}/messages. Returns Meta's message id."""
        self._need('phone_number_id')
        body = dict(payload, messaging_product='whatsapp', recipient_type='individual')
        res = self._call('POST', f'{self.phone_number_id}/messages', json=body)
        messages = res.get('messages') or []
        return messages[0].get('id') if messages else None

    def mark_read(self, wa_message_id):
        self._need('phone_number_id')
        self._call('POST', f'{self.phone_number_id}/messages', json={
            'messaging_product': 'whatsapp', 'status': 'read', 'message_id': wa_message_id,
        })

    # --------------------------------------------------------------------- media
    def upload_media(self, content, mime_type, filename='file'):
        """POST /{phone_number_id}/media (multipart). Returns the media id."""
        self._need('phone_number_id')
        res = self._call('POST', f'{self.phone_number_id}/media',
                         data={'messaging_product': 'whatsapp', 'type': mime_type},
                         files={'file': (filename, content, mime_type)})
        return res.get('id')

    def media_info(self, media_id):
        """GET /{media_id} -> url, mime_type, sha256, file_size."""
        return self._call('GET', media_id)

    def download(self, url):
        """The media url returned by media_info, fetched with the bearer token."""
        response = self._call('GET', url.replace(f'{BASE_URL}/{self.version}/', ''), raw=True) \
            if url.startswith(BASE_URL) else self._download_absolute(url)
        return response

    def _download_absolute(self, url):
        try:
            response = requests.get(url, headers={'Authorization': f'Bearer {self.token}'},
                                    timeout=self.timeout, stream=True)
        except requests.RequestException as exc:
            raise GraphError(f'Could not download media: {exc}') from exc
        if not response.ok:
            raise GraphError(f'Download failed with HTTP {response.status_code}',
                             http_status=response.status_code)
        return response

    def fetch_media(self, media_id):
        """Convenience: media_info + download. Returns (bytes, mime_type)."""
        info = self.media_info(media_id)
        length = int(info.get('file_size') or 0)
        if length > MAX_DOWNLOAD:
            raise GraphError('Media larger than the 100 MB WhatsApp limit')
        response = self._download_absolute(info['url'])
        chunks, total = [], 0
        for chunk in response.iter_content(chunk_size=1 << 16):
            total += len(chunk)
            if total > MAX_DOWNLOAD:
                raise GraphError('Media larger than the 100 MB WhatsApp limit')
            chunks.append(chunk)
        return b''.join(chunks), info.get('mime_type')

    # ------------------------------------------------------------------- number
    PHONE_FIELDS = 'verified_name,display_phone_number,quality_rating,platform_type,is_on_biz_app,code_verification_status'

    def phone_info(self, fields=PHONE_FIELDS):
        self._need('phone_number_id')
        return self._call('GET', self.phone_number_id, params={'fields': fields})

    # ---------------------------------------------------------------- templates
    TEMPLATE_FIELDS = 'id,name,language,category,status,components,rejected_reason,quality_score'

    def list_templates(self, limit=100):
        """GET /{waba_id}/message_templates, following Meta's paging cursor."""
        self._need('waba_id')
        out, after = [], None
        while True:
            params = {'fields': self.TEMPLATE_FIELDS, 'limit': limit}
            if after:
                params['after'] = after
            res = self._call('GET', f'{self.waba_id}/message_templates', params=params)
            out.extend(res.get('data') or [])
            after = ((res.get('paging') or {}).get('cursors') or {}).get('after')
            if not after or not (res.get('paging') or {}).get('next'):
                break
        return out

    def create_template(self, name, language, category, components):
        self._need('waba_id')
        return self._call('POST', f'{self.waba_id}/message_templates', json={
            'name': name, 'language': language, 'category': category,
            'components': components,
        })

    def delete_template(self, name):
        self._need('waba_id')
        return self._call('DELETE', f'{self.waba_id}/message_templates', params={'name': name})

    # ------------------------------------------------------------------ webhook
    def subscribe_app(self, callback_url=None, verify_token=None):
        """POST /{waba_id}/subscribed_apps, optionally overriding the callback per WABA."""
        self._need('waba_id')
        body = {}
        if callback_url:
            body = {'override_callback_uri': callback_url, 'verify_token': verify_token}
        return self._call('POST', f'{self.waba_id}/subscribed_apps', json=body or None)

    def subscribed_apps(self):
        self._need('waba_id')
        return self._call('GET', f'{self.waba_id}/subscribed_apps')

    def unsubscribe_app(self):
        self._need('waba_id')
        return self._call('DELETE', f'{self.waba_id}/subscribed_apps')


    # ------------------------------------------------------------- uploads
    def upload_sample(self, content, mime_type, filename='sample'):
        """Resumable Upload API: the sample file Meta wants for media-header templates.
        Returns the handle to put in the header component's example."""
        self._need('app_id')
        session = self._call('POST', f'{self.app_id}/uploads', params={
            'file_name': filename, 'file_length': len(content), 'file_type': mime_type})
        upload_id = session.get('id')
        if not upload_id:
            raise GraphError('Meta did not open an upload session')
        try:
            response = requests.post(
                self._url(upload_id), data=content, timeout=self.timeout,
                headers={'Authorization': f'OAuth {self.token}', 'file_offset': '0',
                         'Content-Type': 'application/octet-stream'})
        except requests.RequestException as exc:
            raise GraphError(f'Could not upload the sample to Meta: {exc}') from exc
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if not response.ok or 'h' not in payload:
            err = payload.get('error') or {}
            raise GraphError(err.get('message') or f'Upload failed with HTTP {response.status_code}',
                             code=err.get('code'), http_status=response.status_code,
                             user_message=err.get('error_user_msg'))
        return payload['h']

    # -------------------------------------------------------------- coexistence
    def request_sync(self, sync_type):
        """POST /{phone_number_id}/smb_app_data; sync_type is 'history' or 'smb_app_state_sync'."""
        self._need('phone_number_id')
        return self._call('POST', f'{self.phone_number_id}/smb_app_data', json={
            'messaging_product': 'whatsapp', 'sync_type': sync_type,
        })

    def exchange_code(self, code):
        """Embedded Signup: turn the authorization code into a business token."""
        self._need('app_id')
        self._need('app_secret')
        res = self._call('GET', 'oauth/access_token', params={
            'client_id': self.app_id, 'client_secret': self.app_secret, 'code': code,
        }, token='')
        return res.get('access_token')

    def debug_token(self, token):
        self._need('app_id')
        self._need('app_secret')
        return self._call('GET', 'debug_token', params={'input_token': token},
                          token=f'{self.app_id}|{self.app_secret}')
