# -*- coding: utf-8 -*-
from odoo.addons.odnt_price_access_control.models.odnt_product_product import OdntProductProduct

from . import price_lock_bypass
from .stock_move import RECEIPT_COST_UPDATE_CTX

# Solo el costo que recalcula la recepción / factura de una compra con visto
# bueno se salta el candado de Product Price Lock Pro.
price_lock_bypass.register(
    OdntProductProduct, 'write', 'purchase_receipt_price_unlock',
    lambda records, vals: bool(records.env.context.get(RECEIPT_COST_UPDATE_CTX))
    and set(vals) == {'standard_price'},
)
