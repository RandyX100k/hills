# -*- coding: utf-8 -*-
{
    'name': "product_inventory_import",

    'summary': "Importar/actualizar productos e inventario desde Excel",

    'description': """
Wizard en Inventario para subir un Excel de productos (nombre, costo promedio,
precio de venta, cantidad a la mano): si el producto ya existe (por nombre) se
actualiza el costo, el precio de venta y se ajusta la existencia a la cantidad
del Excel; si estaba configurado como Servicio se corrige a Bienes para poder
llevar inventario. Si no existe se crea como Bienes con inventario rastreado y
se carga la existencia inicial. Permite elegir un impuesto (ITBIS) que se
aplica igual a compras y ventas, y deja la política de facturación siempre en
"Cantidades ordenadas".
    """,

    'author': "RutiversoTech",
    'website': "https://www.rutiversotech.com",

    'category': 'Inventory',
    'version': '0.1',

    'depends': ['product', 'stock', 'account', 'sale'],

    'external_dependencies': {
        'python': ['openpyxl'],
    },

    'data': [
        'security/ir.model.access.csv',
        'wizard/product_inventory_import_wizard_views.xml',
    ],
}
