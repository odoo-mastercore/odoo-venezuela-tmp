# -*- coding: utf-8 -*-
##############################################################################
# Author: SINAPSYS GLOBAL SA || MASTERCORE SAS
# Copyleft: 2026-Present.
# License LGPL-3.0 or later (http: //www.gnu.org/licenses/lgpl.html).
#
#
###############################################################################
from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    l10n_ve_sale_vat_book_format = fields.Selection(
        selection=[
            ('documents', 'Forma libre / factura digital'),
            ('fiscal', 'Maquina fiscal con contingencia manual'),
            ('mixed', 'Maquina fiscal y factura digital / forma libre'),
        ],
        string='Formato del Libro IVA de ventas',
        required=True,
        default='documents',
    )

    l10n_ve_vat_ledger_show_import_columns = fields.Boolean(
        string="Segmentar importaciones en Libro IVA de compras",
        default=False,
        help=(
            "Si esta activo, el Libro IVA de compras agrega las columnas de "
            "importacion (planilla, fecha y expediente) y segmenta en ellas "
            "las facturas de los proveedores marcados como compra de "
            "importacion."
        ),
    )
