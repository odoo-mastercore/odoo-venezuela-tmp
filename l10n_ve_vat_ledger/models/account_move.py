# -*- coding: utf-8 -*-
##############################################################################
# Author: SINAPSYS GLOBAL SA || MASTERCORE SAS
# Copyleft: 2020-Present.
# License LGPL-3.0 or later (http: //www.gnu.org/licenses/lgpl.html).
#
#
###############################################################################
from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
from datetime import datetime
import logging
_logger = logging.getLogger(__name__)

class AccountMove(models.Model):
    _inherit = 'account.move'

    l10n_ve_vat_book_emission = fields.Selection(
        selection=[('manual', 'Forma libre'), ('digital', 'Factura digital')],
        string='Emision para el Libro IVA',
        compute='_compute_l10n_ve_vat_book_emission',
        store=True,
        readonly=False,
        copy=True,
        precompute=True,
        tracking=True,
    )

    @api.depends('journal_id', 'move_type')
    def _compute_l10n_ve_vat_book_emission(self):
        for move in self:
            move.l10n_ve_vat_book_emission = move._get_default_l10n_ve_vat_book_emission()

    def _get_default_l10n_ve_vat_book_emission(self):
        self.ensure_one()
        return self.journal_id.l10n_ve_vat_book_emission or 'manual'

    def _get_l10n_ve_vat_book_emission(self):
        self.ensure_one()
        return self.l10n_ve_vat_book_emission or 'manual'

    l10n_ve_purchase_type = fields.Selection(
        selection=[('national', 'Nacional'), ('import', 'Importación')],
        string='Tipo de compra',
        copy=True,
        tracking=True,
        help='Clasifica esta factura en el Libro IVA de compras, independientemente del proveedor.',
    )
    l10n_ve_show_vat_ledger_import_fields = fields.Boolean(
        compute="_compute_l10n_ve_show_vat_ledger_import_fields",
    )
    l10n_ve_importation_form_number = fields.Char(
        string="No planilla de importacion",
        copy=False,
    )
    l10n_ve_importation_form_date = fields.Date(
        string="Fecha de planilla de importacion",
        copy=False,
    )
    l10n_ve_importation_file_number = fields.Char(
        string="No de expediente de importacion",
        copy=False,
    )

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        # Default only new records; historical purchases require an explicit classification.
        if 'l10n_ve_purchase_type' in fields_list:
            values.setdefault('l10n_ve_purchase_type', 'national')
        return values

    @api.depends('move_type', 'l10n_ve_purchase_type')
    def _compute_l10n_ve_show_vat_ledger_import_fields(self):
        for move in self:
            move.l10n_ve_show_vat_ledger_import_fields = bool(
                move.move_type in ('in_invoice', 'in_refund')
                and move.l10n_ve_purchase_type == 'import'
            )

    def _get_name_vat_ledger(self):
        return self.name

    def _get_igtf_amount(self):
        return 0

    def _get_igtf_amount_purchase(self):
        return 0

    def _get_reverse_name_vat_ledger(self):
        return self.reversed_entry_id.name

    def _get_debit_name_vat_ledger(self):
        return self.debit_origin_id.name
