import json
import logging


from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..tools import phone as phone_tools

_logger = logging.getLogger(__name__)

# Models that get the "Send by WhatsApp" action when they exist in the database.
BINDING_MODELS = [
    'res.partner', 'sale.order', 'account.move', 'stock.picking', 'crm.lead', 'project.task',
    'event.registration', 'calendar.event', 'pos.order', 'purchase.order', 'helpdesk.ticket',
    'hr.applicant', 'maintenance.request', 'repair.order',
]


class WhatsAppSend(models.TransientModel):
    """Send a template (or free text) to one or many records, or to a phone."""
    _name = 'rcs.whatsapp.send'
    _description = 'Send by WhatsApp'

    number_id = fields.Many2one('rcs.whatsapp.number', string='From', required=True,
                                domain=[('status', '=', 'connected')])
    model = fields.Char()
    res_ids = fields.Char(help="JSON list of record ids")
    record_count = fields.Integer(compute='_compute_records')
    record_name = fields.Char(compute='_compute_records')
    partner_id = fields.Many2one('res.partner', string='Contact')
    phone = fields.Char()
    phone_wa = fields.Char(compute='_compute_phone_wa')
    conversation_id = fields.Many2one('rcs.whatsapp.conversation', compute='_compute_conversation')
    window_open = fields.Boolean(compute='_compute_conversation')

    mode = fields.Selection([('template', 'Template'), ('text', 'Free text (inside the 24-hour window)')],
                            required=True, default='template')
    template_id = fields.Many2one('rcs.whatsapp.template', string='Template',
                                  domain="[('id', 'in', allowed_template_ids)]")
    allowed_template_ids = fields.Many2many('rcs.whatsapp.template', compute='_compute_allowed_templates')
    template_model_name = fields.Char(related='template_id.model_name')
    record_ref = fields.Reference(
        selection='_selection_target_model', string='Fill from',
        help="The record the template is filled from when the wizard is not opened from one, "
             "e.g. the quotation to attach.")
    param_value_ids = fields.One2many('rcs.whatsapp.send.param', 'send_id', string='Values')
    template_header_format = fields.Selection(related='template_id.header_format')
    location_latitude = fields.Float(digits=(10, 7))
    location_longitude = fields.Float(digits=(10, 7))
    location_name = fields.Char()
    location_address = fields.Char()
    body = fields.Text()
    attachment_ids = fields.Many2many('ir.attachment', string='Attachments')
    preview_text = fields.Text(compute='_compute_preview')
    warning = fields.Char(compute='_compute_warning')

    @api.model
    def _selection_target_model(self):
        """Models any template applies to, so the inbox can fill a template from a record."""
        models = self.env['rcs.whatsapp.template'].sudo()._read_group(
            [('model_id', '!=', False)], ['model_id'], ['__count'])
        out = []
        for model, _count in models:
            if model.model in self.env:
                out.append((model.model, model.name))
        return sorted(out, key=lambda m: m[1])

    @api.model
    def chatter_models(self):
        """Models whose chatter shows the WhatsApp button: the ones with a Send by
        WhatsApp binding in this database."""
        bindings = self.env['ir.actions.act_window'].sudo().search([
            ('res_model', '=', 'rcs.whatsapp.send'), ('binding_model_id', '!=', False)])
        return sorted(set(bindings.mapped('binding_model_id.model')))

    # ---------------------------------------------------------------- defaults
    @api.model
    def default_get(self, fields_list):
        vals = super().default_get(fields_list)
        ctx = self.env.context
        model = ctx.get('active_model') or ctx.get('default_model')
        ids = ctx.get('active_ids') or ([ctx['active_id']] if ctx.get('active_id') else [])
        if model and ids and model in self.env:
            vals['model'] = model
            vals['res_ids'] = json.dumps(ids)
            if len(ids) == 1:
                phone, partner = self._detect_phone(self.env[model].browse(ids[0]))
                vals.setdefault('phone', ctx.get('default_phone') or phone)
                if partner:
                    vals.setdefault('partner_id', partner.id)
        if not vals.get('number_id'):
            vals['number_id'] = self._default_number(vals, model, ids).id
        return vals

    @api.model
    def _detect_phone(self, record):
        """(phone, partner) for a record: its own phone fields first, then its partner's."""
        partner = record if record._name == 'res.partner' else (
            record.partner_id if 'partner_id' in record._fields and record.partner_id else None)
        if hasattr(record, '_phone_get_number_fields'):
            candidates = list(record._phone_get_number_fields())
        else:
            candidates = [f for f in ('mobile', 'phone') if f in record._fields]
        for name in candidates:
            if record[name]:
                return record[name], partner
        if partner is not None:
            for name in ('mobile', 'phone'):
                if name in partner._fields and partner[name]:
                    return partner[name], partner
        return None, partner

    @api.model
    def _default_number(self, vals, model, ids):
        """Reply from the number that already talks to this phone, so the 24-hour window is
        read from the right conversation; otherwise the first connected number."""
        Number = self.env['rcs.whatsapp.number']
        numbers = Number.search([('status', '=', 'connected')])
        if vals.get('phone') and len(numbers) > 1:
            record = self.env[model].browse(ids[0]) if model and len(ids) == 1 else None
            partner = self.env['res.partner'].browse(vals['partner_id']) if vals.get('partner_id') else None
            wa_id = phone_tools.to_wa_id(vals['phone'], self._country_code(record, partner))
            if wa_id:
                conv = self.env['rcs.whatsapp.conversation'].search(
                    [('number_id', 'in', numbers.ids), ('phone', '=', wa_id)],
                    order='last_activity desc, id desc', limit=1)
                if conv:
                    return conv.number_id
        return numbers[:1]

    def _country_code(self, record, partner):
        for candidate in (record, partner, self.env.company):
            if candidate is not None and 'country_id' in candidate._fields and candidate.country_id:
                return candidate.country_id.code
        return None

    # ---------------------------------------------------------------- computes
    @api.depends('number_id', 'model')
    def _compute_allowed_templates(self):
        """From a record: its model's templates plus the generic ones. From the inbox
        (no record): every approved template, the record is picked in the wizard."""
        Template = self.env['rcs.whatsapp.template']
        for wiz in self:
            domain = [('number_id', '=', wiz.number_id.id), ('status', '=', 'approved'),
                      ('available_on_records', '=', True)]
            if wiz.model:
                domain += ['|', ('model_name', '=', wiz.model), ('model_id', '=', False)]
            wiz.allowed_template_ids = Template.search(domain) if wiz.number_id else Template

    @api.depends('model', 'res_ids', 'record_ref')
    def _compute_records(self):
        for wiz in self:
            records = wiz._records()
            wiz.record_count = len(records)
            wiz.record_name = records.display_name if len(records) == 1 else ''

    def _records(self):
        self.ensure_one()
        if not self.model or self.model not in self.env:
            if self.record_ref and self.record_ref.exists():
                return self.record_ref
            return self.env['res.partner'].browse()
        try:
            ids = json.loads(self.res_ids or '[]')
        except ValueError:
            ids = []
        return self.env[self.model].browse(ids).exists()

    @api.depends('phone', 'partner_id')
    def _compute_phone_wa(self):
        for wiz in self:
            records = wiz._records()
            record = records[:1] if len(records) == 1 else None
            wiz.phone_wa = phone_tools.to_wa_id(wiz.phone, wiz._country_code(record, wiz.partner_id or None))

    @api.depends('number_id', 'phone_wa')
    def _compute_conversation(self):
        Conversation = self.env['rcs.whatsapp.conversation']
        for wiz in self:
            conv = Conversation.search([('number_id', '=', wiz.number_id.id), ('phone', '=', wiz.phone_wa)],
                                       limit=1) if wiz.number_id and wiz.phone_wa else Conversation
            wiz.conversation_id = conv
            wiz.window_open = conv.window_open if conv else False

    @api.depends('mode', 'template_id', 'body', 'param_value_ids.value', 'record_ref',
                 'location_latitude', 'location_longitude', 'location_name', 'location_address')
    def _compute_preview(self):
        for wiz in self:
            if wiz.mode == 'text' or not wiz.template_id:
                wiz.preview_text = wiz.body or ''
                continue
            records = wiz._records()
            if wiz.template_id.model_name and not records:
                wiz.preview_text = _('Pick the %s to fill the template from.', wiz.template_id.model_id.name)
                continue
            record = records[:1] if records else None
            try:
                _payload, text, attachment = wiz.template_id.render(record=record, ask_values=wiz._ask_values())
                wiz.preview_text = text + (('\n\n📎 ' + attachment['name']) if attachment else '')
            except Exception as exc:  # noqa: BLE001 - the preview must never block the form
                wiz.preview_text = _('Preview unavailable: %s', exc)

    @api.depends('phone_wa', 'record_count', 'mode', 'window_open', 'conversation_id', 'template_id', 'record_ref')
    def _compute_warning(self):
        for wiz in self:
            warning = False
            if wiz.record_count <= 1 and not wiz.phone_wa:
                warning = _('No valid phone number was found.')
            elif wiz.mode == 'template' and wiz.template_id.model_name and not wiz.record_count:
                warning = _('This template is filled from a %s: pick the record below.',
                            wiz.template_id.model_id.name)
            elif wiz.record_count > 1 and wiz.mode == 'text':
                warning = _('Free text can only be sent to a single recipient.')
            elif wiz.mode == 'text' and not wiz.window_open:
                warning = _('The 24-hour window is closed for this number. Send a template instead.')
            elif wiz.conversation_id and wiz.conversation_id.opted_out:
                warning = _('This customer opted out of WhatsApp messages.')
            wiz.warning = warning

    @api.onchange('template_id')
    def _onchange_template(self):
        if self.record_ref and self.template_id.model_name and self.record_ref._name != self.template_id.model_name:
            self.record_ref = False
        if self.template_id.header_format == 'location' and not (self.location_latitude or self.location_longitude):
            tpl = self.template_id
            self.location_latitude, self.location_longitude = tpl.location_latitude, tpl.location_longitude
            self.location_name, self.location_address = tpl.location_name, tpl.location_address
        commands = [(5, 0, 0)]
        for param in self.template_id.param_ids.filtered(lambda p: p.source == 'ask'):
            commands.append((0, 0, {'param_id': param.id}))
        self.param_value_ids = commands

    def _ask_values(self):
        self.ensure_one()
        values = {f'{line.param_id.component}:{line.param_id.placeholder}': line.value or ''
                  for line in self.param_value_ids}
        if self.template_id.header_format == 'location' and (self.location_latitude or self.location_longitude):
            values['header:location'] = {
                'latitude': self.location_latitude, 'longitude': self.location_longitude,
                'name': self.location_name, 'address': self.location_address}
        return values

    # ----------------------------------------------------------------- sending
    def action_send(self):
        self.ensure_one()
        records = self._records()
        if len(records) > 1:
            return self._send_many(records)
        record = records[:1] or None
        if not self.phone_wa:
            raise UserError(_('No valid phone number to send to.'))
        if self.mode == 'template' and not self.template_id:
            raise UserError(_('Choose a template.'))
        if self.mode == 'template' and self.template_id.model_name and record is None:
            raise UserError(_('Template %s is filled from a %s: pick the record it applies to.',
                              self.template_id.name, self.template_id.model_id.name))
        if self.mode == 'text' and not (self.body or self.attachment_ids):
            raise UserError(_('Write something or attach a file.'))
        conv = self.env['rcs.whatsapp.conversation']._get_or_create(
            self.number_id, self.phone_wa, self.partner_id.name if self.partner_id else None)
        if self.partner_id and not conv.partner_id:
            conv.sudo().partner_id = self.partner_id
        if self.mode == 'text':
            attachment = self.attachment_ids[:1]
            message = conv.send_text(self.body or '', attachment=attachment or None, origin=record)
        else:
            message = conv.send_template(self.template_id, record=record, ask_values=self._ask_values(),
                                         origin=record)
        if message.status == 'failed':
            raise UserError(_('Meta did not accept the message: %s', message.error_message))
        return {'type': 'ir.actions.act_window_close'}

    def _send_many(self, records):
        self.ensure_one()
        if self.mode != 'template' or not self.template_id:
            raise UserError(_('Sending to several records needs a template.'))
        sent, skipped = self._send_template_to_records(self.template_id, records, ask_values=self._ask_values(),
                                                       number=self.number_id)
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {'type': 'success' if sent else 'warning',
                       'message': _('%(sent)s sent, %(skipped)s skipped (no phone or opted out).',
                                    sent=sent, skipped=skipped),
                       'next': {'type': 'ir.actions.act_window_close'}},
        }

    @api.model
    def _send_template_to_records(self, template, records, ask_values=None, number=None):
        """Used by the wizard in bulk and by the server action. Returns (sent, skipped)."""
        number = number or template.number_id
        Conversation = self.env['rcs.whatsapp.conversation']
        sent = skipped = 0
        for record in records:
            phone, partner = self._detect_phone(record)
            wa_id = phone_tools.to_wa_id(phone, self._country_code(record, partner))
            if not wa_id:
                skipped += 1
                continue
            try:
                with self.env.cr.savepoint():
                    conv = Conversation._get_or_create(number, wa_id, partner.name if partner else None)
                    if partner and not conv.partner_id:
                        conv.sudo().partner_id = partner
                    message = conv.send_template(template, record=record, ask_values=ask_values or {}, origin=record)
                    if message.status == 'failed':
                        skipped += 1
                    else:
                        sent += 1
            except UserError as exc:
                _logger.info('WhatsApp bulk send skipped %s: %s', record, exc)
                skipped += 1
        return sent, skipped

    # ---------------------------------------------------------------- bindings
    def init(self):
        """Refresh the contextual actions on every update, not only on install.

        Apps come and go after this module does: install CRM, Helpdesk or Project a month
        later and post_init_hook has long since run, so their records would never get the
        "Send by WhatsApp" button. Creating them is idempotent (each one is looked up by
        its xmlid first), so running it again costs one search per model.
        """
        self._install_bindings()

    @api.model
    def _install_bindings(self):
        """One contextual 'Send by WhatsApp' action per known model that exists here."""
        IrModel = self.env['ir.model'].sudo()
        Action = self.env['ir.actions.act_window'].sudo()
        for model_name in BINDING_MODELS:
            model = IrModel.search([('model', '=', model_name)], limit=1)
            if not model or model_name not in self.env:
                continue
            xmlid = f'rcstudio_whatsapp_coexistence.binding_{model_name.replace(".", "_")}'
            if self.env.ref(xmlid, raise_if_not_found=False):
                continue
            action = Action.create({
                'name': 'Send by WhatsApp',
                'res_model': 'rcs.whatsapp.send',
                'view_mode': 'form',
                'target': 'new',
                'binding_model_id': model.id,
                'binding_type': 'action',
                'binding_view_types': 'form,list',
            })
            self.env['ir.model.data'].sudo()._update_xmlids([{
                'xml_id': xmlid, 'record': action, 'noupdate': True,
            }])


class WhatsAppSendParam(models.TransientModel):
    _name = 'rcs.whatsapp.send.param'
    _description = 'Send by WhatsApp: asked value'

    send_id = fields.Many2one('rcs.whatsapp.send', required=True, ondelete='cascade')
    param_id = fields.Many2one('rcs.whatsapp.template.param', required=True, ondelete='cascade')
    label = fields.Char(compute='_compute_label')
    value = fields.Char()

    @api.depends('param_id')
    def _compute_label(self):
        for line in self:
            line.label = line.param_id.label or f'{line.param_id.component} {{{{{line.param_id.placeholder}}}}}'
