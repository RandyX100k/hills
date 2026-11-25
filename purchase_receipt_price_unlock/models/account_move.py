# -*- coding: utf-8 -*-
from odoo import models

from .stock_move import RECEIPT_COST_UPDATE_CTX


class AccountMove(models.Model):
    _inherit = 'account.move'

    def _post(self, soft=True):
        # Si la factura del proveedor sale a otro precio que la compra, Odoo
        # reajusta el costo promedio al confirmarla. Se permite solo cuando
        # todas las compras de la factura tienen el visto bueno.
        bills = self.filtered(lambda move: move.move_type in ('in_invoice', 'in_refund', 'in_receipt'))
        orders = bills.sudo().invoice_line_ids.purchase_line_id.order_id
        if orders and all(state == 'approved' for state in orders.mapped('receipt_approval_state')):
            self = self.with_context(**{RECEIPT_COST_UPDATE_CTX: True})
        return super()._post(soft=soft)
