"""The prompt library: requests worth asking again, with the parts that change as variables.

A prompt is text. It gives nobody any right: the filled text is sent as a message of the person who
clicked, with their rights and the same confirmation cards as anything they type. It never goes
into the system prompt, and it is never sent by itself.
"""
from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from ..tools import noai_commands, prompt_vars

# Chips shown at most, and the prompts looked at to find them
CHIP_LIMIT = 8
SEARCH_LIMIT = 300
SCOPES = [("private", "Only me"), ("team", "A team"), ("company", "Everybody in the company")]
MODES = [("talk", "Talk"), ("agent", "Agent"), ("dev", "Developer")]


class NudgePrompt(models.Model):
    _name = "nudge.prompt"
    _inherit = ["nudge.recipe.mixin"]
    _description = "Nudge Prompt"
    _order = "sequence, id"

    name = fields.Char(string="Title", required=True, translate=True)
    description = fields.Char(translate=True, help="One line that says what it is for.")
    body = fields.Text(
        string="Prompt", required=True, translate=True,
        help="The message that is sent. Write {{customer}} where a word changes: it is asked for before sending.")
    variables = fields.Json(
        help="What each {{placeholder}} is: its type (text, number, date, record, selection), label and default.")
    mode = fields.Selection(MODES, required=True, default="talk", help="The mode this prompt is sent in.")
    model_names = fields.Char(
        string="Screens", help="Technical names of the models of the screens it is suggested on, separated by commas.")
    apps = fields.Char(string="Apps Needed", help="Module names separated by commas. A prompt whose apps are missing is not suggested.")
    scope = fields.Selection(SCOPES, required=True, default="private", index=True)
    user_id = fields.Many2one(
        "res.users", string="Owner", ondelete="cascade", index=True,
        help="Whose private prompt it is. Empty for the prompts that come with Nudge or a recipe.")
    group_ids = fields.Many2many(
        "res.groups", "nudge_prompt_group_rel", "prompt_id", "group_id", string="Groups",
        help="Who sees it. For a team prompt: the team. Empty: everybody it is shared with.")
    company_id = fields.Many2one("res.company", index=True, help="Empty: every company.")
    version = fields.Char(default="1.0.0")
    tags = fields.Char()
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    use_count = fields.Integer(readonly=True, copy=False, default=0)
    noai = fields.Boolean(
        string="Answered Without AI",
        help="The common request is understood by Nudge's own rules: it is suggested when no AI provider is set up.")
    origin = fields.Selection(
        [("module", "Comes with Nudge"), ("recipe", "Imported from a recipe"), ("ui", "Written here")],
        default="ui", required=True)
    expects = fields.Json(help="Hints for tests: the tools a good answer should use.")
    variable_names = fields.Char(compute="_compute_variable_names")

    # ------------------------------------------------------------------ checks
    @api.depends("body")
    def _compute_variable_names(self):
        for prompt in self:
            prompt.variable_names = ", ".join(prompt_vars.names_in(prompt.body))

    @api.constrains("body", "variables")
    def _check_variables(self):
        for prompt in self:
            if len(prompt.body or "") > prompt_vars.MAX_BODY:
                raise ValidationError(self.env._("A prompt is at most %s characters.", prompt_vars.MAX_BODY))
            try:
                prompt_vars.clean_definitions(prompt.variables)
            except prompt_vars.Invalid as error:
                raise ValidationError(str(error)) from None

    @api.constrains("scope", "group_ids", "user_id")
    def _check_scope(self):
        for prompt in self:
            if prompt.scope == "team" and not prompt.group_ids:
                raise ValidationError(self.env._("A team prompt needs the group of the team."))
            if prompt.scope == "private" and not prompt.user_id:
                raise ValidationError(self.env._("A private prompt belongs to somebody."))

    @api.model
    def _check_may_share(self, vals):
        """Only a manager shares: the rules of access already stop the rest, this says why."""
        if vals.get("scope", "private") != "private" and not self.env.user.has_group("nudge.group_nudge_manager"):
            raise AccessError(self.env._("Only Nudge managers share a prompt with others."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._check_may_share(vals)
            if vals.get("body"):
                vals["variables"] = prompt_vars.complete(vals["body"], vals.get("variables") or [])
            if vals.get("scope", "private") == "private":
                vals.setdefault("user_id", self.env.user.id)
        return super().create(vals_list)

    def write(self, vals):
        if "scope" in vals:
            self._check_may_share(vals)
        if vals.get("body"):
            # A word added to the text is asked for: a variable is declared for it
            for prompt in self:
                definitions = vals.get("variables") if "variables" in vals else prompt.variables
                super(NudgePrompt, prompt).write(
                    dict(vals, variables=prompt_vars.complete(vals["body"], definitions or [])))
            return True
        return super().write(vals)

    # ----------------------------------------------------------------- reading
    @api.model
    def _arabic_lang(self):
        """:return: the code of an installed Arabic language, or False"""
        lang = self.env["res.lang"].search([("code", "=like", "ar%"), ("active", "=", True)], limit=1)
        return lang.code or False

    @api.model
    def _models_of(self, prompt):
        return [name.strip() for name in (prompt.model_names or "").split(",") if name.strip()]

    @api.model
    def _apps_missing(self, prompts):
        """:return: ``{prompt id: [apps not installed]}`` for the prompts that need one"""
        wanted = {name for prompt in prompts for name in (prompt.apps or "").replace(" ", "").split(",") if name}
        if not wanted:
            return {}
        # The list of installed modules is the database's, not the person's: read for this check only
        installed = set(self.env["ir.module.module"].sudo().search(
            [("name", "in", sorted(wanted)), ("state", "=", "installed")]).mapped("name"))
        missing = {}
        for prompt in prompts:
            lacking = [name for name in (prompt.apps or "").replace(" ", "").split(",") if name and name not in installed]
            if lacking:
                missing[prompt.id] = lacking
        return missing

    @api.model
    def _mode_allowed(self, mode):
        conversation = self.env["nudge.conversation"]
        if mode == "dev":
            return conversation._can_develop()
        if mode == "agent":
            return conversation._can_use_agent()
        return True

    def _default_values(self):
        """:return: ``{name: value}``, what each variable of the prompt starts with"""
        self.ensure_one()
        today = fields.Date.context_today(self)
        values = {}
        for variable in prompt_vars.clean_definitions(self.variables):
            default = variable.get("default")
            if default == "today" and variable["type"] == "date":
                default = fields.Date.to_string(today)
            elif default == "me" and variable["type"] == "many2one" and variable["model"] == "res.users":
                default = self.env.user.id
            elif default == "me":
                default = None
            values[variable["name"]] = default
        return values

    def _form_of(self, lang):
        """:return: the variables the person fills, with their labels in their language and where they start"""
        self.ensure_one()
        used = prompt_vars.names_in(self.body)
        start = self._default_values()
        out = []
        for variable in prompt_vars.clean_definitions(self.variables):
            if variable["name"] not in used:
                continue
            item = {key: variable[key] for key in ("name", "type", "required") if key in variable}
            item["label"] = prompt_vars.label_of(variable, lang)
            item["options"] = variable.get("options", [])
            item["model"] = variable.get("model", "")
            item["value"] = start.get(variable["name"])
            if variable["type"] == "many2one" and item["value"]:
                try:
                    item["display"] = self.env[variable["model"]].browse(int(item["value"])).read(["display_name"])[0]["display_name"]
                except Exception:  # noqa: BLE001 - a default the person may not read is simply not offered
                    item["value"], item["display"] = None, ""
            out.append(item)
        return out

    @api.model
    def chips(self, model=None, without_ai=False):
        """The prompts to suggest above the message box, for the screen the person is on.

        :param model: the model of the view in front
        :param without_ai: no provider is set up: only the prompts the rules of Nudge understand
        :return: a list of dicts for the panel; what the person may not read is not there"""
        self.env["nudge.quick"]._check_user()
        model = str(model or "")[:80]
        lang = self.env.user.lang or "en_US"
        prompts = self.search([("active", "=", True)], limit=SEARCH_LIMIT)
        missing = self._apps_missing(prompts)
        here, anywhere = [], []
        for prompt in prompts:
            if prompt.id in missing or not self._mode_allowed(prompt.mode):
                continue
            if without_ai and not (prompt.noai and self._rules_understand(prompt)):
                continue
            models_of = self._models_of(prompt)
            if model and model in models_of:
                here.append(prompt)
            elif not models_of:
                anywhere.append(prompt)
        chosen = (here + anywhere)[:CHIP_LIMIT]
        return [self._chip(prompt, lang) for prompt in chosen]

    def _chip(self, prompt, lang):
        form = prompt._form_of(lang)
        return {
            "id": prompt.id, "name": prompt.name, "description": prompt.description or "", "mode": prompt.mode,
            "body": prompt.body,
            "variables": form,
            # Text from a colleague or a recipe is read before it is sent
            "preview": bool(form) or prompt.origin == "recipe" or prompt.scope != "private",
            "text": self._fill(prompt, {}, strict=False)["text"] if not form else "",
        }

    @api.model
    def _rules_understand(self, prompt):
        """:return: whether the rules of Nudge, with no AI, understand the prompt as it starts"""
        try:
            text = self._fill(prompt, prompt._default_values(), strict=False)["text"]
            with self.env["nudge.noai"]._company_words():
                parsed = noai_commands.parse(text[:400], {})
        except Exception:  # noqa: BLE001 - a chip that cannot be checked is not offered as understood
            return False
        return bool(parsed.intent) and parsed.confidence >= noai_commands.THRESHOLD

    # ----------------------------------------------------------------- filling
    @api.model
    def _fill(self, prompt, values, strict=True):
        """:return: ``{"text", "errors"}``; with ``strict`` a wrong value raises"""
        values = values if isinstance(values, dict) else {}
        texts, errors = {}, []
        for variable in prompt_vars.clean_definitions(prompt.variables):
            value, name = values.get(variable["name"]), None
            if variable["type"] == "many2one" and value not in (None, False, ""):
                value = value.get("id") if isinstance(value, dict) else value
                try:
                    comodel = self.env[variable["model"]]
                    # read() applies the rights of the person: what they cannot read cannot be picked
                    name = comodel.browse(int(value)).read(["display_name"])[0]["display_name"]
                except (AccessError, KeyError, ValueError, TypeError, IndexError):
                    errors.append(prompt_vars.label_of(variable))
                    continue
            try:
                texts[variable["name"]] = prompt_vars.value_text(variable, value, name)
            except prompt_vars.Invalid as error:
                errors.append(str(error))
        if errors and strict:
            raise UserError("\n".join(errors))
        return {"text": prompt_vars.fill(prompt.body, texts).strip(), "errors": errors}

    def render(self, values=None):
        """Fill the prompt with what the person gave and count the use.

        The text comes back to the panel, which shows it and sends it as the person's message when they say so.
        Nothing is sent from here.
        :return: ``{"text", "mode"}``
        :raise UserError: when a value does not fit"""
        self.ensure_one()
        if not self._mode_allowed(self.mode):
            raise AccessError(self.env._("This prompt is for a mode that is not open to you."))
        out = self._fill(self, values)
        if not out["text"]:
            raise UserError(self.env._("The prompt is empty."))
        # One statement, so that two people using it at once both count (the person has read the prompt above)
        self.env.cr.execute("UPDATE nudge_prompt SET use_count = COALESCE(use_count, 0) + 1 WHERE id = %s", [self.id])
        self.invalidate_recordset(["use_count"])
        return {"text": out["text"], "mode": self.mode}

    # ------------------------------------------------------------ save as prompt
    @api.model
    def propose(self, text, shared=False, skip=None):
        """From a message that was sent: the prompt it could become, with the parts that may change as variables.

        :param shared: for a team or the company: nothing of this message stays in it, every part found is a variable
        :param skip: names of the parts the person leaves as written
        :return: ``{"body", "variables", "found"}``; nothing is saved"""
        self.env["nudge.quick"]._check_user()
        text = str(text or "").strip()
        if not text:
            raise UserError(self.env._("There is no message to keep."))
        skip = [str(name) for name in skip or [] if isinstance(name, str)][:prompt_vars.MAX_VARIABLES]
        return prompt_vars.detect(text, shared=bool(shared), skip=skip)

    # --------------------------------------------------------------------- misc
    def action_archive_shipped(self):
        """The prompts that come with Nudge are archived, never deleted"""
        self.write({"active": False})
        return True

    def unlink(self):
        shipped = self.filtered(lambda prompt: prompt.origin == "module")
        if shipped:
            raise UserError(self.env._("A prompt that comes with Nudge is archived, not deleted."))
        return super().unlink()
