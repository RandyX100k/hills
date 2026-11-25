# -*- coding: utf-8 -*-
from odoo import models
from odoo.addons.odnt_price_access_control.models.odnt_product_product import OdntProductProduct

from .stock_move import RECEIPT_COST_UPDATE_CTX


class ProductProduct(models.Model):
    _inherit = 'product.product'

    def write(self, vals):
        # Solo el costo que recalcula la recepción de una compra con visto
        # bueno se salta el candado de Product Price Lock Pro.
        if self.env.context.get(RECEIPT_COST_UPDATE_CTX) and set(vals) == {'standard_price'}:
            return super(OdntProductProduct, self).write(vals)
        return super().write(vals)
