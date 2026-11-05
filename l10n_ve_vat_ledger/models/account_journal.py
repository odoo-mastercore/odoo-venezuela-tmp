# -*- coding: utf-8 -*-
##############################################################################
# Author: Mastercore Sinapsys Global®
# Copyright: 2019-Present.
# License OPL-1 (Odoo Proprietary License v1.0)
# See https://www.odoo.com/documentation/master/legal/licenses.html
#
#
##############################################################################
from odoo import fields, models


class AccountJournal(models.Model):
    _inherit = 'account.journal'

    l10n_ve_vat_book_emission = fields.Selection(
        selection=[('manual', 'Forma libre'), ('digital', 'Factura digital')],
        string='Emision para el Libro IVA',
        required=True,
        default='manual',
    )
