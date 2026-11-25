"""Template composition, Meta round trip and placeholder rendering."""
from datetime import date

from odoo.exceptions import ValidationError
from odoo.tests import tagged

from .common import TAGS, WhatsAppCase


@tagged(*TAGS)
class TestTemplate(WhatsAppCase):

    def test_placeholders_and_components(self):
        tpl = self._make_template(
            self.number, name='full', body='Hi {{1}}, your {{2}} is ready. {{1}} again.',
            header_format='text', header_text='Order {{1}}', footer_text='Thanks',
            button_ids=[(0, 0, {'kind': 'url', 'text': 'Track', 'url': 'https://x.test/t/{{1}}'}),
                        (0, 0, {'kind': 'quick_reply', 'text': 'Stop'})])
        self.assertEqual(tpl._placeholders(), {'header': ['1'], 'body': ['1', '2'], 'button_0': ['1']})
        self.assertEqual(tpl.placeholder_count, 4)
        components = tpl.components
        self.assertEqual([c['type'] for c in components], ['HEADER', 'BODY', 'FOOTER', 'BUTTONS'])
        self.assertEqual(components[0], {'format': 'TEXT', 'type': 'HEADER', 'text': 'Order {{1}}'})
        self.assertEqual(components[3]['buttons'][0], {'type': 'URL', 'text': 'Track', 'url': 'https://x.test/t/{{1}}'})
        self.assertEqual(components[3]['buttons'][1], {'type': 'QUICK_REPLY', 'text': 'Stop'})

    def test_meta_round_trip(self):
        tpl = self._make_template(
            self.number, name='round', body='Body {{1}}', header_format='document', footer_text='F',
            button_ids=[(0, 0, {'kind': 'phone_number', 'text': 'Call', 'phone_number': '+14155550000'})])
        payload = {'name': 'round', 'language': 'en_US', 'category': 'MARKETING', 'status': 'APPROVED',
                   'id': '777', 'components': tpl._compose_components()}
        vals = self.env['rcs.whatsapp.template']._vals_from_meta(payload)
        self.assertEqual(vals['header_format'], 'document')
        self.assertEqual(vals['body_text'], 'Body {{1}}')
        self.assertEqual(vals['footer_text'], 'F')
        self.assertEqual(vals['category'], 'marketing')
        self.assertEqual(vals['status'], 'approved')
        self.assertEqual(vals['button_ids'][1][2]['kind'], 'phone_number')
        # upsert creates once, then updates in place
        Template = self.env['rcs.whatsapp.template']
        Template._upsert_from_meta(self.number, [dict(payload, name='synced')])
        first = Template.search([('number_id', '=', self.number.id), ('name', '=', 'synced')])
        self.assertEqual(len(first), 1)
        self.assertEqual(first.meta_template_id, '777')
        Template._upsert_from_meta(self.number, [dict(payload, name='synced', status='REJECTED', rejected_reason='x')])
        self.assertEqual(first.status, 'rejected')
        self.assertEqual(Template.search_count([('number_id', '=', self.number.id), ('name', '=', 'synced')]), 1)

    def test_render_sources(self):
        partner = self._make_partner(name='Zoe Field', phone='+1 415-555-0100', function='CTO')
        tpl = self._make_template(
            self.number, name='sources', body='Hello {{1}} from {{2}}, code {{3}}, link {{4}}',
            header_format='text', header_text='Dear {{name}}',
            params=[
                {'component': 'body', 'placeholder': '1', 'source': 'field', 'field_path': 'name'},
                {'component': 'body', 'placeholder': '2', 'source': 'fixed', 'fixed_value': 'Acme'},
                {'component': 'body', 'placeholder': '3', 'source': 'ask', 'label': 'Code'},
                {'component': 'body', 'placeholder': '4', 'source': 'portal_url'},
                {'component': 'header', 'placeholder': 'name', 'source': 'field', 'field_path': 'function'},
            ])
        payload, text, attachment = tpl.render(record=partner, ask_values={'body:3': 'XYZ'})
        self.assertIsNone(attachment)
        self.assertEqual(payload['name'], 'sources')
        self.assertEqual(payload['language'], {'code': 'en_US'})
        header, body = payload['components']
        self.assertEqual(header, {'type': 'header', 'parameters': [{'type': 'text', 'text': 'CTO', 'parameter_name': 'name'}]})
        self.assertEqual([p['text'] for p in body['parameters']][:3], ['Zoe Field', 'Acme', 'XYZ'])
        self.assertTrue(body['parameters'][3]['text'].startswith('http'))
        self.assertIn('Hello Zoe Field from Acme, code XYZ', text)
        self.assertTrue(text.startswith('Dear CTO'))

    def test_render_formats_dates_and_relations(self):
        partner = self._make_partner(name='Dated', phone='+1 415-555-0101')
        tpl = self._make_template(
            self.number, name='formats', body='{{1}} {{2}} {{3}}',
            params=[
                {'component': 'body', 'placeholder': '1', 'source': 'field', 'field_path': 'country_id'},
                {'component': 'body', 'placeholder': '2', 'source': 'field', 'field_path': 'country_id.code'},
                {'component': 'body', 'placeholder': '3', 'source': 'field', 'field_path': 'email'},
            ])
        _payload, text, _att = tpl.render(record=partner)
        self.assertEqual(text, 'United States US ')

    def test_render_wrong_model_refused(self):
        tpl = self._make_template(self.number, name='wrong', model='res.partner')
        with self.assertRaises(Exception):
            tpl.render(record=self.env.company)

    def test_sensitive_field_path_refused(self):
        tpl = self._make_template(self.number, name='sens', model='res.users')
        with self.assertRaises(ValidationError):
            self.env['rcs.whatsapp.template.param'].create({
                'template_id': tpl.id, 'component': 'body', 'placeholder': '1',
                'source': 'field', 'field_path': 'password'})
        with self.assertRaises(ValidationError):
            self.env['rcs.whatsapp.template.param'].create({
                'template_id': tpl.id, 'component': 'body', 'placeholder': '1',
                'source': 'field', 'field_path': 'no_such_field'})

    def test_name_constraint(self):
        with self.assertRaises(ValidationError):
            self._make_template(self.number, name='Not Valid')

    def test_fixed_header_attachment(self):
        attachment = self.env['ir.attachment'].create({
            'name': 'brochure.pdf', 'raw': b'%PDF-1.4 fake', 'mimetype': 'application/pdf'})
        tpl = self._make_template(self.number, name='doc', header_format='document',
                                  header_attachment_id=attachment.id)
        _payload, _text, vals = tpl.render(record=self.partner)
        self.assertEqual(vals['name'], 'brochure.pdf')
        self.assertEqual(vals['mimetype'], 'application/pdf')
        injected = tpl._inject_header_media({'name': 'doc', 'language': {'code': 'en_US'}}, 'media-1', 'brochure.pdf')
        self.assertEqual(injected['components'][0],
                         {'type': 'header', 'parameters': [{'type': 'document', 'document': {'id': 'media-1', 'filename': 'brochure.pdf'}}]})

    def test_report_header_renders_pdf(self):
        report = self.env['ir.actions.report'].search([('model', '=', 'res.partner'), ('report_type', '=', 'qweb-pdf')], limit=1)
        if not report:
            self.skipTest('no partner PDF report in this database')
        tpl = self._make_template(self.number, name='rep', header_format='document', report_id=report.id)
        _payload, _text, vals = tpl.render(record=self.partner)
        self.assertTrue(vals['raw'].startswith(b'%PDF'))
        self.assertTrue(vals['name'].endswith('.pdf'))
