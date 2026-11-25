from odoo import api, fields, models


class ServerAction(models.Model):
    """'Send WhatsApp template' as a server action, so automation rules can send one."""
    _inherit = 'ir.actions.server'

    state = fields.Selection(selection_add=[('rcs_whatsapp', 'Send WhatsApp template')],
                             ondelete={'rcs_whatsapp': 'cascade'})
    rcs_whatsapp_template_id = fields.Many2one(
        'rcs.whatsapp.template', string='WhatsApp Template', ondelete='set null',
        domain="[('status', '=', 'approved'), '|', ('model_name', '=', model_name), ('model_id', '=', False)]")

    @api.onchange('state', 'model_id')
    def _onchange_rcs_whatsapp_model(self):
        if self.state == 'rcs_whatsapp' and self.rcs_whatsapp_template_id.model_name \
                and self.rcs_whatsapp_template_id.model_name != self.model_name:
            self.rcs_whatsapp_template_id = False

    def _run_action_rcs_whatsapp_multi(self, eval_context=None):
        records = (eval_context or {}).get('records') or (eval_context or {}).get('record')
        if records is None:
            records = self.env[self.model_name].browse(self.env.context.get('active_ids') or [])
        if records and self.rcs_whatsapp_template_id:
            self.env['rcs.whatsapp.send']._send_template_to_records(self.rcs_whatsapp_template_id, records)
        return False
