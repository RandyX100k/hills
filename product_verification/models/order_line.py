# -*- coding: utf-8 -*-
from odoo import _, api, models
from odoo.exceptions import ValidationError

# Líneas que Odoo genera solo (anticipos, envíos) no pasan por verificación.
EXEMPT_LINE_FLAGS = ('is_downpayment', 'is_delivery')


class ProductVerificationLineMixin(models.AbstractModel):
    _name = 'product.verification.line.mixin'
    _description = "Bloqueo de productos sin verificar en líneas de pedido"

    def _check_product_verification(self):
        lines = self.filtered(
            lambda line: line.product_id
            and not line.display_type
            and not any(flag in line._fields and line[flag] for flag in EXEMPT_LINE_FLAGS)
        )
        pending = lines.product_id.filtered(lambda product: product.verification_state != 'verified')
        if pending:
            raise ValidationError(_(
                "Estos productos no tienen el visto bueno de verificación y no "
                "se pueden usar todavía:\n%(products)s\n\nPídale a un "
                "Verificador de productos que los revise.",
                products="\n".join("- %s" % name for name in pending.mapped('display_name')),
            ))


class PurchaseOrderLine(models.Model):
    _name = 'purchase.order.line'
    _inherit = ['purchase.order.line', 'product.verification.line.mixin']

    @api.constrains('product_id')
    def _constrains_product_verification(self):
        self._check_product_verification()


class SaleOrderLine(models.Model):
    _name = 'sale.order.line'
    _inherit = ['sale.order.line', 'product.verification.line.mixin']

    @api.constrains('product_id')
    def _constrains_product_verification(self):
        self._check_product_verification()


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    def button_confirm(self):
        self.order_line._check_product_verification()
        return super().button_confirm()


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def action_confirm(self):
        self.order_line._check_product_verification()
        return super().action_confirm()
