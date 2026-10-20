"""Addresses of the screen, as this version of Odoo writes them."""

from odoo import release


def record_url(model, record_id):
    """:return: the address that opens a record in its form: ``/odoo/<model>/<id>`` on 18 and 19,
    the hash address on 17, which has no ``/odoo`` route"""
    if release.version_info[0] >= 18:
        return f"/odoo/{model}/{record_id}"
    return f"/web#model={model}&id={record_id}&view_type=form"
