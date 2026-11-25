# -*- coding: utf-8 -*-
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user


@tagged('post_install', '-at_install')
class TestProductVerification(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Product Price Lock Pro (si está instalado) no deja crear productos sin este grupo.
        price_manager = cls.env.ref('odnt_price_access_control.odnt_group_price_manager', raise_if_not_found=False)
        if price_manager:
            cls.env.user.groups_id += price_manager
        cls.partner = cls.env['res.partner'].create({'name': "Proveedor"})
        cls.buyer = new_test_user(cls.env, login='pv_buyer', groups='purchase.group_purchase_user,sales_team.group_sale_salesman')
        cls.verifier = new_test_user(cls.env, login='pv_verifier', groups='base.group_user,product.group_product_manager,product_verification.group_product_verifier')
        cls.product = cls.env['product.product'].create({'name': "Nuevo", 'purchase_ok': True, 'sale_ok': True})

    def _po(self, product):
        return self.env['purchase.order'].with_user(self.buyer).create({
            'partner_id': self.partner.id,
            'order_line': [(0, 0, {'product_id': product.id, 'product_qty': 1, 'price_unit': 10})],
        })

    def test_new_product_is_pending(self):
        self.assertEqual(self.product.verification_state, 'pending')
        self.product.with_user(self.verifier).action_verify_product()
        self.assertEqual(self.product.product_tmpl_id.copy().verification_state, 'pending')

    def test_pending_blocks_purchase_and_sale(self):
        with self.assertRaises(ValidationError):
            self._po(self.product)
        with self.assertRaises(ValidationError):
            self.env['sale.order'].with_user(self.buyer).create({
                'partner_id': self.partner.id,
                'order_line': [(0, 0, {'product_id': self.product.id})],
            })

    def test_verified_allows_and_reset_blocks_confirm(self):
        self.product.with_user(self.verifier).action_verify_product()
        self.assertEqual(self.product.verification_state, 'verified')
        self.assertEqual(self.product.verified_by_id, self.verifier)
        po = self._po(self.product)
        self.product.with_user(self.verifier).action_reset_verification()
        with self.assertRaises(ValidationError):
            po.with_user(self.buyer).button_confirm()

    def test_only_verifier_can_verify(self):
        with self.assertRaises(AccessError):
            self.product.with_user(self.buyer).action_verify_product()
        with self.assertRaises(AccessError):
            self.product.product_tmpl_id.write({'verification_state': 'verified'})
        tmpl = self.env['product.template'].create({'name': "X", 'verification_state': 'verified'})
        self.assertEqual(tmpl.verification_state, 'pending')
