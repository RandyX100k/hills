import base64
import contextlib
import io
import re
import unicodedata

from odoo import _, fields, models
from odoo.exceptions import UserError

try:
    import openpyxl
except ImportError:
    openpyxl = None

NAME_HEADERS = {"nombre en pantalla", "nombre", "name", "display name"}
COST_HEADERS = {"costo promedio", "costo", "cost", "average cost"}
SALE_PRICE_HEADERS = {
    "precio de venta", "precio venta", "precio", "sale price", "pvp", "list price",
}
QTY_HEADERS = {"cantidad a la mano", "cantidad", "quantity on hand", "cantidad disponible"}


class ProductInventoryImportWizard(models.TransientModel):
    _name = "product.inventory.import.wizard"
    _description = "Importar/actualizar productos e inventario desde Excel"

    file_data = fields.Binary(string="Archivo Excel", required=True)
    filename = fields.Char(string="Nombre del archivo")
    tax_id = fields.Many2one(
        "account.tax",
        string="ITBIS / Impuesto",
        domain=[("type_tax_use", "!=", "none")],
        help="Se aplica igual como impuesto de venta y de compra en los "
             "productos creados/actualizados.",
    )
    result = fields.Text(string="Resultado", readonly=True)

    def _get_workbook_lib(self):
        if not openpyxl:
            raise UserError(_("Falta instalar la librería 'openpyxl' en el entorno de Odoo."))
        return openpyxl

    def _normalize(self, value):
        if value in (None, False):
            return ""
        text = str(value).strip().lower()
        text = unicodedata.normalize("NFKD", text)
        text = "".join(ch for ch in text if not unicodedata.combining(ch))
        return re.sub(r"[^a-z0-9 ]+", " ", text).strip()

    def _to_float(self, value):
        if value in (None, False, ""):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value).strip().replace(",", "")
        try:
            return float(text)
        except ValueError:
            return None

    def _find_column(self, headers, candidates):
        for index, header in enumerate(headers):
            if header in candidates:
                return index
        return None

    def _get_default_location(self):
        warehouse = self.env["stock.warehouse"].search(
            [("company_id", "=", self.env.company.id)], limit=1
        )
        return warehouse.lot_stock_id if warehouse else self.env["stock.location"]

    def _set_quantity_on_hand(self, product, qty, location):
        """Ajusta la existencia de `product` en `location` a `qty`, igual que
        un conteo físico manual: setea inventory_quantity y aplica."""
        Quant = self.env["stock.quant"]
        quant = Quant.search([
            ("product_id", "=", product.id),
            ("location_id", "=", location.id),
            ("lot_id", "=", False),
            ("owner_id", "=", False),
            ("package_id", "=", False),
        ], limit=1)
        if not quant:
            quant = Quant.with_context(inventory_mode=True).create({
                "product_id": product.id,
                "location_id": location.id,
            })
        quant.inventory_quantity = qty
        quant.user_id = self.env.user.id
        quant.inventory_date = fields.Date.today()
        quant.action_apply_inventory()

    def _read_rows(self):
        """Devuelve (rows_de_datos, name_idx, cost_idx, sale_idx, qty_idx) ya
        validados, o lanza UserError si falta la columna de nombre."""
        if not self.file_data:
            raise UserError(_("Sube un archivo Excel primero."))
        workbook_lib = self._get_workbook_lib()

        workbook = workbook_lib.load_workbook(
            io.BytesIO(base64.b64decode(self.file_data)), data_only=True
        )
        sheet = workbook.active
        rows = list(sheet.iter_rows(values_only=True))
        if not rows:
            raise UserError(_("El archivo está vacío."))

        headers = [self._normalize(v) for v in rows[0]]
        name_idx = self._find_column(headers, NAME_HEADERS)
        cost_idx = self._find_column(headers, COST_HEADERS)
        sale_idx = self._find_column(headers, SALE_PRICE_HEADERS)
        qty_idx = self._find_column(headers, QTY_HEADERS)
        if name_idx is None:
            raise UserError(_(
                "No encontré la columna de nombre ('Nombre en pantalla') en el Excel."
            ))
        return rows[1:], name_idx, cost_idx, sale_idx, qty_idx

    def _ensure_storable_good(self, template):
        """Los productos deben ser 'Bienes' (type=consu, is_storable=True)
        para poder llevar inventario. Si el producto quedó mal configurado
        como Servicio (u otro tipo no almacenable), lo corrige aquí."""
        if template.type != "consu" or not template.is_storable:
            template.write({"type": "consu", "is_storable": True})

    def _process_rows(self, rows, name_idx, cost_idx, sale_idx, qty_idx):
        """Crea/actualiza productos y existencia fila por fila. Cada fila
        corre en su propio savepoint: si una falla (ej. costo mayor al
        precio), se reporta y se sigue con las demás sin perder el resto."""
        location = self._get_default_location()
        Template = self.env["product.template"]

        # Política de facturación siempre "Cantidades ordenadas", y si se
        # eligió un impuesto en el wizard, se aplica igual a compras y ventas.
        common_vals = {"invoice_policy": "order"}
        if self.tax_id:
            common_vals["taxes_id"] = [(6, 0, [self.tax_id.id])]
            common_vals["supplier_taxes_id"] = [(6, 0, [self.tax_id.id])]

        updated = created = 0
        problems = []
        for row_number, row in enumerate(rows, start=2):
            if not row or name_idx >= len(row) or not row[name_idx]:
                continue
            name = str(row[name_idx]).strip()
            cost = self._to_float(row[cost_idx]) if cost_idx is not None and cost_idx < len(row) else None
            sale_price = self._to_float(row[sale_idx]) if sale_idx is not None and sale_idx < len(row) else None
            qty = self._to_float(row[qty_idx]) if qty_idx is not None and qty_idx < len(row) else None

            try:
                with self.env.cr.savepoint():
                    template = Template.search([("name", "=", name)], limit=1)
                    if template:
                        self._ensure_storable_good(template)
                        if cost is not None:
                            template.standard_price = cost
                        if sale_price is not None:
                            template.list_price = sale_price
                        template.write(common_vals)
                        updated += 1
                    else:
                        template = Template.create({
                            "name": name,
                            "type": "consu",
                            "is_storable": True,
                            "standard_price": cost or 0.0,
                            "list_price": sale_price or 0.0,
                            "purchase_ok": True,
                            "sale_ok": True,
                            **common_vals,
                        })
                        created += 1
                    if qty is not None and location:
                        self._set_quantity_on_hand(
                            template.product_variant_id, qty, location
                        )
            except Exception as exc:  # noqa: BLE001 - se reporta y se sigue con las demás filas
                problems.append(_("Fila %(row)s (%(name)s): %(error)s") % {
                    "row": row_number, "name": name, "error": str(exc),
                })
        return updated, created, problems

    def _run(self, dry_run):
        self.ensure_one()
        rows, name_idx, cost_idx, sale_idx, qty_idx = self._read_rows()

        # En simulación, todo corre igual (incluyendo los ajustes de
        # inventario) pero queda envuelto en un savepoint que se revierte
        # siempre al salir del "with" -- así el resultado que se muestra es
        # real (mismas validaciones), pero nada queda guardado en la BD.
        with contextlib.ExitStack() as stack:
            if dry_run:
                stack.enter_context(contextlib.closing(self.env.cr.savepoint()))
            updated, created, problems = self._process_rows(
                rows, name_idx, cost_idx, sale_idx, qty_idx
            )

        lines = [
            _("*** SIMULACIÓN: nada de esto se guardó ***") if dry_run else _("Importación aplicada."),
            _("Productos que se actualizarían: %s") % updated if dry_run
                else _("Productos actualizados: %s") % updated,
            _("Productos que se crearían: %s") % created if dry_run
                else _("Productos creados: %s") % created,
        ]
        if problems:
            lines.append(_("Filas con problemas:"))
            lines.extend(problems[:50])
            if len(problems) > 50:
                lines.append(_("... y %s más.") % (len(problems) - 50))

        self.result = "\n".join(lines)
        return {
            "type": "ir.actions.act_window",
            "name": _("Importar productos"),
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    def action_simulate(self):
        return self._run(dry_run=True)

    def action_import(self):
        return self._run(dry_run=False)
