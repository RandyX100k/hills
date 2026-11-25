# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import AccessError

VERIFIER_GROUP = 'product_verification.group_product_verifier'
VERIFICATION_FIELDS = ('verification_state', 'verified_by_id', 'verified_date')
VERIFICATION_STATES = [
    ('pending', "Pendiente de verificación"),
    ('verified', "Verificado"),
]


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    verification_state = fields.Selection(
        selection=VERIFICATION_STATES,
        string="Verificación",
        default='pending',
        required=True,
        copy=False,
        readonly=True,
        index=True,
        tracking=True,
        help="Un producto pendiente no se puede usar en compras ni en órdenes "
             "de venta hasta que un Verificador de productos le dé el visto bueno.",
    )
    verified_by_id = fields.Many2one(
        comodel_name='res.users',
        string="Verificado por",
        copy=False,
        readonly=True,
    )
    verified_date = fields.Datetime(
        string="Fecha de verificación",
        copy=False,
        readonly=True,
    )

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.context.get('product_verification_write'):
            for vals in vals_list:
                for field_name in VERIFICATION_FIELDS:
                    vals.pop(field_name, None)
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.context.get('product_verification_write') and any(
            field_name in vals for field_name in VERIFICATION_FIELDS
        ):
            raise AccessError(_(
                "La verificación del producto solo se cambia con los botones "
                "Dar visto bueno / Volver a pendiente."
            ))
        return super().write(vals)

    def _check_verifier(self):
        if not self.env.user.has_group(VERIFIER_GROUP):
            raise AccessError(_("Solo un Verificador de productos puede cambiar la verificación."))

    def _set_verification(self, vals):
        return self.with_context(product_verification_write=True).write(vals)

    def action_verify_product(self):
        self._check_verifier()
        pending = self.filtered(lambda tmpl: tmpl.verification_state != 'verified')
        pending._set_verification({
            'verification_state': 'verified',
            'verified_by_id': self.env.user.id,
            'verified_date': fields.Datetime.now(),
        })
        for tmpl in pending:
            tmpl.message_post(body=_("Visto bueno dado por %(user)s.", user=self.env.user.name))
        return True

    def action_reset_verification(self):
        self._check_verifier()
        verified = self.filtered(lambda tmpl: tmpl.verification_state == 'verified')
        verified._set_verification({
            'verification_state': 'pending',
            'verified_by_id': False,
            'verified_date': False,
        })
        for tmpl in verified:
            tmpl.message_post(body=_("Devuelto a pendiente por %(user)s.", user=self.env.user.name))
        return True


class ProductProduct(models.Model):
    _inherit = 'product.product'

    verification_state = fields.Selection(
        related='product_tmpl_id.verification_state',
        string="Verificación",
    )
    verified_by_id = fields.Many2one(related='product_tmpl_id.verified_by_id')
    verified_date = fields.Datetime(related='product_tmpl_id.verified_date')

    def action_verify_product(self):
        return self.product_tmpl_id.action_verify_product()

    def action_reset_verification(self):
        return self.product_tmpl_id.action_reset_verification()
