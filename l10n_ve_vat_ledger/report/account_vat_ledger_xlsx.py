##############################################################################
# For copyright and license notices, see __manifest__.py file in module root
# directory
##############################################################################
from re import search
from datetime import datetime, timedelta
from odoo import models, fields, api, _
from odoo.exceptions import UserError
import json
# import time
import logging

_logger = logging.getLogger(__name__)

class AccountVatLedgerXlsx(models.AbstractModel):
    _name = 'report.l10n_ve_vat_ledger.account_vat_ledger_xlsx'
    _inherit = 'report.report_xlsx.abstract'
    _description = "Xlsx Account VAT Ledger"



    def find_values(self, id, json_repr):
        results = []

        def _decode_dict(a_dict):
            try:
                results.append(a_dict[id])
            except KeyError:
                pass
            return a_dict
        # Return value ignored.
        json.loads(json_repr, object_hook=_decode_dict)
        return results

    def _get_vat_book_document_kind(self, move):
        if move.move_type in ("in_refund", "out_refund") and not move.debit_origin_id:
            return "credit_note"
        if move.debit_origin_id:
            return "debit_note"
        return "invoice"

    def _round_book_amount(self, amount):
        rounded = round(amount or 0.0, 2)
        return 0.0 if abs(rounded) < 0.005 else rounded

    def _get_sale_invoice_ledger_date(self, invoice):
        invoice_date = invoice.l10n_ve_invoice_date or invoice.invoice_date
        if isinstance(invoice_date, datetime):
            return invoice_date.date()
        return invoice_date

    def _get_move_vat_breakdown(self, move):
        """Return signed VAT buckets from posted tax lines.

        The VAT books must reflect the accounting move as posted. Using tax lines
        avoids recomputing taxes from invoice lines with a potentially different
        exchange rate or rounding path.
        """
        document_kind = self._get_vat_book_document_kind(move)
        sign = -1 if document_kind == "credit_note" else 1
        buckets = {
            16.0: {"base": 0.0, "tax": 0.0, "label": "16%"},
            8.0: {"base": 0.0, "tax": 0.0, "label": "8%"},
            15.0: {"base": 0.0, "tax": 0.0, "label": "15%"},
        }

        for tax_line in move.line_ids.filtered(lambda line: line.tax_line_id):
            rate = round(abs(float(tax_line.tax_line_id.amount or 0.0)), 2)
            if rate not in buckets:
                continue
            buckets[rate]["base"] += abs(tax_line.tax_base_amount or 0.0) * sign
            buckets[rate]["tax"] += abs(tax_line.balance or 0.0) * sign

        untaxed_total = abs(move.amount_untaxed_signed or 0.0) * sign
        taxable_base_total = sum(values["base"] for values in buckets.values())
        base_exempt = untaxed_total - taxable_base_total

        base_16 = self._round_book_amount(buckets[16.0]["base"])
        iva_16 = self._round_book_amount(buckets[16.0]["tax"])
        base_8 = self._round_book_amount(buckets[8.0]["base"])
        iva_8 = self._round_book_amount(buckets[8.0]["tax"])
        base_15 = self._round_book_amount(buckets[15.0]["base"])
        iva_15 = self._round_book_amount(buckets[15.0]["tax"])
        base_exempt = self._round_book_amount(base_exempt)

        return {
            "document_kind": document_kind,
            "sign": sign,
            "untaxed_total": self._round_book_amount(untaxed_total),
            "base_exempt": base_exempt,
            "base_16": base_16,
            "iva_16": iva_16,
            "alic_16": "16%" if base_16 else "",
            "base_8": base_8,
            "iva_8": iva_8,
            "alic_8": "8%" if base_8 else "",
            "base_15": base_15,
            "iva_15": iva_15,
            "alic_15": "15%" if base_15 else "",
        }

    def get_amount_base_amount(self, line, tax=False):
        # Convertir usando la API de currency para evitar búsquedas N+1 en res.currency.rate
        base_imponible = 0
        tax_amount = 0
        total = 0
        company_currency = line.move_id.company_currency_id
        if line.currency_id != company_currency:
            try:
                base_imponible = line.currency_id._convert(
                    line.price_subtotal,
                    company_currency,
                    line.move_id.company_id,
                    line.move_id.l10n_ve_invoice_date,
                )
            except Exception:
                # Fallback: usar el subtotal sin conversión
                base_imponible = line.price_subtotal
        else:
            base_imponible = line.price_subtotal
        tax_amount = round((base_imponible * (tax / 100)), 2) if tax else 0
        total = tax_amount + base_imponible
        return {
            'base_imponible': round(base_imponible, 2),
            'tax_amount': tax_amount,
            'total': total,
        }

    def _get_purchase_withholding_signed_amount(self, withholding):
        """Return withholding amount with sign for purchase VAT ledger.

        Business rule: if withholding is associated to a credit note,
        amount must be negative.
        """
        amount = abs(withholding.amount or 0.0)

        # Preferred source: withholding distribution lines (most reliable in runtime).
        try:
            withholding_data = withholding._get_withholding_lines()
        except Exception:
            withholding_data = {}
        if isinstance(withholding_data, dict):
            detail_lines = withholding_data.get("lines", [])
        elif isinstance(withholding_data, list):
            detail_lines = withholding_data
        else:
            detail_lines = []
        if any(line.get("move_type") in ("in_refund", "out_refund") for line in detail_lines):
            return -amount

        related_moves = self.env["account.move"]

        if "reconciled_invoice_ids" in withholding._fields:
            related_moves |= withholding.reconciled_invoice_ids
        if "invoice_ids" in withholding.payment_id._fields:
            related_moves |= withholding.payment_id.invoice_ids
        if "l10n_ve_move_line_taxes_ids" in withholding._fields:
            related_moves |= withholding.l10n_ve_move_line_taxes_ids.mapped("move_id")

        related_moves = related_moves.filtered(
            lambda move: move.move_type in ("in_invoice", "in_refund", "out_invoice", "out_refund")
        )
        if any(move.move_type in ("in_refund", "out_refund") for move in related_moves):
            return -amount
        return amount

    def _get_withholding_related_moves(self, withholding):
        """Return related invoices/credit notes linked to a withholding.

        Priority:
        1) VAT tax move lines linked to the withholding (`l10n_ve_move_line_taxes_ids`)
        2) Explicit M2M link invoice<->withholding (`account.move.l10n_ve_withholding_ids`)
        3) Payment invoices as compatibility fallback.
        """
        related_moves = self.env["account.move"]

        # Primary relation in l10n_ve_withholding for VAT flows.
        if "l10n_ve_move_line_taxes_ids" in withholding._fields:
            related_moves |= withholding.l10n_ve_move_line_taxes_ids.mapped("move_id")

        # Explicit linkage persisted when posting payments with withholdings.
        related_moves |= self.env["account.move"].search(
            [("l10n_ve_withholding_ids", "in", withholding.id)]
        )

        # Backward compatibility with older data models.
        if "reconciled_invoice_ids" in withholding._fields:
            related_moves |= withholding.reconciled_invoice_ids
        if "invoice_ids" in withholding.payment_id._fields:
            related_moves |= withholding.payment_id.invoice_ids

        return related_moves.filtered(
            lambda move: move.move_type in ("in_invoice", "in_refund", "out_invoice", "out_refund")
        )

    def _get_withholding_docs_text(self, withholding, preferred_field="name"):
        """Return a safe string for affected documents in reports."""
        moves = self._get_withholding_related_moves(withholding)
        if not moves:
            return ""

        values = [value for value in moves.mapped(preferred_field) if value]
        if not values:
            fallback_field = "ref" if preferred_field == "name" else "name"
            values = [value for value in moves.mapped(fallback_field) if value]
        if not values:
            return ""
        # Keep insertion order while removing duplicates.
        return ", ".join(dict.fromkeys(values))

    def _get_sale_withholding_signed_amount(self, withholding):
        """Return withholding amount with sign for sales VAT ledger.

        Business rule: if withholding is associated to a credit note,
        amount must be negative.
        """
        amount = (
            withholding.amount
            if withholding.currency_id.id == withholding.company_id.currency_id.id
            else withholding.amount_company_currency
        )
        amount = abs(amount or 0.0)

        # Preferred source: withholding distribution lines (most reliable in runtime).
        try:
            withholding_data = withholding._get_withholding_lines()
        except Exception:
            withholding_data = {}
        if isinstance(withholding_data, dict):
            detail_lines = withholding_data.get("lines", [])
        elif isinstance(withholding_data, list):
            detail_lines = withholding_data
        else:
            detail_lines = []
        if any(line.get("move_type") in ("in_refund", "out_refund") for line in detail_lines):
            return -amount

        related_moves = self.env["account.move"]

        if "reconciled_invoice_ids" in withholding._fields:
            related_moves |= withholding.reconciled_invoice_ids
        if "invoice_ids" in withholding.payment_id._fields:
            related_moves |= withholding.payment_id.invoice_ids
        if "l10n_ve_move_line_taxes_ids" in withholding._fields:
            related_moves |= withholding.l10n_ve_move_line_taxes_ids.mapped("move_id")

        related_moves = related_moves.filtered(
            lambda move: move.move_type in ("in_invoice", "in_refund", "out_invoice", "out_refund")
        )
        if any(move.move_type in ("in_refund", "out_refund") for move in related_moves):
            return -amount
        return amount

    def _get_report_withholdings(self, ledger):
        """Exclude canceled withholdings, including stale stored relations."""
        return ledger.withholding_ids.filtered(
            lambda withholding: withholding.state != 'cancel'
        )

    def _is_import_purchase_move(self, move):
        return move.l10n_ve_purchase_type == 'import'

    def _get_purchase_book_columns(self):
        return [
            ('operation', 'Nro Oper.'),
            ('date', 'Fecha de la Factura o Documento'),
            ('document_type', 'Tipo de Documento'),
            ('document', 'Número de Documento'),
            ('control', 'Número de Control'),
            ('voucher', 'Número de Comprobante'),
            ('affected_document', 'Número Factura Afectada'),
            ('partner', 'Nombre o Razón Social'),
            ('vat', 'RIF'),
            ('total', 'Total Compras Bs. Incluyendo IVA.'),
            ('internal_exempt', 'Compras sin Derecho a Crédito I.V.A.'),
            ('import_exempt', 'Compras Exentas o Exoneradas (No Gravadas)'),
            ('import_base', 'Base Imponible'),
            ('import_rate', '% Alic.'),
            ('import_tax', 'Imp. I.V.A.'),
            ('base_16', 'Base Imponible'),
            ('alic_16', 'Alícuota 16%'),
            ('iva_16', 'Imp. I.V.A.'),
            ('base_8', 'Base Imponible'),
            ('alic_8', 'Alícuota 8%'),
            ('iva_8', 'Imp. I.V.A.'),
            ('base_15', 'Base Imponible'),
            ('alic_15', 'Alícuota 15%'),
            ('iva_15', 'Imp. I.V.A.'),
            ('withheld', 'I.V.A. Retenido por el comprador'),
        ]

    def _get_purchase_book_lines(self, ledger):
        unclassified = ledger.invoice_ids.filtered(
            lambda move: not move.l10n_ve_purchase_type
        )
        if unclassified:
            raise UserError(_(
                'Clasifique como Nacional o Importación las siguientes compras '
                'antes de imprimir el libro: %s',
                ', '.join('%s (ID %s)' % (move.display_name, move.id)
                          for move in unclassified[:10]),
            ))
        missing_forms = ledger.invoice_ids.filtered(
            lambda move: self._is_import_purchase_move(move)
            and not (move.l10n_ve_importation_form_number or '').strip()
        )
        if missing_forms:
            raise UserError(_(
                'Registre el número de planilla aduanera de las siguientes '
                'importaciones antes de imprimir el libro: %s',
                ', '.join('%s (ID %s)' % (move.display_name, move.id)
                          for move in missing_forms[:10]),
            ))

        lines = []
        labels = {
            'invoice': 'Factura',
            'credit_note': 'Nota de Crédito',
            'debit_note': 'Nota de Débito',
        }
        for move in ledger.invoice_ids:
            breakdown = self._get_move_vat_breakdown(move)
            is_import = self._is_import_purchase_move(move)
            origin = move.debit_origin_id or move.reversed_entry_id
            partner = move.partner_id
            values = {
                'date': move.invoice_date or move.date,
                'document_type': labels[breakdown['document_kind']],
                'document': (move.l10n_ve_importation_form_number or '').strip()
                if is_import else (move.ref or ''),
                'control': move.l10n_ve_control_number or '',
                'affected_document': origin.ref or '' if origin else '',
                'partner': 'Importación' if is_import else (partner.name or ''),
                'vat': '%s-%s' % (
                    partner.l10n_latam_identification_type_id.l10n_ve_code or '',
                    partner.vat or '',
                ),
                'total': -move.amount_total_signed,
                'internal_exempt': 0.0 if is_import else breakdown['base_exempt'],
                'import_exempt': breakdown['base_exempt'] if is_import else 0.0,
                'import_base': 0.0,
                'import_rate': '',
                'import_tax': 0.0,
                'withheld': 0.0,
            }
            rates = []
            for rate in (16, 8, 15):
                base_key, tax_key, rate_key = (
                    'base_%s' % rate, 'iva_%s' % rate, 'alic_%s' % rate
                )
                values[base_key] = 0.0 if is_import else breakdown[base_key]
                values[tax_key] = 0.0 if is_import else breakdown[tax_key]
                values[rate_key] = '' if is_import else breakdown[rate_key]
                if is_import:
                    values['import_base'] += breakdown[base_key]
                    values['import_tax'] += breakdown[tax_key]
                    if breakdown[base_key] or breakdown[tax_key]:
                        rates.append('%s%%' % rate)
            values['import_rate'] = rates[0] if len(rates) == 1 else ('Varias' if rates else '')
            lines.append({
                'values': values, 'id': move.id, 'kind': 'invoice',
                'is_import': is_import, 'breakdown': breakdown,
            })

        for retention in self._get_report_withholdings(ledger):
            partner = retention.partner_id
            lines.append({
                'values': {
                    'date': retention.date or retention.payment_id.date,
                    'document_type': 'Retención',
                    'voucher': retention.name or '',
                    'partner': partner.name or '',
                    'vat': '%s-%s' % (
                        partner.l10n_latam_identification_type_id.l10n_ve_code or '',
                        partner.vat or '',
                    ),
                    'withheld': self._get_purchase_withholding_signed_amount(retention),
                },
                'id': retention.id, 'kind': 'withholding',
            })
        return sorted(lines, key=lambda line: (
            line['values']['date'], line['kind'], line['id'],
        ))

    def _generate_purchase_xlsx_report(self, workbook, ledger):
        columns = self._get_purchase_book_columns()
        positions = {key: index for index, (key, label) in enumerate(columns)}
        lines = self._get_purchase_book_lines(ledger)
        sheet = workbook.add_worksheet(ledger.name[:31])
        header = workbook.add_format({
            'bold': True, 'border': 1, 'align': 'center', 'valign': 'vcenter',
            'fg_color': '#a64d79', 'font_color': 'white', 'text_wrap': True,
        })
        title = workbook.add_format({'bold': True, 'border': 1})
        text = workbook.add_format({'border': 1, 'align': 'center'})
        number = workbook.add_format({
            'border': 1, 'align': 'center', 'num_format': '#,##0.00',
        })
        date = workbook.add_format({
            'border': 1, 'align': 'center', 'num_format': 'dd-mm-yyyy',
        })
        total_format = workbook.add_format({
            'border': 1, 'bold': True, 'fg_color': '#f0f0f0',
            'num_format': '#,##0.00',
        })
        numeric_keys = {
            'total', 'internal_exempt', 'import_exempt', 'import_base',
            'import_tax', 'base_16', 'iva_16', 'base_8', 'iva_8',
            'base_15', 'iva_15', 'withheld',
        }
        for key, column in positions.items():
            width = 9 if key == 'operation' else 20
            if key == 'partner':
                width = 38
            elif key in numeric_keys:
                width = 24
            sheet.set_column(column, column, width)
        company = ledger.company_id
        sheet.merge_range('A1:D1', company.name, title)
        sheet.merge_range('A2:D2', '%s-%s' % (
            company.l10n_latam_identification_type_id.l10n_ve_code or '',
            company.vat or '',
        ), title)
        sheet.merge_range('A3:G3', ', '.join(filter(None, (
            company.street, company.city, company.state_id.name,
        ))), title)
        sheet.merge_range('A4:D4', ledger.name, title)
        sheet.merge_range(3, positions['import_exempt'], 3,
                          positions['import_tax'], 'Importaciones', header)
        sheet.merge_range(3, positions['base_16'], 3,
                          positions['iva_15'], 'Compras Internas', header)
        sheet.set_row(4, 65)
        for column, (key, label) in enumerate(columns):
            sheet.write(4, column, label, header)

        totals = dict.fromkeys(numeric_keys, 0.0)
        for row, entry in enumerate(lines, start=5):
            values = entry['values']
            values['operation'] = row - 4
            for column, (key, label) in enumerate(columns):
                value = values.get(key, '')
                style = date if key == 'date' else (number if key in numeric_keys else text)
                sheet.write(row, column, value, style)
                if key in numeric_keys and isinstance(value, (int, float)):
                    totals[key] += value

        total_row = 5 + len(lines)
        for column, (key, label) in enumerate(columns):
            value = 'TOTAL' if key == 'partner' else totals.get(key, '')
            sheet.write(total_row, column, value, total_format)
        self._write_purchase_book_summary(
            sheet, total_row + 5, positions, lines, totals,
            header, title, number, total_format,
        )

    def _write_purchase_book_summary(
        self, sheet, row, positions, lines, totals, header, title, number, total_format
    ):
        label_column = positions['partner']
        base_column = positions['import_exempt']
        tax_column = base_column + 1
        withheld_column = base_column + 2
        third_party_column = base_column + 3
        sheet.merge_range(row, label_column, row, base_column - 1,
                          'RESUMEN GENERAL', header)
        for column, label in (
            (base_column, 'Base Imponible'),
            (tax_column, 'Crédito fiscal'),
            (withheld_column, 'IVA retenido por el comprador'),
            (third_party_column, 'IVA retenido a terceros'),
        ):
            sheet.write(row, column, label, header)

        # Separate document kinds here; detail totals already include NC/ND once.
        amounts = {}
        for entry in lines:
            if entry['kind'] != 'invoice':
                continue
            scope = 'import' if entry['is_import'] else entry['breakdown']['document_kind']
            breakdown = entry['breakdown']
            for rate in ('exempt', 16, 8, 15):
                base_key = 'base_exempt' if rate == 'exempt' else 'base_%s' % rate
                tax = 0.0 if rate == 'exempt' else breakdown['iva_%s' % rate]
                key = (scope, rate)
                base_total, tax_total = amounts.get(key, (0.0, 0.0))
                amounts[key] = (base_total + breakdown[base_key], tax_total + tax)

        summary = [
            ('Total Compras Internas NO Gravadas', amounts.get(('invoice', 'exempt'), (0, 0))),
            ('Total Notas de Crédito NO Gravadas', amounts.get(('credit_note', 'exempt'), (0, 0))),
            ('Total Notas de Débito NO Gravadas', amounts.get(('debit_note', 'exempt'), (0, 0))),
            ('Total Importaciones Exentas o Exoneradas', amounts.get(('import', 'exempt'), (0, 0))),
            ('Total Compras de Importación Gravadas', tuple(
                sum(amounts.get(('import', rate), (0, 0))[index] for rate in (16, 8, 15))
                for index in (0, 1)
            )),
        ]
        for kind, label in (
            ('invoice', 'Total Compras Internas'),
            ('credit_note', 'Total Notas de Crédito aplicadas en Compras'),
            ('debit_note', 'Total Notas de Débito aplicadas en Compras'),
        ):
            for rate in (16, 8, 15):
                summary.append(('%s %s%%' % (label, rate), amounts.get((kind, rate), (0, 0))))

        base_total = sum(totals[key] for key in (
            'internal_exempt', 'import_exempt', 'import_base', 'base_16', 'base_8', 'base_15',
        ))
        tax_total = sum(totals[key] for key in ('import_tax', 'iva_16', 'iva_8', 'iva_15'))
        summary.append(('Total', (base_total, tax_total)))
        for offset, (label, (base, tax)) in enumerate(summary, start=1):
            last_row = offset == len(summary)
            style = total_format if last_row else number
            sheet.merge_range(row + offset, label_column, row + offset,
                              base_column - 1, label, total_format if last_row else title)
            sheet.write(row + offset, base_column, self._round_book_amount(base), style)
            sheet.write(row + offset, tax_column, self._round_book_amount(tax), style)
            sheet.write(row + offset, withheld_column, totals['withheld'] if last_row else 0, style)
            sheet.write(row + offset, third_party_column, 0, style)

    def generate_xlsx_report(self, workbook, data, account_vat):
        for ledger in account_vat:
            if ledger.type == 'purchase':
                self._generate_purchase_xlsx_report(workbook, ledger)
            else:
                self._generate_sale_xlsx_report(workbook, ledger)
