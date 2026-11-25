# -*- coding: utf-8 -*-
from odoo.exceptions import AccessError
from odoo.tests import Form, tagged
from odoo.tests.common import TransactionCase, new_test_user


@tagged('post_install', '-at_install')
class TestCreateWithPrice(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Puede crear productos pero no es Price Manager (como "vendedor"/"cajero" en hills).
        cls.user = new_test_user(cls.env, login='pvpl_user', groups='base.group_user,product.group_product_manager')

    def test_template_form_creates_pending_with_price(self):
        with Form(self.env['product.template'].with_user(self.user)) as form:
            form.name = "Filtro nuevo"
            form.list_price = 500
            form.standard_price = 300
        tmpl = form.record
        self.assertEqual(tmpl.verification_state, 'pending')
        self.assertEqual(tmpl.list_price, 500)
        self.assertEqual(tmpl.standard_price, 300)

    def test_variant_form_creates_pending_with_price(self):
        with Form(self.env['product.product'].with_user(self.user)) as form:
            form.name = "Bomba nueva"
            form.lst_price = 800
            form.standard_price = 450
        product = form.record
        self.assertEqual(product.verification_state, 'pending')
        self.assertEqual(product.lst_price, 800)
        self.assertEqual(product.standard_price, 450)

    def test_price_locked_after_creation(self):
        tmpl = self.env['product.template'].with_user(self.user).create({'name': "X", 'list_price': 10})
        with self.assertRaises(AccessError):
            tmpl.write({'list_price': 20})
        with self.assertRaises(AccessError):
            tmpl.product_variant_id.write({'standard_price': 20})
        # La marca de creación no sirve en un producto ya verificado.
        tmpl.with_user(self.env.ref('base.user_admin')).action_verify_product()
        with self.assertRaises(AccessError):
            tmpl.with_context(product_verification_new_product=True).write({'list_price': 30})
