"""Shared fixtures. Every test class inherits WhatsAppCase (or WhatsAppHttpCase) and
gets a connected business number, a partner with a phone, and a guard that turns any
attempt to reach Meta into a test failure."""
import json
import time
from unittest.mock import patch

from odoo.tests import HttpCase, TransactionCase

from odoo.addons.rcstudio_whatsapp_coexistence.tools import graph
from odoo.addons.rcstudio_whatsapp_coexistence.tools.graph import GraphClient

TAGS = ('post_install', '-at_install', 'rcs_whatsapp')

# Numbers phonenumbers accepts as valid: a US and a Spanish mobile, digits only (wa_id form)
CUSTOMER_WA_ID = '14155552671'
CUSTOMER_PHONE = '+1 415-555-2671'
OTHER_WA_ID = '34612345678'
OTHER_PHONE = '+34 612 34 56 78'


def _no_network(*args, **kwargs):
    raise AssertionError('A test tried to reach Meta: %s %s' % (args, kwargs))


class WhatsAppMixin:
    """Helpers usable from both TransactionCase and HttpCase."""

    @classmethod
    def _make_number(cls, name='Test Number', **extra):
        vals = {
            'name': name,
            'connection_mode': 'cloud_api',
            'app_id': '1000000000000001',
            'app_secret': 'test-app-secret',
            'waba_id': f'waba-{name}-{time.time_ns()}',
            'phone_number_id': f'pnid-{name}-{time.time_ns()}',
            'access_token': 'test-token',
            'status': 'connected',
        }
        vals.update(extra)
        return cls.env['rcs.whatsapp.number'].create(vals)

    @classmethod
    def _make_partner(cls, name='Alice Customer', phone=CUSTOMER_PHONE, country='US', **extra):
        vals = {'name': name, 'phone': phone,
                'country_id': cls.env.ref(f'base.{country.lower()}').id}
        vals.update(extra)
        return cls.env['res.partner'].create(vals)

    @classmethod
    def _make_template(cls, number, name='test_template', body='Hello {{1}}', status='approved',
                       model='res.partner', params=None, **extra):
        vals = {
            'number_id': number.id, 'name': name, 'language': 'en_US', 'category': 'utility',
            'status': status, 'body_text': body,
            'model_id': cls.env['ir.model']._get_id(model) if model else False,
        }
        vals.update(extra)
        template = cls.env['rcs.whatsapp.template'].create(vals)
        for p in params or []:
            cls.env['rcs.whatsapp.template.param'].create(dict(p, template_id=template.id))
        return template

    # ------------------------------------------------------------ webhook data
    @classmethod
    def _entry(cls, field, value, waba_id=None):
        # Meta names the business account in entry.id; the module drops what is not its own
        waba_id = waba_id or getattr(cls, 'number', None) and cls.number.waba_id or 'waba'
        return {'object': 'whatsapp_business_account',
                'entry': [{'id': waba_id, 'changes': [{'field': field, 'value': value}]}]}

    @classmethod
    def _messages_value(cls, number, messages=(), statuses=(), contacts=None):
        value = {
            'messaging_product': 'whatsapp',
            'metadata': {'display_phone_number': '14152004477', 'phone_number_id': number.phone_number_id},
        }
        if contacts is not None:
            value['contacts'] = contacts
        if messages:
            value['messages'] = list(messages)
        if statuses:
            value['statuses'] = list(statuses)
        return value

    @classmethod
    def _text_payload(cls, number, wa_id, body, wa_message_id, profile_name='Alice', ts=None, context=None):
        message = {'from': wa_id, 'id': wa_message_id, 'timestamp': str(ts or int(time.time())),
                   'type': 'text', 'text': {'body': body}}
        if context:
            message['context'] = context
        contacts = [{'profile': {'name': profile_name}, 'wa_id': wa_id}] if profile_name else []
        return cls._entry('messages', cls._messages_value(number, messages=[message], contacts=contacts),
                          waba_id=number.waba_id)

    @classmethod
    def _status_payload(cls, number, wa_message_id, status, errors=None, ts=None):
        entry = {'id': wa_message_id, 'status': status, 'timestamp': str(ts or int(time.time())),
                 'recipient_id': CUSTOMER_WA_ID}
        if errors:
            entry['errors'] = errors
        return cls._entry('messages', cls._messages_value(number, statuses=[entry]), waba_id=number.waba_id)

    @staticmethod
    def _json(payload):
        return json.dumps(payload).encode()


class WhatsAppCase(WhatsAppMixin, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.number = cls._make_number()
        cls.partner = cls._make_partner()
        cls.company_partner = cls.env.company.partner_id

    def setUp(self):
        super().setUp()
        # Belt and braces: nothing in these tests may talk to graph.facebook.com.
        patcher = patch.object(graph.requests, 'request', side_effect=_no_network)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _mock_send(self, return_value='wamid.test.sent', side_effect=None):
        patcher = patch.object(GraphClient, 'send', return_value=return_value, side_effect=side_effect)
        mocked = patcher.start()
        self.addCleanup(patcher.stop)
        return mocked


class WhatsAppHttpCase(WhatsAppMixin, HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.number = cls._make_number(name='Http Number')
