"""Signed recipes (S2): the publishers an administrator trusts, the key that signs our exports, and the check.

A recipe that carries a signature is ``Signed by <publisher>`` when the signature verifies against the key of a
trusted publisher, ``untrusted`` when it names a key nobody here trusts, and refused when it names a trusted key
and does not verify (the file was changed after it was signed). A recipe with none is ``unsigned``. The
signature never grants anything: what comes in from a recipe is a draft either way, and an unsigned or untrusted
recipe is imported only by a system administrator who read the warning.

The private key is uploaded by an administrator, stored in a field only the system group may read (named so the
snapshot and the record tools hide it), used by ``_sign`` and never written to a log, a message or a prompt.
"""
import base64
import json
import logging

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from ..tools import recipe_sign
from ..tools.compliance_feed import FeedError

_logger = logging.getLogger(__name__)


class NudgeRecipePublisher(models.Model):
    """A publisher whose signed recipes are trusted on this database. Only the system group changes the list."""
    _name = "nudge.recipe.publisher"
    _description = "Trusted Recipe Publisher"
    _order = "builtin desc, name"

    name = fields.Char(required=True, help="Shown as Signed by this name.")
    public_key = fields.Text(
        string="Public Key", required=True, help="An Ed25519 public key: PEM, or the 32 bytes in base64.")
    key_id = fields.Char(string="Fingerprint", compute="_compute_key_id", store=True, index=True)
    builtin = fields.Boolean(string="Comes with Nudge", readonly=True)
    active = fields.Boolean(default=True)

    @api.depends("public_key")
    def _compute_key_id(self):
        for publisher in self:
            try:
                publisher.key_id = recipe_sign.key_id_of(publisher.public_key)
            except FeedError:
                publisher.key_id = False

    @api.constrains("public_key")
    def _check_public_key(self):
        for publisher in self:
            try:
                recipe_sign.key_id_of(publisher.public_key)
            except FeedError as error:
                raise ValidationError(error.args[0]) from None

    @api.constrains("key_id", "active")
    def _check_unique_key(self):
        for publisher in self.filtered("active"):
            if self.search_count([("id", "!=", publisher.id), ("key_id", "=", publisher.key_id)]):
                raise ValidationError(self.env._("This key is already in the list."))


class NudgeRecipeSigning(models.Model):
    """The key that signs the recipes of this database: one row, system only."""
    _name = "nudge.recipe.signing"
    _description = "Recipe Signing Key"

    publisher_name = fields.Char(string="Publisher Name", required=True)
    private_key = fields.Text(groups="base.group_system", prefetch=False)
    public_key = fields.Text(string="Public Key", readonly=True)
    key_id = fields.Char(string="Fingerprint", readonly=True)
    stored_on = fields.Datetime(readonly=True, default=fields.Datetime.now)

    @api.model
    def _current(self):
        """:return: the signing row, or an empty recordset; read as Nudge itself (managers may sign, not read the key)"""
        # sudo: the key is for Nudge's own use; the manager who exports asks to sign, and never gets to read it
        return self.sudo().search([], limit=1)


class NudgeRecipeSigningKey(models.TransientModel):
    _name = "nudge.recipe.signing.key"
    _description = "Upload the Recipe Signing Key"

    publisher_name = fields.Char(string="Publisher Name", required=True, default=lambda self: self.env.company.name)
    key_file = fields.Binary(string="Private Key (PEM)", attachment=False)
    key_file_name = fields.Char()
    stored = fields.Boolean(readonly=True)
    public_key = fields.Text(string="Public Key to Share", readonly=True)
    key_id = fields.Char(string="Fingerprint", readonly=True)
    current_key_id = fields.Char(string="Key Now in Use", compute="_compute_current")

    @api.depends_context("uid")
    def _compute_current(self):
        for wizard in self:
            wizard.current_key_id = self.env["nudge.recipe.signing"]._current().key_id or False

    def _check_system(self):
        if not self.env.user.has_group("base.group_system"):
            raise AccessError(self.env._("Only a system administrator sets the signing key."))

    def action_store(self):
        """Check the key, keep it where only the system group reads it, and clear it from this form."""
        self.ensure_one()
        self._check_system()
        if not self.key_file:
            raise UserError(self.env._("Choose the private key file first."))
        try:
            pem = base64.b64decode(self.key_file).decode("utf-8")
            _key, public = recipe_sign.load_private(pem)
        except (ValueError, UnicodeDecodeError, FeedError):
            raise UserError(self.env._("This is not an unprotected Ed25519 private key in PEM form.")) from None
        values = {
            "publisher_name": self.publisher_name.strip()[:100], "private_key": pem, "public_key": public,
            "key_id": recipe_sign.key_id_of(public), "stored_on": fields.Datetime.now()}
        Signing = self.env["nudge.recipe.signing"]
        current = Signing._current()
        if current:
            current.write(values)
        else:
            # sudo: only a system administrator gets here (checked above); the row is Nudge's own
            Signing.sudo().create(values)
        self.write({"key_file": False, "key_file_name": False, "stored": True,
                    "public_key": public, "key_id": values["key_id"]})
        return self._reopen()

    def action_remove(self):
        self.ensure_one()
        self._check_system()
        self.env["nudge.recipe.signing"]._current().unlink()
        self.write({"stored": False, "public_key": False, "key_id": False})
        return self._reopen()

    def _reopen(self):
        return {"type": "ir.actions.act_window", "res_model": self._name, "res_id": self.id,
                "view_mode": "form", "target": "new", "name": self.env._("Recipe signing key")}


class NudgeRecipeSigned(models.AbstractModel):
    _inherit = "nudge.recipe"

    @api.model
    def _sign(self, package):
        """:return: ``package`` with a signature when a signing key is set, else as it is (unsigned)"""
        signing = self.env["nudge.recipe.signing"]._current()
        if not signing:
            return package
        # sudo: reading the key to sign with it, by Nudge's own code; the key goes nowhere else
        pem = signing.sudo().private_key
        try:
            signature = recipe_sign.sign(package["recipe"], pem)
        except FeedError:
            _logger.warning("Nudge: the recipe signing key could not be used; the recipe is exported unsigned.")
            return package
        return dict(package, signature=signature)

    @api.model
    def _verify(self, text):
        """:return: ``{"state": signed|untrusted|unsigned, "publisher": name or ""}`` for the recipe file ``text``
        :raise UserError: when the signature is damaged, or names a trusted key and does not verify"""
        try:
            package = json.loads(text)
            body = package["recipe"]
        except (ValueError, KeyError, TypeError):
            raise UserError(self.env._("This file is not a Nudge recipe.")) from None
        signature = package.get("signature")
        if signature is None:
            return {"state": "unsigned", "publisher": ""}
        if not recipe_sign.well_formed(signature):
            raise UserError(self.env._("The signature of this recipe cannot be read: it is not imported."))
        publisher = self.env["nudge.recipe.publisher"].search([("key_id", "=", signature["key_id"])], limit=1)
        if not publisher:
            return {"state": "untrusted", "publisher": ""}
        if not recipe_sign.verify(body, signature, publisher.public_key):
            raise UserError(self.env._(
                "The signature does not match: the recipe was changed after %s signed it. It is not imported.",
                publisher.name))
        return {"state": "signed", "publisher": publisher.name}
