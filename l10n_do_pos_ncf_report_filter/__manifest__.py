# -*- coding: utf-8 -*-
{
    'name': "l10n_do_pos_ncf_report_filter",

    'summary': "Filtro por Numeración NCF en el Reporte de Ventas por Tipo de NCF del POS",

    'description': """
Pantalla OWL interactiva "Reporte por Tipo de NCF" para el Punto de Venta:
filtra en vivo por fecha, Punto de Venta y una o varias "Numeraciones NCF"
(si no se selecciona ninguna, se muestra el reporte general, incluyendo
Sin NCF), con una pestaña adicional de Rentabilidad (Total Vendido, Costo
y Rentabilidad Total) calculada sobre las mismas líneas de venta filtradas.
    """,

    'author': "RutiversoTech",
    'website': "https://www.rutiversotech.com",

    'category': 'Point of Sale',
    'version': '0.4',

    'depends': ['l10n_do_pos_role', 'l10n_do_ncf_sequence'],

    'data': [
        'security/ir.model.access.csv',
        'wizard/reporte_ncf_views.xml',
        'views/ncf_report_client_action.xml',
    ],

    'assets': {
        'web.assets_backend': [
            'l10n_do_pos_ncf_report_filter/static/src/**/*',
        ],
    },
}
