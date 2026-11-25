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
{
    "name": "Product Price Lock Pro | Restrict Price Edit by User Role | Price Access Control",
    "summary": "Restrict Product Price Edit | Lock Sales Price by User Group | Price Access Control Odoo | Prevent Price Change Odoo | Product Price Permission | Role Based Price Lock | Sales Price Edit Restriction | Cost Price Lock Odoo | Price Manager Role Odoo | Odonity Price Control App",
    "version": "18.0.1.0.0",
    "author": "Odonity",
    "maintainer": "Odonity",
    "website": "https://www.odonity.com",
    "support": "odonity.services@gmail.com",
    "category": "Sales/Sales",
    "price": 30,
    "currency": "USD",
    "depends": ["product"],
    "data": [
        "security/security.xml",
        "views/odnt_product_template_views.xml",
        "views/odnt_product_product_views.xml",
    ],
    "images": ["static/description/banner.png"],
    "description": """
Restrict who can change product Sales Price and Cost Price in Odoo.
This module adds a dedicated Price Manager security group. Only users assigned to this group can edit list_price or standard_price on any product. All other users see those fields as read-only.

Protection is enforced at two levels: the UI (fields rendered read-only) and the ORM (write blocked server-side), making it impossible to bypass — even via API or scripts.

- Role-based price edit control using Odoo security groups
- Blocks Sales Price and Cost Price changes for unauthorized users
- Applies on Product form, Variant form, and List view
- Clear, user-friendly error message on unauthorized attempt
- Clean, lightweight, and migration-friendly

Developed by Odonity — trusted creators of professional Odoo productivity and security tools.
    """,
    "installable": True,
    "application": True,
    "auto_install": False,
}
