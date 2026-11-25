# -*- coding: utf-8 -*-
{
    'name': "Productos - Visto Bueno de Verificación",

    'summary': "Los productos nuevos quedan pendientes de verificación y no se "
               "pueden usar en compras ni en órdenes de venta hasta su visto bueno.",

    'description': """
Cada producto nuevo nace "Pendiente de verificación". Mientras no tenga el
visto bueno de un Verificador de productos no se puede agregar a una orden de
compra ni a una orden de venta (tampoco confirmar una orden que lo tenga).

- Botones "Dar visto bueno" y "Volver a pendiente" en la ficha del producto,
  solo para el grupo Verificador de productos.
- Filtros "Pendientes de verificación" / "Verificados" en la lista de productos.
- Los productos que ya existían al instalar el módulo quedan verificados.
- Un producto duplicado vuelve a quedar pendiente.
    """,

    'author': "RutiversoTech",
    'website': "https://www.rutiversotech.com",

    'category': 'Inventory/Products',
    'version': '18.0.1.0.0',
    'license': 'LGPL-3',

    'depends': ['product', 'mail', 'purchase', 'sale'],

    'data': [
        'security/product_verification_security.xml',
        'views/product_template_views.xml',
    ],

    'post_init_hook': '_post_init_verify_existing_products',
    'installable': True,
    'auto_install': False,
    'application': False,
}
