"""What arrives from Meta and what Odoo does with it.

Payload shapes follow the Cloud API webhook reference:
https://developers.facebook.com/docs/whatsapp/cloud-api/webhooks/payload-examples
Each `entry.changes[].field` has a handler `_webhook_<field>` below.
"""
import json
import logging

import psycopg2.errors
import re
import unicodedata
from datetime import datetime, timezone

from odoo import api, fields, models

from ..tools.graph import GraphError

_logger = logging.getLogger(__name__)

MEDIA_TYPES = ('image', 'video', 'audio', 'document', 'sticker')


def _fold(text):
    """lowercase, no accents, no punctuation: what an opt-out word is compared against."""
    text = unicodedata.normalize('NFKD', text or '')
    text = ''.join(c for c in text if not unicodedata.combining(c))
    return re.sub(r'[^a-z0-9 ]+', ' ', text.lower()).strip()


class WhatsAppNumberWebhook(models.Model):
    _inherit = 'rcs.whatsapp.number'

    # ----------------------------------------------------------------- dispatch
    def _webhook_dispatch(self, payload):
        """Route every change of every entry to its handler. Each change runs in its own
        savepoint and is logged on failure: one bad event must never lose the others, and
        the controller answers 200 regardless, because Meta does not retry on 200 and
        disables webhooks that keep failing."""
        self.ensure_one()
        self.sudo().write({'webhook_last_event': fields.Datetime.now()})
        for entry in payload.get('entry') or []:
            if not self._webhook_owns_entry(entry):
                continue
            for change in entry.get('changes') or []:
                field = change.get('field') or ''
                if not self._webhook_owns_change(change):
                    continue
                handler = getattr(self, f'_webhook_{field}', None)
                if handler is None:
                    _logger.info('WhatsApp webhook: ignoring field %r on number %s', field, self.id)
                    continue
                try:
                    with self.env.cr.savepoint():
                        handler(change.get('value') or {})
                except (psycopg2.errors.SerializationFailure, psycopg2.errors.DeadlockDetected):
                    # Two deliveries touched the same conversation at once. Let Odoo retry
                    # the whole request: swallowing this would answer 200 to Meta and lose
                    # the message for good. Re-processing is safe, messages are deduplicated.
                    raise
                except Exception:  # noqa: BLE001
                    _logger.exception('WhatsApp webhook: %s failed on number %s', field, self.id)

    def _webhook_owns_entry(self, entry):
        """Meta puts the WhatsApp Business Account in entry.id. A delivery naming another
        one is not ours: it is dropped and the request still answers 200, because a 4xx
        would only make Meta retry something that will never match."""
        self.ensure_one()
        waba_id = self.sudo().waba_id
        other = entry.get('id')
        if waba_id and other and str(other) != waba_id:
            _logger.warning('WhatsApp webhook: entry for business account %s is not this '
                            "number's (%s); dropped.", other, waba_id)
            return False
        return True

    def _webhook_owns_change(self, change):
        """Same for the number itself: value.metadata.phone_number_id, when Meta sends it,
        must be this number. Template events carry no metadata and pass through."""
        self.ensure_one()
        value = change.get('value')
        if not isinstance(value, dict):
            # Malformed: let the handler deal with it inside its own savepoint, as before
            return True
        theirs = (value.get('metadata') or {}).get('phone_number_id')
        mine = self.sudo().phone_number_id
        if theirs and mine and str(theirs) != mine:
            _logger.warning('WhatsApp webhook: event for phone number id %s is not this '
                            "number's (%s); dropped.", theirs, mine)
            return False
        return True

    # ----------------------------------------------------------------- messages
    def _webhook_messages(self, value):
        profiles = {c.get('wa_id'): (c.get('profile') or {}).get('name')
                    for c in value.get('contacts') or [] if c.get('wa_id')}
        for raw in value.get('messages') or []:
            with self.env.cr.savepoint():
                self._inbound_message(raw, profiles)
        for status in value.get('statuses') or []:
            with self.env.cr.savepoint():
                self.env['rcs.whatsapp.message']._apply_meta_status(status)

    def _webhook_smb_message_echoes(self, value):
        """Messages the business sent from the phone app (coexistence)."""
        for raw in value.get('message_echoes') or []:
            with self.env.cr.savepoint():
                self._inbound_message(raw, {}, echo=True)

    def _inbound_message(self, raw, profiles, echo=False):
        Message = self.env['rcs.whatsapp.message'].sudo()
        wa_message_id = raw.get('id')
        if wa_message_id and Message.search_count([('wa_message_id', '=', wa_message_id)]):
            return Message
        wa_id = raw.get('to') if echo else raw.get('from')
        if not wa_id:
            return Message
        conv = self.env['rcs.whatsapp.conversation']._get_or_create(self, wa_id, profiles.get(wa_id))
        vals = {
            'conversation_id': conv.id,
            'direction': 'out' if echo else 'in',
            'status': 'sent' if echo else 'received',
            'is_echo': echo,
            'wa_message_id': wa_message_id,
            'date': self._ts(raw.get('timestamp')),
        }
        vals.update(self._message_vals(raw, conv))
        message = Message.create(vals)
        if not echo and message.kind == 'text':
            self._maybe_opt_out(conv, message.body)
        return message

    def _message_vals(self, raw, conv):
        Message = self.env['rcs.whatsapp.message'].sudo()
        mtype = raw.get('type') or 'unsupported'
        vals = {}
        context = raw.get('context') or {}
        if context.get('id'):
            parent = Message.search([('wa_message_id', '=', context['id'])], limit=1)
            vals['reply_to_id'] = parent.id or False
        if mtype == 'text':
            vals.update(kind='text', body=(raw.get('text') or {}).get('body', ''))
        elif mtype in MEDIA_TYPES:
            media = raw.get(mtype) or {}
            vals.update(kind=mtype, caption=media.get('caption'))
            if mtype == 'audio':
                vals['is_voice'] = bool(media.get('voice'))
            attachment = self._download_media(media, conv, mtype)
            vals['attachment_id'] = attachment.id if attachment else False
            if not attachment:
                vals['body'] = media.get('filename') or ''
        elif mtype == 'location':
            loc = raw.get('location') or {}
            vals.update(kind='location', latitude=loc.get('latitude'), longitude=loc.get('longitude'),
                        location_name=loc.get('name'), location_address=loc.get('address'))
        elif mtype == 'contacts':
            names = [((c.get('name') or {}).get('formatted_name') or '') for c in raw.get('contacts') or []]
            phones = [p.get('phone') for c in raw.get('contacts') or [] for p in c.get('phones') or []]
            vals.update(kind='contacts', body='\n'.join(f'{n} {p or ""}'.strip() for n, p in zip(names, phones)) or ', '.join(names))
        elif mtype == 'reaction':
            reaction = raw.get('reaction') or {}
            target = Message.search([('wa_message_id', '=', reaction.get('message_id'))], limit=1)
            vals.update(kind='reaction', reaction_emoji=reaction.get('emoji') or '', reaction_to_id=target.id or False)
        elif mtype == 'button':
            vals.update(kind='interactive', body=(raw.get('button') or {}).get('text', ''))
        elif mtype == 'interactive':
            inter = raw.get('interactive') or {}
            reply = inter.get('button_reply') or inter.get('list_reply') or {}
            vals.update(kind='interactive', body=reply.get('title') or reply.get('id') or '')
        elif mtype == 'system':
            vals.update(kind='system', body=(raw.get('system') or {}).get('body', ''))
        else:
            vals.update(kind='unsupported', body=json.dumps(raw.get('errors') or raw.get(mtype) or {})[:500])
        return vals

    def _download_media(self, media, conv, mtype):
        media_id = media.get('id')
        if not media_id:
            return None
        try:
            content, mime = self._graph().fetch_media(media_id)
        except GraphError as exc:
            _logger.warning('WhatsApp media %s could not be downloaded: %s', media_id, exc)
            return None
        mime = media.get('mime_type') or mime or 'application/octet-stream'
        name = media.get('filename') or self._media_name(mtype, mime, media_id)
        return self.env['ir.attachment'].sudo().create({
            'name': name, 'raw': content, 'mimetype': mime.split(';')[0],
            'res_model': 'rcs.whatsapp.conversation', 'res_id': conv.id,
        })

    @staticmethod
    def _media_name(mtype, mime, media_id):
        ext = {'image/jpeg': 'jpg', 'image/png': 'png', 'image/webp': 'webp', 'video/mp4': 'mp4',
               'audio/ogg': 'ogg', 'audio/ogg; codecs=opus': 'ogg', 'audio/mpeg': 'mp3', 'audio/aac': 'aac',
               'application/pdf': 'pdf'}.get(mime, mime.split('/')[-1].split(';')[0] or 'bin')
        return f'{mtype}-{media_id[-10:]}.{ext}'

    @staticmethod
    def _ts(value):
        try:
            return datetime.fromtimestamp(int(value), tz=timezone.utc).replace(tzinfo=None)
        except (TypeError, ValueError, OverflowError):
            return fields.Datetime.now()

    # ------------------------------------------------------------------ opt-out
    def _maybe_opt_out(self, conv, body):
        words = {_fold(w) for w in (self._param('opt_out_words') or '').split(',') if w.strip()}
        if _fold(body) in words:
            self.env['phone.blacklist'].sudo()._add(['+' + conv.phone], message=f'WhatsApp opt-out on {self.name}')
            conv._notify()

    # ---------------------------------------------------------- coexistence sync
    def _webhook_history(self, value):
        """History packets Meta sends after a coexistence onboarding: one thread per
        customer, each with its messages, oldest first."""
        business = (value.get('metadata') or {}).get('display_phone_number') or ''
        business_digits = re.sub(r'\D', '', business)
        for packet in value.get('history') or []:
            for thread in packet.get('threads') or []:
                wa_id = thread.get('id')
                if not wa_id:
                    continue
                for raw in thread.get('messages') or []:
                    with self.env.cr.savepoint():
                        sender = re.sub(r'\D', '', raw.get('from') or '')
                        echo = bool(business_digits) and sender == business_digits
                        raw = dict(raw, to=wa_id) if echo else raw
                        self._inbound_message(raw, {}, echo=echo)

    def _webhook_smb_app_state_sync(self, value):
        """Contacts of the phone app (coexistence). Creates partners only when the setting
        allows it, and never touches an existing one."""
        if self._param('sync_contacts') != 'True':
            return
        Partner = self.env['res.partner'].sudo()
        Conversation = self.env['rcs.whatsapp.conversation']
        for item in value.get('state_sync') or []:
            if item.get('type') != 'contact' or item.get('action') != 'add':
                continue
            contact = item.get('contact') or {}
            wa_id = re.sub(r'\D', '', contact.get('phone_number') or '')
            name = contact.get('full_name') or contact.get('first_name')
            if not wa_id or not name:
                continue
            with self.env.cr.savepoint():
                if not Conversation._find_partner(wa_id):
                    field = 'mobile' if 'mobile' in Partner._fields else 'phone'
                    Partner.create({'name': name, field: '+' + wa_id})

    # ----------------------------------------------------------------- templates
    def _webhook_message_template_status_update(self, value):
        Template = self.env['rcs.whatsapp.template'].sudo()
        template = Template.with_context(active_test=False).search([
            ('number_id', '=', self.id),
            '|', ('meta_template_id', '=', str(value.get('message_template_id') or '')),
            '&', ('name', '=', value.get('message_template_name')),
            ('language', '=', value.get('message_template_language')),
        ], limit=1)
        if not template:
            return
        event = (value.get('event') or '').lower()
        if event in ('approved', 'rejected', 'pending', 'paused', 'disabled'):
            from .template import cls_reason
            template.write({'status': event, 'rejection_reason': cls_reason(value.get('reason'))})

    def _webhook_account_update(self, value):
        self._refresh_info_quietly()

    def _webhook_phone_number_quality_update(self, value):
        self._refresh_info_quietly()

    def _refresh_info_quietly(self):
        try:
            info = self._graph().phone_info()
        except GraphError:
            return
        self.sudo().write({'quality_rating': info.get('quality_rating'), 'last_check': fields.Datetime.now()})

    # ------------------------------------------------------------- verification
    @api.model
    def _webhook_find(self, number_id, token):
        number = self.sudo().browse(int(number_id)).exists()
        if number and number.webhook_token and token == number.webhook_token:
            return number
        return self.sudo().browse()
