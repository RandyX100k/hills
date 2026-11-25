# -*- coding: utf-8 -*-
{
    'name': "Productos - Crear con precio cuando pasan por verificación",

    'summary': "Quien no es Price Manager puede crear productos con su precio y "
               "costo; el producto queda pendiente de verificación.",

    'description': """
Product Price Lock Pro (odnt_price_access_control) no deja crear ningún
producto a quien no sea Price Manager, porque el formulario siempre manda el
precio de venta y el costo.

Con este módulo cualquier usuario con permiso de crear productos puede
crearlos con su precio y costo. El producto nace "Pendiente de verificación"
(product_verification) y no se puede usar en compras ni ventas hasta el visto
bueno del Verificador, que revisa también el precio. Después de creado, cambiar
el precio o el costo sigue siendo solo para Price Manager.
    """,

    'author': "RutiversoTech",
    'website': "https://www.rutiversotech.com",

    'category': 'Inventory/Products',
    'version': '18.0.1.0.0',
    'license': 'LGPL-3',

    'depends': ['product_verification', 'odnt_price_access_control'],

    'data': [
        'views/product_views.xml',
    ],

    'installable': True,
    'auto_install': True,
    'application': False,
}
