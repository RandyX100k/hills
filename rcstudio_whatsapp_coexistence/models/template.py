import re

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import format_amount, format_date, format_datetime, html2plaintext

from ..tools.graph import GraphError

PLACEHOLDER = re.compile(r'{{\s*([A-Za-z0-9_]+)\s*}}')
NAME_OK = re.compile(r'^[a-z0-9_]{1,512}$')
SENSITIVE = re.compile(r'(password|passwd|secret|token|api_key|apikey|access_key|private|pin\b)', re.I)

CATEGORIES = [('marketing', 'Marketing'), ('utility', 'Utility'), ('authentication', 'Authentication')]
STATUSES = [('draft', 'Draft'), ('pending', 'Pending review'), ('approved', 'Approved'),
            ('rejected', 'Rejected'), ('paused', 'Paused'), ('disabled', 'Disabled')]
HEADER_FORMATS = [('none', 'None'), ('text', 'Text'), ('image', 'Image'), ('video', 'Video'),
                  ('document', 'Document'), ('location', 'Location')]


def cls_reason(value):
    """Meta's rejected_reason / reason: 'NONE' means there is none."""
    value = (value or '').strip()
    return False if not value or value.upper() == 'NONE' else value

class WhatsAppTemplate(models.Model):
    """A message template as Meta understands it: header, body, footer and buttons.

    On top of that, how Odoo fills it: which model it is meant for, which report to
    attach, and what goes into each placeholder."""
    _name = 'rcs.whatsapp.template'
    _description = 'WhatsApp Template'
    _order = 'name, language'

    number_id = fields.Many2one('rcs.whatsapp.number', required=True, ondelete='cascade', index=True)
    name = fields.Char(required=True, help="Lowercase letters, digits and underscores, as Meta requires.")
    label = fields.Char(help="A friendly name shown to the people who send it.")
    language = fields.Char(required=True, default='en_US',
                           help="Meta's language code: en_US, es, es_ES, fr, de, pt_BR...")
    category = fields.Selection(CATEGORIES, required=True, default='utility')
    status = fields.Selection(STATUSES, required=True, default='draft', copy=False)
    meta_template_id = fields.Char(readonly=True, copy=False)
    rejection_reason = fields.Text(readonly=True, copy=False)
    quality = fields.Char(readonly=True, copy=False)

    header_format = fields.Selection(HEADER_FORMATS, required=True, default='none')
    header_text = fields.Char()
    header_attachment_id = fields.Many2one(
        'ir.attachment', string='Header file',
        help="A fixed image, video or document sent as the header. Leave empty and choose a "
             "report to send the document of the record instead.")
    # Location header: the fixed point sent unless the sender picks another one
    location_latitude = fields.Float(digits=(10, 7))
    location_longitude = fields.Float(digits=(10, 7))
    location_name = fields.Char()
    location_address = fields.Char()
    body_text = fields.Text(required=True)
    footer_text = fields.Char()
    button_ids = fields.One2many('rcs.whatsapp.template.button', 'template_id', copy=True)
    components = fields.Json(compute='_compute_components', store=True)
    placeholder_count = fields.Integer(compute='_compute_placeholders', store=True)
    placeholder_summary = fields.Char(compute='_compute_placeholders', store=True)

    model_id = fields.Many2one('ir.model', string='Applies to', ondelete='cascade',
                               help="The kind of record this template is filled from. Leave empty "
                                    "for templates sent to a contact only.")
    model_name = fields.Char(related='model_id.model', store=True)
    report_id = fields.Many2one('ir.actions.report', string='Attach report',
                                domain="[('model', '=', model_name)]",
                                help="Rendered as PDF for the record and sent as the document header.")
    param_ids = fields.One2many('rcs.whatsapp.template.param', 'template_id', copy=True)
    available_on_records = fields.Boolean(default=True,
                                          help="Offer this template in the Send by WhatsApp action.")
    is_library = fields.Boolean(readonly=True, help="Loaded from the sample library.")
    active = fields.Boolean(default=True)

    # 18.0: models.Constraint does not exist, _sql_constraints instead
    _sql_constraints = [
        ('number_name_language_unique', 'UNIQUE(number_id, name, language)',
         'A template name and language can only exist once per business number.'),
    ]

    # ------------------------------------------------------------------ checks
    @api.constrains('name')
    def _check_name(self):
        for tpl in self:
            if not NAME_OK.match(tpl.name or ''):
                raise ValidationError(_('Template names use lowercase letters, digits and underscores only.'))

    @api.constrains('header_format', 'header_text', 'header_attachment_id', 'report_id')
    def _check_header(self):
        for tpl in self:
            if tpl.header_format == 'text' and not tpl.header_text:
                raise ValidationError(_('A text header needs its text.'))
            if tpl.header_format == 'document' and tpl.report_id and tpl.header_attachment_id:
                raise ValidationError(_('Choose either a fixed header file or a report, not both.'))

    @api.constrains('button_ids')
    def _check_buttons(self):
        for tpl in self:
            if len(tpl.button_ids) > 10:
                raise ValidationError(_('Meta allows at most ten buttons.'))

    # ---------------------------------------------------------------- computes
    @api.depends('header_format', 'header_text', 'body_text', 'footer_text',
                 'button_ids.kind', 'button_ids.text', 'button_ids.url', 'button_ids.phone_number', 'button_ids.sequence')
    def _compute_components(self):
        for tpl in self:
            tpl.components = tpl._compose_components()

    @api.depends('header_text', 'body_text', 'button_ids.url')
    def _compute_placeholders(self):
        for tpl in self:
            keys = tpl._placeholders()
            tpl.placeholder_count = sum(len(v) for v in keys.values())
            tpl.placeholder_summary = ', '.join(f'{c}:{k}' for c, ks in keys.items() for k in ks)

    def _placeholders(self):
        """{'header': ['1'], 'body': ['1','2'], 'button_0': ['1']} in order of appearance."""
        self.ensure_one()
        out = {}
        if self.header_format == 'text' and self.header_text:
            keys = list(dict.fromkeys(PLACEHOLDER.findall(self.header_text)))
            if keys:
                out['header'] = keys
        keys = list(dict.fromkeys(PLACEHOLDER.findall(self.body_text or '')))
        if keys:
            out['body'] = keys
        for i, button in enumerate(self.button_ids.sorted('sequence')):
            if button.kind == 'url':
                keys = list(dict.fromkeys(PLACEHOLDER.findall(button.url or '')))
                if keys:
                    out[f'button_{i}'] = keys
            elif button.kind == 'copy_code':
                out[f'button_{i}'] = ['code']
        return out

    def _compose_components(self):
        """Meta's `components` array for creating this template."""
        self.ensure_one()
        components = []
        if self.header_format == 'text':
            components.append({'format': 'TEXT', 'type': 'HEADER', 'text': self.header_text or ''})
        elif self.header_format != 'none':
            components.append({'format': self.header_format.upper(), 'type': 'HEADER'})
        components.append({'type': 'BODY', 'text': self.body_text or ''})
        if self.footer_text:
            components.append({'type': 'FOOTER', 'text': self.footer_text})
        buttons = []
        for button in self.button_ids.sorted('sequence'):
            if button.kind == 'url':
                buttons.append({'type': 'URL', 'text': button.text, 'url': button.url or ''})
            elif button.kind == 'phone_number':
                buttons.append({'type': 'PHONE_NUMBER', 'text': button.text, 'phone_number': button.phone_number or ''})
            elif button.kind == 'copy_code':
                buttons.append({'type': 'COPY_CODE', 'text': button.text or 'Copy code'})
            else:
                buttons.append({'type': 'QUICK_REPLY', 'text': button.text})
        if buttons:
            components.append({'type': 'BUTTONS', 'buttons': buttons})
        return components

    # ------------------------------------------------------------ Meta sync
    @api.model
    def _upsert_from_meta(self, number, payloads):
        """Create or refresh templates from GET /message_templates."""
        existing = {(t.name, t.language): t for t in
                    self.with_context(active_test=False).search([('number_id', '=', number.id)])}
        for payload in payloads:
            key = (payload.get('name'), payload.get('language'))
            vals = self._vals_from_meta(payload)
            if key in existing:
                existing[key].write(vals)
            else:
                vals['number_id'] = number.id
                existing[key] = self.create(vals)

    @api.model
    def _vals_from_meta(self, payload):
        vals = {
            'name': payload.get('name'),
            'language': payload.get('language'),
            'category': (payload.get('category') or 'utility').lower(),
            'status': (payload.get('status') or 'pending').lower(),
            'meta_template_id': payload.get('id'),
            # Meta answers the literal 'NONE' when there is no rejection
            'rejection_reason': cls_reason(payload.get('rejected_reason')),
            'quality': ((payload.get('quality_score') or {}).get('score')) or False,
            'header_format': 'none', 'header_text': False, 'footer_text': False,
            'button_ids': [(5, 0, 0)],
        }
        if vals['status'] not in dict(STATUSES):
            vals['status'] = 'pending'
        if vals['category'] not in dict(CATEGORIES):
            vals['category'] = 'utility'
        buttons = []
        for component in payload.get('components') or []:
            ctype = (component.get('type') or '').upper()
            if ctype == 'HEADER':
                fmt = (component.get('format') or 'TEXT').lower()
                vals['header_format'] = fmt if fmt in dict(HEADER_FORMATS) else 'none'
                vals['header_text'] = component.get('text') if fmt == 'text' else False
            elif ctype == 'BODY':
                vals['body_text'] = component.get('text') or ''
            elif ctype == 'FOOTER':
                vals['footer_text'] = component.get('text')
            elif ctype == 'BUTTONS':
                for i, button in enumerate(component.get('buttons') or []):
                    kind = {'URL': 'url', 'PHONE_NUMBER': 'phone_number', 'COPY_CODE': 'copy_code'}.get(
                        (button.get('type') or '').upper(), 'quick_reply')
                    buttons.append((0, 0, {'sequence': i, 'kind': kind, 'text': button.get('text') or '',
                                           'url': button.get('url'), 'phone_number': button.get('phone_number')}))
        vals['button_ids'] += buttons
        vals.setdefault('body_text', '')
        return vals

    # ------------------------------------------------------------ submission
    # Meta reviews templates against samples: every variable needs an example value and
    # a media header needs a sample file uploaded through the Resumable Upload API.
    _SAMPLE_PDF = (b'%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n'
                   b'3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 144]>>endobj\nxref\n0 4\n0000000000 65535 f \n'
                   b'0000000009 00000 n \n0000000052 00000 n \n0000000101 00000 n \ntrailer<</Size 4/Root 1 0 R>>\nstartxref\n160\n%%EOF\n')
    _SAMPLE_PNG = bytes.fromhex(
        '89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415478da63f8cfc0'
        '0000030101007a5b0d660000000049454e44ae426082')

    def _sample_values(self, component, keys):
        """Example values for the placeholders of a component, from a sample record when
        the mapping allows it, otherwise a readable stand-in."""
        record = self._sample_record()
        params = {(p.component, p.placeholder): p for p in self.param_ids}
        out = []
        for key in keys:
            value = ''
            param = params.get((component, key))
            if param is not None and param.source != 'ask':
                try:
                    value = str(param._value(record, {}) or '')
                except Exception:  # noqa: BLE001 - a sample must never block the submission
                    value = ''
            out.append(value.strip() or f'Sample {key}')
        return out

    def _sample_media(self):
        """(bytes, mime, filename) for the media header sample."""
        self.ensure_one()
        if self.header_attachment_id:
            att = self.header_attachment_id.sudo()
            return att.raw, att.mimetype, att.name
        if self.header_format == 'document':
            record = self._sample_record()
            if self.report_id and record is not None:
                try:
                    pdf, _kind = self.env['ir.actions.report'].sudo()._render_qweb_pdf(self.report_id.id, [record.id])
                    return pdf, 'application/pdf', 'sample.pdf'
                except Exception:  # noqa: BLE001 - fall back to the blank sample
                    pass
            return self._SAMPLE_PDF, 'application/pdf', 'sample.pdf'
        if self.header_format == 'image':
            return self._SAMPLE_PNG, 'image/png', 'sample.png'
        raise UserError(_('A %s header needs a sample file: set one in "Header file" before submitting.',
                          self.header_format))

    def _submission_components(self):
        """`_compose_components()` plus the examples Meta requires for review."""
        self.ensure_one()
        components = self._compose_components()
        placeholders = self._placeholders()
        for component in components:
            ctype = component.get('type')
            if ctype == 'HEADER':
                if component.get('format') == 'TEXT' and placeholders.get('header'):
                    component['example'] = {'header_text': self._sample_values('header', placeholders['header'])}
                elif component.get('format') in ('IMAGE', 'VIDEO', 'DOCUMENT'):
                    content, mime, name = self._sample_media()
                    handle = self.number_id._graph().upload_sample(content, mime, name)
                    component['example'] = {'header_handle': [handle]}
            elif ctype == 'BODY' and placeholders.get('body'):
                keys = placeholders['body']
                values = self._sample_values('body', keys)
                if all(k.isdigit() for k in keys):
                    component['example'] = {'body_text': [values]}
                else:
                    component['example'] = {'body_text_named_params': [
                        {'param_name': k, 'example': v} for k, v in zip(keys, values)]}
            elif ctype == 'BUTTONS':
                for i, button in enumerate(component.get('buttons') or []):
                    if button.get('type') == 'URL' and placeholders.get(f'button_{i}'):
                        sample = PLACEHOLDER.sub('sample', button['url'])
                        button['example'] = [sample]
        return components

    def _check_meta_rules(self):
        """The rules Meta enforces on submission, checked here so the message is clear."""
        self.ensure_one()
        body = (self.body_text or '').strip()
        problems = []
        if PLACEHOLDER.match(body):
            problems.append(_('the body starts with a variable'))
        if re.search(r'{{\s*[A-Za-z0-9_]+\s*}}$', body):
            problems.append(_('the body ends with a variable'))
        keys = PLACEHOLDER.findall(body)
        numbered = [k for k in keys if k.isdigit()]
        if numbered and sorted(set(int(k) for k in numbered)) != list(range(1, len(set(numbered)) + 1)):
            problems.append(_('numbered variables must run {{1}}, {{2}}, {{3}}... without gaps'))
        if keys and numbered and len(numbered) != len(keys):
            problems.append(_('mix of numbered and named variables'))
        if self.header_format == 'text' and len(PLACEHOLDER.findall(self.header_text or '')) > 1:
            problems.append(_('a text header may contain one variable at most'))
        if problems:
            raise UserError(_(
                'Meta will reject template %(name)s: %(problems)s. A variable must be surrounded by text; '
                'add a word before or after it and try again.',
                name=self.name, problems='; '.join(problems)))

    def action_submit_to_meta(self):
        for tpl in self:
            if tpl.status not in ('draft', 'rejected'):
                raise UserError(_('%s was already submitted.', tpl.name))
            tpl._check_meta_rules()
            try:
                res = tpl.number_id._graph().create_template(
                    tpl.name, tpl.language, tpl.category.upper(), tpl._submission_components())
            except GraphError as exc:
                raise UserError(_('Meta refused the template: %s', exc))
            tpl.write({'status': (res.get('status') or 'pending').lower(), 'meta_template_id': res.get('id'),
                       'rejection_reason': False})
        return True

    def action_refresh_from_meta(self):
        numbers = self.mapped('number_id')
        for number in numbers:
            number._sync_templates()
        return True

    def action_reset_to_draft(self):
        self.write({'status': 'draft', 'meta_template_id': False, 'rejection_reason': False})

    # -------------------------------------------------------------- rendering
    def render(self, record=None, ask_values=None):
        """Fill the placeholders for `record`.

        Returns (template payload for POST /messages, preview text, attachment values or
        None). The attachment is the report rendered for the record, or the fixed header
        file, both to be uploaded by the message when it is sent."""
        self.ensure_one()
        ask_values = ask_values or {}
        if record is not None and self.model_name and record._name != self.model_name:
            raise UserError(_('Template %s is meant for %s, not for %s.', self.name, self.model_name, record._name))
        placeholders = self._placeholders()
        params_by_key = {(p.component, p.placeholder): p for p in self.param_ids}
        components = []
        substitutions = {}

        def values_for(component):
            out = []
            for key in placeholders.get(component, []):
                param = params_by_key.get((component, key))
                value = param._value(record, ask_values) if param else ask_values.get(f'{component}:{key}', '')
                substitutions[(component, key)] = value
                out.append((key, value))
            return out

        if self.header_format == 'text' and placeholders.get('header'):
            components.append({'type': 'header', 'parameters': [
                self._text_param(k, v) for k, v in values_for('header')]})
        elif self.header_format == 'location':
            location = self._location_for(record, ask_values)
            if not location:
                raise UserError(_('Template %s has a location header: set the point on the template '
                                  'or pick one when sending.', self.name))
            components.append({'type': 'header', 'parameters': [{'type': 'location', 'location': location}]})
        body = values_for('body')
        if body:
            components.append({'type': 'body', 'parameters': [self._text_param(k, v) for k, v in body]})
        for i, button in enumerate(self.button_ids.sorted('sequence')):
            key = f'button_{i}'
            if key not in placeholders:
                continue
            vals = values_for(key)
            if button.kind == 'url':
                components.append({'type': 'button', 'sub_type': 'url', 'index': str(i),
                                   'parameters': [{'type': 'text', 'text': str(v)} for _k, v in vals]})
            elif button.kind == 'copy_code':
                components.append({'type': 'button', 'sub_type': 'copy_code', 'index': str(i),
                                   'parameters': [{'type': 'coupon_code', 'coupon_code': str(vals[0][1]) if vals else ''}]})

        attachment_vals = self._header_attachment_vals(record)
        payload = {'name': self.name, 'language': {'code': self.language}}
        if components:
            payload['components'] = components
        return payload, self._preview_text(substitutions), attachment_vals

    @staticmethod
    def _text_param(key, value):
        param = {'type': 'text', 'text': str(value) if value is not None else ''}
        if not key.isdigit():
            param['parameter_name'] = key
        return param

    def _preview_text(self, substitutions):
        def fill(text, component):
            return PLACEHOLDER.sub(lambda m: str(substitutions.get((component, m.group(1)), m.group(0))), text or '')
        parts = []
        if self.header_format == 'text' and self.header_text:
            parts.append(fill(self.header_text, 'header'))
        elif self.header_format == 'location':
            parts.append(_('📍 Location'))
        parts.append(fill(self.body_text, 'body'))
        if self.footer_text:
            parts.append(self.footer_text)
        return '\n\n'.join(parts)

    def _header_attachment_vals(self, record):
        if self.header_format not in ('image', 'video', 'document'):
            return None
        if self.header_attachment_id:
            att = self.header_attachment_id.sudo()
            return {'name': att.name, 'raw': att.raw, 'mimetype': att.mimetype}
        if self.header_format == 'document' and self.report_id and record is not None:
            pdf, _kind = self.env['ir.actions.report'].sudo()._render_qweb_pdf(self.report_id.id, [record.id])
            name = f'{record.display_name}.pdf'.replace('/', '-')
            return {'name': name, 'raw': pdf, 'mimetype': 'application/pdf'}
        return None

    def _inject_header_media(self, template_payload, media_id, filename):
        """Add the uploaded header media to an already rendered template payload."""
        self.ensure_one()
        payload = dict(template_payload)
        components = [c for c in payload.get('components', []) if c.get('type') != 'header']
        media = {'id': media_id}
        if self.header_format == 'document' and filename:
            media['filename'] = filename
        components.insert(0, {'type': 'header', 'parameters': [{'type': self.header_format, self.header_format: media}]})
        payload['components'] = components
        return payload

    def _sample_record(self):
        self.ensure_one()
        if not self.model_name or self.model_name not in self.env:
            return None
        return self.env[self.model_name].search([], limit=1, order='id desc') or None

    def action_preview(self):
        self.ensure_one()
        record = self._sample_record()
        _payload, text, attachment = self.render(record=record, ask_values={})
        message = text + (('\n\n📎 ' + attachment['name']) if attachment else '')
        return {'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {'title': _('Preview'), 'message': message, 'sticky': True, 'type': 'info'}}


class WhatsAppTemplateButton(models.Model):
    _name = 'rcs.whatsapp.template.button'
    _description = 'WhatsApp Template Button'
    _order = 'sequence, id'

    template_id = fields.Many2one('rcs.whatsapp.template', required=True, ondelete='cascade')
    sequence = fields.Integer(default=10)
    kind = fields.Selection([('quick_reply', 'Quick reply'), ('url', 'Visit website'),
                             ('phone_number', 'Call'), ('copy_code', 'Copy code')],
                            required=True, default='quick_reply')
    text = fields.Char(required=True, size=25)
    url = fields.Char(help="May end with {{1}} for a part filled per record.")
    phone_number = fields.Char()


class WhatsAppTemplateParam(models.Model):
    """What goes into one placeholder of one component."""
    _name = 'rcs.whatsapp.template.param'
    _description = 'WhatsApp Template Placeholder'
    _order = 'component, placeholder, id'

    template_id = fields.Many2one('rcs.whatsapp.template', required=True, ondelete='cascade')
    model_name = fields.Char(related='template_id.model_name')
    component = fields.Char(required=True, default='body',
                            help="header, body, or button_0 … button_9")
    placeholder = fields.Char(required=True, default='1', help="1, 2 … or the placeholder's name")
    label = fields.Char(help="Shown to the sender when the value is asked for.")
    source = fields.Selection([('field', 'Field of the record'), ('fixed', 'Fixed text'),
                               ('ask', 'Asked when sending'), ('portal_url', "Record's portal link")],
                              required=True, default='field')
    field_path = fields.Char(help="Dotted path on the record, e.g. partner_id.name or amount_total.")
    fixed_value = fields.Char()

    @api.constrains('field_path', 'source')
    def _check_field_path(self):
        for param in self:
            if param.source != 'field':
                continue
            if not param.field_path:
                raise ValidationError(_('Placeholder %s needs a field path.', param.placeholder))
            if SENSITIVE.search(param.field_path):
                raise ValidationError(_('%s looks like a secret and cannot be sent by WhatsApp.', param.field_path))
            if param.model_name and param.model_name in self.env:
                param._resolve_field(self.env[param.model_name], param.field_path)

    def _resolve_field(self, model, path):
        """Walk the path to make sure every segment is a real field; returns the last field."""
        field = None
        for segment in path.split('.'):
            if segment not in model._fields:
                raise ValidationError(_('%s has no field %s.', model._name, segment))
            field = model._fields[segment]
            if field.relational:
                model = self.env[field.comodel_name]
        return field

    def _value(self, record, ask_values):
        self.ensure_one()
        if self.source == 'fixed':
            return self.fixed_value or ''
        if self.source == 'ask':
            return ask_values.get(f'{self.component}:{self.placeholder}', '')
        if record is None:
            return ''
        if self.source == 'portal_url':
            getter = getattr(record, 'get_portal_url', None)
            if getter is None:
                return record.get_base_url()
            return record.get_base_url() + getter()
        try:
            value = record
            for segment in self.field_path.split('.'):
                value = value[segment]
                if isinstance(value, models.BaseModel) and len(value) > 1:
                    value = value[:1]
        except Exception:  # noqa: BLE001 - a broken path renders empty, never blocks the send
            return ''
        return self._format(record, self.field_path, value)

    def _format(self, record, path, value):
        if isinstance(value, models.BaseModel):
            return value.display_name or ''
        if value is False or value is None:
            return ''
        field = self._resolve_field(self.env[record._name], path)
        if field.type == 'date':
            return format_date(self.env, value)
        if field.type == 'datetime':
            return format_datetime(self.env, value)
        if field.type == 'monetary':
            currency_field = getattr(field, 'currency_field', None) or 'currency_id'
            currency = record[currency_field] if currency_field in record._fields else None
            return format_amount(self.env, value, currency) if currency else f'{value:,.2f}'
        if field.type == 'html':
            return html2plaintext(value)
        if field.type == 'selection':
            return dict(field.get_description(self.env)['selection']).get(value, value)
        return str(value)
