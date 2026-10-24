"""Skills (NP-03): the instructions of one kind of work, kept apart and given only when needed.

A skill holds the full instructions of one kind of work (setting Odoo up, importing files, designed
emails...), the words that bring it, the tools it needs, who may use it and in which modes. Every call
to the model carries the index of the skills the person may use, one line each; the full text of one
or two joins it when the question needs them (``tools/skills.py``). What was once in every call is
sent only when it serves, and a manager can write the company's own.

A skill is the company's own instruction to Nudge, so it is written and published by Nudge managers
only. What comes from a recipe, a marketplace or from approved lessons lands as a draft: a draft is
never sent to a model. Every change is kept with its difference. A skill can never give a tool: the
tools it names are offered only when the person may use them in that mode anyway.
"""
import difflib
import logging

from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError

from .. import tools
from ..llm import prompt
from ..tools import base, skills as skill_rules

_logger = logging.getLogger(__name__)

MODES = ("talk", "agent", "dev")
# What a change of the skill is kept for, with its difference
TRACKED = ("key", "name", "summary", "instructions", "trigger_words", "model_names", "tool_names", "modes",
           "group_ids", "company_id", "state", "version", "test_sentence", "test_expect", "active")
# What decides what the model reads: changed by a manager, it is approved by that manager
STEERING = ("summary", "instructions", "trigger_words", "model_names", "tool_names", "modes", "group_ids",
            "company_id")
INLINE_PARAM = "nudge.skills_inline"
MAX_INSTRUCTIONS = 12000


