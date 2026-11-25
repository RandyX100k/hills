====================================================================================
Product Price Lock Pro | Restrict Price Edit by User Role | Price Access Control
====================================================================================

**Prevent Unauthorized Product Price Changes in Odoo with Role-Based Access Control**

This Odoo module by **Odonity** lets you control exactly who can change product
Sales Price and Cost Price. Only users assigned to the **Price Manager** security
group can edit price fields. All other users see those fields as read-only —
enforced at both the view level and the server (ORM) level.

Key Features
============

- 🔒 Dedicated **Price Manager** security group for price edit authorization
- 👁 Sales Price (list_price) and Cost Price (standard_price) read-only for unauthorized users
- 🛡 **Dual-layer protection**: UI (view) + ORM (server-side write block)
- 📋 Works on Product Template form, Product Variant form, and List view
- 🚫 Clear, user-friendly ``AccessError`` message on unauthorized attempt
- ⚡ Blocks API-level and script-level unauthorized writes — not just the UI
- 🧹 Clean, compressed, migration-friendly code using ``odnt`` brand prefix

Installation
============

1. Copy the ``odnt_price_access_control`` folder into your Odoo custom addons directory.
2. Restart the Odoo server.
3. Activate Developer Mode (Settings → Activate Developer Mode).
4. Go to Apps → Update App List.
5. Search for **Product Price Lock Pro** and click Install.

Configuration
=============

1. Go to **Settings → Users & Companies → Users**.
2. Open any user record.
3. Under the **Price Access Control** section, tick **Price Manager**.
4. Save.

That user now has full price edit access. All other users will see price
fields as read-only across all product views.

Compatibility
=============

- Edition: Community & Enterprise
- Depends: ``product``

Support & Contact
=================

Website: https://www.odonity.com
Email: odonity.services@gmail.com

About Odonity
=============

Odonity builds business-ready Odoo applications focused on automation,
security, productivity, and professional workflows. Trusted by businesses
worldwide for reliable, clean, and scalable Odoo solutions.
