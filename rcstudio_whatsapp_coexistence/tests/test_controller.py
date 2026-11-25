"""The webhook endpoint over HTTP: verification handshake and signed deliveries."""
import hashlib
import hmac
import json
import time

from odoo.tests import tagged

from .common import CUSTOMER_WA_ID, TAGS, WhatsAppHttpCase


@tagged(*TAGS)
class TestWebhookController(WhatsAppHttpCase):

    def _url(self, number=None, token=None):
        number = number or self.number
        return f'/rcs_whatsapp/webhook/{number.id}/{token or number.webhook_token}'

    def _sign(self, body, secret):
        return 'sha256=' + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    def test_verify_handshake(self):
        res = self.url_open(self._url() + f'?hub.mode=subscribe&hub.verify_token={self.number.webhook_token}&hub.challenge=abc123')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.text, 'abc123')

    def test_verify_wrong_token(self):
        res = self.url_open(self._url(token='nope') + '?hub.mode=subscribe&hub.verify_token=nope&hub.challenge=x')
        self.assertEqual(res.status_code, 403)
        res = self.url_open(self._url() + '?hub.mode=subscribe&hub.verify_token=other&hub.challenge=x')
        self.assertEqual(res.status_code, 403)

    def _payload(self, wa_message_id):
        return self._text_payload(self.number, CUSTOMER_WA_ID, 'hello over http', wa_message_id, ts=int(time.time()))

    def test_signed_delivery_is_processed(self):
        body = json.dumps(self._payload('wamid.http.ok')).encode()
        res = self.url_open(self._url(), data=body, headers={
            'Content-Type': 'application/json',
            'X-Hub-Signature-256': self._sign(body, self.number.app_secret)})
        self.assertEqual(res.status_code, 200)
        message = self.env['rcs.whatsapp.message'].search([('wa_message_id', '=', 'wamid.http.ok')])
        self.assertTrue(message)
        self.assertEqual(message.body, 'hello over http')
        self.assertEqual(message.conversation_id.number_id, self.number)

    def test_bad_signature_is_rejected(self):
        body = json.dumps(self._payload('wamid.http.bad')).encode()
        res = self.url_open(self._url(), data=body, headers={
            'Content-Type': 'application/json',
            'X-Hub-Signature-256': self._sign(body, 'wrong-secret')})
        self.assertEqual(res.status_code, 403)
        self.assertFalse(self.env['rcs.whatsapp.message'].search([('wa_message_id', '=', 'wamid.http.bad')]))

    def test_missing_signature_is_rejected(self):
        body = json.dumps(self._payload('wamid.http.none')).encode()
        res = self.url_open(self._url(), data=body, headers={'Content-Type': 'application/json'})
        self.assertEqual(res.status_code, 403)

    def test_relay_signature_accepted(self):
        relayed = self._make_number(name='Relayed', connection_mode='coexistence', provider='rcstudio',
                                    app_secret=False, webhook_secret='relay-secret',
                                    webhook_delivery='relay')
        body = json.dumps(self._text_payload(relayed, CUSTOMER_WA_ID, 'via relay', 'wamid.http.relay')).encode()
        res = self.url_open(self._url(relayed), data=body, headers={
            'Content-Type': 'application/json',
            'X-RCStudio-Signature': self._sign(body, 'relay-secret')})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(self.env['rcs.whatsapp.message'].search([('wa_message_id', '=', 'wamid.http.relay')]))

    def test_unknown_number_is_forbidden(self):
        res = self.url_open('/rcs_whatsapp/webhook/999999/token', data=b'{}', headers={'Content-Type': 'application/json'})
        self.assertEqual(res.status_code, 403)

    def test_direct_delivery_accepts_an_unsigned_post(self):
        """Meta signs with the App Secret of RC Studio's app, which is not in this Odoo.
        The token in the URL is what authenticates the delivery."""
        self.number.write({'connection_mode': 'coexistence', 'provider': 'rcstudio',
                           'app_secret': False})
        body = json.dumps(self._payload('wamid.direct.ok')).encode()
        res = self.url_open(self._url(), data=body, headers={'Content-Type': 'application/json'})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(self.env['rcs.whatsapp.message'].search([('wa_message_id', '=', 'wamid.direct.ok')]))

    def test_direct_delivery_still_needs_the_right_token(self):
        self.number.write({'connection_mode': 'coexistence', 'provider': 'rcstudio',
                           'app_secret': False})
        body = json.dumps(self._payload('wamid.direct.badtoken')).encode()
        res = self.url_open(self._url(token='not-the-token'), data=body,
                            headers={'Content-Type': 'application/json'})
        self.assertEqual(res.status_code, 403)
        self.assertFalse(self.env['rcs.whatsapp.message'].search([('wa_message_id', '=', 'wamid.direct.badtoken')]))

    def test_direct_delivery_drops_a_foreign_business_account(self):
        self.number.write({'connection_mode': 'coexistence', 'provider': 'rcstudio',
                           'app_secret': False})
        payload = self._payload('wamid.direct.foreign')
        payload['entry'][0]['id'] = 'waba-of-someone-else'
        res = self.url_open(self._url(), data=json.dumps(payload).encode(),
                            headers={'Content-Type': 'application/json'})
        # 200 on purpose: a 4xx would make Meta retry something that can never match
        self.assertEqual(res.status_code, 200)
        self.assertFalse(self.env['rcs.whatsapp.message'].search([('wa_message_id', '=', 'wamid.direct.foreign')]))
