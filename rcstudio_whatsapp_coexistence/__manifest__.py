{
    'name': 'WhatsApp Coexistence Community',
    'version': '18.0.1.2.0',  # 18.0: port of the 19.0 module
    'category': 'Productivity/WhatsApp',
    'summary': 'Connect WhatsApp to Odoo without losing your phone connection: '
               'official Embedded Signup, no bans, no second app. For Community',
    'description': """
WhatsApp Coexistence Community
==============================

WhatsApp for Odoo Community, built on Meta's Cloud API with its own conversation model
and its own inbox.

* **Coexistence.** Connect the number you already use on the WhatsApp Business app by
  scanning a QR code. The phone keeps working; Odoo sees every message. Onboarding
  goes through RC Studio's Tech Provider service (licence key) or through your own
  Meta Tech Provider app, whose App ID, App Secret and Login Configuration your
  provider must give you.
* **Dedicated Cloud API numbers** with your own Meta app.
* **An inbox** for the people who answer: conversations, search, unread, one number or all,
  voice notes recorded in the browser and delivered as real WhatsApp voice messages.
* **Templates** synced with Meta, mapped to fields of any model, sent from any record
  with the document attached as PDF, in bulk from a list, or automatically from an
  automation rule.
* **In the chatter.** Every message, in and out, is logged on the document the
  conversation is about -- an opportunity, a quotation, a ticket -- so a salesperson
  reads the whole exchange in the record, like an email thread, without leaving it.
  A customer who writes first becomes an opportunity in one click, with the messages
  already there and WhatsApp recorded as the source.
* One conversation per customer and number. Nothing goes through Discuss.

Runs on-premise. Odoo Online does not accept third-party modules. Requires a public
HTTPS URL for Meta's webhook.

WhatsApp is a trademark of Meta Platforms, Inc. RC Studio is not affiliated with Meta.
    """,
    'author': 'RC Studio',
    'website': 'https://rcstudio.dev',
    'license': 'OPL-1',
    'price': 99.00,
    'currency': 'USD',
    'depends': ['mail', 'phone_validation', 'base_automation'],
    'external_dependencies': {'python': ['phonenumbers', 'requests']},
    'data': [
        'security/groups.xml',
        'security/ir.model.access.csv',
        'security/rules.xml',
        'data/params.xml',
        'data/cron.xml',
        'views/number_views.xml',
        'views/coexistence_views.xml',
        'views/inbox_views.xml',
        'views/conversation_views.xml',
        'views/message_views.xml',
        'views/template_views.xml',
        'views/res_partner_views.xml',
        'views/res_config_settings_views.xml',
        'views/ir_actions_server_views.xml',
        'wizard/send_views.xml',
        'views/menus.xml',
        'data/actions.xml',
    ],
    'demo': [
        'demo/demo.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'rcstudio_whatsapp_coexistence/static/src/**/*',
            ('remove', 'rcstudio_whatsapp_coexistence/static/src/**/*.dark.scss'),
        ],
        'web.assets_web_dark': [
            'rcstudio_whatsapp_coexistence/static/src/**/*.dark.scss',
        ],
    },
    'images': [
        'static/description/banner.gif',
        'static/description/banner.png',
        'static/description/inbox-all-numbers.png',
        'static/description/inbox-sales.png',
        'static/description/number-coexistence.png',
        'static/description/send-wizard.png',
    ],
    'post_init_hook': 'post_init_hook',
    'installable': True,
    'application': True,
    'auto_install': False,
}
