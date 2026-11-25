"""Sample templates, created on demand for the apps installed.

Nothing here is loaded as XML data: a template for sales orders only makes sense when
Sales is installed, and the module must not depend on every app. `action_load_samples`
walks the catalogue, keeps the entries whose model, field paths and report exist in
this database, and creates the ones the number does not have yet, in draft, ready to
be reviewed and submitted to Meta.
"""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

FOOTER = 'Reply STOP to stop receiving messages'
LANGUAGE = 'en_US'


def _p(component, placeholder, source='field', field_path=None, fixed_value=None, label=None):
    """One placeholder mapping of the catalogue, in rcs.whatsapp.template.param terms."""
    vals = {'component': component, 'placeholder': str(placeholder), 'source': source}
    if field_path:
        vals['field_path'] = field_path
    if fixed_value is not None:
        vals['fixed_value'] = fixed_value
    if label:
        vals['label'] = label
    return vals


def _button(text, url):
    return {'kind': 'url', 'text': text, 'url': url}


class WhatsAppNumberLibrary(models.Model):
    _inherit = 'rcs.whatsapp.number'

    # --------------------------------------------------------------- catalogue
    @api.model
    def _sample_template_catalogue(self):
        """Every sample we know how to build. Each entry:

        name, label, category, header_format (+ header_text), body_text, footer_text,
        buttons, model, optional report xmlid, params (placeholder mappings), optional
        `needs` (extra field paths that must exist on the model).
        Only utility templates: they are the ones Meta approves without a marketing
        opt-in and the ones a business sends from a record.
        """
        company = self.env.company.name or 'our team'
        base = self.env['ir.config_parameter'].sudo().get_param('web.base.url') or ''
        return [
            # ------------------------------------------------------- contacts
            {
                'name': 'welcome', 'label': 'Welcome', 'model': 'res.partner',
                'header_format': 'none',
                'body_text': "Hi {{1}},\n\nThis is {{2}} on WhatsApp. Save this number and reply to this "
                             "message whenever you need us.",
                'params': [_p('body', 1, field_path='name'),
                           _p('body', 2, source='fixed', fixed_value=company, label='Business name')],
            },
            {
                'name': 'contact_follow_up', 'label': 'Contact follow-up', 'model': 'res.partner',
                'header_format': 'none',
                'body_text': "Hi {{1}},\n\nThis is {{2}} from {{3}}, following up on {{4}}. Is there anything "
                             "we can help you with? Reply here whenever suits you.",
                'params': [_p('body', 1, field_path='name'),
                           _p('body', 2, field_path='user_id.name'),
                           _p('body', 3, source='fixed', fixed_value=company, label='Business name'),
                           _p('body', 4, source='ask', label='What you are following up on')],
            },
            # ----------------------------------------------------------- sale
            {
                'name': 'quotation_sent', 'label': 'Quotation sent', 'model': 'sale.order',
                'report': 'sale.action_report_saleorder', 'header_format': 'document',
                'body_text': "Hi {{1}},\n\nHere is quotation {{2}} from {{3}} for {{4}}, valid until {{5}}. "
                             "The document is attached.\n\nReview or accept it online: {{6}}\n\nWe are happy to answer any question.",
                'buttons': [_button('My quotations', f'{base}/my/quotes')],
                'params': [_p('body', 1, field_path='partner_id.name'),
                           _p('body', 2, field_path='name'),
                           _p('body', 3, field_path='company_id.name'),
                           _p('body', 4, field_path='amount_total'),
                           _p('body', 5, field_path='validity_date'),
                           _p('body', 6, source='portal_url')],
            },
            {
                'name': 'order_confirmed', 'label': 'Order confirmed', 'model': 'sale.order',
                'report': 'sale.action_report_saleorder', 'header_format': 'document',
                'body_text': "Hi {{1}},\n\nYour order {{2}} with {{3}} is confirmed, for a total of {{4}}. "
                             "The order is attached.\n\nFollow it online: {{5}}\n\nThank you for your trust!",
                'buttons': [_button('My orders', f'{base}/my/orders')],
                'params': [_p('body', 1, field_path='partner_id.name'),
                           _p('body', 2, field_path='name'),
                           _p('body', 3, field_path='company_id.name'),
                           _p('body', 4, field_path='amount_total'),
                           _p('body', 5, source='portal_url')],
            },
            # -------------------------------------------------------- account
            {
                'name': 'invoice_available', 'label': 'Invoice available', 'model': 'account.move',
                'report': 'account.account_invoices', 'header_format': 'document',
                'body_text': "Hi {{1}},\n\nInvoice {{2}} from {{3}} for {{4}} is available; it is due on {{5}}. "
                             "The invoice is attached.\n\nReview or pay it online: {{6}}\n\nThank you!",
                'buttons': [_button('My invoices', f'{base}/my/invoices')],
                'params': [_p('body', 1, field_path='partner_id.name'),
                           _p('body', 2, field_path='name'),
                           _p('body', 3, field_path='company_id.name'),
                           _p('body', 4, field_path='amount_total'),
                           _p('body', 5, field_path='invoice_date_due'),
                           _p('body', 6, source='portal_url')],
            },
            # ---------------------------------------------------------- stock
            {
                'name': 'delivery_on_its_way', 'label': 'Delivery on its way', 'model': 'stock.picking',
                'report': 'stock.action_report_delivery', 'header_format': 'document',
                'body_text': "Hi {{1}},\n\nYour order {{2}} from {{3}} is on its way; it is scheduled for {{4}}. "
                             "The delivery slip is attached.\n\nReply here if you need to change anything.",
                'params': [_p('body', 1, field_path='partner_id.name'),
                           _p('body', 2, field_path='origin'),
                           _p('body', 3, field_path='company_id.name'),
                           _p('body', 4, field_path='scheduled_date')],
            },
            # ---------------------------------------------------------- event
            {
                'name': 'event_registration_confirmed', 'label': 'Event registration confirmed',
                'model': 'event.registration',
                'report': 'event.action_report_event_registration_full_page_ticket', 'header_format': 'document',
                'body_text': "Hi {{1}},\n\nYou are registered for {{2}} by {{3}}, {{4}}. Your ticket is attached."
                             "\n\nSee you there!",
                'params': [_p('body', 1, field_path='name'),
                           _p('body', 2, field_path='event_id.name'),
                           _p('body', 3, field_path='company_id.name'),
                           _p('body', 4, field_path='event_date_range')],
            },
            # ------------------------------------------------------- calendar
            {
                'name': 'appointment_reminder', 'label': 'Appointment reminder', 'model': 'calendar.event',
                'header_format': 'text', 'header_text': 'Reminder: {{1}}',
                'body_text': "Hi {{1}},\n\nThis is a reminder of your appointment {{2}} with {{3}}, {{4}}. "
                             "Reply here if you need to reschedule.\n\nSee you then!",
                'params': [_p('header', 1, field_path='name'),
                           _p('body', 1, field_path='partner_ids.name'),
                           _p('body', 2, field_path='name'),
                           _p('body', 3, field_path='user_id.name'),
                           _p('body', 4, field_path='display_time')],
            },
            # ------------------------------------------------------------ pos
            {
                'name': 'pos_receipt', 'label': 'Point of Sale receipt', 'model': 'pos.order',
                'header_format': 'none',
                'body_text': "Hi {{1}},\n\nThank you for your purchase at {{2}}. Receipt {{3}} of {{4}} "
                             "for a total of {{5}}.\n\nSee you soon!",
                'params': [_p('body', 1, field_path='partner_id.name'),
                           _p('body', 2, field_path='company_id.name'),
                           _p('body', 3, field_path='pos_reference'),
                           _p('body', 4, field_path='date_order'),
                           _p('body', 5, field_path='amount_total')],
            },
            # ------------------------------------------------------- purchase
            {
                'name': 'purchase_order_sent', 'label': 'Purchase order sent', 'model': 'purchase.order',
                'report': 'purchase.action_report_purchase_order', 'header_format': 'document',
                'body_text': "Hello {{1}},\n\nPlease find attached purchase order {{2}} from {{3}} for {{4}}. "
                             "We expect the goods by {{5}}.\n\nConfirm it online: {{6}}",
                'buttons': [_button('My purchases', f'{base}/my/purchase')],
                'params': [_p('body', 1, field_path='partner_id.name'),
                           _p('body', 2, field_path='name'),
                           _p('body', 3, field_path='company_id.name'),
                           _p('body', 4, field_path='amount_total'),
                           _p('body', 5, field_path='date_planned'),
                           _p('body', 6, source='portal_url')],
            },
            # ------------------------------------------------------------ crm
            {
                'name': 'lead_follow_up', 'label': 'Lead follow-up', 'model': 'crm.lead',
                'header_format': 'none',
                'body_text': "Hi {{1}},\n\nThis is {{2}} from {{3}} about {{4}}. Is this a good time to talk? "
                             "Reply here whenever suits you.",
                'params': [_p('body', 1, field_path='contact_name'),
                           _p('body', 2, field_path='user_id.name'),
                           _p('body', 3, field_path='company_id.name'),
                           _p('body', 4, field_path='name')],
            },
            # -------------------------------------------------------- project
            {
                'name': 'task_update', 'label': 'Task update', 'model': 'project.task',
                'header_format': 'text', 'header_text': '{{1}}',
                'body_text': "Hi {{1}},\n\nTask {{2}} in project {{3}} is now {{4}}; the deadline is {{5}}."
                             "\n\nFollow it online: {{6}}",
                'buttons': [_button('My tasks', f'{base}/my/tasks')],
                'params': [_p('header', 1, field_path='project_id.name'),
                           _p('body', 1, field_path='partner_id.name'),
                           _p('body', 2, field_path='name'),
                           _p('body', 3, field_path='project_id.name'),
                           _p('body', 4, field_path='stage_id.name'),
                           _p('body', 5, field_path='date_deadline'),
                           _p('body', 6, source='portal_url')],
            },
        ]

    # ----------------------------------------------------------------- probing
    @api.model
    def _sample_template_available(self, entry):
        """Whether this database can host the sample: the model exists, every field path
        of its placeholders resolves, and its report exists for that model."""
        model_name = entry['model']
        if model_name not in self.env:
            return False
        Model = self.env[model_name]
        Param = self.env['rcs.whatsapp.template.param']
        paths = [p['field_path'] for p in entry.get('params', []) if p.get('source') == 'field']
        paths += entry.get('needs', [])
        for path in paths:
            try:
                Param._resolve_field(Model, path)
            except (ValidationError, KeyError):
                return False
        if entry.get('report'):
            report = self.env.ref(entry['report'], raise_if_not_found=False)
            if not report or report._name != 'ir.actions.report' or report.model != model_name:
                return False
        return True

    @api.model
    def _sample_templates_available(self):
        """The catalogue entries this database can host, in catalogue order."""
        return [entry for entry in self._sample_template_catalogue() if self._sample_template_available(entry)]

    def _sample_template_vals(self, entry):
        self.ensure_one()
        vals = {
            'number_id': self.id,
            'name': entry['name'],
            'label': entry.get('label') or entry['name'].replace('_', ' ').capitalize(),
            'language': entry.get('language', LANGUAGE),
            'category': entry.get('category', 'utility'),
            'status': 'draft',
            'is_library': True,
            'header_format': entry.get('header_format', 'none'),
            'header_text': entry.get('header_text') or False,
            'body_text': entry['body_text'],
            'footer_text': entry.get('footer_text', FOOTER),
            'model_id': self.env['ir.model']._get_id(entry['model']),
            'button_ids': [fields.Command.create(dict(b, sequence=i)) for i, b in enumerate(entry.get('buttons', []))],
            'param_ids': [fields.Command.create(p) for p in entry.get('params', [])],
        }
        if entry.get('report'):
            vals['report_id'] = self.env.ref(entry['report']).id
        return vals

    # ------------------------------------------------------------------ action
    def action_load_samples(self):
        """Create, in draft, the sample templates that fit this database and that the
        number does not have yet (same name and language)."""
        Template = self.env['rcs.whatsapp.template']
        created = Template
        for number in self:
            existing = {(t.name, t.language) for t in Template.with_context(active_test=False).search(
                [('number_id', '=', number.id)])}
            to_create = [number._sample_template_vals(entry) for entry in self._sample_templates_available()
                         if (entry['name'], entry.get('language', LANGUAGE)) not in existing]
            if to_create:
                created |= Template.create(to_create)
        if created:
            return self._toast(_('%s sample templates created for the apps installed. Review them, then '
                                 'submit them to Meta for approval.', len(created)))
        return self._toast(_('Every sample template that fits this database already exists on this number.'),
                           kind='info')
