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
        orders = super()._get_orders()
        orders = orders.filtered(lambda o: not o.refunded_order_id)
        if self.ncf_sequence_ids or self.include_no_ncf:
            doc_type_ids = self.ncf_sequence_ids.l10n_latam_document_type_id.ids
            orders = orders.filtered(
                lambda o: (
                    (doc_type_ids and o.l10n_latam_document_type_id.id in doc_type_ids)
                    or (self.include_no_ncf and o.l10n_do_pos_no_ncf)
                )
            )
        return orders

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
                'date_order': fields.Datetime.to_string(o.date_order),
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
