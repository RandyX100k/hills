"""Webhook processing through number._webhook_dispatch(payload), with Meta's shapes."""
import time
from datetime import datetime

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import CUSTOMER_WA_ID, OTHER_WA_ID, TAGS, WhatsAppCase


@tagged(*TAGS)
class TestWebhook(WhatsAppCase):

    def _conv(self, wa_id=CUSTOMER_WA_ID):
        return self.env['rcs.whatsapp.conversation'].search(
            [('number_id', '=', self.number.id), ('phone', '=', wa_id)])

    def _messages(self, wa_id=CUSTOMER_WA_ID):
        return self.env['rcs.whatsapp.message'].search([('conversation_id', '=', self._conv(wa_id).id)])

    # --------------------------------------------------------------- inbound
    def test_inbound_text_creates_conversation_and_message(self):
        ts = int(time.time()) - 60
        self.number._webhook_dispatch(self._text_payload(self.number, CUSTOMER_WA_ID, 'Hello there', 'wamid.in.1', ts=ts))
        conv = self._conv()
        self.assertEqual(len(conv), 1)
        self.assertEqual(conv.profile_name, 'Alice')
        self.assertEqual(conv.partner_id, self.partner, 'matched by phone')
        self.assertEqual(conv.unread_count, 1)
        self.assertEqual(conv.last_inbound_at, datetime.utcfromtimestamp(ts))
        self.assertTrue(conv.window_open)
        self.assertEqual(conv.last_message_preview, 'Hello there')
        message = conv.message_ids
        self.assertEqual(len(message), 1)
        self.assertRecordValues(message, [{'direction': 'in', 'kind': 'text', 'body': 'Hello there',
                                           'status': 'received', 'wa_message_id': 'wamid.in.1', 'is_echo': False}])
        self.assertEqual(message.date, datetime.utcfromtimestamp(ts))
        self.assertTrue(self.number.webhook_last_event)

    def test_second_inbound_bumps_unread(self):
        self.number._webhook_dispatch(self._text_payload(self.number, CUSTOMER_WA_ID, 'one', 'wamid.in.1', ts=1000))
        self.number._webhook_dispatch(self._text_payload(self.number, CUSTOMER_WA_ID, 'two', 'wamid.in.2', ts=2000))
        conv = self._conv()
        self.assertEqual(conv.unread_count, 2)
        self.assertEqual(len(conv.message_ids), 2)
        self.assertEqual(conv.last_inbound_at, datetime.utcfromtimestamp(2000))
        self.assertEqual(conv.last_message_preview, 'two')
        conv.mark_read()
        self.assertEqual(conv.unread_count, 0)
        self.assertTrue(all(conv.message_ids.mapped('read_at')))

    def test_duplicate_wa_message_id_ignored(self):
        payload = self._text_payload(self.number, CUSTOMER_WA_ID, 'Hello', 'wamid.dup')
        self.number._webhook_dispatch(payload)
        self.number._webhook_dispatch(payload)
        self.assertEqual(len(self._messages()), 1)
        self.assertEqual(self._conv().unread_count, 1)

    def test_reply_context_links_parent(self):
        self.number._webhook_dispatch(self._text_payload(self.number, CUSTOMER_WA_ID, 'first', 'wamid.p'))
        self.number._webhook_dispatch(self._text_payload(
            self.number, CUSTOMER_WA_ID, 'quoting you', 'wamid.c', context={'from': CUSTOMER_WA_ID, 'id': 'wamid.p'}))
        parent = self.env['rcs.whatsapp.message'].search([('wa_message_id', '=', 'wamid.p')])
        child = self.env['rcs.whatsapp.message'].search([('wa_message_id', '=', 'wamid.c')])
        self.assertEqual(child.reply_to_id, parent)
        self.assertEqual(child._to_inbox()['reply_to']['body'], 'first')

    def test_reply_to_unknown_message_is_kept(self):
        self.number._webhook_dispatch(self._text_payload(
            self.number, CUSTOMER_WA_ID, 'orphan quote', 'wamid.o', context={'id': 'wamid.never.seen'}))
        message = self.env['rcs.whatsapp.message'].search([('wa_message_id', '=', 'wamid.o')])
        self.assertTrue(message)
        self.assertFalse(message.reply_to_id)

    def test_message_without_sender_is_dropped(self):
        value = self._messages_value(self.number, messages=[{'id': 'wamid.nofrom', 'type': 'text', 'text': {'body': 'x'}}])
        self.number._webhook_dispatch(self._entry('messages', value))
        self.assertFalse(self.env['rcs.whatsapp.message'].search([('wa_message_id', '=', 'wamid.nofrom')]))

    # ------------------------------------------------------------- statuses
    def _outbound(self, wa_message_id='wamid.out.1', status='sent'):
        conv = self.env['rcs.whatsapp.conversation']._get_or_create(self.number, CUSTOMER_WA_ID)
        return self.env['rcs.whatsapp.message'].create({
            'conversation_id': conv.id, 'direction': 'out', 'kind': 'text', 'body': 'hi',
            'status': status, 'wa_message_id': wa_message_id})

    def test_status_ladder_never_regresses(self):
        message = self._outbound()
        self.number._webhook_dispatch(self._status_payload(self.number, 'wamid.out.1', 'delivered'))
        self.assertEqual(message.status, 'delivered')
        self.number._webhook_dispatch(self._status_payload(self.number, 'wamid.out.1', 'read'))
        self.assertEqual(message.status, 'read')
        # late 'delivered' and 'sent' after 'read' are ignored
        self.number._webhook_dispatch(self._status_payload(self.number, 'wamid.out.1', 'delivered'))
        self.assertEqual(message.status, 'read')
        self.number._webhook_dispatch(self._status_payload(self.number, 'wamid.out.1', 'sent'))
        self.assertEqual(message.status, 'read')

    def test_status_failed_stores_error(self):
        message = self._outbound()
        self.number._webhook_dispatch(self._status_payload(self.number, 'wamid.out.1', 'failed', errors=[{
            'code': 131047, 'title': 'Re-engagement message',
            'message': 'Re-engagement message',
            'error_data': {'details': 'Message failed to send because more than 24 hours have passed'},
        }]))
        self.assertEqual(message.status, 'failed')
        self.assertEqual(message.error_code, '131047')
        self.assertEqual(message.error_message, 'Re-engagement message')

    def test_status_played_counts_as_read(self):
        """Meta sends 'played' the first time a voice note is played (March 2026)."""
        message = self._outbound(status='delivered')
        self.number._webhook_dispatch(self._status_payload(self.number, 'wamid.out.1', 'played'))
        self.assertEqual(message.status, 'read')

    def test_unknown_status_is_ignored_not_raised(self):
        """A status we cannot read must not fail the delivery: Meta would retry it
        for 36 hours with the same result, and the acknowledgement would be lost."""
        message = self._outbound(status='delivered')
        self.number._webhook_dispatch(self._status_payload(self.number, 'wamid.out.1', 'invented_by_meta'))
        self.assertEqual(message.status, 'delivered')

    def test_status_for_unknown_message_is_ignored(self):
        self.number._webhook_dispatch(self._status_payload(self.number, 'wamid.ghost', 'delivered'))
        self.assertFalse(self.env['rcs.whatsapp.message'].search([('wa_message_id', '=', 'wamid.ghost')]))

    # ---------------------------------------------------------------- echoes
    def test_smb_message_echo_creates_outbound(self):
        value = {'messaging_product': 'whatsapp',
                 'metadata': {'display_phone_number': '14152004477', 'phone_number_id': self.number.phone_number_id},
                 'message_echoes': [{'from': '14152004477', 'to': CUSTOMER_WA_ID, 'id': 'wamid.echo.1',
                                     'timestamp': '1700000000', 'type': 'text', 'text': {'body': 'Typed on the phone'}}]}
        self.number._webhook_dispatch(self._entry('smb_message_echoes', value))
        conv = self._conv()
        self.assertEqual(len(conv), 1)
        message = conv.message_ids
        self.assertRecordValues(message, [{'direction': 'out', 'is_echo': True, 'status': 'sent',
                                           'body': 'Typed on the phone', 'wa_message_id': 'wamid.echo.1'}])
        self.assertEqual(conv.unread_count, 0, 'an echo is not something to read')
        self.assertFalse(conv.last_inbound_at, 'an echo does not open the window')
        self.assertEqual(message._to_inbox()['author'], '')

    # ------------------------------------------------------------- templates
    def test_template_status_update(self):
        template = self._make_template(self.number, name='hello_world', status='pending',
                                       meta_template_id='300000000000009')
        self.number._webhook_dispatch(self._entry('message_template_status_update', {
            'event': 'APPROVED', 'message_template_id': 300000000000009,
            'message_template_name': 'hello_world', 'message_template_language': 'en_US', 'reason': None}))
        self.assertEqual(template.status, 'approved')
        self.assertFalse(template.rejection_reason)
        self.number._webhook_dispatch(self._entry('message_template_status_update', {
            'event': 'REJECTED', 'message_template_id': 0,  # unknown id: falls back to name + language
            'message_template_name': 'hello_world', 'message_template_language': 'en_US',
            'reason': 'INVALID_FORMAT'}))
        self.assertEqual(template.status, 'rejected')
        self.assertEqual(template.rejection_reason, 'INVALID_FORMAT')

    def test_template_status_update_other_number_untouched(self):
        other = self._make_number(name='Other')
        template = self._make_template(other, name='hello_world', status='pending', meta_template_id='1')
        self.number._webhook_dispatch(self._entry('message_template_status_update', {
            'event': 'APPROVED', 'message_template_id': 1,
            'message_template_name': 'hello_world', 'message_template_language': 'en_US'}))
        self.assertEqual(template.status, 'pending')

    # --------------------------------------------------------------- opt-out
    def test_opt_out_word_blacklists_and_blocks_sending(self):
        self.number._webhook_dispatch(self._text_payload(self.number, CUSTOMER_WA_ID, 'hi', 'wamid.in.1', ts=1000))
        conv = self._conv()
        self.assertFalse(conv.opted_out)
        self.number._webhook_dispatch(self._text_payload(self.number, CUSTOMER_WA_ID, '  Stop! ', 'wamid.in.2'))
        self.assertTrue(self.env['phone.blacklist'].sudo().search([('number', '=', '+' + CUSTOMER_WA_ID)]))
        conv.invalidate_recordset(['opted_out'])
        self.assertTrue(conv.opted_out)
        self.assertTrue(conv.window_open)
        mocked = self._mock_send()
        with self.assertRaises(UserError):
            conv.send_text('Are you still there?')
        mocked.assert_not_called()

    def test_opt_out_accent_insensitive(self):
        self.env['ir.config_parameter'].sudo().set_param('rcstudio_whatsapp_coexistence.opt_out_words', 'stop, cancelación')
        self.number._webhook_dispatch(self._text_payload(self.number, OTHER_WA_ID, 'CANCELACION', 'wamid.es.1'))
        self.assertTrue(self.env['phone.blacklist'].sudo().search([('number', '=', '+' + OTHER_WA_ID)]))

    def test_normal_text_does_not_opt_out(self):
        self.number._webhook_dispatch(self._text_payload(self.number, CUSTOMER_WA_ID, 'please stop by tomorrow', 'wamid.in.1'))
        self.assertFalse(self.env['phone.blacklist'].sudo().search([('number', '=', '+' + CUSTOMER_WA_ID)]))

    # ----------------------------------------------------------- other kinds
    def test_location(self):
        value = self._messages_value(self.number, messages=[{
            'from': CUSTOMER_WA_ID, 'id': 'wamid.loc', 'timestamp': '1700000000', 'type': 'location',
            'location': {'latitude': 4.6097, 'longitude': -74.0817, 'name': 'Office', 'address': 'Calle 1 # 2-3'}}])
        self.number._webhook_dispatch(self._entry('messages', value))
        message = self._messages()
        self.assertRecordValues(message, [{'kind': 'location', 'location_name': 'Office',
                                           'location_address': 'Calle 1 # 2-3'}])
        self.assertAlmostEqual(message.latitude, 4.6097, places=4)
        self.assertAlmostEqual(message.longitude, -74.0817, places=4)
        self.assertIn('Office', self._conv().last_message_preview)
        self.assertEqual(message._to_inbox()['location']['name'], 'Office')

    def test_unsupported(self):
        value = self._messages_value(self.number, messages=[{
            'from': CUSTOMER_WA_ID, 'id': 'wamid.unsup', 'timestamp': '1700000000', 'type': 'unsupported',
            'errors': [{'code': 131051, 'title': 'Message type unknown'}]}])
        self.number._webhook_dispatch(self._entry('messages', value))
        message = self._messages()
        self.assertEqual(message.kind, 'unsupported')
        self.assertIn('131051', message.body)
        self.assertEqual(self._conv().unread_count, 1)

    def test_reaction_interactive_and_contacts(self):
        self.number._webhook_dispatch(self._text_payload(self.number, CUSTOMER_WA_ID, 'target', 'wamid.t'))
        target = self.env['rcs.whatsapp.message'].search([('wa_message_id', '=', 'wamid.t')])
        value = self._messages_value(self.number, messages=[
            {'from': CUSTOMER_WA_ID, 'id': 'wamid.r', 'timestamp': '1700000000', 'type': 'reaction',
             'reaction': {'message_id': 'wamid.t', 'emoji': '👍'}},
            {'from': CUSTOMER_WA_ID, 'id': 'wamid.b', 'timestamp': '1700000001', 'type': 'button',
             'button': {'payload': 'YES', 'text': 'Yes'}},
            {'from': CUSTOMER_WA_ID, 'id': 'wamid.i', 'timestamp': '1700000002', 'type': 'interactive',
             'interactive': {'type': 'button_reply', 'button_reply': {'id': 'opt-1', 'title': 'Option one'}}},
            {'from': CUSTOMER_WA_ID, 'id': 'wamid.k', 'timestamp': '1700000003', 'type': 'contacts',
             'contacts': [{'name': {'formatted_name': 'Bob Vendor'}, 'phones': [{'phone': '+34 612 345 678'}]}]},
        ])
        self.number._webhook_dispatch(self._entry('messages', value))
        by_id = {m.wa_message_id: m for m in self._messages()}
        self.assertEqual(by_id['wamid.r'].kind, 'reaction')
        self.assertEqual(by_id['wamid.r'].reaction_emoji, '👍')
        self.assertEqual(by_id['wamid.r'].reaction_to_id, target)
        self.assertEqual(by_id['wamid.b'].kind, 'interactive')
        self.assertEqual(by_id['wamid.b'].body, 'Yes')
        self.assertEqual(by_id['wamid.i'].body, 'Option one')
        self.assertEqual(by_id['wamid.k'].kind, 'contacts')
        self.assertIn('Bob Vendor', by_id['wamid.k'].body)

    def test_media_without_download_keeps_filename(self):
        # fetch_media raises (the token is fake, and the network is blocked): the message
        # is still stored, without attachment, and the filename survives as the body
        from odoo.addons.rcstudio_whatsapp_coexistence.tools.graph import GraphClient, GraphError
        from unittest.mock import patch
        with patch.object(GraphClient, 'fetch_media', side_effect=GraphError('nope', code=100)):
            value = self._messages_value(self.number, messages=[{
                'from': CUSTOMER_WA_ID, 'id': 'wamid.doc', 'timestamp': '1700000000', 'type': 'document',
                'document': {'id': 'media-1', 'mime_type': 'application/pdf', 'filename': 'quote.pdf', 'caption': 'Signed'}}])
            self.number._webhook_dispatch(self._entry('messages', value))
        message = self._messages()
        self.assertRecordValues(message, [{'kind': 'document', 'caption': 'Signed', 'body': 'quote.pdf', 'attachment_id': False}])

    def test_media_downloaded_becomes_attachment(self):
        from odoo.addons.rcstudio_whatsapp_coexistence.tools.graph import GraphClient
        from unittest.mock import patch
        with patch.object(GraphClient, 'fetch_media', return_value=(b'\x89PNG fake', 'image/png')):
            value = self._messages_value(self.number, messages=[{
                'from': CUSTOMER_WA_ID, 'id': 'wamid.img', 'timestamp': '1700000000', 'type': 'image',
                'image': {'id': 'media-2', 'mime_type': 'image/png', 'sha256': 'x'}}])
            self.number._webhook_dispatch(self._entry('messages', value))
        message = self._messages()
        self.assertEqual(message.kind, 'image')
        self.assertTrue(message.attachment_id)
        self.assertEqual(message.attachment_id.mimetype, 'image/png')
        self.assertEqual(message.attachment_id.raw, b'\x89PNG fake')
        self.assertEqual(message.attachment_id.name, 'image-media-2.png')

    def test_unknown_field_and_failing_change_do_not_stop_the_batch(self):
        payload = {'object': 'whatsapp_business_account', 'entry': [{'id': self.number.waba_id, 'changes': [
            {'field': 'something_new', 'value': {'foo': 'bar'}},
            {'field': 'messages', 'value': 'not-a-dict'},  # raises inside the handler
            {'field': 'messages', 'value': self._messages_value(self.number, messages=[
                {'from': CUSTOMER_WA_ID, 'id': 'wamid.ok', 'timestamp': '1700000000', 'type': 'text', 'text': {'body': 'ok'}}])},
        ]}]}
        from odoo.tools import mute_logger
        with mute_logger('odoo.addons.rcstudio_whatsapp_coexistence.models.webhook'):
            self.number._webhook_dispatch(payload)
        self.assertTrue(self.env['rcs.whatsapp.message'].search([('wa_message_id', '=', 'wamid.ok')]))

    # --------------------------------------------------- direct delivery (no relay)
    def test_entry_of_another_business_account_is_dropped(self):
        """A delivery naming another WABA must not create anything, and must not raise:
        in direct delivery the URL is public and only the token guards it."""
        payload = self._text_payload(self.number, CUSTOMER_WA_ID, 'not mine', 'wamid.foreign')
        payload['entry'][0]['id'] = 'waba-of-someone-else'
        self.number._webhook_dispatch(payload)
        self.assertFalse(self.env['rcs.whatsapp.message'].search([('wa_message_id', '=', 'wamid.foreign')]))

    def test_event_for_another_number_is_dropped(self):
        payload = self._text_payload(self.number, CUSTOMER_WA_ID, 'not mine either', 'wamid.othernum')
        payload['entry'][0]['changes'][0]['value']['metadata']['phone_number_id'] = 'pnid-somebody-else'
        self.number._webhook_dispatch(payload)
        self.assertFalse(self.env['rcs.whatsapp.message'].search([('wa_message_id', '=', 'wamid.othernum')]))

    def test_template_event_without_metadata_still_passes(self):
        template = self._make_template(self.number, name='pending_one', status='pending')
        self.number._webhook_dispatch(self._entry('message_template_status_update', {
            'message_template_id': template.meta_template_id or template.id,
            'message_template_name': 'pending_one', 'message_template_language': template.language,
            'event': 'APPROVED'}))
        self.assertEqual(template.status, 'approved')

    def test_direct_is_the_default_and_has_no_secret_to_check(self):
        number = self._make_number('Direct', connection_mode='coexistence', provider='rcstudio',
                                   app_secret=False, webhook_secret='relay-secret')
        self.assertEqual(number.webhook_delivery, 'direct')
        self.assertTrue(number._webhook_unsigned())
        self.assertFalse(number._webhook_secret())
        # the relay stays as the legacy path for numbers connected with an older version
        number.webhook_delivery = 'relay'
        self.assertFalse(number._webhook_unsigned())
        self.assertEqual(number._webhook_secret(), 'relay-secret')

    def test_rotating_the_token_changes_the_url(self):
        number = self._make_number('Rotating', status='draft')
        before = number.webhook_token
        url_before = number.webhook_url
        number.action_rotate_webhook_token()
        self.assertNotEqual(number.webhook_token, before)
        self.assertNotEqual(number.webhook_url, url_before)
        self.assertGreaterEqual(len(number.webhook_token), 32)
        self.assertFalse(number.webhook_subscribed)
