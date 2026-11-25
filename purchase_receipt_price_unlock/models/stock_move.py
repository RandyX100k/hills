# -*- coding: utf-8 -*-
from odoo import models

RECEIPT_COST_UPDATE_CTX = 'approved_receipt_cost_update'


class StockMove(models.Model):
    _inherit = 'stock.move'

    def _action_done(self, cancel_backorder=False):
        purchase_moves = self.sudo().filtered(
            lambda move: move.picking_type_id.code == 'incoming' and move.purchase_line_id
        )
        orders = purchase_moves.purchase_line_id.order_id
        if orders and all(state == 'approved' for state in orders.mapped('receipt_approval_state')):
            self = self.with_context(**{RECEIPT_COST_UPDATE_CTX: True})
        return super()._action_done(cancel_backorder=cancel_backorder)
