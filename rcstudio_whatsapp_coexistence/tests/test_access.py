"""Operators only reach the numbers they are assigned to; managers reach everything."""
from odoo.exceptions import AccessError
from odoo.tests import tagged

from .common import CUSTOMER_WA_ID, OTHER_WA_ID, TAGS, WhatsAppCase


@tagged(*TAGS)
class TestAccess(WhatsAppCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        group_user = cls.env.ref('rcstudio_whatsapp_coexistence.group_user')
        group_manager = cls.env.ref('rcstudio_whatsapp_coexistence.group_manager')
        # 18.0: res.users.groups_id (19.0: group_ids)
        cls.operator = cls.env['res.users'].create({
            'name': 'Op', 'login': 'op@test', 'groups_id': [(6, 0, [cls.env.ref('base.group_user').id, group_user.id])]})
        cls.other_operator = cls.env['res.users'].create({
            'name': 'Op2', 'login': 'op2@test', 'groups_id': [(6, 0, [cls.env.ref('base.group_user').id, group_user.id])]})
        cls.manager = cls.env['res.users'].create({
            'name': 'Mgr', 'login': 'mgr@test', 'groups_id': [(6, 0, [cls.env.ref('base.group_user').id, group_manager.id])]})
        cls.restricted = cls._make_number(name='Restricted', user_ids=[(6, 0, [cls.operator.id])])
        cls.open_number = cls._make_number(name='Open')
        Conversation = cls.env['rcs.whatsapp.conversation']
        cls.conv_restricted = Conversation._get_or_create(cls.restricted, CUSTOMER_WA_ID, 'A')
        cls.conv_open = Conversation._get_or_create(cls.open_number, OTHER_WA_ID, 'B')

    def _visible(self, user):
        return self.env['rcs.whatsapp.conversation'].with_user(user).search([]).mapped('id')

    def test_listed_operator_sees_restricted_number(self):
        self.assertIn(self.conv_restricted.id, self._visible(self.operator))
        self.assertIn(self.conv_open.id, self._visible(self.operator))

    def test_unlisted_operator_does_not(self):
        visible = self._visible(self.other_operator)
        self.assertNotIn(self.conv_restricted.id, visible)
        self.assertIn(self.conv_open.id, visible)
        Conversation = self.env['rcs.whatsapp.conversation'].with_user(self.other_operator)
        with self.assertRaises(AccessError):
            Conversation.browse(self.conv_restricted.id).check_access('read')
        Message = self.env['rcs.whatsapp.message'].with_user(self.other_operator)
        self.assertFalse(Message.search([('conversation_id', '=', self.conv_restricted.id)]))

    def test_manager_sees_all(self):
        visible = self._visible(self.manager)
        self.assertIn(self.conv_restricted.id, visible)
        self.assertIn(self.conv_open.id, visible)

    def test_operator_cannot_touch_credentials(self):
        number = self.restricted.with_user(self.operator)
        self.assertEqual(number.name, 'Restricted')
        with self.assertRaises(AccessError):
            number.write({'name': 'x'})
        with self.assertRaises(AccessError):
            self.env['rcs.whatsapp.template'].with_user(self.operator).create({
                'number_id': self.restricted.id, 'name': 'nope', 'body_text': 'x'})

    def test_operator_inbox_rpc(self):
        Conversation = self.env['rcs.whatsapp.conversation'].with_user(self.other_operator)
        result = Conversation.inbox_list()
        ids = [c['id'] for c in result['conversations']]
        self.assertIn(self.conv_open.id, ids)
        self.assertNotIn(self.conv_restricted.id, ids)
        self.assertEqual([n['name'] for n in result['numbers'] if n['name'] in ('Restricted', 'Open')], ['Open'])