class NudgeSkill(models.Model):
    _name = "nudge.skill"
    _inherit = ["nudge.recipe.mixin"]
    _description = "Nudge Skill"
    _order = "sequence, key, id"

    key = fields.Char(
        required=True, index=True,
        help="Short name the model asks for it by, in small letters and dashes: invoice-follow-up.")
    name = fields.Char(string="Title", required=True, translate=True)
    summary = fields.Char(
        required=True, size=skill_rules.MAX_SUMMARY,
        help="One line, in English, that the model reads in every call to know when this skill serves.")
    instructions = fields.Text(
        required=True,
        help="The full instructions, in English, given to the model only when the question needs them.")
    trigger_words = fields.Text(
        string="Words That Bring It",
        help="Words in English and in Arabic, separated by commas: a question that holds one gets the skill, "
             "without any AI.")
    model_names = fields.Char(
        string="Screens", help="Technical names of the models whose screens bring it, separated by commas.")
    tool_names = fields.Char(
        string="Tools It Needs",
        help="Technical names of the tools, separated by commas. They are offered only to the people who may "
             "use them in that mode anyway: a skill never gives a tool.")
    modes = fields.Char(default="talk,agent,dev", required=True, help="talk, agent and dev, separated by commas.")
    group_ids = fields.Many2many(
        "res.groups", "nudge_skill_group_rel", "skill_id", "group_id", string="For the Groups",
        help="Who may use it. Empty: every Nudge user.")
    company_id = fields.Many2one("res.company", index=True, help="Empty: every company.")
    version = fields.Char(default="1.0.0")
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    state = fields.Selection(
        [("draft", "Draft"), ("published", "Published")], default="draft", required=True, index=True,
        help="Only a published skill is given to the model. What comes from a recipe or from lessons is a draft "
             "until a manager publishes it.")
    origin = fields.Selection(
        [("module", "Comes with Nudge"), ("recipe", "Imported from a recipe"), ("lessons", "Proposed from lessons"),
         ("ui", "Written here")], default="ui", required=True, readonly=True)
    approved_by = fields.Many2one("res.users", readonly=True, copy=False)
    approved_on = fields.Datetime(readonly=True, copy=False)
    load_count = fields.Integer(
        string="Given", readonly=True, copy=False, default=0,
        help="How many questions were given its full text. Counted when nobody else is counting at the same "
             "instant: a figure to compare skills, not an exact one.")
    token_estimate = fields.Integer(
        string="Tokens", compute="_compute_token_estimate",
        help="About what its full text adds to a call, at four characters a token.")
    test_sentence = fields.Char(
        string="Test Sentence", help="A question that must bring this skill, checked every week without any AI.")
    test_expect = fields.Char(
        string="Expected Tool", help="The tool a good answer to the test sentence starts with.")
    test_state = fields.Selection(
        [("none", "Not Run"), ("ok", "Passed"), ("failed", "Failed")], default="none", readonly=True, copy=False)
    test_detail = fields.Char(readonly=True, copy=False)
    test_date = fields.Datetime(string="Tested On", readonly=True, copy=False)
    lesson_ids = fields.One2many(
        "nudge.note", "skill_id", string="Lessons", readonly=True, domain=[("kind", "=", "lesson")])
    revision_ids = fields.One2many("nudge.skill.revision", "skill_id", string="Changes", readonly=True)

    # ------------------------------------------------------------------ checks
    @api.depends("instructions")
    def _compute_token_estimate(self):
        for skill in self:
            skill.token_estimate = len(skill.instructions or "") // 4

    @api.constrains("key")
    def _check_key(self):
        for skill in self:
            if not skill_rules.KEY.match(skill.key or ""):
                raise ValidationError(self.env._(
                    "The key of a skill is 2 to 63 small letters, digits and dashes: %s", skill.key))

    @api.constrains("key", "company_id", "active")
    def _check_unique_key(self):
        for skill in self.filtered("active"):
            twin = self.with_context(active_test=True).sudo().search_count([
                ("id", "!=", skill.id), ("key", "=", skill.key), ("company_id", "=", skill.company_id.id)])
            if twin:
                raise ValidationError(self.env._("Another skill already has the key %s here.", skill.key))

    @api.constrains("modes")
    def _check_modes(self):
        for skill in self:
            modes = skill_rules.split_names(skill.modes)
            if not modes or set(modes) - set(MODES):
                raise ValidationError(self.env._("The modes of a skill are talk, agent and dev, separated by commas."))

    @api.constrains("tool_names")
    def _check_tool_names(self):
        for skill in self:
            unknown = [name for name in skill_rules.split_names(skill.tool_names) if name not in base.TOOLS]
            if unknown:
                raise ValidationError(self.env._("No tool of Nudge here is named %s.", ", ".join(unknown)))

    @api.constrains("instructions")
    def _check_instructions(self):
        for skill in self:
            if len(skill.instructions or "") > MAX_INSTRUCTIONS:
                raise ValidationError(self.env._("The instructions of a skill are at most %s characters.", MAX_INSTRUCTIONS))

    # ----------------------------------------------------------- the changes
    def _snapshot(self, names):
        self.ensure_one()
        values = {}
        for name in names:
            value = self[name]
            if isinstance(value, models.BaseModel):
                value = ", ".join(sorted(value.mapped("display_name")))
            values[name] = "" if value is False or value is None else str(value)
        return values

    @api.model
    def _diff(self, before, after):
        """:return: the difference of two snapshots, line by line, as a reader of code knows it"""
        out = []
        for name in sorted(set(before) | set(after)):
            old, new = before.get(name, ""), after.get(name, "")
            if old == new:
                continue
            out.append(f"@@ {name}")
            out.extend(line.rstrip("\n") for line in difflib.unified_diff(
                old.splitlines(), new.splitlines(), lineterm="", n=1) if not line.startswith(("---", "+++")))
        return "\n".join(out)

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su and not self.env.user.has_group("nudge.group_nudge_manager"):
            raise AccessError(self.env._("Only Nudge managers write skills."))
        for vals in vals_list:
            if vals.get("key"):
                vals["key"] = vals["key"].strip().lower()
            if vals.get("state") == "published" and not self.env.su:
                vals.setdefault("approved_by", self.env.uid)
                vals.setdefault("approved_on", fields.Datetime.now())
        skills = super().create(vals_list)
        for skill in skills:
            self.env["nudge.skill.revision"].sudo().create({
                # sudo: the history of a skill is written by Nudge for every change, whoever makes it
                "skill_id": skill.id, "user_id": self.env.uid, "what": self.env._("Created"),
                "diff": self._diff({}, skill._snapshot(TRACKED))})
        return skills

    def write(self, vals):
        if not self.env.su and not self.env.user.has_group("nudge.group_nudge_manager"):
            raise AccessError(self.env._("Only Nudge managers change skills."))
        if vals.get("key"):
            vals["key"] = vals["key"].strip().lower()
        names = [name for name in TRACKED if name in vals]
        if not names:
            return super().write(vals)
        before = {skill.id: skill._snapshot(names) for skill in self}
        steering = any(name in vals for name in STEERING)
        stays_published = vals.get("state") == "published" or (
            "state" not in vals and all(skill.state == "published" for skill in self))
        if steering and stays_published and not self.env.su and "approved_by" not in vals:
            # A manager who changes what the model reads approves it by saving it
            vals = dict(vals, approved_by=self.env.uid, approved_on=fields.Datetime.now())
        result = super().write(vals)
        Revision = self.env["nudge.skill.revision"].sudo()
        for skill in self:
            diff = self._diff(before[skill.id], skill._snapshot(names))
            if diff:
                Revision.create({
                    "skill_id": skill.id, "user_id": self.env.uid, "diff": diff,
                    "what": ", ".join(skill._fields[name].string for name in names
                                      if before[skill.id][name] != skill._snapshot([name])[name])})
        return result

    # -------------------------------------------------------------- buttons
    def _check_manager(self):
        if not self.env.user.has_group("nudge.group_nudge_manager"):
            raise AccessError(self.env._("Only Nudge managers publish skills."))

    def action_publish(self):
        """A manager read it and lets the model have it: what came from a recipe or from lessons too"""
        self._check_manager()
        self.write({"state": "published", "approved_by": self.env.uid, "approved_on": fields.Datetime.now()})
        return True

    def action_unpublish(self):
        self._check_manager()
        self.write({"state": "draft"})
        return True

    def action_reject(self):
        """A draft no one wants: archived, never deleted; its lessons stay with it, not proposed again"""
        self._check_manager()
        self.write({"active": False, "state": "draft"})
        return True

    def action_run_test(self):
        self._check_manager()
        self._run_self_tests()
        return True

    # ------------------------------------------------------------ the router
    @api.model
    def _inline(self):
        """:return: whether every skill goes in full into every call, as before skills (the fallback)"""
        return self.env["ir.config_parameter"].sudo().get_param(INLINE_PARAM) in ("True", "true", "1", "yes")

    def _modes_list(self):
        self.ensure_one()
        return skill_rules.split_names(self.modes)

    def _in_mode(self, mode):
        """The developer may do what the agent does: a skill of Agent mode serves Developer mode too"""
        modes = self._modes_list()
        return mode in modes or (mode == base.MODE_DEV and base.MODE_AGENT in modes)

    @api.model
    def _visible(self, mode):
        """:return: the published skills the current person may use in ``mode``, in this company, one per
        key (the company's own before one of every company), sorted by key"""
        try:
            found = self.search([
                ("state", "=", "published"), ("company_id", "in", [self.env.company.id, False])], order="id")
        except AccessError:
            return self.browse()
        groups = self.env["nudge.note"]._user_groups(self.env.user)
        chosen = {}
        for skill in found:
            if not skill._in_mode(mode) or (skill.group_ids and not skill.group_ids & groups):
                continue
            current = chosen.get(skill.key)
            if current is None or (skill.company_id and not current.company_id) or (
                    bool(skill.company_id) == bool(current.company_id)):
                chosen[skill.key] = skill
        return self.browse([chosen[key].id for key in sorted(chosen)])

    def _router_entries(self):
        return [(skill.key, skill_rules.forms_of(skill.trigger_words), set(skill_rules.split_names(skill.model_names)))
                for skill in self]

    def _count_given(self):
        """Count the question each of these skills was given to. Never waits: a skill another question is
        counting at this instant is not counted twice over (SKIP LOCKED), the figure is an indication"""
        if not self:
            return
        self.env.cr.execute(
            "UPDATE nudge_skill SET load_count = COALESCE(load_count, 0) + 1 WHERE id IN ("
            " SELECT id FROM nudge_skill WHERE id IN %s ORDER BY id FOR UPDATE SKIP LOCKED)", [tuple(self.ids)])
        self.invalidate_recordset(["load_count"])

    # ------------------------------------------------------------ self-tests
    def _self_test(self):
        """Check, without any AI and without changing anything, that the test sentence still brings this
        skill and that the tool it expects is still here and works in its modes.

        :return: ``(state, detail)``"""
        self.ensure_one()
        if not (self.test_sentence or "").strip():
            return "none", self.env._("No test sentence.")
        names = skill_rules.split_names(self.tool_names)
        unknown = [name for name in names if name not in base.TOOLS]
        if unknown:
            return "failed", self.env._(
                "These tools are not here any more (a module was removed, or they were renamed): %s.", ", ".join(unknown))
        expected = (self.test_expect or "").strip()
        if expected:
            tool = base.TOOLS.get(expected)
            if tool is None:
                return "failed", self.env._("No tool is named %s here any more.", expected)
            if names and expected not in names:
                return "failed", self.env._("The expected tool %s is not among the tools of the skill.", expected)
            if not any(tools.groups.in_mode(tool, mode) for mode in self._modes_list()):
                return "failed", self.env._("The tool %(tool)s does not work in the modes %(modes)s.",
                                            tool=expected, modes=self.modes)
        # The skills it could meet: those of its companies and its modes, itself whatever its state
        rivals = self.search([
            ("state", "=", "published"), ("id", "!=", self.id),
            ("company_id", "in", [self.company_id.id, False] if self.company_id else [False])])
        rivals = rivals.filtered(lambda other: set(other._modes_list()) & set(self._modes_list()))
        picked = skill_rules.pick((self | rivals)._router_entries(), self.test_sentence)
        if self.key not in picked:
            return "failed", self.env._(
                "The test sentence does not bring the skill any more; it brings %s.",
                ", ".join(picked) or self.env._("no skill"))
        return "ok", self.env._("The test sentence brings the skill%s.",
                                self.env._(" and its tool %s", expected) if expected else "")

    def _run_self_tests(self):
        """Run the test of each skill and keep its result. :return: the skills that failed"""
        failed = self.browse()
        now = fields.Datetime.now()
        for skill in self:
            state, detail = skill._self_test()
            # Not a change of the skill: no history, and the approval stands
            super(NudgeSkill, skill).write({"test_state": state, "test_detail": detail[:250], "test_date": now})
            if state == "failed":
                failed |= skill
        return failed


