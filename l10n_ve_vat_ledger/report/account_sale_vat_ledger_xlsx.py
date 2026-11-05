# -*- coding: utf-8 -*-
##############################################################################
# Author: Mastercore Sinapsys Global®
# Copyright: 2019-Present.
# License OPL-1 (Odoo Proprietary License v1.0)
# See https://www.odoo.com/documentation/master/legal/licenses.html
#
#
##############################################################################
from odoo import _, models
from odoo.exceptions import UserError


class AccountSaleVatLedgerXlsx(models.AbstractModel):
    _inherit = 'report.l10n_ve_vat_ledger.account_vat_ledger_xlsx'

    def _get_sale_book_entries(self, ledger):
        invoices, withholdings = ledger._get_sale_book_source_data()
        layout = ledger.sale_book_format or 'documents'
        fiscal_moves = invoices.filtered(
            lambda move: move._get_l10n_ve_vat_book_emission() == 'fiscal'
        )
        if layout == 'documents' and fiscal_moves:
            raise UserError(_('Este periodo contiene documentos fiscales. Configure un formato con maquina fiscal.'))
        if layout == 'fiscal' and invoices.filtered(
            lambda move: move._get_l10n_ve_vat_book_emission() == 'digital'
        ):
            raise UserError(_('Este periodo contiene facturas digitales. Utilice el formato mixto.'))
        entries, covered_ids, warnings = [], set(), []
        if layout != 'documents':
            if not ledger._has_sale_book_fiscal_source():
                raise UserError(_('El formato fiscal requiere una integracion de reportes Z instalada.'))
            entries, covered_ids, warnings = ledger._get_sale_book_fiscal_entries(invoices)
            missing = fiscal_moves.filtered(lambda move: move.id not in covered_ids)
            if missing:
                raise UserError(_(
                    'No se encontraron reportes Z que cubran estos documentos: %s',
                    ', '.join(missing[:10].mapped('display_name')),
                ))
        labels = {'invoice': 'Factura', 'credit_note': 'Nota de credito', 'debit_note': 'Nota de debito'}
        for move in invoices:
            if move.id in covered_ids:
                continue
            emission = move._get_l10n_ve_vat_book_emission()
            kind = self._get_vat_book_document_kind(move)
            canceled = move.state == 'cancel'
            document = move._get_name_vat_ledger() or ''
            document_label = labels[kind]
            if emission == 'digital':
                document_label = 'Factura Digital' if kind == 'invoice' else document_label + ' - Factura Digital'
            values = {
                'date': move.invoice_date or move.date,
                'document_type': document_label,
                'document': document, 'control': move.l10n_ve_control_number or '',
                'partner': 'ANULADA' if canceled else (move.partner_id.name or ''),
                'vat': '%s-%s' % (
                    move.partner_id.l10n_latam_identification_type_id.l10n_ve_code or '',
                    move.partner_id.vat or '',
                ),
                'branch': move.company_id.name,
                'affected': '', 'total': 0.0 if canceled else move.amount_total_signed,
            }
            if kind == 'credit_note' and move.reversed_entry_id:
                values['affected'] = move._get_reverse_name_vat_ledger() or ''
            elif kind == 'debit_note' and move.debit_origin_id:
                values['affected'] = move._get_debit_name_vat_ledger() or ''
            values['manual_invoice'] = document if kind == 'invoice' else ''
            values['manual_control'] = values['control']
            values['manual_debit'] = document if kind == 'debit_note' else ''
            values['credit_document'] = document if kind == 'credit_note' else ''
            breakdown = self._get_move_vat_breakdown(move)
            values['exempt'] = 0.0 if canceled else breakdown['base_exempt']
            for rate in (16, 8, 15):
                for key in ('base_%s' % rate, 'iva_%s' % rate):
                    values[key] = 0.0 if canceled else breakdown[key]
                values['alic_%s' % rate] = '' if canceled else breakdown['alic_%s' % rate]
            entries.append({'values': values, 'emission': emission, 'kind': kind,
                            'company_id': move.company_id.id, 'id': move.id})
        for withholding in withholdings:
            related_moves = self._get_withholding_related_moves(withholding)
            if related_moves:
                selected = related_moves.filtered(lambda move: move.journal_id in ledger.journal_ids)
                if not selected:
                    continue
                if len(selected) != len(related_moves):
                    raise UserError(_(
                        'La retencion %s abarca diarios incluidos y excluidos. Seleccione todos sus diarios para no dividir su importe.',
                        withholding.name or withholding.id,
                    ))
            values = {
                'date': withholding.date or withholding.payment_id.date,
                'document_type': 'Retencion', 'partner': withholding.partner_id.name or '',
                'vat': '%s-%s' % (
                    withholding.partner_id.l10n_latam_identification_type_id.l10n_ve_code or '',
                    withholding.partner_id.vat or '',
                ),
                'branch': withholding.company_id.name,
                'voucher': withholding.name or '',
                'withholding_date': withholding.date or withholding.payment_id.date,
                'withheld': self._get_sale_withholding_signed_amount(withholding),
            }
            entries.append({'values': values, 'emission': '', 'kind': 'withholding',
                            'company_id': withholding.company_id.id, 'id': withholding.id})
        entries.sort(key=lambda entry: (
            entry['values']['date'], entry['company_id'],
            entry['values'].get('printer', ''), entry['id'], entry['kind'],
        ))
        return entries, warnings

    def _get_sale_book_columns(self, ledger):
        fiscal = ledger.sale_book_format in ('fiscal', 'mixed')
        if fiscal:
            columns = [
                ('date', 'Fecha del documento'), ('document_type', 'Tipo de documento'),
                ('vat', 'RIF'), ('partner', 'Nombre o razon social'),
                ('manual_invoice', 'Nro. factura forma libre / digital'),
                ('manual_control', 'Nro. control forma libre / digital'),
                ('manual_debit', 'Nro. nota de debito forma libre / digital'),
                ('z_number', 'Nro. reporte Z'), ('printer', 'Maquina fiscal'),
                ('invoice_from', 'Factura desde'), ('invoice_to', 'Factura hasta'),
                ('debit_document', 'Nro. nota de debito fiscal'),
                ('credit_document', 'Nro. nota de credito'), ('affected', 'Factura afectada'),
            ]
        else:
            columns = [
                ('operation', 'Nro. operacion'), ('date', 'Fecha del documento'),
                ('document_type', 'Tipo de documento'), ('document', 'Numero de documento'),
                ('control', 'Numero de control'), ('voucher', 'Numero comprobante de retencion IVA'),
                ('affected', 'Factura afectada'),
                ('partner', 'Nombre o razon social'), ('vat', 'RIF'),
            ]
        if ledger.sale_book_scope == 'consolidated':
            columns.append(('branch', 'Principal / sucursal'))
        columns.append(('total', 'Total Venta Incluyendo el IVA'))
        if fiscal:
            columns.append(('exempt', 'Ventas Internas No gravadas'))
        if not fiscal:
            columns.extend([
                ('third_exempt', 'Ventas no gravadas'), ('third_base', 'Base imponible'),
                ('third_rate', '% alicuota'), ('third_tax', 'Impuesto IVA'),
            ])
            columns.append(('exempt', 'Ventas Internas No gravadas'))
        for rate in (16, 8, 15):
            columns.extend([
                ('base_%s' % rate, 'Base imponible'),
                ('alic_%s' % rate, 'Alicuota %s%%' % rate),
                ('iva_%s' % rate, 'Impuesto IVA'),
            ])
        if ledger.sale_book_format == 'mixed':
            for rate in (16, 8, 15):
                columns.extend([
                    ('digital_base_%s' % rate, 'Base imponible'),
                    ('digital_alic_%s' % rate, 'Alicuota %s%%' % rate),
                    ('digital_iva_%s' % rate, 'Impuesto IVA'),
                ])
        if fiscal:
            columns.extend([
                ('withholding_date', 'Fecha comprobante de retencion IVA'),
                ('voucher', 'Numero comprobante de retencion IVA'),
            ])
        columns.append(('withheld', 'IVA retenido por el comprador'))
        return columns

    def _generate_sale_xlsx_report(self, workbook, ledger):
        ledger = ledger.with_context(ledger._get_sale_book_context())
        self = self.with_context(ledger.env.context)
        entries, warnings = self._get_sale_book_entries(ledger)
        columns = self._get_sale_book_columns(ledger)
        positions = {key: column for column, (key, label) in enumerate(columns)}
        sheet = workbook.add_worksheet(ledger.name[:31])
        header = workbook.add_format({
            'bold': True, 'border': 1, 'align': 'center', 'valign': 'vcenter',
            'fg_color': '#a64d79', 'font_color': 'white', 'text_wrap': True,
        })
        title = workbook.add_format({'bold': True, 'border': 1, 'text_wrap': True})
        text = workbook.add_format({'border': 1, 'text_wrap': True})
        centered_text = workbook.add_format({'border': 1, 'text_wrap': True, 'align': 'center'})
        number = workbook.add_format({'border': 1, 'num_format': '#,##0.00', 'align': 'center'})
        date_format = workbook.add_format({'border': 1, 'num_format': 'dd-mm-yyyy', 'align': 'center'})
        total_format = workbook.add_format({
            'border': 1, 'bold': True, 'num_format': '#,##0.00', 'fg_color': '#f0f0f0',
            'align': 'center',
        })
        total_label = workbook.add_format({
            'border': 1, 'bold': True, 'fg_color': '#f0f0f0', 'align': 'left',
        })
        company = ledger.company_id
        sheet.merge_range('A1:D1', company.name, title)
        sheet.merge_range('A2:D2', '%s-%s' % (
            company.partner_id.l10n_latam_identification_type_id.l10n_ve_code or '',
            company.vat or '',
        ), title)
        sheet.merge_range('A3:H3', ledger.name, title)
        sheet.set_row(4, 70)
        for column, (key, label) in enumerate(columns):
            sheet.set_column(column, column, 35 if key in ('partner', 'branch') else 23)
            sheet.write(4, column, label, header)
        if 'third_base' in positions:
            sheet.merge_range(3, positions['third_exempt'], 3, positions['third_tax'],
                              'Ventas por cuenta de terceros', header)
        sheet.merge_range(3, positions['exempt'] if ledger.sale_book_format == 'documents' else positions['base_16'],
                          3, positions['iva_15'],
                          'Contribuyente' if ledger.sale_book_format != 'mixed'
                          else 'Contribuyente - Maquina fiscal / forma libre', header)
        if 'digital_base_16' in positions:
            sheet.merge_range(3, positions['digital_base_16'], 3, positions['digital_iva_15'],
                              'Contribuyente - Factura Digital', header)

        numeric_keys = {'total', 'exempt', 'third_exempt', 'third_base', 'third_tax', 'withheld'}
        for prefix in ('', 'digital_'):
            for rate in (16, 8, 15):
                numeric_keys.update((prefix + 'base_%s' % rate, prefix + 'iva_%s' % rate))
        identifier_keys = {
            'operation', 'document', 'control', 'voucher', 'affected', 'vat',
            'manual_invoice', 'manual_control', 'manual_debit', 'z_number', 'printer',
            'invoice_from', 'invoice_to', 'debit_document', 'credit_document', 'third_rate',
        }
        totals = dict.fromkeys(numeric_keys, 0.0)
        summaries = {}
        for row, entry in enumerate(entries, start=5):
            values = dict(entry['values'], operation=row - 4)
            base = values.get('exempt', 0.0) + sum(values.get('base_%s' % rate, 0.0) for rate in (16, 8, 15))
            tax = sum(values.get('iva_%s' % rate, 0.0) for rate in (16, 8, 15))
            kind = entry['kind']
            previous = summaries.get(kind, (0.0, 0.0))
            summaries[kind] = (previous[0] + base, previous[1] + tax)
            if ledger.sale_book_format == 'mixed' and entry['emission'] == 'digital':
                for rate in (16, 8, 15):
                    for key in ('base_%s' % rate, 'iva_%s' % rate, 'alic_%s' % rate):
                        values['digital_' + key] = values.pop(key, '')
            for column, (key, label) in enumerate(columns):
                value = values.get(key, '')
                style = date_format if key in ('date', 'withholding_date') else (
                    number if key in numeric_keys else (
                        centered_text if key in identifier_keys or key.startswith(('alic_', 'digital_alic_')) else text
                    )
                )
                # Document numbers are strings so fiscal leading zeros survive.
                if isinstance(value, str) and value:
                    sheet.write_string(row, column, value, style)
                else:
                    sheet.write(row, column, value, style)
                if key in totals and isinstance(value, (int, float)):
                    totals[key] += value
        row = 5 + len(entries)
        for column, (key, label) in enumerate(columns):
            sheet.write(row, column, 'TOTAL' if key == 'partner' else totals.get(key, ''),
                        total_label if key == 'partner' else total_format)
        row += 4
        sheet.merge_range(row, 0, row, 3, 'RESUMEN GENERAL', header)
        for column, label in enumerate(('Base / exentos', 'Debito fiscal', 'IVA retenido'), start=4):
            sheet.write(row, column, label, header)
        summary_labels = (
            ('invoice', 'Facturas'), ('credit_note', 'Notas de credito'),
            ('debit_note', 'Notas de debito'), ('fiscal', 'Reportes Z (neto de NC/ND)'),
        )
        for kind, label in summary_labels:
            row += 1
            base, tax = summaries.get(kind, (0.0, 0.0))
            sheet.merge_range(row, 0, row, 3, label, title)
            sheet.write(row, 4, self._round_book_amount(base), number)
            sheet.write(row, 5, self._round_book_amount(tax), number)
        row += 1
        sheet.merge_range(row, 0, row, 3, 'TOTAL', total_label)
        sheet.write(row, 4, self._round_book_amount(sum(value[0] for value in summaries.values())), total_format)
        sheet.write(row, 5, self._round_book_amount(sum(value[1] for value in summaries.values())), total_format)
        sheet.write(row, 6, self._round_book_amount(totals['withheld']), total_format)
        for warning in dict.fromkeys(warnings):
            row += 2
            sheet.merge_range(row, 0, row, 7, warning, title)
