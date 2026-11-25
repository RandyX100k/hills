# -*- coding: utf-8 -*-
{
    'name': "Compras - Recepción con visto bueno actualiza el costo",

    'summary': "Quien recibe o factura una compra con visto bueno puede "
               "validarla aunque no sea Price Manager (Product Price Lock Pro).",

    'description': """
Product Price Lock Pro (odnt_price_access_control) bloquea cualquier cambio de
costo o precio de venta si el usuario no es Price Manager. Al validar una
recepción de compra, Odoo recalcula el costo promedio del producto, y ese
bloqueo hacía fallar la validación con "You do not have permission to change
product prices".

Este módulo deja pasar únicamente esa actualización automática del costo
(standard_price) cuando la recepción viene de una compra con el visto bueno
dado (purchase_receipt_approval). Lo mismo al confirmar la factura del
proveedor: si sale a un precio distinto al de la compra, Odoo reajusta el
costo promedio, y eso también se permite cuando todas las compras de la
factura tienen el visto bueno. El precio de venta y cualquier edición
manual del costo siguen bloqueados para quien no sea Price Manager.
    """,

    'author': "RutiversoTech",
    'website': "https://www.rutiversotech.com",

    'category': 'Inventory/Purchase',
    'version': '18.0.1.1.0',
    'license': 'LGPL-3',

    'depends': ['stock_account', 'purchase_receipt_approval', 'odnt_price_access_control'],

    'installable': True,
    'auto_install': True,
    'application': False,
}
