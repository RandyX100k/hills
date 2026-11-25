from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    rcs_whatsapp_opt_out_words = fields.Char(
        'Opt-out words', config_parameter='rcstudio_whatsapp_coexistence.opt_out_words',
        help="Comma separated. A customer who replies one of these is blacklisted.")
    rcs_whatsapp_auto_read = fields.Boolean(
        'Send read receipts', config_parameter='rcstudio_whatsapp_coexistence.auto_read',
        help="Tell Meta the customer's messages were read when an operator opens the conversation.")
    rcs_whatsapp_sync_contacts = fields.Boolean(
        'Create contacts from the phone', config_parameter='rcstudio_whatsapp_coexistence.sync_contacts',
        help="Coexistence: create a contact for each phone contact Meta sends during the "
             "one-time sync, when no contact with that number exists yet.")
    rcs_whatsapp_mirror_chatter = fields.Boolean(
        'Log conversations in the chatter', config_parameter='rcstudio_whatsapp_coexistence.mirror_chatter',
        help="Copy every message, in and out, to the chatter of the document the "
             "conversation is about, so the whole exchange reads in the record like an "
             "email thread. Without a document, messages are logged on the contact.")
    rcs_whatsapp_graph_version = fields.Char(
        'Graph API version', config_parameter='rcstudio_whatsapp_coexistence.graph_version')
    rcs_whatsapp_broker_url = fields.Char(
        "RC Studio service URL", config_parameter='rcstudio_whatsapp_coexistence.broker_url')

    def action_open_rcs_whatsapp_numbers(self):
        return self.env['ir.actions.act_window']._for_xml_id('rcstudio_whatsapp_coexistence.action_number')
