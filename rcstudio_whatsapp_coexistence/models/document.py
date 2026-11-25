"""The documents a conversation can be about, and the ones it can create.

An email carries ``In-Reply-To``, so Odoo always knows which thread a reply belongs to.
A WhatsApp message carries nothing of the sort: Meta hands over one phone number talking
to another, and there is a single, everlasting conversation per customer and business
number. No rule can tell whether "when are you coming?" answers the loft quote or the
facade one.

So the conversation remembers what it is currently about -- its anchor -- and every
message, inbound and outbound, is mirrored to that document's chatter. The anchor is set
by sending from a record, changed by hand from the inbox, and let go when the document
closes or the thread goes quiet.

Nothing here imports crm or any other application. Each entry is looked up in the
registry when it is needed, so the module installs and runs in a database without them.
"""
from markupsafe import Markup

from odoo import _

# A thread nobody has touched in this long stops feeding its document's chatter.
ANCHOR_STALE_DAYS = 30


# ---------------------------------------------------------------------- crm.lead
def _crm_lead_label(self):
    return _('Opportunity')


def _crm_lead_closed(lead):
    """Won opportunities are done with. Lost ones are archived, which the generic
    check catches on its own."""
    return bool(lead.stage_id.is_won)


def _crm_lead_candidates(self):
    """Open opportunities of this customer: what the operator is most likely to mean."""
    partner = self.partner_id
    Lead = self.env['crm.lead']
    if not partner:
        return Lead
    return Lead.search([('partner_id', '=', partner.id), ('type', '=', 'opportunity'),
                        ('stage_id.is_won', '=', False)], limit=10)


def _crm_lead_create(self):
    """An opportunity for a customer who wrote first. Created with the user's own rights:
    the operator answering WhatsApp is not necessarily a salesperson."""
    env = self.env
    first = env['rcs.whatsapp.message'].search(
        [('conversation_id', '=', self.id), ('direction', '=', 'in')],
        order='date asc, id asc', limit=1)
    # `phone` is the one number field crm.lead has on every supported version: 17 and 18
    # also carry `mobile`, 19 and 20 dropped it.
    vals = {
        'type': 'opportunity',
        'name': _crm_lead_name(self, first),
        'phone': '+' + (self.phone or ''),
    }
    source = _whatsapp_source(env)
    if source:
        vals['source_id'] = source.id
    if self.partner_id:
        vals['partner_id'] = self.partner_id.id
    else:
        # No contact on purpose: a lead holds a name and a phone by itself, and a
        # partner for every stranger who writes is how a database turns to mud.
        vals['contact_name'] = self.profile_name or self.phone_display
    if first and first.body:
        vals['description'] = Markup('<p>%s</p>') % first.body
    return env['crm.lead'].create(vals)


def _crm_lead_name(self, first):
    """What the customer actually asked for, trimmed. Ugly but honest, and renamed in
    two seconds from the link in the notification."""
    text = ' '.join((first.body or '').split())[:60] if first else ''
    return text or _('WhatsApp: %s', self.name or self.phone_display or '')


def _whatsapp_source(env):
    """The utm.source that answers "how many opportunities came in through WhatsApp,
    and how many did we win?". crm always brings utm along, so it exists whenever
    crm.lead does. Created once and kept under an xmlid so it is never duplicated."""
    if 'utm.source' not in env:
        return None
    xmlid = 'rcstudio_whatsapp_coexistence.utm_source_whatsapp'
    source = env.ref(xmlid, raise_if_not_found=False)
    if not source:
        source = env['utm.source'].sudo().create({'name': 'WhatsApp'})
        env['ir.model.data'].sudo()._update_xmlids([
            {'xml_id': xmlid, 'record': source, 'noupdate': True}])
    return source


# --------------------------------------------------------------------- registry
# model -> how to create one, when it counts as closed, and what to offer as an anchor.
# `group` is the access group the button needs; `create` is omitted for models that can
# only be anchored to, never created from the inbox. Every callable takes the
# conversation recordset, named `self` so that _() can read the language off it.
DOCUMENT_TYPES = {
    'crm.lead': {
        'sequence': 10,
        'label': _crm_lead_label,
        'group': 'sales_team.group_sale_salesman',
        'create': _crm_lead_create,
        'closed': _crm_lead_closed,
        'candidates': _crm_lead_candidates,
    },
}


def document_types(env):
    """The entries whose model exists in this database, in display order."""
    return [(model, entry) for model, entry in sorted(
        DOCUMENT_TYPES.items(), key=lambda item: item[1].get('sequence', 100))
        if model in env]
