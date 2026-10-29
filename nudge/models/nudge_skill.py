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

from psycopg2 import OperationalError

from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError

from .. import tools
from ..llm import fingerprint, prompt
from ..tools import base, skill_router, skills as skill_rules

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
            # sudo: which groups may use the skill, its configuration (a portal user, the service user of an agent's
            # public channel, reads no group)
            if not skill._in_mode(mode) or (skill.sudo().group_ids and not skill.sudo().group_ids & groups):
                continue
            current = chosen.get(skill.key)
            if current is None or (skill.company_id and not current.company_id) or (
                    bool(skill.company_id) == bool(current.company_id)):
                chosen[skill.key] = skill
        return self.browse([chosen[key].id for key in sorted(chosen)])

    def _route(self, text, model=None, screen=None):
        """S1: the skills a question needs, even when it holds none of their trigger words
        (``tools/skill_router.py``: the trigger words and the screen, then the words of the skills, then
        the local search model if it is already on). :return: the ``Choice``, with the skills in
        ``keys`` and every skill the question scored for in ``ranked``"""
        # The skills are read in English, as their summary and instructions are written and as the router was
        # measured: their titles in the person's language made the same question bring other skills to an Arabic
        # screen than to an English one (R3-38: three more lines in the index of the sample question)
        index = skill_router.index_of(self.env, self.with_context(lang="en_US"))
        return skill_router.route(index, text, model, screen, engine_of=self._search_engine)

    def _search_engine(self):
        """:return: the local search model when an administrator has already switched it on, else None"""
        try:
            # sudo: reads a server parameter and the model kept in memory; it shows the person nothing
            return self.env["nudge.embedding"].sudo()._embedder()
        except Exception:  # noqa: BLE001 - the search model is never needed to choose a skill
            return None

    def _router_entries(self):
        return [(skill.key, skill_rules.forms_of(skill.trigger_words), set(skill_rules.split_names(skill.model_names)))
                for skill in self]

    def _count_given(self):
        """Count the question each of these skills was given to. Never waits and never fails: a skill
        another question is counting at this instant (SKIP LOCKED), or counted since this one began
        (a serialization error), is not counted this time; the figure is an indication"""
        if not self:
            return
        try:
            with self.env.cr.savepoint():
                self.env.cr.execute(
                    "UPDATE nudge_skill SET load_count = COALESCE(load_count, 0) + 1 WHERE id IN ("
                    " SELECT id FROM nudge_skill WHERE id IN %s ORDER BY id FOR UPDATE SKIP LOCKED)",
                    [tuple(self.ids)], log_exceptions=False)
        except OperationalError:
            _logger.info("Nudge: skills %s counted by another question at the same time, left uncounted", self.ids)
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
        # By its words, as the weekly check always said: the other layers would hide a trigger word that is gone
        index = skill_router.index_of(self.env, self | rivals)
        picked = skill_router.route(index, self.test_sentence, layers=("rules",)).keys
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
    offered_tools = fields.Char(
        copy=False, readonly=True,
        help="The tools offered beyond the core of the mode in this conversation: offered with every next question.")

    def _skill_keep(self, skill):
        """use_skill: the skill is given with every next question, the last ones asked for kept"""
        self.ensure_one()
        keys = [key for key in (self.sudo().skill_keys or "").split(",") if key and key != skill.key]
        # sudo: the conversation is the person's own, as with more_tools (tool_groups)
        self.sudo().skill_keys = ",".join([*keys, skill.key][-skill_rules.MAX_KEPT:])

    def _skill_questions(self):
        """:return: the question being answered and the one before it (sudo: the conversation is the
        person's own, as everywhere it is read to answer)"""
        questions = self.env["nudge.message"].sudo().search([
            ("conversation_id", "=", self.id), ("role", "=", "user")], order="id desc", limit=2)
        return questions[:1], questions[1:2]

    def _tool_route(self, mode, question, previous):
        """NP-03: the tools of the groups the question needs, beyond the core of the mode
        (tools/groups.py, with no AI): by its words and those of the question before it, which it may
        answer, by its screen, its context, its command and its file, and the tools called since the
        question before it, so that the work goes on with them"""
        answers = self.env["nudge.message"].sudo().search([
            ("conversation_id", "=", self.id), ("role", "=", "assistant"),
            ("id", ">", previous.id if previous else 0)], order="id desc", limit=50)
        called = {call.get("name") for answer in answers for call in (answer.tool_calls or ())
                  if isinstance(call, dict)}
        return tools.groups.routed(
            self, mode, [question.content, previous.content], screen=question.snapshot,
            context=question.context_text, command=question.command,
            attachment=bool(question.attachment_name), called=called)

    def _skill_plan(self, mode, compact=False):
        """:return: what the skills add to the call for the question being answered: ``index`` (what
        is the same for every question of this person: the one-line list, not for a small model, or
        every skill in full in the fallback), ``text`` (the full instructions of the skills of this
        question, a list), ``given`` (the skills given in full) and ``offer`` (for tools.base.available:
        the tools of the skills, and for a model that is not small the tools its question needs
        beyond the core of the mode, ``route``; none in the fallback, which offers every tool)"""
        self.ensure_one()
        Skill = self.env["nudge.skill"]
        plan = {"index": "", "text": [], "given": Skill, "offer": {}}
        inline = Skill._inline()
        question, previous = self._skill_questions()
        if not inline and not compact and question:
            plan["offer"] = {"route": self._tool_route(mode, question, previous)}
        # NP-03: what only this question needs to be told (a small model reads its own short rules)
        situation = "" if compact else prompt.situations(question.context_text, question.snapshot)
        plan["text"] = [situation] if situation else []
        visible = Skill._visible(mode)
        if not visible:
            return plan
        if inline:
            # The fallback: every skill in full, as the instructions were before skills
            given, entries, shown = visible, [], None
        else:
            shown = None
            screen = question.snapshot if isinstance(question.snapshot, dict) else {}
            choice = visible._route(question.content or "", screen.get("model"), screen)
            keys = list(choice.keys)
            # An answer to a question of Nudge goes on with the skills of the question it answers
            keys += [key for key in visible._route(previous.content or "").keys if key not in keys]
            kept_all = [key for key in reversed((self.sudo().skill_keys or "").split(",")) if key]
            kept = [key for key in kept_all if key not in keys]
            if not keys and not kept and question.content:
                # No skill for this question: the model may ask for one (use_skill); the miss is kept
                skill_router.note_miss(fingerprint.sample(question.content))
            keys = (keys + kept)[:skill_rules.MAX_LOADED]
            by_key = {skill.key: skill for skill in visible}
            given = Skill.browse([by_key[key].id for key in keys if key in by_key])
            # S1: the index lists the skills this question scored for, the best first, and those already in
            # the conversation, not every skill: the others are asked for by key or by describing the work
            shown = skill_router.offered(choice, [*keys, *kept_all]) if len(visible) > skill_router.INDEX_MAX else None
            entries = [] if compact else [
                (skill.key, skill.summary) for skill in visible if shown is None or skill.key in shown]
        texts = given.mapped("instructions")
        plan.update(
            # Every skill in full is the same text for every question: it stays in the cached prefix
            # A list cut to the question changes from one question to the next: it goes with the skills of
            # the question, after the part of the prompt the cache keeps; a whole list is the same each time
            index=prompt.render_skills(texts) if inline else ("" if shown is not None else prompt.render_skill_index(entries)),
            text=plan["text"] + ([] if inline else (
                [prompt.render_skill_index(entries)] if shown is not None and entries else []) + list(texts)),
            given=given,
            offer={**plan["offer"],
                   "tools": sorted({name for skill in given for name in skill_rules.split_names(skill.tool_names)}),
                   "index": bool(visible) and not compact and not inline})
        return plan

    def _skill_plan_or_nothing(self, mode, compact):
        try:
            with self.env.cr.savepoint():
                return self._skill_plan(mode, compact)
        except Exception:  # noqa: BLE001 - a skill that cannot be read never stops an answer
            _logger.exception("Nudge: the skills of conversation %s could not be read", self.id)
            return {"index": "", "text": [], "given": self.env["nudge.skill"], "offer": {}}

    def _chat_tools(self, mode, tool_set):
        # The tools of the skills given with the question, and use_skill with their index
        offer = self.env.context.get("nudge_skill_offer")
        if offer is None:
            offer = self._skill_plan_or_nothing(mode, tool_set == "essential")["offer"]
        if tool_set == "full" and offer.get("route") is not None:
            # NP-03: the core of the mode, the tools of the groups the question needs and those of its
            # skills, rather than every tool; each still passes Tool.is_available (tools/base.py)
            tool_set = "core"
            names = set(offer.get("tools") or ()) | set(offer["route"])
            offer = {**offer, "tools": sorted(names | self._tools_kept(names))}
        return super(NudgeConversation, self.with_context(nudge_skills=offer))._chat_tools(mode, tool_set)

    def _tools_kept(self, names):
        """S8: a tool offered beyond the core of the mode stays offered for the rest of the conversation.
        The tools are the start of every request: a list that changed from one question to the next made
        the provider write the system prompt and the conversation after it again, rather than read them
        from its cache. :return: the tools offered before in this conversation; ``names`` join them"""
        conversation = self.sudo()
        kept = {name for name in (conversation.offered_tools or "").split(",") if name}
        if not set(names) <= kept:
            # sudo: the conversation is the person's own, as with more_tools (tool_groups)
            conversation.offered_tools = ",".join(sorted(kept | set(names)))
        return kept

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
        calls = [call for call in outcome.get("tool_calls") or () if call.get("name") == "use_skill"]
        if not calls:
            return self._chat_record_routed(outcome, super()._chat_record(outcome))
        mode = self._chat_mode()
        # What the question was given before the calls run: use_skill answers that these are there
        # already (tools/skills.py); every other skill it names joins, newly kept or kept from before
        loaded = set(self._skill_plan_or_nothing(mode, False)["given"].mapped("key"))
        step = super()._chat_record(outcome)
        if "turns" not in step or "event" in step:
            return step
        # use_skill: the skill joins the instructions from the next step of this answer, with its tools,
        # as a system block (llm/loop.py), never as a tool result; a text already there is not given twice
        self.invalidate_recordset(["skill_keys"])
        visible = self.env["nudge.skill"]._visible(mode)
        asked = set()
        for call in calls:
            arguments = call.get("input") or {}
            key = str(arguments.get("key") or "").strip().lower()
            if not key and arguments.get("work"):
                # use_skill with the work described: the same skill the tool itself chose
                found = visible._route(str(arguments["work"]).strip()).ranked
                key = found[0][0] if found else ""
            asked.add(key)
        asked -= loaded
        given = visible.filtered(lambda skill: skill.key in asked)
        if given:
            provider = self.sudo().provider_id
            step["skills"] = given.mapped("instructions")
            step["tools"] = self._chat_tools(mode, provider.tool_set if provider else "full")
            given._count_given()
        return self._chat_record_routed(outcome, step)

    def _chat_record_routed(self, outcome, step):
        """NP-03: a tool of a group that the model called without being offered it (named in the
        instructions, or known from before) ran as any call does when the person may use it; it is
        offered from the next step of this answer, with its schema, rather than called blind again"""
        if "turns" not in step or "event" in step or step.get("tools"):
            return step
        mode = self._chat_mode()
        beyond = tools.groups.routed_tools() - tools.base.CORE_BY_MODE.get(mode, set())
        if not any(call.get("name") in beyond for call in outcome.get("tool_calls") or ()):
            return step
        provider = self.sudo().provider_id
        tool_set = provider.tool_set if provider else "full"
        if tool_set == "full":
            step["tools"] = self._chat_tools(mode, tool_set)
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
        help="Send the full text of every skill and every tool with every question, as before skills: longer and "
             "costlier calls. Off: a one-line list, the full text of the skills a question needs, and the tools it needs.")
