"""What a Nudge agent picks in the forms of Nudge (the records a rule counts, the field of a
policy): the kinds of records and the fields they may read, looked up in sudo.

Stock Odoo gives employees no right on ir.model and ir.model.fields: their rows hold the record
count of every table and the code of custom fields. Nudge gives none either, and answers only
these searches, on the names and types of what the person may read.

Written for Odoo 17, 18 and 19 alike: odoo.fields.Domain, api.readonly, has_access and
_has_field_access are used only where they exist.
"""
from odoo import _, api, models
from odoo.exceptions import AccessError
from odoo.osv import expression

from odoo.addons.nudge.tools.records import is_closed

try:
    from odoo.fields import Domain  # Odoo 19
except ImportError:  # Odoo 17 and 18: domains are lists
    Domain = None

readonly = getattr(api, "readonly", lambda method: method)

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


def may_read(model):
    """:return: whether the person may read the records of ``model`` (an empty recordset)"""
    if hasattr(model, "has_access"):
        return model._nudge_has_access("read")
    return model.check_access_rights("read", raise_exception=False)


def may_read_field(model, field):
    """:return: whether the person may read ``field`` of ``model``: the groups the field is kept for"""
    if hasattr(model, "_has_field_access"):
        return model._has_field_access(field, "read")
    if not field.groups:
        return True
    has_groups = getattr(model.env.user, "has_groups", None) or model.user_has_groups
    return has_groups(field.groups)


def within(records, domain, columns):
    """:return: ``domain`` when it filters on ``columns`` only, else refused as Odoo does"""
    refused = AccessError(_("You are not allowed to access '%s' records.", records._description))
    if Domain is not None:
        domain = Domain(domain or Domain.TRUE)
        if all(condition.field_expr in columns and condition.operator in OPERATORS
               for condition in domain.iter_conditions()):
            return domain
        raise refused
    domain = list(domain or [])
    for leaf in domain:
        if isinstance(leaf, str) and leaf in ("&", "|", "!"):
            continue
        if not (isinstance(leaf, (list, tuple)) and len(leaf) == 3
                and leaf[0] in columns and leaf[1] in OPERATORS):
            raise refused
    return domain


def narrowed(domain, field_name, ids):
    """:return: ``domain`` and ``field_name`` in ``ids``"""
    if Domain is not None:
        return domain & Domain(field_name, "in", ids)
    return expression.AND([domain, [(field_name, "in", ids)]])


class IrModel(models.Model):
    _inherit = "ir.model"

    @api.model
    def _nudge_readable(self):
        """:return: in sudo, the models the person may read: not transient nor abstract, not
        kept closed by Nudge"""
        env = self.env
        found = self.sudo().search([("transient", "=", False)])
        return found.filtered(lambda line: line.model in env and not env[line.model]._abstract
                              and not is_closed(env, line.model) and may_read(env[line.model]))

    def _nudge_domain(self, domain):
        return narrowed(within(self, domain, MODEL_COLUMNS), "id", self._nudge_readable().ids)

    @api.model
    @readonly
    def name_search(self, name="", domain=None, operator="ilike", limit=100, **kwargs):
        # Odoo 17 and 18 name the domain "args"
        domain = kwargs.pop("args", domain)
        if picked_in_sudo(self):
            return self.sudo().name_search(name, self._nudge_domain(domain), operator, limit)
        return super().name_search(name, domain, operator, limit)

    @api.model
    @readonly
    def web_name_search(self, name, specification, domain=None, operator="ilike", limit=100):
        if picked_in_sudo(self):
            return self.sudo().web_name_search(name, {"display_name": {}}, self._nudge_domain(domain), operator, limit)
        return super().web_name_search(name, specification, domain, operator, limit)

    @api.model
    @readonly
    def web_search_read(self, domain, specification, offset=0, limit=None, order=None, count_limit=None):
        if picked_in_sudo(self):
            shown = {key: value for key, value in specification.items() if key in MODEL_SHOWN}
            return self.sudo().web_search_read(self._nudge_domain(domain), shown, offset, limit, order, count_limit)
        return super().web_search_read(domain, specification, offset, limit, order, count_limit)


class IrModelFields(models.Model):
    _inherit = "ir.model.fields"

    def _nudge_domain(self, domain):
        return narrowed(within(self, domain, FIELD_COLUMNS), "model_id", self.env["ir.model"]._nudge_readable().ids)

    def _nudge_readable(self, ids):
        """:return: of the fields ``ids`` (in sudo), those the person may read: the groups of a field"""
        env = self.env
        return {field.id for field in self.sudo().browse(ids)
                if field.name in env[field.model]._fields
                and may_read_field(env[field.model], env[field.model]._fields[field.name])}

    @api.model
    @readonly
    def name_search(self, name="", domain=None, operator="ilike", limit=100, **kwargs):
        domain = kwargs.pop("args", domain)
        if picked_in_sudo(self):
            found = self.sudo().name_search(name, self._nudge_domain(domain), operator, limit)
            readable = self._nudge_readable([pair[0] for pair in found])
            return [pair for pair in found if pair[0] in readable]
        return super().name_search(name, domain, operator, limit)

    @api.model
    @readonly
    def web_name_search(self, name, specification, domain=None, operator="ilike", limit=100):
        if picked_in_sudo(self):
            found = self.sudo().web_name_search(name, {"display_name": {}}, self._nudge_domain(domain), operator, limit)
            readable = self._nudge_readable([line["id"] for line in found])
            return [line for line in found if line["id"] in readable]
        return super().web_name_search(name, specification, domain, operator, limit)

    @api.model
    @readonly
    def web_search_read(self, domain, specification, offset=0, limit=None, order=None, count_limit=None):
        if picked_in_sudo(self):
            shown = {key: value for key, value in specification.items() if key in FIELD_SHOWN}
            result = self.sudo().web_search_read(self._nudge_domain(domain), shown, offset, limit, order, count_limit)
            readable = self._nudge_readable([line["id"] for line in result["records"]])
            result["records"] = [line for line in result["records"] if line["id"] in readable]
            return result
        return super().web_search_read(domain, specification, offset, limit, order, count_limit)
