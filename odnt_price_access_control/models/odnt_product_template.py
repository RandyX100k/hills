# -*- coding: utf-8 -*-
# Odoo Proprietary License v1.1 - Strict Commercial Use
# © 2026-Present Odonity. All Rights Reserved.
# Module Name: Product Price Lock Pro | Restrict Price Edit by User Role | Price Access Control (By Odonity)
# License Owner: Odonity (https://www.odonity.com)
# Support Email: odonity.services@gmail.com
# This software is licensed, not sold. Unauthorized distribution, reproduction,
# modification, or use is strictly prohibited. You are granted a non-transferable,
# non-exclusive, and non-sublicensable license under strict terms. See full license
# at the bottom of this module or contact Odonity for details.

from odoo import api, fields, models, _
from odoo.exceptions import AccessError

_ODNT_PRICE_GROUP = 'odnt_price_access_control.odnt_group_price_manager'
_ODNT_PRICE_FIELDS = {'list_price', 'standard_price'}


class OdntProductTemplate(models.Model):
    _inherit = 'product.template'

    odnt_price_editable = fields.Boolean(
        string='Price Editable',
        compute='_odnt_compute_price_editable',
        store=False,
    )

    @api.depends_context('uid')
    def _odnt_compute_price_editable(self):
        odnt_can_edit = self.env.user.has_group(_ODNT_PRICE_GROUP)
        for rec in self:
            rec.odnt_price_editable = odnt_can_edit

    def write(self, vals):
        if _ODNT_PRICE_FIELDS.intersection(vals):
            if not self.env.user.has_group(_ODNT_PRICE_GROUP):
                raise AccessError(_(
                    "You do not have permission to change product prices.\n"
                    "Please contact a Price Manager or your system administrator."
                ))
        return super().write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.user.has_group(_ODNT_PRICE_GROUP):
            for vals in vals_list:
                if _ODNT_PRICE_FIELDS.intersection(vals):
                    raise AccessError(_(
                        "You do not have permission to set a custom price during creation.\n"
                        "Please contact a Price Manager or your system administrator."
                    ))
        return super().create(vals_list)
