"""Conversation lookup and partner matching."""
from datetime import timedelta

import psycopg2

from odoo import fields
from odoo.tests import tagged
from odoo.tools import mute_logger

from .common import CUSTOMER_WA_ID, OTHER_PHONE, OTHER_WA_ID, TAGS, WhatsAppCase


@tagged(*TAGS)
class TestConversation(WhatsAppCase):

    def test_get_or_create_is_idempotent(self):
        Conversation = self.env['rcs.whatsapp.conversation']
        first = Conversation._get_or_create(self.number, CUSTOMER_WA_ID, 'Alice')
        second = Conversation._get_or_create(self.number, CUSTOMER_WA_ID, 'Alice')
        self.assertEqual(first, second)
        self.assertEqual(Conversation.search_count([('number_id', '=', self.number.id),
                                                    ('phone', '=', CUSTOMER_WA_ID)]), 1)
        self.assertEqual(first.profile_name, 'Alice')
        self.assertEqual(first.phone_display, '+1 415-555-2671')
        self.assertEqual(first.state, 'open')

    def test_get_or_create_updates_profile_name(self):
        Conversation = self.env['rcs.whatsapp.conversation']
        conv = Conversation._get_or_create(self.number, OTHER_WA_ID, 'Bob')
        self.assertEqual(conv.profile_name, 'Bob')
        same = Conversation._get_or_create(self.number, OTHER_WA_ID, 'Robert')
        self.assertEqual(same, conv)
        self.assertEqual(conv.profile_name, 'Robert')
        # an empty profile name never erases the one we have
        Conversation._get_or_create(self.number, OTHER_WA_ID, None)
        self.assertEqual(conv.profile_name, 'Robert')

    def test_partner_matched_by_phone(self):
        conv = self.env['rcs.whatsapp.conversation']._get_or_create(self.number, CUSTOMER_WA_ID, 'Alice')
        self.assertEqual(conv.partner_id, self.partner)
        self.assertEqual(conv.name, self.partner.name, 'the partner name wins over the profile name')

    def test_partner_matched_with_local_format(self):
        # A Spanish partner whose phone was typed without the country prefix
        partner = self._make_partner(name='Carlos', phone='612 34 56 78', country='ES')
        found = self.env['rcs.whatsapp.conversation']._find_partner(OTHER_WA_ID)
        self.assertEqual(found, partner)

    def test_company_partner_never_matched(self):
        self.company_partner.write({'phone': OTHER_PHONE})
        # make sure nobody else carries that number in this test
        others = self.env['res.partner'].search([
            '|', ('phone', 'ilike', '612345678'), ('phone_sanitized', '=', '+' + OTHER_WA_ID),
            ('id', '!=', self.company_partner.id)])
        others.write({'phone': False})
        found = self.env['rcs.whatsapp.conversation']._find_partner(OTHER_WA_ID)
        self.assertFalse(found, 'our own company must never be picked as the customer')
        conv = self.env['rcs.whatsapp.conversation']._get_or_create(self.number, OTHER_WA_ID, 'Someone')
        self.assertFalse(conv.partner_id)
        self.assertEqual(conv.name, 'Someone')

    def test_unknown_phone_has_no_partner(self):
        found = self.env['rcs.whatsapp.conversation']._find_partner('573001234567')
        self.assertFalse(found)

    def test_unique_per_number_and_phone(self):
        Conversation = self.env['rcs.whatsapp.conversation']
        Conversation._get_or_create(self.number, CUSTOMER_WA_ID)
        with self.assertRaises(psycopg2.IntegrityError), mute_logger('odoo.sql_db'), self.cr.savepoint():
            Conversation.create({'number_id': self.number.id, 'phone': CUSTOMER_WA_ID})
        # the same phone on another number is a different thread
        other = self._make_number(name='Other')
        self.assertNotEqual(Conversation._get_or_create(other, CUSTOMER_WA_ID),
                            Conversation._get_or_create(self.number, CUSTOMER_WA_ID))

    def test_window(self):
        Conversation = self.env['rcs.whatsapp.conversation']
        conv = Conversation._get_or_create(self.number, CUSTOMER_WA_ID)
        self.assertFalse(conv.window_open, 'no inbound message yet: window closed')
        conv.sudo().write({'last_inbound_at': fields.Datetime.now() - timedelta(hours=2)})
        self.assertTrue(conv.window_open)
        self.assertIn(conv, Conversation.search([('window_open', '=', True)]))
        conv.sudo().write({'last_inbound_at': fields.Datetime.now() - timedelta(hours=25)})
        self.assertFalse(conv.window_open)
        self.assertIn(conv, Conversation.search([('window_open', '=', False)]))
        self.assertNotIn(conv, Conversation.search([('window_open', '=', True)]))

    def test_link_documents(self):
        conv = self.env['rcs.whatsapp.conversation']._get_or_create(self.number, CUSTOMER_WA_ID)
        conv._link('res.partner', self.partner.id)
        conv._link('res.partner', self.partner.id)
        self.assertEqual(len(conv.link_ids), 1)
        self.assertEqual(conv.link_ids.display_name, self.partner.display_name)
        self.assertEqual(conv.link_count, 1)

    def test_inbox_create_partner(self):
        Conversation = self.env['rcs.whatsapp.conversation']
        stranger = '573001112233'
        conv = Conversation._get_or_create(self.number, stranger, 'Nuevo Cliente')
        other = Conversation._get_or_create(self._make_number(name='Second Number'), stranger, 'Nuevo Cliente')
        self.assertFalse(conv.partner_id, 'a stranger never gets a contact on their own')
        result = conv.inbox_create_partner()
        partner = conv.partner_id
        self.assertEqual(result['partner_id'], partner.id)
        self.assertEqual(result['conversation']['partner_id'], partner.id)
        self.assertEqual(partner.name, 'Nuevo Cliente')
        self.assertEqual(partner.country_id.code, 'CO')
        self.assertEqual(other.partner_id, partner, 'every thread with that phone is linked')
        self.assertEqual(conv.inbox_create_partner()['partner_id'], partner.id, 'never twice')
        self.assertEqual(self.env['res.partner'].search_count([('name', '=', 'Nuevo Cliente')]), 1)
