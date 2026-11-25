from odoo import _, fields, models


class ResPartner(models.Model):
    _inherit = 'res.partner'

    rcs_whatsapp_conversation_ids = fields.One2many('rcs.whatsapp.conversation', 'partner_id')
    rcs_whatsapp_conversation_count = fields.Integer(compute='_compute_rcs_whatsapp_conversation_count')

    def _compute_rcs_whatsapp_conversation_count(self):
        counts = dict(self.env['rcs.whatsapp.conversation']._read_group(
            [('partner_id', 'in', self.ids)], ['partner_id'], ['__count']))
        for partner in self:
            partner.rcs_whatsapp_conversation_count = counts.get(partner, 0)

    def action_open_rcs_whatsapp_conversations(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('WhatsApp'),
            'res_model': 'rcs.whatsapp.conversation',
            'view_mode': 'list,form',
            'domain': [('partner_id', '=', self.id)],
            'context': {'default_partner_id': self.id},
        }