class NudgeSkillRevision(models.Model):
    """What changed in a skill, by whom and when, with the difference: kept, never edited."""
    _name = "nudge.skill.revision"
    _description = "Nudge Skill Change"
    _order = "id desc"

    skill_id = fields.Many2one("nudge.skill", required=True, ondelete="cascade", index=True)
    user_id = fields.Many2one("res.users", string="By", required=True, readonly=True)
    date = fields.Datetime(default=fields.Datetime.now, required=True, readonly=True)
    what = fields.Char(string="Changed", readonly=True)
    diff = fields.Text(string="Difference", readonly=True)


class NudgeConversation(models.Model):
    _inherit = "nudge.conversation"

    skill_keys = fields.Char(
        copy=False, readonly=True,
        help="The skills the model asked for with use_skill: given with every next question of the conversation.")

    def _skill_keep(self, skill):
        """use_skill: the skill is given with every next question, the last ones asked for kept"""
        self.ensure_one()
        keys = [key for key in (self.sudo().skill_keys or "").split(",") if key and key != skill.key]
        # sudo: the conversation is the person's own, as with more_tools (tool_groups)
        self.sudo().skill_keys = ",".join([*keys, skill.key][-skill_rules.MAX_KEPT:])

    def _skill_plan(self, mode, compact=False):
        """:return: what the skills add to the call for the question being answered: ``index`` (the
        one-line list, not for a small model), ``text`` (the full instructions of the skills given),
        ``given`` (those skills) and ``offer`` (for tools.base.available)"""
        self.ensure_one()
        Skill = self.env["nudge.skill"]
        plan = {"index": "", "text": "", "given": Skill, "offer": {}}
        visible = Skill._visible(mode)
        if not visible:
            return plan
        if Skill._inline():
            # The fallback: every skill in full, as the instructions were before skills
            given, entries = visible, []
        else:
            question = self.env["nudge.message"].sudo().search([
                ("conversation_id", "=", self.id), ("role", "=", "user")], order="id desc", limit=1)
            screen = question.snapshot if isinstance(question.snapshot, dict) else {}
            keys = skill_rules.pick(visible._router_entries(), question.content or "", screen.get("model"))
            kept = [key for key in reversed((self.sudo().skill_keys or "").split(",")) if key and key not in keys]
            keys = (keys + kept)[:skill_rules.MAX_LOADED]
            by_key = {skill.key: skill for skill in visible}
            given = Skill.browse([by_key[key].id for key in keys if key in by_key])
            entries = [] if compact else [(skill.key, skill.summary) for skill in visible]
        plan.update(
            index=prompt.render_skill_index(entries),
            text=prompt.render_skills(given.mapped("instructions")),
            given=given,
            offer={"tools": sorted({name for skill in given for name in skill_rules.split_names(skill.tool_names)}),
                   "index": bool(entries)})
        return plan

    def _skill_plan_or_nothing(self, mode, compact):
        try:
            with self.env.cr.savepoint():
                return self._skill_plan(mode, compact)
        except Exception:  # noqa: BLE001 - a skill that cannot be read never stops an answer
            _logger.exception("Nudge: the skills of conversation %s could not be read", self.id)
            return {"index": "", "text": "", "given": self.env["nudge.skill"], "offer": {}}

    def _chat_tools(self, mode, tool_set):
        # The tools of the skills given with the question, and use_skill with their index
        offer = self.env.context.get("nudge_skill_offer")
        if offer is None:
            offer = self._skill_plan_or_nothing(mode, tool_set == "essential")["offer"]
        return super(NudgeConversation, self.with_context(nudge_skills=offer))._chat_tools(mode, tool_set)

    def _chat_job(self, provider, mode, rounds):
        # One plan for the question: the tools it offers (_chat_tools) and the instructions it adds
        plan = self._skill_plan_or_nothing(mode, (provider.tool_set if provider else "full") == "essential")
        job = super(NudgeConversation, self.with_context(nudge_skill_offer=plan["offer"]))._chat_job(
            provider, mode, rounds)
        if not job.get("system") or job["system"][0] == prompt.FOCUS_PERSONA:
            # One precise thing with its own brief: no skill
            return job
        job["system"] = prompt.with_skills(job["system"], plan["index"], plan["text"])
        plan["given"]._count_given()
        return job

    def _chat_record(self, outcome):
        before = set(filter(None, (self.sudo().skill_keys or "").split(",")))
        step = super()._chat_record(outcome)
        if "turns" not in step or "event" in step or not any(
                call.get("name") == "use_skill" for call in outcome.get("tool_calls") or ()):
            return step
        # use_skill: the skill joins the instructions from the next step of this answer, with its tools,
        # as a system block (llm/loop.py), never as a tool result
        self.invalidate_recordset(["skill_keys"])
        asked = {key for key in (self.sudo().skill_keys or "").split(",") if key and key not in before}
        if not asked:
            return step
        mode = self._chat_mode()
        given = self.env["nudge.skill"]._visible(mode).filtered(lambda skill: skill.key in asked)
        if given:
            provider = self.sudo().provider_id
            step["skills"] = prompt.render_skills(given.mapped("instructions"))
            step["tools"] = self._chat_tools(mode, provider.tool_set if provider else "full")
            given._count_given()
        return step


class NudgeRecipeExport(models.TransientModel):
    _inherit = "nudge.recipe.export"

    with_skills = fields.Boolean(string="Skills (written here or imported)", default=True)


class NudgeRecipePublish(models.TransientModel):
    _inherit = "nudge.recipe.publish"

    with_skills = fields.Boolean(string="Skills (written here or imported)", default=False)


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    nudge_skills_inline = fields.Boolean(
        string="Every Skill in Full", config_parameter=INLINE_PARAM,
        help="Send the full text of every skill with every question, as before skills: longer and costlier calls. "
             "Off: a one-line list, and the full text of the skills a question needs.")
