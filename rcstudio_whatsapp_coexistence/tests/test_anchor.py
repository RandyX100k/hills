"""What a conversation is about, and how its messages reach a record's chatter.

A WhatsApp message carries no thread reference, so the conversation remembers the
document it is currently about. These tests pin down that choice: how it is made, when
it is dropped, and what ends up in the chatter.
"""
from datetime import timedelta

from odoo import fields
from odoo.tests import tagged

from .common import CUSTOMER_WA_ID, TAGS, WhatsAppCase


@tagged(*TAGS)
class TestAnchor(WhatsAppCase):

    def setUp(self):
        super().setUp()
        # a second partner standing in for "a document with a chatter", so the anchor is
        # never confused with the fallback to the customer's own record
        self.document = self.env['res.partner'].create({'name': 'Loft insulation', 'is_company': True})

    def _conv(self):
        return self.env['rcs.whatsapp.conversation'].search(
            [('number_id', '=', self.number.id), ('phone', '=', CUSTOMER_WA_ID)])

    def _inbound(self, body='Hello', wa_message_id='wamid.in.1'):
        self.number._webhook_dispatch(
            self._text_payload(self.number, CUSTOMER_WA_ID, body, wa_message_id))
        return self._conv()

    def _notes(self, record):
        return record.message_ids.filtered(lambda m: 'WhatsApp' in (m.body or ''))

    # ------------------------------------------------------------- mirroring
    def test_inbound_lands_on_the_contact_without_an_anchor(self):
        """No document yet: the customer's own record is the last resort, never nothing."""
        conv = self._inbound('I would like a quote')
        notes = self._notes(self.partner)
        self.assertEqual(len(notes), 1)
        self.assertIn('I would like a quote', notes.body)
        self.assertIn('WhatsApp from', notes.body)
        self.assertEqual(notes.author_id, self.partner, 'the customer speaks in their own voice')
        self.assertEqual(conv.message_ids.mail_message_id, notes)

    def test_inbound_lands_on_the_anchor(self):
        conv = self._inbound()
        conv._anchor_set(self.document)
        self.number._webhook_dispatch(
            self._text_payload(self.number, CUSTOMER_WA_ID, 'When are you coming?', 'wamid.in.2'))
        self.assertIn('When are you coming?', self._notes(self.document).body)
        self.assertNotIn('When are you coming?', ''.join(self._notes(self.partner).mapped('body')))

    def test_outbound_from_the_inbox_lands_on_the_anchor(self):
        conv = self._inbound()
        conv._anchor_set(self.document)
        self._mock_send(return_value='wamid.out.1')
        conv.send_text('On Tuesday morning')
        notes = self._notes(self.document)
        self.assertIn('On Tuesday morning', ''.join(notes.mapped('body')))
        self.assertIn('WhatsApp to', ''.join(notes.mapped('body')))

    def test_a_failed_send_is_not_mirrored(self):
        """The chatter shows what the customer got, not what we tried to send."""
        conv = self._inbound()
        conv._anchor_set(self.document)
        self._mock_send(side_effect=Exception('Meta said no'))
        conv.send_text('never arrives')
        self.assertNotIn('never arrives', ''.join(self._notes(self.document).mapped('body')))

    def test_media_is_copied_to_the_chatter(self):
        """A photo of the job site belongs in the record, not only in the inbox."""
        conv = self._inbound()
        conv._anchor_set(self.document)
        attachment = self.env['ir.attachment'].create({
            'name': 'obra.png', 'raw': b'\x89PNG fake', 'mimetype': 'image/png',
            'res_model': 'rcs.whatsapp.message', 'res_id': 0})
        message = self.env['rcs.whatsapp.message'].create({
            'conversation_id': conv.id, 'direction': 'in', 'kind': 'image',
            'caption': 'la buhardilla', 'attachment_id': attachment.id})
        note = message.mail_message_id
        self.assertTrue(note, 'the message was mirrored')
        self.assertEqual(note.attachment_ids.mapped('name'), ['obra.png'])
        self.assertEqual(bytes(note.attachment_ids.raw), b'\x89PNG fake')
        self.assertIn('la buhardilla', note.body)

    def test_voice_notes_are_linked_not_copied(self):
        """Twenty voice notes from one customer must not double the database."""
        conv = self._inbound()
        conv._anchor_set(self.document)
        attachment = self.env['ir.attachment'].create({
            'name': 'voice.ogg', 'raw': b'OggS fake', 'mimetype': 'audio/ogg',
            'res_model': 'rcs.whatsapp.message', 'res_id': 0})
        message = self.env['rcs.whatsapp.message'].create({
            'conversation_id': conv.id, 'direction': 'in', 'kind': 'audio',
            'is_voice': True, 'attachment_id': attachment.id})
        note = message.mail_message_id
        self.assertFalse(note.attachment_ids, 'kept in the inbox')
        self.assertIn(f'/web/content/{attachment.id}', note.body)

    def test_mirroring_is_idempotent_per_document(self):
        conv = self._inbound()
        message = conv.message_ids
        message._mirror_to_chatter()
        message._mirror_to_chatter()
        self.assertEqual(len(self._notes(self.partner)), 1)

    def test_mirroring_can_be_switched_off(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'rcstudio_whatsapp_coexistence.mirror_chatter', 'False')
        self._inbound()
        self.assertFalse(self._notes(self.partner))

    def test_reactions_are_not_mirrored(self):
        """A thumbs-up is noise in a record's history."""
        conv = self._inbound()
        self.env['rcs.whatsapp.message'].create({
            'conversation_id': conv.id, 'direction': 'in', 'kind': 'reaction',
            'reaction_emoji': '👍', 'reaction_to_id': conv.message_ids[0].id})
        self.assertEqual(len(self._notes(self.partner)), 1)

    # ---------------------------------------------------------------- anchor
    def test_sending_from_a_record_anchors_the_conversation(self):
        self._mock_send(return_value='wamid.out.1')
        conv = self._inbound()
        conv.send_text('Here is your quote', origin=self.document)
        self.assertEqual(conv.anchor_model, 'res.partner')
        self.assertEqual(conv.anchor_res_id, self.document.id)
        self.assertEqual(conv.anchor_name, self.document.display_name)
        self.assertIn((self.document._name, self.document.id),
                      [(link.model, link.res_id) for link in conv.link_ids])

    def test_an_open_anchor_is_never_stolen(self):
        """Writing from an invoice does not make the conversation about the invoice."""
        self._mock_send(return_value='wamid.out.1')
        conv = self._inbound()
        conv._anchor_set(self.document)
        other = self.env['res.partner'].create({'name': 'Facade insulation'})
        conv.send_text('a word about something else', origin=other)
        self.assertEqual(conv.anchor_res_id, self.document.id)
        # ...but that message is still logged where it was sent from
        self.assertIn('a word about something else', ''.join(self._notes(other).mapped('body')))

    def test_a_closed_anchor_is_replaced(self):
        self._mock_send(return_value='wamid.out.1')
        conv = self._inbound()
        conv._anchor_set(self.document)
        self.document.active = False
        other = self.env['res.partner'].create({'name': 'Facade insulation'})
        conv.send_text('new job', origin=other)
        self.assertEqual(conv.anchor_res_id, other.id)

    def test_backlog_is_poured_into_a_freshly_anchored_document(self):
        conv = self._inbound('First question')
        self.number._webhook_dispatch(
            self._text_payload(self.number, CUSTOMER_WA_ID, 'And a second one', 'wamid.in.2'))
        conv.inbox_set_anchor('res.partner', self.document.id)
        body = ''.join(self._notes(self.document).mapped('body'))
        self.assertIn('First question', body)
        self.assertIn('And a second one', body)

    def test_anchor_options_offer_the_linked_documents(self):
        conv = self._inbound()
        conv._link('res.partner', self.document.id)
        options = conv.inbox_anchor_options()
        self.assertIn((('res.partner'), self.document.id),
                      [(o['model'], o['res_id']) for o in options['options']])

    def test_clearing_the_anchor_stops_the_logging(self):
        conv = self._inbound()
        conv._anchor_set(self.document)
        conv.inbox_clear_anchor()
        self.assertFalse(conv.anchor_model)
        self.number._webhook_dispatch(
            self._text_payload(self.number, CUSTOMER_WA_ID, 'after unlinking', 'wamid.in.9'))
        self.assertNotIn('after unlinking', ''.join(self._notes(self.document).mapped('body')))

    # ------------------------------------------------------------------ cron
    def test_cron_releases_a_closed_document(self):
        conv = self._inbound()
        conv._anchor_set(self.document)
        self.document.active = False
        self.env['rcs.whatsapp.conversation']._cron_release_anchors()
        self.assertFalse(conv.anchor_model)

    def test_cron_releases_a_deleted_document(self):
        conv = self._inbound()
        conv._anchor_set(self.document)
        self.document.unlink()
        self.env['rcs.whatsapp.conversation']._cron_release_anchors()
        self.assertFalse(conv.anchor_model)

    def test_cron_releases_a_quiet_thread(self):
        """The WhatsApp thread is forever; the opportunity is not."""
        conv = self._inbound()
        conv._anchor_set(self.document)
        conv.sudo().last_activity = fields.Datetime.now() - timedelta(days=40)
        self.env['rcs.whatsapp.conversation']._cron_release_anchors()
        self.assertFalse(conv.anchor_model)

    def test_cron_keeps_a_live_document(self):
        conv = self._inbound()
        conv._anchor_set(self.document)
        self.env['rcs.whatsapp.conversation']._cron_release_anchors()
        self.assertEqual(conv.anchor_res_id, self.document.id)

    # ---------------------------------------------------------------- bindings
    def test_bindings_cover_apps_installed_later(self):
        """post_init_hook runs once, at install. An app activated a month later would
        otherwise never get its "Send by WhatsApp" button, which is what init() fixes."""
        if 'crm.lead' not in self.env:
            self.skipTest('crm is not installed in this database')
        Action = self.env['ir.actions.act_window'].sudo()
        domain = [('res_model', '=', 'rcs.whatsapp.send'), ('binding_model_id.model', '=', 'crm.lead')]
        Action.search(domain).unlink()
        self.assertFalse(Action.search(domain), 'as if crm had been installed afterwards')
        self.env['rcs.whatsapp.send']._install_bindings()
        self.assertTrue(Action.search(domain), 'the button comes back on update')
        # and running it again must not pile up duplicates
        self.env['rcs.whatsapp.send']._install_bindings()
        self.assertEqual(len(Action.search(domain)), 1)

    # ------------------------------------------------------------- crm.lead
    def test_create_opportunity_from_a_conversation(self):
        """The customer who writes first: no record to anchor to, so make one."""
        if 'crm.lead' not in self.env:
            self.skipTest('crm is not installed in this database')
        conv = self._inbound('Hello, I need my loft insulated')
        result = conv.inbox_create_document('crm.lead')
        lead = self.env['crm.lead'].browse(result['res_id'])
        self.assertEqual(conv.anchor_model, 'crm.lead')
        self.assertEqual(conv.anchor_res_id, lead.id)
        self.assertEqual(lead.type, 'opportunity')
        self.assertEqual(lead.name, 'Hello, I need my loft insulated')
        self.assertEqual(lead.partner_id, self.partner)
        self.assertEqual(lead.phone, '+' + CUSTOMER_WA_ID)
        self.assertEqual(lead.source_id.name, 'WhatsApp', 'so the channel can be measured')
        self.assertIn('I need my loft insulated', ''.join(self._notes(lead).mapped('body')))

    def test_opportunity_for_an_unknown_number_carries_the_name_not_a_contact(self):
        if 'crm.lead' not in self.env:
            self.skipTest('crm is not installed in this database')
        self.number._webhook_dispatch(self._text_payload(
            self.number, '34600111222', 'Do you insulate garages?', 'wamid.in.x', profile_name='Marta'))
        conv = self.env['rcs.whatsapp.conversation'].search(
            [('number_id', '=', self.number.id), ('phone', '=', '34600111222')])
        self.assertFalse(conv.partner_id, 'nobody in the database has that number')
        lead = self.env['crm.lead'].browse(conv.inbox_create_document('crm.lead')['res_id'])
        self.assertFalse(lead.partner_id, 'no contact is created for a stranger')
        self.assertEqual(lead.contact_name, 'Marta')
        self.assertEqual(lead.phone, '+34600111222')

    def test_the_next_reply_lands_in_the_opportunity(self):
        """The whole point: the salesperson reads the thread in the opportunity."""
        if 'crm.lead' not in self.env:
            self.skipTest('crm is not installed in this database')
        conv = self._inbound('Hello')
        lead = self.env['crm.lead'].browse(conv.inbox_create_document('crm.lead')['res_id'])
        self.number._webhook_dispatch(
            self._text_payload(self.number, CUSTOMER_WA_ID, 'Tuesday works for me', 'wamid.in.2'))
        self.assertIn('Tuesday works for me', ''.join(self._notes(lead).mapped('body')))
