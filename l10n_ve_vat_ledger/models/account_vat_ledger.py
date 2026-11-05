# -*- coding: utf-8 -*-
##############################################################################
# Author: SINAPSYS GLOBAL SA || MASTERCORE SAS
# Copyleft: 2020-Present.
# License LGPL-3.0 or later (http: //www.gnu.org/licenses/lgpl.html).
#
#
###############################################################################
from odoo import models, fields, api, _, Command
from odoo.exceptions import AccessError, UserError
from odoo.osv import expression
from odoo.tools import format_date
import logging

_logger = logging.getLogger(__name__)



class AccountVatLedger(models.Model):

    _name = "account.vat.ledger"
    _description = "Account VAT Ledger"
    _inherit = ['mail.thread']
    _order = 'date_from desc'

    _sql_constraints = [
        ('date_range_check', 'CHECK (date_from <= date_to)', _('La fecha de inicio debe ser anterior a la fecha de fin.')),
    ]

    company_id = fields.Many2one(
        'res.company',
        string='Company',
        required=True,
        readonly=True,
        default=lambda self: self.env[
            'res.company']._company_default_get('account.vat.ledger')
    )
    type = fields.Selection(
        [('sale', 'Sale'), ('purchase', 'Purchase')],
        "Type",
        required=True
    )
    date_from = fields.Date(
        string='Start Date',
        required=True,
    )
    date_to = fields.Date(
        string='End Date',
        required=True,
    )
    journal_ids = fields.Many2many(
        'account.journal', 'account_vat_ledger_journal_rel',
        'vat_ledger_id', 'journal_id',
        string='Journals',
        required=True,
    )
    state = fields.Selection(
        [('draft', 'Draft'), ('presented', 'Presented'), ('cancel', 'Cancel')],
        'State',
        required=True,
        default='draft'
    )
    note = fields.Html(
        "Notes"
    )

    # Computed fields
    name = fields.Char(
        'Title',
        compute='_compute_name'
    )
    reference = fields.Char(
        'Reference',
    )
    invoice_ids = fields.Many2many(
        'account.move',
        string="Invoices",
        compute="_compute_invoices",
        store=True
    )
    withholding_ids = fields.Many2many(
        'l10n_ve.payment.withholding',
        string="Withholdings",
        compute="_compute_invoices",
        store=True
    )

    sale_book_format = fields.Selection(
        selection=[
            ('documents', 'Forma libre / factura digital'),
            ('fiscal', 'Maquina fiscal con contingencia manual'),
            ('mixed', 'Maquina fiscal y factura digital / forma libre'),
        ],
        string='Formato de ventas',
        compute='_compute_sale_book_format', store=True, readonly=False, copy=True,
    )
    sale_book_scope = fields.Selection(
        [('own', 'Movimientos propios'), ('consolidated', 'Consolidado')],
        string='Alcance', default='own', required=True,
    )
    sale_book_can_consolidate = fields.Boolean(compute='_compute_sale_book_companies')
    sale_book_company_ids = fields.Many2many(
        'res.company', compute='_compute_sale_book_companies', string='Empresas del libro',
    )

    @api.depends('company_id', 'type')
    def _compute_sale_book_format(self):
        for ledger in self:
            ledger.sale_book_format = ledger.company_id.l10n_ve_sale_vat_book_format or 'documents'

    def _get_sale_book_companies(self):
        self.ensure_one()
        company = self.company_id
        if not company:
            return company
        if self.sale_book_scope == 'consolidated':
            if company.parent_id:
                raise UserError(_('El consolidado solo puede generarse desde la empresa principal.'))
            # Read hierarchy metadata only; financial records never use sudo.
            companies = self.env['res.company'].sudo().with_context(active_test=False).search([
                ('id', 'child_of', company.id),
            ])
        else:
            companies = company
        if not self.env.su and set(companies.ids) - set(self.env.user._get_company_ids()):
            raise AccessError(_('Necesita acceso a todas las sucursales para generar este libro completo.'))
        def tax_identity(item):
            return (
                item.partner_id.l10n_latam_identification_type_id.l10n_ve_code,
                ''.join(char for char in (item.vat or '') if char.isdigit()),
            )
        if self.sale_book_scope == 'consolidated' and any(
            not tax_identity(company)[1] or tax_identity(item) != tax_identity(company)
            for item in companies
        ):
            raise UserError(_('El consolidado requiere que la principal y sus sucursales compartan el RIF.'))
        return self.env['res.company'].browse(companies.ids)

    @api.depends('company_id', 'company_id.parent_id', 'company_id.child_ids', 'sale_book_scope', 'type')
    def _compute_sale_book_companies(self):
        for ledger in self:
            ledger.sale_book_can_consolidate = bool(
                ledger.company_id.with_context(active_test=False).child_ids
                and not ledger.company_id.parent_id
            )
            ledger.sale_book_company_ids = (
                ledger._get_sale_book_companies() if ledger.type == 'sale' else ledger.company_id
            )

    def _get_sale_book_context(self):
        companies = self._get_sale_book_companies()
        return dict(self.env.context, allowed_company_ids=companies.ids)

    def _get_sale_book_source_data(self):
        self.ensure_one()
        scoped = self.with_context(self._get_sale_book_context())
        company_ids = scoped._get_sale_book_companies().ids
        invoices = scoped.env['account.move'].search([
            ('company_id', 'in', company_ids), ('journal_id', 'in', scoped.journal_ids.ids),
            ('move_type', 'in', ['out_invoice', 'out_refund']),
            ('state', 'in', ['posted', 'cancel']), ('name', '!=', False),
            ('invoice_date', '>=', scoped.date_from), ('invoice_date', '<=', scoped.date_to),
        ], order='invoice_date, company_id, id')
        period = expression.OR([
            [('date', '>=', scoped.date_from), ('date', '<=', scoped.date_to)],
            [('date', '=', False), ('payment_id.date', '>=', scoped.date_from),
             ('payment_id.date', '<=', scoped.date_to)],
        ])
        withholdings = scoped.env['l10n_ve.payment.withholding'].search(expression.AND([
            [('company_id', 'in', company_ids), ('state', '!=', 'cancel'),
             ('payment_id.state', 'in', ['in_process', 'paid']),
             ('tax_id.l10n_ve_withholding_payment_type', '=', 'customer'),
             ('tax_id.l10n_ve_withholding_ingoing_type', '=', 'iva')], period,
        ]), order='date, id')
        return invoices, withholdings

    def _has_sale_book_fiscal_source(self):
        return False

    def _get_sale_book_fiscal_entries(self, invoices):
        """Optional integrations return fiscal rows and the covered invoice IDs."""
        return [], set(), []

    @api.depends('journal_ids', 'date_from', 'date_to', 'company_id', 'type', 'sale_book_scope')
    def _compute_invoices(self):
        for rec in self:
            if rec.type == 'sale':
                if rec.company_id and rec.date_from and rec.date_to:
                    rec.invoice_ids, rec.withholding_ids = rec._get_sale_book_source_data()
                else:
                    rec.invoice_ids = False
                    rec.withholding_ids = False
                continue
            company_ids = [rec.company_id.id]
            if rec.company_id.child_ids:
                company_ids += rec.company_id.child_ids.ids
            invoices_domain = [
                ('state', 'not in', ['draft']),
                ('journal_id', 'in', rec.journal_ids.ids),
                ('company_id', 'in', company_ids),
            ]
            withholdings_domain = [
                ('payment_id.state', 'in', ['in_process', 'paid']),
                ('company_id', 'in', company_ids),
                ('state', '!=', 'cancel'),
            ]
            if rec.type == 'purchase':
                invoices_domain += [
                    ('move_type', 'in', ['in_invoice', 'in_refund']),
                    ('date', '>=', rec.date_from),
                    ('date', '<=', rec.date_to),
                    ('state', '!=', 'cancel'),
                ]
                # Se filtra por las caracteristicas del impuesto, igual que la
                # rama de ventas de arriba, y no por un xmlid concreto.
                # account.<id_compania>_tax_retencion_iva lo creaba la plantilla
                # contable de 15 al instanciarla; en 18 ese registro puede no
                # existir, y env.ref sin raise_if_not_found=False dentro de un
                # compute no devuelve un vacio: revienta la carga entera del
                # registro.
                withholdings_domain += [
                    ('tax_id.l10n_ve_withholding_payment_type', '=', 'supplier'),
                    ('tax_id.l10n_ve_withholding_ingoing_type', '=', 'iva'),
                    ('date', '>=', rec.date_from),
                    ('date', '<=', rec.date_to),
                ]
            rec.invoice_ids = rec.env['account.move'].search(
                invoices_domain,
                order='invoice_date desc, l10n_ve_control_number desc'
            )
            rec.withholding_ids = rec.env['l10n_ve.payment.withholding'].search(
                withholdings_domain,
                order='name desc'
            )
            
    @api.depends('type', 'reference')
    def _compute_name(self):
        for rec in self:
            if rec.type == 'sale':
                ledger_type = _('Ventas')
            elif rec.type == 'purchase':
                ledger_type = _('Compras')
            # Usar format_date para aplicar localización
            if rec.date_from and rec.date_to:
                name = _("Libro IVA ({0})  {1} - {2}").format(
                    ledger_type,
                    format_date(self.env, rec.date_from),
                    format_date(self.env, rec.date_to),
                )
            else:
                name = _("Libro IVA (%s)") % (ledger_type,)
            rec.name = name

 

    @api.onchange('company_id', 'type', 'sale_book_scope')
    def change_company(self):
        self.ensure_one()
        if self.type == 'sale':
            if not self.sale_book_can_consolidate:
                self.sale_book_scope = 'own'
            scoped = self.with_context(self._get_sale_book_context())
            self.journal_ids = [Command.set(scoped.env['account.journal'].with_context(
                active_test=False,
            ).search([
                ('type', '=', 'sale'), ('company_id', 'in', self._get_sale_book_companies().ids),
            ]).ids)]
            return
        domain = []
        if self.type == 'sale':
            domain.append(('type', '=', 'sale'))
        elif self.type == 'purchase':
            domain.append(('type', '=', 'purchase'))
        if self.company_id.child_ids:
            domain.extend([
                '|',
                ('company_id', 'in', self.company_id.child_ids.ids),
                ('company_id', '=', self.company_id.id)
            ])
        else:
            domain.append(('company_id', '=', self.company_id.id))
        journals = self.env['account.journal'].search(domain)
        self.journal_ids = [(6, 0, journals.ids)]

    def action_present(self):
        for ledger in self.filtered(lambda item: item.type == 'sale'):
            if not ledger.sale_book_format:
                ledger.sale_book_format = ledger.company_id.l10n_ve_sale_vat_book_format
            if ledger.sale_book_format != 'documents' and not ledger._has_sale_book_fiscal_source():
                raise UserError(_('El formato fiscal requiere una integracion de reportes Z instalada.'))
            ledger._get_sale_book_companies()
        self.write({'state': 'presented'})

    def write(self, values):
        if {'sale_book_format', 'sale_book_scope'} & values.keys():
            if any(ledger.type == 'sale' and ledger.state != 'draft' for ledger in self):
                raise UserError(_('Restablezca el libro a borrador antes de cambiar su formato o alcance.'))
        return super().write(values)

    def action_cancel(self):
        self.write({'state': 'cancel'})

    def action_to_draft(self):
        self.write({'state': 'draft'})

    def action_print(self):
        self.ensure_one()
        model_name = \
            "l10n_ve_vat_ledger.action_account_vat_ledger_report_xlsx"
        report = self.env.ref(model_name)
        return report.report_action(self, data={'report_file': self.display_name})
