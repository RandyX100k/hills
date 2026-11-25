# -*- coding: utf-8 -*-
from odoo import api, models
from odoo.addons.odnt_price_access_control.models.odnt_product_product import (
    OdntProductProduct, _ODNT_VARIANT_PRICE_FIELDS,
)
from odoo.addons.odnt_price_access_control.models.odnt_product_template import (
    OdntProductTemplate, _ODNT_PRICE_FIELDS,
)

from . import price_lock_bypass

# Marca de "este producto se está creando ahora". Solo dentro de create() pasan
# el precio y el costo iniciales; el producto nace pendiente de verificación y
# no se usa hasta que el Verificador lo revise.
NEW_PRODUCT_CTX = 'product_verification_new_product'
NAME = 'product_verification_price_lock'


def _allow_create(model, vals_list):
    # La regla vive en la clase de Odonity, compartida por todas las bases del
    # servidor: solo aplica en las bases que tienen este módulo instalado.
    if getattr(type(model), '_pvpl_installed', False):
        return {NEW_PRODUCT_CTX: True}
    return None


def _creating(records, vals, price_fields):
    return bool(records.env.context.get(NEW_PRODUCT_CTX)) and set(vals) <= price_fields and all(
        state != 'verified' for state in records.mapped('verification_state')
    )


price_lock_bypass.register(OdntProductTemplate, 'create', NAME, _allow_create)
# Los inversos de precio/costo que Odoo dispara dentro de la creación.
price_lock_bypass.register(
    OdntProductTemplate, 'write', NAME,
    lambda records, vals: _creating(records, vals, _ODNT_PRICE_FIELDS),
)
price_lock_bypass.register(
    OdntProductProduct, 'write', NAME,
    lambda records, vals: _creating(records, vals, _ODNT_VARIANT_PRICE_FIELDS),
)


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    # Marca: este módulo está instalado en esta base (ver _allow_create).
    _pvpl_installed = True


class ProductProduct(models.Model):
    _inherit = 'product.product'

    @api.model_create_multi
    def create(self, vals_list):
        # Al crear una variante Odoo escribe lst_price/standard_price después
        # de crearla: la marca de creación cubre esos inversos.
        records = super(ProductProduct, self.with_context(**{NEW_PRODUCT_CTX: True})).create(vals_list)
        return records.with_env(self.env)
