import base64
import zlib
from collections import defaultdict
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from ..tools import phone as phone_tools
from ..tools import audio as voice_tools
from .document import ANCHOR_STALE_DAYS, DOCUMENT_TYPES, document_types

WINDOW_HOURS = 24  # Meta's customer service window


class WhatsAppConversation(models.Model):
    """The thread with one customer on one business number. Nothing to do with Discuss."""
    _name = 'rcs.whatsapp.conversation'
    _description = 'WhatsApp Conversation'
    _order = 'last_activity desc, id desc'
    _rec_name = 'name'

    number_id = fields.Many2one('rcs.whatsapp.number', required=True, ondelete='cascade', index=True)
    phone = fields.Char(required=True, index=True,
                        help="The customer's number as WhatsApp identifies it: country code and "
                             "number, digits only.")
    phone_display = fields.Char(compute='_compute_phone_display', store=True)
    partner_id = fields.Many2one('res.partner', string='Contact', index=True, ondelete='set null')
    profile_name = fields.Char(help="The name the customer shows on WhatsApp.")
    name = fields.Char(compute='_compute_name', store=True)

    message_ids = fields.One2many('rcs.whatsapp.message', 'conversation_id')
    last_message_id = fields.Many2one('rcs.whatsapp.message', readonly=True)
    last_message_preview = fields.Char(readonly=True)
    last_activity = fields.Datetime(readonly=True, index=True, default=fields.Datetime.now)
    last_inbound_at = fields.Datetime(readonly=True)
    window_expires_at = fields.Datetime(compute='_compute_window', store=True)
    window_open = fields.Boolean(compute='_compute_window_open', search='_search_window_open')

    unread_count = fields.Integer(readonly=True, default=0)
    assigned_user_id = fields.Many2one('res.users', string='Assigned to', index=True)
    state = fields.Selection([('open', 'Open'), ('closed', 'Closed')], default='open', required=True)
    opted_out = fields.Boolean(compute='_compute_opted_out')
    link_ids = fields.One2many('rcs.whatsapp.conversation.link', 'conversation_id', string='Documents')
    link_count = fields.Integer(compute='_compute_link_count')

    # What the conversation is currently about. See models/document.py for why this is
    # a remembered choice and not something the module works out per message.
    anchor_model = fields.Char('Anchored on', index='btree_not_null', copy=False)
    anchor_res_id = fields.Integer('Anchored record', copy=False)
    anchor_name = fields.Char(compute='_compute_anchor_name')

    # 18.0: models.Constraint does not exist, _sql_constraints instead
    _sql_constraints = [
        ('number_phone_unique', 'UNIQUE(number_id, phone)',
         'There is already a conversation with this phone on this business number.'),
    ]

    # ------------------------------------------------------------------ computes
    @api.depends('phone')
    def _compute_phone_display(self):
        for conv in self:
            conv.phone_display = phone_tools.to_display(conv.phone)

    @api.depends('partner_id.name', 'profile_name', 'phone_display')
    def _compute_name(self):
        for conv in self:
            conv.name = conv.partner_id.name or conv.profile_name or conv.phone_display or conv.phone

    @api.depends('last_inbound_at')
    def _compute_window(self):
        for conv in self:
            conv.window_expires_at = (conv.last_inbound_at + timedelta(hours=WINDOW_HOURS)
                                      if conv.last_inbound_at else False)

    @api.depends('window_expires_at')
    def _compute_window_open(self):
        now = fields.Datetime.now()
        for conv in self:
            conv.window_open = bool(conv.window_expires_at and conv.window_expires_at > now)

    def _search_window_open(self, operator, value):
        # the ORM may hand over ('in', [True]) for ('=', True): normalise both shapes
        if isinstance(value, (list, tuple, set)):
            value = any(value)
        wants_open = bool(value) != (operator in ('!=', 'not in'))
        if wants_open:
            return [('window_expires_at', '>', fields.Datetime.now())]
        return ['|', ('window_expires_at', '=', False), ('window_expires_at', '<=', fields.Datetime.now())]

    def _compute_opted_out(self):
        Blacklist = self.env['phone.blacklist'].sudo()
        numbers = {'+' + conv.phone for conv in self if conv.phone}
        listed = set(Blacklist.search([('number', 'in', list(numbers)), ('active', '=', True)]).mapped('number')) if numbers else set()
        for conv in self:
            conv.opted_out = ('+' + (conv.phone or '')) in listed

    def _compute_link_count(self):
        for conv in self:
            conv.link_count = len(conv.link_ids)

    @api.depends('anchor_model', 'anchor_res_id')
    def _compute_anchor_name(self):
        """Resolved one browse per model, not one per conversation: the inbox list reads
        eighty of these at a time."""
        wanted = defaultdict(set)
        for conv in self:
            conv.anchor_name = False
            if conv.anchor_model and conv.anchor_res_id and conv.anchor_model in self.env:
                wanted[conv.anchor_model].add(conv.anchor_res_id)
        names = {}
        for model, ids in wanted.items():
            try:
                for record in self.env[model].sudo().browse(ids).exists():
                    names[(model, record.id)] = record.display_name
            except Exception:  # noqa: BLE001 - a broken document must not break the inbox
                continue
        for conv in self:
            if conv.anchor_model and conv.anchor_res_id:
                conv.anchor_name = names.get((conv.anchor_model, conv.anchor_res_id),
                                             f'{conv.anchor_model} #{conv.anchor_res_id}')

    # ------------------------------------------------------------- get or create
    @api.model
    def _get_or_create(self, number, wa_id, profile_name=None):
        """The one entry point for finding a thread. Serialised per (number, phone) with an
        advisory lock so two webhooks arriving together cannot create it twice."""
        self.env.cr.execute('SELECT pg_advisory_xact_lock(%s)',
                            [zlib.crc32(f'{number.id}:{wa_id}'.encode())])
        conv = self.sudo().search([('number_id', '=', number.id), ('phone', '=', wa_id)], limit=1)
        if conv:
            if profile_name and conv.profile_name != profile_name:
                conv.profile_name = profile_name
            return conv
        return self.sudo().create({
            'number_id': number.id,
            'phone': wa_id,
            'profile_name': profile_name,
            'partner_id': self._find_partner(wa_id).id,
        })

    @api.model
    def _find_partner(self, wa_id):
        """Best contact for a phone: never one of our own companies, and among several
        matches the one updated most recently (a rough proxy for 'the one in use')."""
        Partner = self.env['res.partner'].sudo()
        own = self.env['res.company'].sudo().search([]).partner_id
        fields_ = [f for f in ('phone', 'mobile') if f in Partner._fields]
        if not fields_:
            return Partner
        # the sanitized column compares exactly; the raw columns need a digits match
        domain = ['|'] * (len(fields_) - 1) + [(f, 'ilike', wa_id[-8:]) for f in fields_]
        if 'phone_sanitized' in Partner._fields:
            domain = ['|', ('phone_sanitized', '=', '+' + wa_id)] + domain
        candidates = Partner.search(domain + [('id', 'not in', own.ids)], order='write_date desc', limit=20)
        for partner in candidates:
            for f in fields_:
                if phone_tools.to_wa_id(partner[f], partner.country_id.code) == wa_id:
                    return partner
        return Partner

    # ----------------------------------------------------------------- lifecycle
    def _touch(self, message):
        """Keep the summary fields in step with the latest message."""
        for conv in self:
            vals = {
                'last_message_id': message.id,
                'last_message_preview': message._preview(),
                'last_activity': message.date or fields.Datetime.now(),
                'state': 'open',
            }
            if message.direction == 'in' and not message.is_echo:
                vals['last_inbound_at'] = message.date
                if not message.read_at:
                    vals['unread_count'] = conv.unread_count + 1
            conv.sudo().write(vals)

    def mark_read(self):
        now = fields.Datetime.now()
        for conv in self:
            unread = conv.message_ids.filtered(lambda m: m.direction == 'in' and not m.read_at)
            unread.sudo().write({'read_at': now})
            conv.sudo().write({'unread_count': 0})
            if unread and conv.number_id._param('auto_read') == 'True':
                last = unread.sorted('date')[-1:]
                if last.wa_message_id:
                    try:
                        conv.number_id._graph().mark_read(last.wa_message_id)
                    except Exception:  # noqa: BLE001 - a read receipt is never worth an error
                        pass
        self._notify()

    def action_open_inbox(self):
        self.ensure_one()
        action = self.env['ir.actions.actions']._for_xml_id('rcstudio_whatsapp_coexistence.action_inbox')
        action['params'] = {'conversation_id': self.id}
        action['context'] = {'conversation_id': self.id}
        return action

    def action_blacklist_add(self):
        for conv in self:
            self.env['phone.blacklist'].sudo()._add(['+' + conv.phone], message=_('Added from the WhatsApp conversation'))
        self.invalidate_recordset(['opted_out'])
        self._notify()

    def action_blacklist_remove(self):
        for conv in self:
            self.env['phone.blacklist'].sudo()._remove(['+' + conv.phone], message=_('Removed from the WhatsApp conversation'))
        self.invalidate_recordset(['opted_out'])
        self._notify()

    def action_assign_me(self):
        self.write({'assigned_user_id': self.env.uid})
        self._notify()

    def action_close(self):
        self.write({'state': 'closed'})
        self._notify()

    def action_reopen(self):
        self.write({'state': 'open'})
        self._notify()

    def _link(self, model, res_id):
        """Remember a document from which this customer was written to."""
        self.ensure_one()
        Link = self.env['rcs.whatsapp.conversation.link'].sudo()
        if not Link.search_count([('conversation_id', '=', self.id), ('model', '=', model), ('res_id', '=', res_id)]):
            Link.create({'conversation_id': self.id, 'model': model, 'res_id': res_id})

    # -------------------------------------------------------------------- anchor
    def _anchor_record(self):
        """The document this conversation is about, or None. Never raises: the record may
        have been deleted, or its module uninstalled, long after the anchor was set."""
        self.ensure_one()
        if not self.anchor_model or not self.anchor_res_id or self.anchor_model not in self.env:
            return None
        record = self.env[self.anchor_model].sudo().browse(self.anchor_res_id).exists()
        return record or None

    def _anchor_summary(self):
        self.ensure_one()
        if not self.anchor_model or not self.anchor_res_id:
            return False
        return {'model': self.anchor_model, 'res_id': self.anchor_res_id,
                'name': self.anchor_name or f'{self.anchor_model} #{self.anchor_res_id}'}

    def _anchor_set(self, record):
        """Make these conversations about `record`, and remember it among the documents."""
        for conv in self:
            conv.sudo().write({'anchor_model': record._name, 'anchor_res_id': record.id})
            conv._link(record._name, record.id)
        self.invalidate_recordset(['anchor_name'])
        self._notify()

    def _anchor_clear(self, notify=True):
        self.sudo().write({'anchor_model': False, 'anchor_res_id': False})
        if notify:
            self._notify()

    def _anchor_auto(self, record):
        """Sending from a record makes the conversation about it -- unless it is already
        about something else that is still open. An anchor is never stolen silently."""
        self.ensure_one()
        if record is None or not hasattr(record, 'message_post'):
            return
        current = self._anchor_record()
        if current is None or self._anchor_is_closed(current):
            self._anchor_set(record)

    @api.model
    def _anchor_is_closed(self, record):
        """Archived counts as closed for anything; the rest is per model."""
        try:
            if 'active' in record._fields and not record.sudo().active:
                return True
            check = DOCUMENT_TYPES.get(record._name, {}).get('closed')
            return bool(check and check(record.sudo()))
        except Exception:  # noqa: BLE001 - the cron must survive a broken document
            return False

    def _mirror_enabled(self):
        self.ensure_one()
        return self.number_id._param('mirror_chatter', 'True') != 'False'

    def _mirror_target(self, message):
        """Where a message is logged: the record it was sent from when there is one, the
        anchor otherwise, the contact as a last resort."""
        self.ensure_one()
        if not self._mirror_enabled():
            return None
        if message.origin_model and message.origin_res_id and message.origin_model in self.env:
            record = self.env[message.origin_model].sudo().browse(message.origin_res_id).exists()
            if record:
                return record
        return self._anchor_record() or (self.partner_id.sudo() or None)

    def _mirror_backlog(self, record, limit=50):
        """Pour what has already been said into a document just created or just anchored,
        so the salesperson opens it with the customer's request already in the chatter."""
        self.ensure_one()
        messages = self.env['rcs.whatsapp.message'].sudo().search(
            [('conversation_id', '=', self.id)], order='date asc, id asc', limit=limit)
        messages._mirror_to_chatter(target=record)

    @api.model
    def _cron_release_anchors(self):
        """Let go of documents that are finished, gone, or that nobody has touched in a
        month. The WhatsApp thread is forever; the opportunity is not."""
        stale_before = fields.Datetime.now() - timedelta(days=ANCHOR_STALE_DAYS)
        convs = self.sudo().search([('anchor_model', '!=', False)])
        quiet = convs.filtered(lambda c: not c.last_activity or c.last_activity < stale_before)
        quiet._anchor_clear(notify=False)
        by_model = defaultdict(lambda: self.browse())
        for conv in convs - quiet:
            by_model[conv.anchor_model] |= conv
        for model, group in by_model.items():
            if model not in self.env:
                group._anchor_clear(notify=False)
                continue
            records = self.env[model].sudo().browse(group.mapped('anchor_res_id')).exists()
            keep = {record.id for record in records if not self._anchor_is_closed(record)}
            group.filtered(lambda c: c.anchor_res_id not in keep)._anchor_clear(notify=False)

    # --------------------------------------------------------- anchor from the inbox
    def inbox_anchor_options(self):
        """What the header dropdown offers: documents already written to from here, open
        documents of this customer, and the types the user may create."""
        self.ensure_one()
        seen, options = set(), []
        for link in self.link_ids:
            key = (link.model, link.res_id)
            if key not in seen:
                seen.add(key)
                options.append({'model': link.model, 'res_id': link.res_id, 'name': link.display_name})
        for model, entry in document_types(self.env):
            finder = entry.get('candidates')
            if not finder:
                continue
            try:
                for record in finder(self):
                    key = (model, record.id)
                    if key not in seen:
                        seen.add(key)
                        options.append({'model': model, 'res_id': record.id, 'name': record.display_name})
            except AccessError:
                continue  # no rights on that model: simply nothing to offer
        return {'options': options, 'current': self._anchor_summary()}

    @api.model
    def _document_allowed(self, entry):
        group = entry.get('group')
        return not group or self.env.user.has_group(group)

    def inbox_set_anchor(self, model, res_id):
        self.ensure_one()
        if model not in self.env:
            raise UserError(_('Unknown document type %s.', model))
        record = self.env[model].browse(res_id)
        record.check_access('read')
        if not record.exists():
            raise UserError(_('That document no longer exists.'))
        self._anchor_set(record)
        self._mirror_backlog(record)
        return self._inbox_summary()

    def inbox_clear_anchor(self):
        self.ensure_one()
        self._anchor_clear()
        return self._inbox_summary()

    def inbox_create_document(self, model):
        """The "Create opportunity" button: create it, anchor to it, pour the history in
        and hand back a link, so the operator never leaves the conversation."""
        self.ensure_one()
        entry = DOCUMENT_TYPES.get(model)
        if not entry or not entry.get('create') or model not in self.env:
            raise UserError(_('Nothing of type %s can be created from a conversation.', model))
        if not self._document_allowed(entry):
            raise AccessError(_('You are not allowed to create a %s.', entry['label'](self)))
        record = entry['create'](self)
        self._anchor_set(record)
        self._mirror_backlog(record)
        return {'model': record._name, 'res_id': record.id, 'name': record.display_name,
                'conversation': self._inbox_summary()}

    def inbox_create_partner(self):
        """The "Create contact" button. Never automatic (see _crm_lead_create): the operator
        decides this stranger is worth a contact. Reuses a contact that appeared since, and
        links every thread with this phone that has none yet."""
        self.ensure_one()
        partner = self.partner_id or self._find_partner(self.phone)
        if not partner:
            region = phone_tools.region_of(self.phone)
            country = self.env['res.country'].search([('code', '=', region)], limit=1) if region else None
            partner = self.env['res.partner'].create({
                'name': self.profile_name or self.phone_display,
                'phone': self.phone_display,
                'country_id': country.id if country else False,
            })
        threads = self | self.sudo().search([('phone', '=', self.phone), ('partner_id', '=', False)])
        threads.sudo().write({'partner_id': partner.id})
        threads._notify()
        return {'partner_id': partner.id, 'name': partner.display_name,
                'conversation': self._inbox_summary()}

    # ------------------------------------------------------------------- sending
    def _check_can_send(self):
        self.ensure_one()
        if self.opted_out:
            raise UserError(_('%s opted out of WhatsApp messages from you.', self.name))
        if self.number_id.status != 'connected':
            raise UserError(_('The business number %s is not connected.', self.number_id.name))

    def send_text(self, body, reply_to=None, attachment=None, origin=None):
        """A free-form message: only inside the 24-hour window."""
        self.ensure_one()
        self._check_can_send()
        if not self.window_open:
            raise UserError(_('The 24-hour window is closed. Send a template to reopen the conversation.'))
        kind = 'text'
        if attachment:
            kind = self.env['rcs.whatsapp.message']._kind_for_mimetype(attachment.mimetype)
        if origin is not None:
            self._anchor_auto(origin)
            self._link(origin._name, origin.id)
        message = self.env['rcs.whatsapp.message'].create({
            'conversation_id': self.id,
            'direction': 'out',
            'kind': kind,
            'body': body if kind == 'text' else False,
            'caption': body if kind != 'text' else False,
            'attachment_id': attachment.id if attachment else False,
            'reply_to_id': reply_to.id if reply_to else False,
            'author_user_id': self.env.uid,
            'origin_model': origin._name if origin is not None else False,
            'origin_res_id': origin.id if origin is not None else False,
        })
        message._send()
        return message

    def send_template(self, template, record=None, ask_values=None, origin=None):
        """A template message; allowed at any time. `record` feeds the placeholders."""
        self.ensure_one()
        self._check_can_send()
        payload, preview, attachment_vals = template.render(record=record, ask_values=ask_values)
        vals = {
            'conversation_id': self.id,
            'direction': 'out',
            'kind': 'template',
            'body': preview,
            'template_id': template.id,
            'template_payload': payload,
            'author_user_id': self.env.uid,
        }
        if origin:
            vals.update(origin_model=origin._name, origin_res_id=origin.id)
            self._anchor_auto(origin)
        message = self.env['rcs.whatsapp.message'].create(vals)
        if attachment_vals:
            attachment = self.env['ir.attachment'].sudo().create(dict(
                attachment_vals, res_model='rcs.whatsapp.message', res_id=message.id))
            message.sudo().write({'attachment_id': attachment.id})
        if origin:
            self._link(origin._name, origin.id)
        message._send()
        return message

    # --------------------------------------------------------------- live inbox
    def _notify(self):
        for conv in self:
            conv.number_id._notify('rcs_whatsapp/conversation', conv._inbox_summary())

    def _inbox_summary(self):
        self.ensure_one()
        partner = self.partner_id
        return {
            'id': self.id,
            'name': self.name,
            'phone': self.phone,
            'phone_display': self.phone_display,
            'partner_id': partner.id or False,
            'partner_name': partner.name or False,
            'avatar_url': f'/web/image/res.partner/{partner.id}/avatar_128' if partner else False,
            'number_id': self.number_id.id,
            'number_name': self.number_id.name,
            'number_icon_url': (f'/web/image/rcs.whatsapp.number/{self.number_id.id}/icon'
                                if self.number_id.icon else False),
            'preview': self.last_message_preview or '',
            'last_activity': fields.Datetime.to_string(self.last_activity) if self.last_activity else False,
            'unread_count': self.unread_count,
            'assigned_user_id': self.assigned_user_id.id or False,
            'assigned_user_name': self.assigned_user_id.name or False,
            'state': self.state,
            'window_open': self.window_open,
            'window_expires_at': fields.Datetime.to_string(self.window_expires_at) if self.window_expires_at else False,
            'opted_out': self.opted_out,
            'links': [{'model': l.model, 'res_id': l.res_id, 'name': l.display_name} for l in self.link_ids],
            'anchor': self._anchor_summary(),
        }

    @api.model
    def inbox_list(self, number_id=None, filter_='all', search='', limit=80, offset=0):
        domain = [('state', '=', 'open')] if filter_ != 'closed' else [('state', '=', 'closed')]
        if number_id:
            domain.append(('number_id', '=', number_id))
        if filter_ == 'unread':
            domain.append(('unread_count', '>', 0))
        elif filter_ == 'mine':
            domain.append(('assigned_user_id', '=', self.env.uid))
        if search:
            domain += ['|', '|', ('name', 'ilike', search), ('phone', 'ilike', phone_tools.digits(search) or search),
                       ('message_ids.body', 'ilike', search)]
        convs = self.search(domain, limit=limit, offset=offset)
        numbers = self.env['rcs.whatsapp.number'].search([])
        return {
            'conversations': [c._inbox_summary() for c in convs],
            'numbers': [{'id': n.id, 'name': n.name, 'color': n.color,
                         'icon_url': f'/web/image/rcs.whatsapp.number/{n.id}/icon' if n.icon else False,
                         'status': n.status} for n in numbers],
            'unread_total': sum(self.search([('state', '=', 'open'), ('unread_count', '>', 0)]).mapped('unread_count')),
        }

    def inbox_open(self, limit=60):
        self.ensure_one()
        self.mark_read()
        messages = self.env['rcs.whatsapp.message'].search(
            [('conversation_id', '=', self.id)], order='date desc, id desc', limit=limit)
        return {
            'conversation': self._inbox_summary(),
            'messages': [m._to_inbox() for m in reversed(messages)],
            'has_more': len(messages) == limit,
        }

    def inbox_send(self, body='', reply_to_id=None, attachment_id=None):
        self.ensure_one()
        reply_to = self.env['rcs.whatsapp.message'].browse(reply_to_id) if reply_to_id else None
        attachment = self.env['ir.attachment'].browse(attachment_id) if attachment_id else None
        if attachment:
            attachment.sudo().write({'res_model': 'rcs.whatsapp.message', 'res_id': 0})
        message = self.send_text(body, reply_to=reply_to, attachment=attachment)
        if attachment:
            attachment.sudo().write({'res_id': message.id})
        return message._to_inbox()

    def inbox_send_location(self, latitude, longitude, name='', address=''):
        """A location pin from the inbox composer (inside the 24-hour window)."""
        self.ensure_one()
        self._check_can_send()
        if not self.window_open:
            raise UserError(_('The 24-hour window is closed. Send a template to reopen the conversation.'))
        message = self.env['rcs.whatsapp.message'].create({
            'conversation_id': self.id, 'direction': 'out', 'kind': 'location',
            'latitude': latitude, 'longitude': longitude,
            'location_name': name or False, 'location_address': address or False,
            'author_user_id': self.env.uid,
        })
        message._send()
        return message._to_inbox()

    @api.model
    def inbox_capabilities(self):
        """What the server can do for the composer. Voice notes are made in the browser
        (Opus recorded and packaged as OGG there); ffmpeg on the server is only needed for
        browsers that cannot record Opus, Safari above all."""
        return {
            'voice_conversion': bool(voice_tools.ffmpeg_path()),
            'documents': [{'model': model, 'label': entry['label'](self)}
                          for model, entry in document_types(self.env)
                          if entry.get('create') and self._document_allowed(entry)],
        }

    def inbox_send_voice(self, data, mimetype='', duration=0):
        """A voice note recorded in the inbox composer. Whatever the browser produced is
        converted to OGG/Opus, which WhatsApp renders as a voice message rather than as an
        audio file. Inside the 24-hour window only, like any free-form message."""
        self.ensure_one()
        self._check_can_send()
        if not self.window_open:
            raise UserError(_('The 24-hour window is closed. Send a template to reopen the conversation.'))
        try:
            content, mimetype = voice_tools.to_voice_note(base64.b64decode(data or b''), mimetype)
        except voice_tools.AudioError as exc:
            raise UserError(_('The voice note could not be prepared: %s', exc))
        attachment = self.env['ir.attachment'].sudo().create({
            'name': fields.Datetime.now().strftime('voice-%Y%m%d-%H%M%S.ogg'),
            'raw': content, 'mimetype': 'audio/ogg',
            'res_model': 'rcs.whatsapp.message', 'res_id': 0,
        })
        message = self.env['rcs.whatsapp.message'].create({
            'conversation_id': self.id, 'direction': 'out', 'kind': 'audio', 'is_voice': True,
            'attachment_id': attachment.id, 'author_user_id': self.env.uid,
        })
        attachment.write({'res_id': message.id})
        message._send()
        return message._to_inbox()

    @api.model
    def inbox_unread_total(self):
        return sum(self.search([('state', '=', 'open'), ('unread_count', '>', 0)]).mapped('unread_count'))


class WhatsAppConversationLink(models.Model):
    """A document the customer was written to from: the quotation, the delivery..."""
    _name = 'rcs.whatsapp.conversation.link'
    _description = 'WhatsApp Conversation Document'
    _order = 'id desc'

    conversation_id = fields.Many2one('rcs.whatsapp.conversation', required=True, ondelete='cascade', index=True)
    model = fields.Char(required=True)
    res_id = fields.Integer(required=True)
    display_name = fields.Char(compute='_compute_display_name')

    @api.depends('model', 'res_id')
    def _compute_display_name(self):
        for link in self:
            record = self.env[link.model].browse(link.res_id) if link.model in self.env else None
            try:
                link.display_name = record.display_name if record and record.exists() else f'{link.model} #{link.res_id}'
            except Exception:  # noqa: BLE001 - a deleted or restricted record must not break the form
                link.display_name = f'{link.model} #{link.res_id}'

    def action_open(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': self.model, 'res_id': self.res_id,
                'view_mode': 'form', 'target': 'current'}
