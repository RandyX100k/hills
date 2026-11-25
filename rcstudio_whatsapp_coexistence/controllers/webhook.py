import hashlib
import hmac
import json
import logging

from odoo import http
from odoo.http import request
from odoo.tools import consteq

_logger = logging.getLogger(__name__)


class WhatsAppWebhook(http.Controller):
    """Meta's webhook for one business number.

    Verification (GET) checks the token in the URL against hub.verify_token. Delivery
    (POST) checks the payload signature, then hands the payload to the number. It
    always answers 200 once the request is authenticated: Meta does not retry after a
    200, so processing errors are logged, never surfaced.
    """

    @http.route('/rcs_whatsapp/webhook/<int:number_id>/<string:token>', type='http',
                auth='public', methods=['GET'], csrf=False)
    def verify(self, number_id, token, **kw):
        number = request.env['rcs.whatsapp.number']._webhook_find(number_id, token)
        if not number or kw.get('hub.mode') != 'subscribe' or kw.get('hub.verify_token') != token:
            return request.make_response('Forbidden', status=403)
        return request.make_response(kw.get('hub.challenge') or '', headers=[('Content-Type', 'text/plain')])

    @http.route('/rcs_whatsapp/webhook/<int:number_id>/<string:token>', type='http',
                auth='public', methods=['POST'], csrf=False)
    def receive(self, number_id, token, **kw):
        number = request.env['rcs.whatsapp.number']._webhook_find(number_id, token)
        if not number:
            return request.make_response('Forbidden', status=403)
        raw = request.httprequest.get_data()
        if not self._signature_ok(number, raw):
            _logger.warning('WhatsApp webhook: bad signature on number %s', number.id)
            return request.make_response('Forbidden', status=403)
        try:
            payload = json.loads(raw or b'{}')
        except ValueError:
            return request.make_response('Bad Request', status=400)
        if payload.get('object') not in ('whatsapp_business_account', None):
            return request.make_response('OK')
        number._webhook_dispatch(payload)
        return request.make_response('OK')

    def _signature_ok(self, number, raw):
        """X-Hub-Signature-256 = sha256=HMAC(app_secret, body). In RC Studio's relay mode
        the relay signs with the session secret instead (X-RCStudio-Signature)."""
        headers = request.httprequest.headers
        if number._webhook_unsigned():
            # Meta signs with the App Secret of RC Studio's Tech Provider app, which is not
            # here and is not going to be: the token in the URL is what authenticates the
            # request, and _webhook_dispatch drops anything not addressed to this number.
            return True
        secret = number.sudo()._webhook_secret()
        if not secret:
            # Nothing to verify against (a number still being set up): the URL token is
            # the only guard. Log it so it is visible in the server log.
            _logger.info('WhatsApp webhook: number %s has no secret yet, accepting on token only', number.id)
            return True
        header = headers.get('X-RCStudio-Signature') or headers.get('X-Hub-Signature-256') or ''
        if not header.startswith('sha256='):
            return False
        expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        return consteq(header[7:], expected)
