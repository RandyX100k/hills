# -*- coding: utf-8 -*-

from odoo import api, fields, models


class ReporteNcfWizard(models.TransientModel):
    _inherit = 'pos.reporte.ncf.wizard'

    ncf_sequence_ids = fields.Many2many(
        'l10n_do.ncf.sequence',
        string='Numeraciones NCF',
    )
    include_no_ncf = fields.Boolean(string='Incluir Sin NCF')

    def _get_orders(self):
        # Las devoluciones (Notas de Crédito del POS) se incluyen: antes se
        # sacaban con "not o.refunded_order_id", así que una NC B04/E34 no
        # aparecía nunca en el reporte, ni seleccionando su numeración.
        # _get_ncf_summary (l10n_do_pos_role) ya las agrupa aparte por el
        # tipo de comprobante de su factura, con monto negativo.
        orders = super()._get_orders()
        if self.ncf_sequence_ids or self.include_no_ncf:
            doc_type_ids = self.ncf_sequence_ids.l10n_latam_document_type_id.ids
            orders = orders.filtered(
                lambda o: (
                    (doc_type_ids and self._get_order_doc_type(o).id in doc_type_ids)
                    or (self.include_no_ncf and self._is_order_no_ncf(o))
                )
            )
        return orders

    def _get_order_doc_type(self, order):
        # El tipo real es el de la factura: en una NC del POS la orden se
        # queda con el tipo de la venta original (p. ej. B01) y es
        # _prepare_invoice_vals quien le pone el B04 a la factura. Filtrar
        # por el de la orden hacía que al elegir la numeración B04 la NC no
        # saliera (y que saliera al elegir B01).
        return (
            order.account_move.l10n_latam_document_type_id
            or order.l10n_latam_document_type_id
        )

    def _is_order_no_ncf(self, order):
        # Sin NCF = la factura no tiene tipo de comprobante (diario sin
        # NCF), incluida la NC de una venta que no tenía NCF. Una NC con
        # B04 nunca es "Sin NCF" aunque la orden traiga el
        # l10n_do_pos_no_ncf=True por defecto.
        if order.account_move:
            return not order.account_move.l10n_latam_document_type_id
        return order.l10n_do_pos_no_ncf

    def _serialize_groups(self, groups):
        return [{
            'name': g['name'],
            'doc_type_code': g['doc_type_code'],
            'no_ncf': g['no_ncf'],
            'count': g['count'],
            'total': g['total'],
            'tax': g['tax'],
            'base': g['total'] - g['tax'],
            'tax_breakdown': g['tax_breakdown'],
            'orders': [{
                'id': o.id,
                'name': o.name,
                # date_order está en UTC: mostrarlo tal cual adelantaba la hora
                # 4 horas respecto al POS y a Facturación (RD es UTC-4). Mismo
                # formato que usa el PDF de este reporte.
                'date_order': fields.Datetime.context_timestamp(
                    self, o.date_order
                ).strftime('%d/%m/%Y %H:%M'),
                'partner_name': o.partner_id.name or '',
                'ncf': o.l10n_do_fiscal_number or o.l10n_do_pos_no_ncf_number or '',
                'base': o.amount_total - o.amount_tax,
                'tax': o.amount_tax,
                'total': o.amount_total,
            } for o in g['orders']],
        } for g in groups]

    def _get_profitability(self, orders):
        sold = cost = 0.0
        for order in orders:
            for line in order.lines:
                sold += line.price_subtotal
                cost += line.product_id.standard_price * line.qty
        margin = sold - cost
        return {
            'total_sold': sold,
            'total_cost': cost,
            'total_margin': margin,
            'margin_pct': (margin / sold * 100) if sold else 0.0,
        }

    @api.model
    def get_dashboard_data(self, date_from, date_to, config_id=None, ncf_sequence_ids=None,
                            include_no_ncf=False):
        wizard = self.new({
            'date_from': date_from,
            'date_to': date_to,
            'config_id': config_id,
            'ncf_sequence_ids': [(6, 0, ncf_sequence_ids or [])],
            'include_no_ncf': include_no_ncf,
        })
        orders = wizard._get_orders()
        groups = wizard._get_ncf_summary(orders)
        return {
            'groups': self._serialize_groups(groups),
            'totals': {
                'count': sum(g['count'] for g in groups),
                'base': sum(g['total'] - g['tax'] for g in groups),
                'tax': sum(g['tax'] for g in groups),
                'total': sum(g['total'] for g in groups),
            },
            'profitability': self._get_profitability(orders),
        }

    @api.model
    def get_report_action_pdf(self, date_from, date_to, config_id=None, ncf_sequence_ids=None,
                               include_no_ncf=False):
        wizard = self.create({
            'date_from': date_from,
            'date_to': date_to,
            'config_id': config_id,
            'ncf_sequence_ids': [(6, 0, ncf_sequence_ids or [])],
            'include_no_ncf': include_no_ncf,
        })
        return wizard.action_print_report()
