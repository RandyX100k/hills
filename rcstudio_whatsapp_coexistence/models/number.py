import logging
import secrets

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import config

from ..tools.graph import GraphClient, GraphError

_logger = logging.getLogger(__name__)


class WhatsAppNumber(models.Model):
    """A business phone number connected to Odoo.

    It is the unit of everything: credentials, webhook, operators, templates and
    conversations hang from it. A number is either a dedicated Cloud API number or a
    coexistence number that keeps working on the phone.
    """
    _name = 'rcs.whatsapp.number'
    _description = 'WhatsApp Business Number'
    _order = 'sequence, id'

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    color = fields.Integer()
    icon = fields.Image(max_width=256, max_height=256,
                        help="Shown on every conversation of this number in the inbox.")
    company_ids = fields.Many2many(
        'res.company', string='Allowed Companies',
        help="Leave empty to make the number available in every company.")
    user_ids = fields.Many2many(
        'res.users', string='Operators',
        # 18.0: res.users has groups_id, not all_group_ids
        domain=[('share', '=', False)],
        help="Users who answer on this number. Leave empty to let every WhatsApp user in. "
             "Anyone listed here is given the WhatsApp Operator role automatically.")

    connection_mode = fields.Selection([
        ('cloud_api', 'WABA API Connection (Cloud API)'),
        ('coexistence', 'Coexistence (the number stays on the phone)'),
    ], required=True, default='cloud_api')
    provider = fields.Selection([
        ('rcstudio', "RC Studio's Tech Provider service (licence key)"),
        ('own', 'Your own Meta Tech Provider app'),
    ], string='Onboarding through', default='rcstudio',
        help="Coexistence is onboarded with Meta's Embedded Signup, which only works through "
             "an app approved by Meta as a Tech Provider for WhatsApp Business App Onboarding. "
             "Use RC Studio's app with a licence key, or your own approved app.")

    # ---- Meta credentials. Secrets are readable by managers only (see the form view).
    app_id = fields.Char('App ID')
    app_secret = fields.Char('App Secret', groups='rcstudio_whatsapp_coexistence.group_manager')
    waba_id = fields.Char('WhatsApp Business Account ID')
    phone_number_id = fields.Char('Phone Number ID', index='btree_not_null')
    access_token = fields.Char('Access Token', groups='rcstudio_whatsapp_coexistence.group_manager')
    login_config_id = fields.Char(
        'Login Configuration ID',
        help="The 'Facebook Login for Business' configuration of your Tech Provider app.")
    licence_key = fields.Char('RC Studio Licence', groups='rcstudio_whatsapp_coexistence.group_manager')

    # ---- What Meta says about the number
    display_phone = fields.Char('Phone Number', readonly=True)
    verified_name = fields.Char(readonly=True)
    quality_rating = fields.Char(readonly=True)
    platform_type = fields.Char(readonly=True)
    is_on_biz_app = fields.Boolean('On the WhatsApp Business app', readonly=True)
    last_check = fields.Datetime(readonly=True)

    status = fields.Selection([
        ('draft', 'Not connected'),
        ('connected', 'Connected'),
        ('error', 'Error'),
    ], default='draft', readonly=True, copy=False)
    status_message = fields.Text(readonly=True, copy=False)

    # ---- Webhook
    webhook_token = fields.Char(readonly=True, copy=False, default=lambda self: secrets.token_urlsafe(24))
    webhook_url = fields.Char(compute='_compute_webhook_url')
    webhook_subscribed = fields.Boolean(readonly=True, copy=False)
    webhook_last_event = fields.Datetime(readonly=True, copy=False)
    webhook_secret = fields.Char(
        readonly=True, copy=False, groups='rcstudio_whatsapp_coexistence.group_manager',
        help="Secret the RC Studio relay signs deliveries with. With your own app, Meta signs "
             "with the App Secret instead.")
    webhook_delivery = fields.Selection([
        ('direct', 'Straight from Meta to this Odoo'),
        ('relay', 'Through the RC Studio service (older connections)'),
    ], default='direct', required=True, readonly=True, copy=False, string='Webhook Delivery',
        help="How Meta's deliveries reach this Odoo. The module always asks for direct delivery, "
             "so no message passes through RC Studio, which only takes part in the connection. "
             "Numbers connected with an older version stay on the relay until they are connected "
             "again; that is the only case where this reads otherwise.")

    # ---- Coexistence bookkeeping (the flow itself lives in coexistence.py)
    sync_contacts_done = fields.Datetime(readonly=True, copy=False)
    sync_history_done = fields.Datetime(readonly=True, copy=False)

    template_ids = fields.One2many('rcs.whatsapp.template', 'number_id')
    conversation_ids = fields.One2many('rcs.whatsapp.conversation', 'number_id')
    template_count = fields.Integer(compute='_compute_counts')
    conversation_count = fields.Integer(compute='_compute_counts')

    # 18.0: models.Constraint does not exist, _sql_constraints instead
    _sql_constraints = [
        ('phone_number_id_unique', 'UNIQUE(phone_number_id)',
         'This Phone Number ID is already connected on another business number.'),
    ]


    # ---------------------------------------------------------------- operators
    @api.model_create_multi
    def create(self, vals_list):
        numbers = super().create(vals_list)
        numbers._ensure_operator_role()
        return numbers

    def write(self, vals):
        res = super().write(vals)
        if 'user_ids' in vals:
            self._ensure_operator_role()
        return res

    def _ensure_operator_role(self):
        """Listing a user as operator is enough: the WhatsApp Operator group is added for
        them, so a manager never has to visit Settings > Users first."""
        group = self.env.ref('rcstudio_whatsapp_coexistence.group_user')
        Users = self.env['res.users'].sudo()
        field = 'group_ids' if 'group_ids' in Users._fields else 'groups_id'
        missing = self.sudo().mapped('user_ids').filtered(lambda u: group not in u[field])
        if missing:
            missing.write({field: [(4, group.id)]})

    # ------------------------------------------------------------------ computes
    def _compute_webhook_url(self):
        # Read the base URL from the system parameter, not from get_base_url(): that helper
        # accepts a single record only, and a compute runs on whole batches - the scheduler
        # registering two pending numbers in a row was enough to raise a singleton error.
        base = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '')
        for number in self:
            number.webhook_url = (f'{base}/rcs_whatsapp/webhook/{number.id}/{number.webhook_token}'
                                  if number.id else False)

    def _compute_counts(self):
        for number in self:
            number.template_count = len(number.template_ids)
            number.conversation_count = len(number.conversation_ids)

    @api.constrains('connection_mode', 'provider', 'app_id', 'login_config_id')
    def _check_provider(self):
        for number in self:
            if number.connection_mode == 'coexistence' and not number.provider:
                raise ValidationError(_('Choose how the coexistence number is onboarded.'))
            if number.connection_mode == 'coexistence' and number.provider == 'own' \
                    and number.status == 'connected' and not (number.app_id and number.login_config_id):
                raise ValidationError(_('Your own Tech Provider app needs an App ID and a Login Configuration ID.'))

    # ------------------------------------------------------------------- helpers
    def _param(self, key, default=None):
        return self.env['ir.config_parameter'].sudo().get_param(f'rcstudio_whatsapp_coexistence.{key}', default)

    def _graph(self):
        """A Graph client for this number. Secrets are read with sudo on purpose: the
        operator sending a message must not need read access to the token."""
        self.ensure_one()
        me = self.sudo()
        return GraphClient(
            token=me.access_token, phone_number_id=me.phone_number_id, waba_id=me.waba_id,
            app_id=me.app_id, app_secret=me.app_secret,
            version=self._param('graph_version') or None)

    def _webhook_secret(self):
        """What signs the webhook body: the relay's session secret in licence mode, the
        Meta App Secret otherwise. Empty while the number is still being set up, and empty
        on purpose in direct delivery, where Meta signs with a secret we do not hold."""
        self.ensure_one()
        me = self.sudo()
        if me.connection_mode == 'coexistence' and me.provider == 'rcstudio':
            return '' if me.webhook_delivery == 'direct' else me.webhook_secret
        return me.app_secret

    def _webhook_unsigned(self):
        """True when this number's deliveries come straight from Meta through the licence
        service: the signature is made with an App Secret that never reaches this Odoo, so
        the token in the URL is what authenticates the request."""
        self.ensure_one()
        me = self.sudo()
        return (me.connection_mode == 'coexistence' and me.provider == 'rcstudio'
                and me.webhook_delivery == 'direct')

    def action_rotate_webhook_token(self):
        """A new token, hence a new URL, and Meta told about it. The old URL stops
        working the moment this is saved.

        Announcing the new URL is handed to the scheduler and never done here: whoever
        verifies it - Meta, or the licence service on its behalf - knocks on the new URL
        in a separate request, which cannot see the new token while this transaction is
        still open. Registering it inline is what makes that handshake answer 403.
        """
        self.ensure_one()
        me = self.sudo()
        me.write({'webhook_token': secrets.token_urlsafe(32), 'webhook_subscribed': False})
        self.invalidate_recordset(['webhook_url'])
        if me.status != 'connected':
            return self._toast(_('New webhook URL: %s', self.webhook_url))
        if self._queue_webhook_subscription():
            return self._toast(_('New webhook URL: %s\nRegistering it takes a few seconds; the '
                                 'Webhook tab shows it as soon as it is confirmed.',
                                 self.webhook_url))
        # No scheduler on this instance, so nothing runs after this transaction, and the new
        # token cannot be announced before it is saved. The registration waits for the
        # Subscribe webhook button, which runs in a request of its own.
        return self._toast(_('New webhook URL: %s\nPress Subscribe webhook to register it: '
                             'this instance has no scheduler to do it on its own.',
                             self.webhook_url), kind='warning')

    def _bus_partners(self):
        """Partners that receive live inbox updates for this number: its operators, or
        every WhatsApp user when the number is open to all."""
        self.ensure_one()
        users = self.sudo().user_ids
        if not users:
            # 18.0: res.groups has `users`, not all_user_ids
            users = self.env.ref('rcstudio_whatsapp_coexistence.group_user').sudo().users
        return users.partner_id

    def _notify(self, notification_type, payload):
        bus = self.env['bus.bus']
        for partner in self._bus_partners():
            bus._sendone(partner, notification_type, payload)

    def _set_status(self, status, message=None):
        self.sudo().write({'status': status, 'status_message': message or False})

    # ------------------------------------------------------------------- actions
    def action_test_connection(self):
        self.ensure_one()
        try:
            info = self._graph().phone_info()
        except GraphError as exc:
            self._set_status('error', str(exc))
            raise UserError(_('Meta rejected the credentials: %s', exc))
        self.sudo().write({
            'display_phone': info.get('display_phone_number'),
            'verified_name': info.get('verified_name'),
            'quality_rating': info.get('quality_rating'),
            'platform_type': info.get('platform_type'),
            'is_on_biz_app': bool(info.get('is_on_biz_app')),
            'last_check': fields.Datetime.now(),
            'status': 'connected',
            'status_message': False,
        })
        return self._toast(_('Connected. Meta reports %s as %s.',
                             info.get('display_phone_number'), info.get('verified_name')))

    def action_subscribe_webhook(self):
        """Register this number's webhook, out of the web request.

        Registering makes Meta verify the callback URL straight away, and Meta gives up
        after six seconds. Doing it here would hold the worker serving this click while
        Meta knocks on the door, and an Odoo with a single worker would have nobody left
        to answer: the verification would always time out. So it is handed to the
        scheduler, which runs in its own process and leaves every worker free.
        """
        self.ensure_one()
        if self._queue_webhook_subscription():
            return self._toast(_('Registering the webhook. It takes a few seconds; the '
                                 'Webhook tab shows it as soon as Meta confirms.'))
        self._subscribe_webhook_now()
        return self._toast(_('Meta will deliver this number’s events to %s', self.webhook_url))

    def _queue_webhook_subscription(self):
        """Ask the scheduler to register the webhook. False when there is no scheduler to
        ask (max_cron_threads = 0), in which case the caller does it inline."""
        self.ensure_one()
        self.sudo().write({'webhook_subscribed': False})
        if not config['max_cron_threads']:
            return False
        try:
            self.env.ref('rcstudio_whatsapp_coexistence.cron_subscribe_webhooks').sudo()._trigger()
        except Exception as exc:  # noqa: BLE001 - the module may be mid-upgrade
            _logger.warning('Could not queue the webhook registration: %s', exc)
            return False
        return True

    def _subscribe_webhook_now(self):
        """Register it here and now: what the scheduler runs, and the fallback when there
        is no scheduler."""
        self.ensure_one()
        if self._is_relayed():
            self._coexistence_subscribe()
            return
        try:
            self._graph().subscribe_app(self.webhook_url, self.webhook_token)
        except GraphError as exc:
            raise UserError(_('Meta refused the webhook subscription: %s', exc))
        self.sudo().write({'webhook_subscribed': True})

    @api.model
    def _cron_subscribe_webhooks(self):
        """Register the webhook of the numbers that still have none.

        This runs in the cron thread on purpose. Registering makes Meta verify the callback
        URL right away, and Meta gives up after 6 seconds: if the registration were made
        from an HTTP worker, that worker would be blocked waiting for the service while Meta
        knocks on the door, and on a small instance the verification can queue behind it and
        time out. From the cron thread every HTTP worker stays free to answer."""
        pending = self.search([('status', '=', 'connected'), ('webhook_subscribed', '=', False)])
        for number in pending:
            try:
                with self.env.cr.savepoint():
                    number._subscribe_webhook_now()
            except Exception as exc:  # noqa: BLE001 - one number must not stop the others
                _logger.warning('Could not register the webhook of number %s: %s', number.id, exc)
                number._coexistence_log(f'The webhook could not be registered: {exc}')

    def action_unsubscribe_webhook(self):
        self.ensure_one()
        try:
            self._graph().unsubscribe_app()
        except GraphError as exc:
            raise UserError(_('Meta refused: %s', exc))
        self.sudo().write({'webhook_subscribed': False})
        return self._toast(_('Webhook subscription removed.'))

    def action_sync_templates(self):
        for number in self:
            number._sync_templates()
        return self._toast(_('Templates refreshed from Meta.'))

    def _sync_templates(self):
        self.ensure_one()
        try:
            payloads = self._graph().list_templates()
        except GraphError as exc:
            raise UserError(_('Could not read the templates from Meta: %s', exc))
        self.env['rcs.whatsapp.template']._upsert_from_meta(self, payloads)

    @api.model
    def _cron_sync_templates(self):
        for number in self.search([('status', '=', 'connected')]):
            try:
                number._sync_templates()
            except Exception:  # noqa: BLE001 - one number must not stop the others
                _logger.exception('Template sync failed for number %s', number.id)

    def action_open_conversations(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Conversations'),
            'res_model': 'rcs.whatsapp.conversation',
            'view_mode': 'list,form',
            'domain': [('number_id', '=', self.id)],
            'context': {'default_number_id': self.id},
        }

    def action_open_templates(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Templates'),
            'res_model': 'rcs.whatsapp.template',
            'view_mode': 'list,form',
            'domain': [('number_id', '=', self.id)],
            'context': {'default_number_id': self.id},
        }

    def _toast(self, message, kind='success'):
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {'message': message, 'type': kind, 'next': {'type': 'ir.actions.act_window_close'}},
        }
