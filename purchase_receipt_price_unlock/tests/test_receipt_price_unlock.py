# -*- coding: utf-8 -*-
from odoo.exceptions import AccessError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user


@tagged('post_install', '-at_install')
class TestReceiptPriceUnlock(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.groups_id += cls.env.ref('odnt_price_access_control.odnt_group_price_manager')
        cls.user = new_test_user(
            cls.env, login='rpu_buyer',
            groups='purchase.group_purchase_user,stock.group_stock_user',
        )
        cls.manager = new_test_user(
            cls.env, login='rpu_manager',
            groups='purchase.group_purchase_manager,stock.group_stock_manager,'
                   'purchase_receipt_approval.group_receipt_approval_manager',
        )
        cls.partner = cls.env['res.partner'].create({'name': "Proveedor"})
        categ = cls.env['product.category'].create({
            'name': "AVCO", 'property_cost_method': 'average', 'property_valuation': 'manual_periodic',
        })
        cls.product = cls.env['product.product'].create({
            'name': "Pieza", 'is_storable': True, 'categ_id': categ.id,
            'purchase_ok': True,
        })
        if hasattr(cls.product, 'action_verify_product'):  # product_verification instalado
            cls.product.action_verify_product()

    def _receive(self, approval):
        self.env.company.purchase_approval_email_to = 'encargado@example.com' if approval else False
        po = self.env['purchase.order'].with_user(self.user).create({
            'partner_id': self.partner.id,
            'order_line': [(0, 0, {'product_id': self.product.id, 'product_qty': 2, 'price_unit': 150})],
        })
        po.button_confirm()
        if approval:
            self.assertEqual(po.receipt_approval_state, 'pending')
            po.with_user(self.manager).action_give_receipt_approval()
        picking = po.picking_ids.with_user(self.user)
        picking.move_ids.quantity = 2
        picking.button_validate()
        return picking

    def test_approved_receipt_updates_cost(self):
        picking = self._receive(approval=True)
        self.assertEqual(picking.state, 'done')
        self.assertEqual(self.product.standard_price, 150)

    def test_price_still_locked_outside_receipt(self):
        self._receive(approval=True)
        with self.assertRaises(AccessError):
            self.product.with_user(self.user).write({'standard_price': 1})
        with self.assertRaises(AccessError):
            self.product.with_user(self.user).with_context(
                approved_receipt_cost_update=True).write({'standard_price': 1, 'lst_price': 1})

    def test_receipt_without_approval_keeps_lock(self):
        with self.assertRaises(AccessError):
            self._receive(approval=False)
