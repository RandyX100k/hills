import base64
import logging
from datetime import timedelta

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.tools.mail import plaintext2html

from ..tools.graph import GraphError

_logger = logging.getLogger(__name__)
# What Meta sends in statuses[] -> our status. 'played', added in March 2026 for
# voice notes, means the same as 'read' here: a voice note cannot be played
# without the chat being opened.
META_STATUS = {'sent': 'sent', 'delivered': 'delivered', 'read': 'read',
               'played': 'read', 'failed': 'failed'}


# Mirroring to the chatter. Reactions are noise in a record's history, and a status
# message is not a message. Media is copied up to this size; above it, and for voice
# notes whatever their size, the note carries a link to the file instead.
NOT_MIRRORED = ('reaction', 'system', 'unsupported')
MIRROR_MAX_BYTES = 10 * 1024 * 1024

# Meta's delivery ladder. A status never goes backwards.
STATUS_RANK = {'queued': 0, 'sending': 1, 'sent': 2, 'delivered': 3, 'read': 4, 'failed': 5, 'received': 5}
MAX_ATTEMPTS = 5
MEDIA_KINDS = ('image', 'video', 'audio', 'document', 'sticker')


class WhatsAppMessage(models.Model):
    """One message, in or out. This is the message itself, not a log of a send."""
    _name = 'rcs.whatsapp.message'
    _description = 'WhatsApp Message'
    _order = 'date desc, id desc'

    conversation_id = fields.Many2one('rcs.whatsapp.conversation', required=True, ondelete='cascade', index=True)
    number_id = fields.Many2one(related='conversation_id.number_id', store=True, index=True)
    partner_id = fields.Many2one(related='conversation_id.partner_id', store=True)
    direction = fields.Selection([('in', 'Inbound'), ('out', 'Outbound')], required=True, index=True)
    kind = fields.Selection([
        ('text', 'Text'), ('image', 'Image'), ('video', 'Video'), ('audio', 'Audio'),
        ('document', 'Document'), ('sticker', 'Sticker'), ('location', 'Location'),
        ('contacts', 'Contacts'), ('reaction', 'Reaction'), ('template', 'Template'),
        ('interactive', 'Interactive reply'), ('unsupported', 'Unsupported'), ('system', 'System'),
    ], required=True, default='text')
    body = fields.Text()
    caption = fields.Char()
    attachment_id = fields.Many2one('ir.attachment', ondelete='set null')
    filename = fields.Char(related='attachment_id.name')
    mime_type = fields.Char(related='attachment_id.mimetype')
    is_voice = fields.Boolean('Voice note', help="Recorded and played as a voice message, not sent as an audio file.")
    latitude = fields.Float(digits=(10, 7))
    longitude = fields.Float(digits=(10, 7))
    location_name = fields.Char()
    location_address = fields.Char()
    reaction_emoji = fields.Char()
    reaction_to_id = fields.Many2one('rcs.whatsapp.message', ondelete='set null')
    reply_to_id = fields.Many2one('rcs.whatsapp.message', string='In reply to', ondelete='set null')

    wa_message_id = fields.Char('Meta Message ID', index='btree_not_null', copy=False)
    date = fields.Datetime(required=True, default=fields.Datetime.now, index=True)
    status = fields.Selection([
        ('queued', 'Queued'), ('sending', 'Sending'), ('sent', 'Sent'), ('delivered', 'Delivered'),
        ('read', 'Read'), ('failed', 'Failed'), ('received', 'Received'),
    ], default='queued', required=True, index=True)
    error_code = fields.Char()
    error_message = fields.Text()
    attempts = fields.Integer(default=0)
    next_attempt = fields.Datetime()
    author_user_id = fields.Many2one('res.users', string='Sent by')
    is_echo = fields.Boolean(help="Sent from the phone (coexistence), mirrored here.")
    read_at = fields.Datetime()
    template_id = fields.Many2one('rcs.whatsapp.template', ondelete='set null')
    template_payload = fields.Json()
    origin_model = fields.Char()
    origin_res_id = fields.Integer()
    mail_message_id = fields.Many2one(
        'mail.message', 'Chatter note', readonly=True, copy=False, ondelete='set null',
        help="The log note this message left on the document the conversation is about.")

    # 18.0: models.Constraint does not exist, _sql_constraints instead
    _sql_constraints = [
        ('wa_message_id_unique', 'UNIQUE(wa_message_id)', 'A WhatsApp message id can only be stored once.'),
    ]

    # ---------------------------------------------------------------- lifecycle
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('direction') == 'in' and not vals.get('status'):
                vals['status'] = 'received'
        messages = super().create(vals_list)
        for message in messages:
            message.conversation_id._touch(message)
            message._notify()
        # Inbound messages, and the ones sent from the phone, are final on arrival.
        # Outbound ones are mirrored by _send() once Meta has accepted them.
        messages.filtered(lambda m: m.direction == 'in' or m.is_echo)._mirror_to_chatter()
        return messages

    def _notify(self):
        for message in self:
            message.number_id._notify('rcs_whatsapp/message', {
                'conversation_id': message.conversation_id.id,
                'message': message._to_inbox(),
                'conversation': message.conversation_id._inbox_summary(),
            })

    # ----------------------------------------------------------------- helpers
    @api.model
    def _kind_for_mimetype(self, mimetype):
        mimetype = mimetype or ''
        if mimetype.startswith('image/'):
            return 'image'
        if mimetype.startswith('video/'):
            return 'video'
        if mimetype.startswith('audio/'):
            return 'audio'
        return 'document'

    def _preview(self):
        self.ensure_one()
        if self.kind in ('text', 'template', 'interactive'):
            return (self.body or '')[:120]
        if self.kind == 'reaction':
            return self.reaction_emoji or ''
        if self.kind == 'location':
            return _('📍 %s', self.location_name or self.location_address or _('Location'))
        if self.kind == 'audio' and self.is_voice:
            return _('🎤 Voice note')
        if self.kind in MEDIA_KINDS:
            return self.caption or _('📎 %s', self.filename or dict(self._fields['kind'].selection)[self.kind])
        return dict(self._fields['kind'].selection).get(self.kind, '')

    def _attachment_url(self):
        self.ensure_one()
        return f'/web/content/{self.attachment_id.id}?download=true' if self.attachment_id else False

    def _to_inbox(self):
        self.ensure_one()
        reply = self.reply_to_id
        return {
            'id': self.id,
            'conversation_id': self.conversation_id.id,
            'direction': self.direction,
            'kind': self.kind,
            'body': self.body or '',
            'caption': self.caption or '',
            'date': fields.Datetime.to_string(self.date),
            'status': self.status,
            'error_message': self.error_message or '',
            'is_echo': self.is_echo,
            'is_voice': self.is_voice,
            'author': self.author_user_id.name if self.direction == 'out' and not self.is_echo else '',
            'attachment': {
                'id': self.attachment_id.id, 'name': self.attachment_id.name,
                'mimetype': self.attachment_id.mimetype, 'url': self._attachment_url(),
                'image_url': f'/web/image/{self.attachment_id.id}' if self.kind in ('image', 'sticker') else False,
            } if self.attachment_id else False,
            'location': {
                'latitude': self.latitude, 'longitude': self.longitude,
                'name': self.location_name, 'address': self.location_address,
            } if self.kind == 'location' else False,
            'reaction': {'emoji': self.reaction_emoji, 'to_id': self.reaction_to_id.id} if self.kind == 'reaction' else False,
            'reply_to': {'id': reply.id, 'body': reply._preview(), 'direction': reply.direction} if reply else False,
            'template': self.template_id.name if self.template_id else False,
        }

    @api.model
    def inbox_fetch(self, conversation_id, before_id, limit=60):
        messages = self.search([('conversation_id', '=', conversation_id), ('id', '<', before_id)],
                               order='date desc, id desc', limit=limit)
        return {'messages': [m._to_inbox() for m in reversed(messages)], 'has_more': len(messages) == limit}

    # ----------------------------------------------------------------- chatter
    def _mirror_to_chatter(self, target=None):
        """Log these messages on the document their conversation is about, so the whole
        exchange reads in the record's chatter the way an email thread does.

        Never fatal: a chatter that refuses a note must not cost us the WhatsApp message,
        which is why each note runs in its own savepoint. And nothing here may talk to the
        network -- the caller still holds the conversation's advisory lock.
        """
        for message in self:
            if message.kind in NOT_MIRRORED:
                continue
            where = target if target is not None else message.conversation_id._mirror_target(message)
            if where is None or not hasattr(where, 'message_post'):
                continue
            note = message.mail_message_id
            if note and note.model == where._name and note.res_id == where.id:
                continue  # already logged there
            try:
                with message.env.cr.savepoint():
                    posted = message._mirror_post(where)
                    message.sudo().mail_message_id = posted
            except Exception:  # noqa: BLE001 - the message matters more than its copy
                _logger.exception('WhatsApp: mirroring message %s to %s/%s failed',
                                  message.id, where._name, where.id)

    def _mirror_post(self, where):
        self.ensure_one()
        conv = self.conversation_id
        inbound = self.direction == 'in' and not self.is_echo
        who = conv.name or conv.phone_display or conv.phone
        if inbound:
            header = _('WhatsApp from %s', who)
            author = conv.partner_id or self.env.ref('base.partner_root', raise_if_not_found=False)
        elif self.is_echo:
            header = _('WhatsApp to %s, sent from the phone', who)
            author = self.env.ref('base.partner_root', raise_if_not_found=False)
        else:
            header = _('WhatsApp to %s', who)
            author = self.author_user_id.partner_id
        body = Markup('<p><i class="fa fa-whatsapp" role="img"></i> <b>%s</b> '
                      '&middot; <a href="#" data-oe-model="rcs.whatsapp.conversation" '
                      'data-oe-id="%s">%s</a></p>%s') % (
            header, conv.id, _('open the conversation'), self._mirror_body())
        return where.sudo().message_post(
            body=body, message_type='comment', subtype_xmlid='mail.mt_note',
            author_id=author.id if author else False,
            attachments=self._mirror_attachments())

    def _mirror_body(self):
        self.ensure_one()
        if self.kind in ('text', 'template', 'interactive'):
            text = self.body or ''
        elif self.kind == 'location':
            text = self._preview()
            if self.latitude or self.longitude:
                url = f'https://www.google.com/maps?q={self.latitude},{self.longitude}'
                return Markup('<p>%s<br/><a href="%s" target="_blank" rel="noreferrer">%s</a></p>') % (
                    text, url, _('Open in Maps'))
        else:
            text = self.caption or self._preview()
        body = plaintext2html(text) if text else Markup('')
        if self.template_id:
            body += Markup('<p class="text-muted small">%s</p>') % _('Template: %s', self.template_id.name)
        if self.attachment_id and not self._mirror_copies_file():
            body += Markup('<p><a href="/web/content/%s?download=true">%s</a></p>') % (
                self.attachment_id.id, self.attachment_id.name or _('Attachment'))
        return body

    def _mirror_copies_file(self):
        """Voice notes stay in the inbox and travel as a link: a customer who sends
        twenty of them should not double the size of the database."""
        self.ensure_one()
        attachment = self.attachment_id.sudo()
        return bool(attachment) and not self.is_voice and attachment.file_size <= MIRROR_MAX_BYTES

    def _mirror_attachments(self):
        """(name, raw content) pairs for message_post, which stores them on the record."""
        self.ensure_one()
        if not self._mirror_copies_file():
            return []
        attachment = self.attachment_id.sudo()
        # 20.0 hands back a lazy BinaryValue instead of bytes; bytes() covers both
        content = bytes(attachment.raw or b'') or base64.b64decode(attachment.datas or b'')
        return [(attachment.name or _('attachment'), content)]

    # ------------------------------------------------------------------ sending
    def _payload(self):
        """The body of POST /messages for this outbound message, in Meta's format."""
        self.ensure_one()
        conv = self.conversation_id
        payload = {'to': conv.phone}
        if self.kind == 'text':
            payload.update(type='text', text={'body': self.body or '', 'preview_url': True})
        elif self.kind in MEDIA_KINDS:
            media_id = self._upload_attachment()
            media = {'id': media_id}
            if self.caption and self.kind in ('image', 'video', 'document'):
                media['caption'] = self.caption
            if self.kind == 'document' and self.attachment_id.name:
                media['filename'] = self.attachment_id.name
            if self.kind == 'audio' and self.is_voice:
                # rendered as a voice note: microphone icon, business picture, auto-download
                media['voice'] = True
            payload.update(type=self.kind, **{self.kind: media})
        elif self.kind == 'location':
            payload.update(type='location', location={
                'latitude': self.latitude, 'longitude': self.longitude,
                'name': self.location_name or '', 'address': self.location_address or ''})
        elif self.kind == 'reaction':
            payload.update(type='reaction', reaction={
                'message_id': self.reaction_to_id.wa_message_id, 'emoji': self.reaction_emoji or ''})
        elif self.kind == 'template':
            template = dict(self.template_payload or {})
            if self.attachment_id and self.template_id.header_format in ('image', 'video', 'document'):
                media_id = self._upload_attachment()
                template = self.template_id._inject_header_media(template, media_id, self.attachment_id.name)
            payload.update(type='template', template=template)
        else:
            raise GraphError(_('Messages of type %s cannot be sent.', self.kind))
        if self.reply_to_id and self.reply_to_id.wa_message_id:
            payload['context'] = {'message_id': self.reply_to_id.wa_message_id}
        return payload

    def _upload_attachment(self):
        self.ensure_one()
        attachment = self.attachment_id.sudo()
        content = attachment.raw if attachment.raw else base64.b64decode(attachment.datas or b'')
        return self.number_id._graph().upload_media(content, attachment.mimetype, attachment.name or 'file')

    def _send(self):
        for message in self.filtered(lambda m: m.direction == 'out' and m.status in ('queued', 'failed')):
            conv = message.conversation_id
            if conv.opted_out:
                message._fail('opted_out', _('The customer opted out of WhatsApp messages.'), final=True)
                continue
            message.sudo().write({'status': 'sending', 'attempts': message.attempts + 1})
            try:
                wa_id = message.number_id._graph().send(message._payload())
            except GraphError as exc:
                message._fail(exc.code, str(exc), final=not exc.transient)
                continue
            except Exception as exc:  # noqa: BLE001 - never leave a message stuck in "sending"
                _logger.exception('Sending WhatsApp message %s failed', message.id)
                message._fail('internal', str(exc)[:500], final=True)
                continue
            message.sudo().write({'status': 'sent', 'wa_message_id': wa_id, 'error_code': False,
                                  'error_message': False, 'next_attempt': False})
            message._notify()
            message._mirror_to_chatter()

    def action_send_again(self):
        """Button and list action: retry the failed outbound messages of the selection."""
        self.filtered(lambda m: m.direction == 'out' and m.status == 'failed')._send()
        return True

    def _fail(self, code, text, final):
        self.ensure_one()
        retry = not final and self.attempts < MAX_ATTEMPTS
        self.sudo().write({
            'status': 'queued' if retry else 'failed',
            'error_code': str(code) if code is not None else False,
            'error_message': text,
            'next_attempt': fields.Datetime.now() + timedelta(minutes=5 * self.attempts) if retry else False,
        })
        self._notify()

    @api.model
    def _cron_send_queue(self):
        now = fields.Datetime.now()
        queued = self.search([('direction', '=', 'out'), ('status', '=', 'queued'),
                              '|', ('next_attempt', '=', False), ('next_attempt', '<=', now)], limit=200)
        for message in queued:
            with self.env.cr.savepoint():
                message._send()

    # ---------------------------------------------------------------- statuses
    @api.model
    def _apply_meta_status(self, status):
        """One entry of value.statuses[] from the webhook."""
        message = self.sudo().search([('wa_message_id', '=', status.get('id'))], limit=1)
        if not message:
            return
        new = META_STATUS.get(status.get('status'))
        if not new:
            # Meta adds statuses over time; one we cannot read is not a reason to
            # reject the delivery, which it would then retry for 36 hours.
            _logger.info('WhatsApp: ignoring the unknown message status %r.', status.get('status'))
            return
        if STATUS_RANK.get(new, 0) < STATUS_RANK.get(message.status, 0) and new != 'failed':
            return
        vals = {'status': new}
        if new == 'failed':
            errors = status.get('errors') or []
            first = errors[0] if errors else {}
            vals.update(error_code=str(first.get('code', '')),
                        error_message=first.get('title') or first.get('message') or
                        (first.get('error_data') or {}).get('details') or _('Meta reported a failure'))
        message.write(vals)
        message._notify()
