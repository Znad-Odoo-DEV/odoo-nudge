"""What a Nudge agent picks in the forms of Nudge (the records a rule counts, the field of a
policy): the kinds of records and the fields they may read, looked up in sudo.

Stock Odoo gives employees no right on ir.model and ir.model.fields: their rows hold the record
count of every table and the code of custom fields. Nudge gives none either, and answers only
these searches, on the names and types of what the person may read.
"""
from odoo import _, api, models
from odoo.exceptions import AccessError
from odoo.fields import Domain

from odoo.addons.nudge.tools.records import is_closed

# The columns a search of a Nudge agent may filter on: names and types, never a path
MODEL_COLUMNS = frozenset(("id", "name", "model", "transient", "display_name"))
FIELD_COLUMNS = frozenset((
    "id", "name", "field_description", "model", "model_id", "ttype", "store", "relation", "display_name",
))
# What a list of "Search More..." shows them
MODEL_SHOWN = frozenset(("id", "name", "model", "display_name"))
FIELD_SHOWN = frozenset(("id", "name", "field_description", "model", "model_id", "ttype", "display_name"))
# Comparisons only: "any" would carry a search of its own on what the column points to
OPERATORS = frozenset((
    "=", "!=", "in", "not in", "like", "not like", "ilike", "not ilike", "=like", "=ilike", "<", ">", "<=", ">=",
))


def picked_in_sudo(records):
    """:return: whether this search is one of a Nudge agent with no right on the model"""
    env = records.env
    return (not env.su and env.user.has_group("nudge.group_nudge_agent")
            and not env["ir.model.access"].check(records._name, "read", raise_exception=False))


def within(records, domain, columns):
    """:return: ``domain`` as a Domain when it filters on ``columns`` only, else refused as Odoo does"""
    domain = Domain(domain or Domain.TRUE)
    if all(condition.field_expr in columns and condition.operator in OPERATORS
           for condition in domain.iter_conditions()):
        return domain
    raise AccessError(_("You are not allowed to access '%s' records.", records._description))


class IrModel(models.Model):
    _inherit = "ir.model"

    @api.model
    def _nudge_readable(self):
        """:return: in sudo, the models the person may read: not transient nor abstract, not
        kept closed by Nudge"""
        env = self.env
        found = self.sudo().search([("transient", "=", False)])
        return found.filtered(lambda line: line.model in env and not env[line.model]._abstract
                              and not is_closed(env, line.model) and env[line.model]._nudge_has_access("read"))

    def _nudge_domain(self, domain):
        return within(self, domain, MODEL_COLUMNS) & Domain("id", "in", self._nudge_readable().ids)

    @api.model
    @api.readonly
    def name_search(self, name="", domain=None, operator="ilike", limit=100):
        if picked_in_sudo(self):
            return self.sudo().name_search(name, self._nudge_domain(domain), operator, limit)
        return super().name_search(name, domain, operator, limit)

    @api.model
    @api.readonly
    def web_name_search(self, name, specification, domain=None, operator="ilike", limit=100):
        if picked_in_sudo(self):
            return self.sudo().web_name_search(name, {"display_name": {}}, self._nudge_domain(domain), operator, limit)
        return super().web_name_search(name, specification, domain, operator, limit)

    @api.model
    @api.readonly
    def web_search_read(self, domain, specification, offset=0, limit=None, order=None, count_limit=None):
        if picked_in_sudo(self):
            shown = {key: value for key, value in specification.items() if key in MODEL_SHOWN}
            return self.sudo().web_search_read(self._nudge_domain(domain), shown, offset, limit, order, count_limit)
        return super().web_search_read(domain, specification, offset, limit, order, count_limit)


class IrModelFields(models.Model):
    _inherit = "ir.model.fields"

    def _nudge_domain(self, domain):
        return within(self, domain, FIELD_COLUMNS) & Domain("model_id", "in", self.env["ir.model"]._nudge_readable().ids)

    def _nudge_readable(self, ids):
        """:return: of the fields ``ids`` (in sudo), those the person may read: the groups of a field"""
        env = self.env
        return {field.id for field in self.sudo().browse(ids)
                if field.name in env[field.model]._fields
                and env[field.model]._has_field_access(env[field.model]._fields[field.name], "read")}

    @api.model
    @api.readonly
    def name_search(self, name="", domain=None, operator="ilike", limit=100):
        if picked_in_sudo(self):
            found = self.sudo().name_search(name, self._nudge_domain(domain), operator, limit)
            readable = self._nudge_readable([pair[0] for pair in found])
            return [pair for pair in found if pair[0] in readable]
        return super().name_search(name, domain, operator, limit)

    @api.model
    @api.readonly
    def web_name_search(self, name, specification, domain=None, operator="ilike", limit=100):
        if picked_in_sudo(self):
            found = self.sudo().web_name_search(name, {"display_name": {}}, self._nudge_domain(domain), operator, limit)
            readable = self._nudge_readable([line["id"] for line in found])
            return [line for line in found if line["id"] in readable]
        return super().web_name_search(name, specification, domain, operator, limit)

    @api.model
    @api.readonly
    def web_search_read(self, domain, specification, offset=0, limit=None, order=None, count_limit=None):
        if picked_in_sudo(self):
            shown = {key: value for key, value in specification.items() if key in FIELD_SHOWN}
            result = self.sudo().web_search_read(self._nudge_domain(domain), shown, offset, limit, order, count_limit)
            readable = self._nudge_readable([line["id"] for line in result["records"]])
            result["records"] = [line for line in result["records"] if line["id"] in readable]
            return result
        return super().web_search_read(domain, specification, offset, limit, order, count_limit)
