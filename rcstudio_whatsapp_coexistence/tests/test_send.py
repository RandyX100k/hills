"""Outbound messages: the send path, the queue and the Send by WhatsApp wizard."""
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.rcstudio_whatsapp_coexistence.tools.graph import GraphClient, GraphError

from .common import CUSTOMER_WA_ID, TAGS, WhatsAppCase


@tagged(*TAGS)
class TestSend(WhatsAppCase):

    def _open_conversation(self):
        conv = self.env['rcs.whatsapp.conversation']._get_or_create(self.number, CUSTOMER_WA_ID, 'Alice')
        conv.sudo().write({'last_inbound_at': fields.Datetime.now() - timedelta(hours=1)})
        return conv

    def test_send_text_success(self):
        conv = self._open_conversation()
        mocked = self._mock_send(return_value='wamid.out.1')
        message = conv.send_text('Hello there')
        self.assertEqual(message.status, 'sent')
        self.assertEqual(message.wa_message_id, 'wamid.out.1')
        payload = mocked.call_args.args[0]
        self.assertEqual(payload['to'], CUSTOMER_WA_ID)
        self.assertEqual(payload['type'], 'text')
        self.assertEqual(payload['text']['body'], 'Hello there')
        self.assertEqual(conv.last_message_id, message)
        self.assertEqual(conv.last_message_preview, 'Hello there')

    def test_reply_context(self):
        conv = self._open_conversation()
        inbound = self.env['rcs.whatsapp.message'].create({
            'conversation_id': conv.id, 'direction': 'in', 'kind': 'text', 'body': 'q', 'wa_message_id': 'wamid.in.q'})
        mocked = self._mock_send()
        conv.send_text('a', reply_to=inbound)
        self.assertEqual(mocked.call_args.args[0]['context'], {'message_id': 'wamid.in.q'})

    def test_transient_error_requeues(self):
        conv = self._open_conversation()
        self._mock_send(side_effect=GraphError('slow down', code=130429, http_status=400))
        message = conv.send_text('x')
        self.assertEqual(message.status, 'queued')
        self.assertEqual(message.attempts, 1)
        self.assertTrue(message.next_attempt)
        self.assertEqual(message.error_code, '130429')

    def test_final_error_fails(self):
        conv = self._open_conversation()
        self._mock_send(side_effect=GraphError('bad token', code=190, http_status=401))
        message = conv.send_text('x')
        self.assertEqual(message.status, 'failed')
        self.assertIn('bad token', message.error_message)

    def test_opted_out_never_calls_meta(self):
        conv = self._open_conversation()
        self.env['phone.blacklist'].sudo()._add(['+' + CUSTOMER_WA_ID])
        conv.invalidate_recordset(['opted_out'])
        mocked = self._mock_send()
        with self.assertRaises(UserError):
            conv.send_text('x')
        mocked.assert_not_called()

    def test_closed_window_refuses_text(self):
        conv = self.env['rcs.whatsapp.conversation']._get_or_create(self.number, CUSTOMER_WA_ID)
        self._mock_send()
        with self.assertRaises(UserError):
            conv.send_text('x')

    def test_queue_cron_retries(self):
        conv = self._open_conversation()
        message = self.env['rcs.whatsapp.message'].create({
            'conversation_id': conv.id, 'direction': 'out', 'kind': 'text', 'body': 'later',
            'status': 'queued', 'next_attempt': fields.Datetime.now() - timedelta(minutes=1)})
        self._mock_send(return_value='wamid.cron')
        self.env['rcs.whatsapp.message']._cron_send_queue()
        self.assertEqual(message.status, 'sent')

    def test_media_upload(self):
        conv = self._open_conversation()
        attachment = self.env['ir.attachment'].create({'name': 'pic.png', 'raw': b'png', 'mimetype': 'image/png'})
        mocked = self._mock_send()
        from unittest.mock import patch
        from odoo.addons.rcstudio_whatsapp_coexistence.tools.graph import GraphClient
        with patch.object(GraphClient, 'upload_media', return_value='media-9') as upload:
            message = conv.send_text('caption here', attachment=attachment)
        upload.assert_called_once()
        self.assertEqual(message.kind, 'image')
        self.assertEqual(mocked.call_args.args[0]['image'], {'id': 'media-9', 'caption': 'caption here'})

    # ----------------------------------------------------------------- wizard
    def test_wizard_defaults_from_partner(self):
        wiz = self.env['rcs.whatsapp.send'].with_context(active_model='res.partner', active_id=self.partner.id).create({})
        self.assertEqual(wiz.partner_id, self.partner)
        self.assertEqual(wiz.phone_wa, CUSTOMER_WA_ID)
        self.assertEqual(wiz.record_count, 1)
        self.assertEqual(wiz.number_id.status, 'connected')

    def test_wizard_defaults_to_the_number_already_talking_to_the_phone(self):
        # another connected number sorts first; the window lives on the conversation's number
        self._make_number(name='First Number', sequence=-1)
        conv = self._open_conversation()
        wiz = self.env['rcs.whatsapp.send'].with_context(active_model='res.partner', active_id=self.partner.id).create({})
        self.assertEqual(wiz.number_id, self.number)
        self.assertEqual(wiz.conversation_id, conv)
        self.assertTrue(wiz.window_open)

    def test_wizard_template_send_links_and_logs(self):
        tpl = self._make_template(self.number, name='wiz', body='Hi {{1}}', params=[
            {'component': 'body', 'placeholder': '1', 'source': 'field', 'field_path': 'name'}])
        self._mock_send(return_value='wamid.wiz')
        wiz = self.env['rcs.whatsapp.send'].with_context(active_model='res.partner', active_id=self.partner.id).create({
            'number_id': self.number.id, 'mode': 'template', 'template_id': tpl.id})
        self.assertIn('Hi Alice Customer', wiz.preview_text)
        wiz.action_send()
        conv = self.env['rcs.whatsapp.conversation'].search([('number_id', '=', self.number.id), ('phone', '=', CUSTOMER_WA_ID)])
        self.assertEqual(conv.partner_id, self.partner)
        message = conv.message_ids
        self.assertEqual(message.kind, 'template')
        self.assertEqual(message.status, 'sent')
        self.assertEqual(message.origin_model, 'res.partner')
        self.assertEqual(conv.link_ids.mapped('res_id'), [self.partner.id])
        note = self.partner.message_ids.filtered(lambda m: 'WhatsApp' in (m.body or '') and 'wiz' in (m.body or ''))
        self.assertTrue(note)

    def test_wizard_asked_values(self):
        tpl = self._make_template(self.number, name='ask', body='Code {{1}}', params=[
            {'component': 'body', 'placeholder': '1', 'source': 'ask', 'label': 'Code'}])
        wiz = self.env['rcs.whatsapp.send'].with_context(active_model='res.partner', active_id=self.partner.id).new({
            'mode': 'template', 'template_id': tpl.id})
        wiz._onchange_template()
        self.assertEqual(len(wiz.param_value_ids), 1)
        wiz.param_value_ids.value = '4242'
        self.assertIn('Code 4242', wiz.preview_text)

    def test_bulk_counts(self):
        second = self._make_partner(name='Bob', phone='+57 321 474 1592', country='CO')
        no_phone = self._make_partner(name='Nobody', phone=False)
        # a leftover blacklist entry in the database must not skew the count
        self.env['phone.blacklist'].sudo().search([('number', 'in', ['+' + CUSTOMER_WA_ID, '+573214741592'])]).unlink()
        tpl = self._make_template(self.number, name='bulk', body='Hi')
        self._mock_send(side_effect=['wamid.bulk.1', 'wamid.bulk.2', 'wamid.bulk.3'])
        sent, skipped = self.env['rcs.whatsapp.send']._send_template_to_records(tpl, self.partner | second | no_phone)
        self.assertEqual((sent, skipped), (2, 1))

    def test_wizard_free_text_needs_open_window(self):
        wiz = self.env['rcs.whatsapp.send'].with_context(active_model='res.partner', active_id=self.partner.id).create({
            'mode': 'text', 'body': 'hello'})
        self.assertTrue(wiz.warning)
        self._mock_send()
        with self.assertRaises(UserError):
            wiz.action_send()

    def test_server_action_sends_template(self):
        tpl = self._make_template(self.number, name='auto', body='Hi {{1}}', params=[
            {'component': 'body', 'placeholder': '1', 'source': 'field', 'field_path': 'name'}])
        action = self.env['ir.actions.server'].create({
            'name': 'wa', 'model_id': self.env['ir.model']._get_id('res.partner'),
            'state': 'rcs_whatsapp', 'rcs_whatsapp_template_id': tpl.id})
        mocked = self._mock_send()
        action.with_context(active_model='res.partner', active_ids=self.partner.ids, active_id=self.partner.id).run()
        mocked.assert_called_once()
        self.assertEqual(mocked.call_args.args[0]['template']['name'], 'auto')

    # ------------------------------------------------------------- voice notes
    def _webm_recording(self):
        """One second of a 440 Hz tone as WebM/Opus, what Chrome's MediaRecorder produces."""
        from odoo.addons.rcstudio_whatsapp_coexistence.tools import audio
        if not audio.ffmpeg_path():
            self.skipTest('ffmpeg is not installed')
        import subprocess
        return subprocess.run(
            [audio.ffmpeg_path(), '-hide_banner', '-loglevel', 'error', '-f', 'lavfi',
             '-i', 'sine=frequency=440:duration=1', '-c:a', 'libopus', '-f', 'webm', 'pipe:1'],
            capture_output=True, check=True).stdout

    def test_voice_note_goes_out_as_ogg_opus(self):
        import base64
        webm = self._webm_recording()
        conv = self._open_conversation()
        mocked = self._mock_send(return_value='wamid.voice')
        with patch.object(GraphClient, 'upload_media', return_value='media.voice') as upload:
            data = conv.inbox_send_voice(base64.b64encode(webm).decode(), 'audio/webm;codecs=opus', 1)
        message = self.env['rcs.whatsapp.message'].browse(data['id'])
        self.assertEqual(message.kind, 'audio')
        self.assertTrue(message.is_voice)
        self.assertTrue(data['is_voice'])
        self.assertEqual(message.status, 'sent')
        self.assertEqual(message.attachment_id.mimetype, 'audio/ogg')
        self.assertTrue(message.attachment_id.raw.startswith(b'OggS'), 'ffmpeg must write an OGG container')
        self.assertEqual(upload.call_args.args[1], 'audio/ogg')
        payload = mocked.call_args.args[0]
        self.assertEqual(payload['type'], 'audio')
        self.assertEqual(payload['audio'], {'id': 'media.voice', 'voice': True})
        # el texto se traduce con el idioma del usuario; lo estable es el icono
        self.assertTrue(conv.last_message_preview.startswith('🎤'), conv.last_message_preview)

    def test_voice_note_without_ffmpeg_explains(self):
        import base64
        from odoo.addons.rcstudio_whatsapp_coexistence.tools import audio
        conv = self._open_conversation()
        with patch.object(audio, 'ffmpeg_path', return_value=None):
            with self.assertRaises(UserError) as cm:
                conv.inbox_send_voice(base64.b64encode(b'\x1aE\xdf\xa3 not really webm').decode(), 'audio/webm', 1)
            self.assertIn('ffmpeg', str(cm.exception))
            # a recording that already is OGG/Opus needs no conversion
            mocked = self._mock_send(return_value='wamid.voice2')
            with patch.object(GraphClient, 'upload_media', return_value='media.v2'):
                data = conv.inbox_send_voice(base64.b64encode(b'OggS fake').decode(), 'audio/ogg;codecs=opus', 1)
            with self.assertRaises(UserError):  # claims OGG, is not
                conv.inbox_send_voice(base64.b64encode(b'not ogg').decode(), 'audio/ogg;codecs=opus', 1)
        self.assertTrue(data['is_voice'])
        self.assertEqual(mocked.call_args.args[0]['type'], 'audio')
        # anything that is not OGG/Opus (Safari records MP4/AAC) needs ffmpeg
        with patch.object(audio, 'ffmpeg_path', return_value=None):
            with self.assertRaises(UserError) as cm:
                conv.inbox_send_voice(base64.b64encode(b'....ftypM4A fake').decode(), 'audio/mp4', 2)
            self.assertIn('Safari', str(cm.exception))
            self.assertFalse(self.env['rcs.whatsapp.conversation'].inbox_capabilities()['voice_conversion'])
        self.assertEqual(self.env['rcs.whatsapp.conversation'].inbox_capabilities()['voice_conversion'],
                         bool(audio.ffmpeg_path()))

    def test_inbound_voice_flag(self):
        conv = self._open_conversation()
        with patch.object(GraphClient, 'fetch_media', return_value=(b'OggS', 'audio/ogg')):
            vals = self.number._message_vals(
                {'type': 'audio', 'audio': {'id': 'm.in.1', 'mime_type': 'audio/ogg; codecs=opus', 'voice': True}}, conv)
            plain = self.number._message_vals(
                {'type': 'audio', 'audio': {'id': 'm.in.2', 'mime_type': 'audio/mpeg'}}, conv)
        self.assertEqual(vals['kind'], 'audio')
        self.assertTrue(vals['is_voice'])
        self.assertFalse(plain['is_voice'])
