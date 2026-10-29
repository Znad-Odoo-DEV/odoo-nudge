"""S9: the questions people keep asking the AI become rules that cost nothing.

A daily job reads the turns the AI answered in the last thirty days. Turns of one company that say
the same thing (the same sentence once its changing parts, a name or a number, are cut out) and that
the AI answered with the same one tool and the same shape of arguments are a group. A group that
reaches the threshold of the recurring questions becomes a proposal for the managers: the patterns of
the sentence with the changing parts as ``{slots}``, the tool and the template of its arguments.

Accepted, a proposal for a tool that only reads is a rule of the no-AI router: the next sentence that
fits a pattern fills the template and the tool runs as the person, with no provider. A tool that writes
never becomes a rule: it becomes a prompt of the library, for the person to send. What is kept is the
pattern of the sentence and the shape of the arguments, never a value that was asked, never an answer
or a record; turns of private mode and commands are never read; each company has its own."""
import json
import logging
from collections import Counter
from datetime import timedelta

from psycopg2 import OperationalError

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from ..llm import fingerprint
from ..tools import base
from ..tools.noai_commands import learned
from ..tools.noai_commands.grammar import Parse

_logger = logging.getLogger(__name__)

WINDOW_DAYS = 30
SCANNED_QUESTIONS = 5000
MAX_PATTERNS = 5
MIN_PATTERN_TIMES = 2
MAX_RULES_READ = 300
OBSERVE_FROM_PARAM = "nudge.rules_observe_from"
# The reading tools whose result the router can say on its own
RULE_TOOLS = frozenset({"aggregate_records", "search_records", "open_screen", "filter_screen"})
SHOWN_ROWS = 10


class NudgeMessage(models.Model):
    _inherit = "nudge.message"

    private_mode = fields.Boolean(
        readonly=True, copy=False,
        help="The company asked that nothing of its data reaches an outside provider when this was said. "
             "Such a turn is never read to find the questions that come back.")

    @api.model_create_multi
    def create(self, vals_list):
        private = self.env["nudge.provider"]._is_private_mode()
        return super().create([dict(vals, private_mode=vals.get("private_mode", private)) for vals in vals_list])


class NudgeRule(models.Model):
    _name = "nudge.rule"
    _description = "Nudge Learned Rule"
    _order = "state, times desc, id"
    _rec_name = "name"

    if hasattr(models, "Constraint"):
        _unique_rule = models.Constraint("unique(company_id, key)", "A request is proposed once in a company.")
    else:  # Odoo 17 and 18
        _sql_constraints = [("unique_rule", "unique(company_id, key)", "A request is proposed once in a company.")]

    name = fields.Char(string="Request", readonly=True, help="One way the request is written, with its changing parts as {slots}.")
    company_id = fields.Many2one(
        "res.company", required=True, index=True, ondelete="cascade", default=lambda self: self.env.company)
    key = fields.Char(required=True, index=True, readonly=True)
    kind = fields.Selection(
        [("rule", "Rule without AI"), ("prompt", "Prompt of the library")], required=True, readonly=True,
        help="A tool that only reads becomes a rule that answers with no AI. One that writes becomes a prompt.")
    state = fields.Selection(
        [("proposed", "To Decide"), ("accepted", "Accepted"), ("rejected", "Rejected")],
        default="proposed", required=True, index=True, readonly=True)
    tool_name = fields.Char(string="Tool", readonly=True)
    template = fields.Json(readonly=True, help="The arguments of the tool, with the changing parts as slots.")
    slots = fields.Json(readonly=True)
    patterns = fields.Json(string="Pattern List", readonly=True, help="The ways the request was written, the changing parts cut out.")
    patterns_text = fields.Text(string="Patterns", compute="_compute_patterns_text")
    fingerprint = fields.Char(readonly=True, index=True)
    times = fields.Integer(string="Times (30 Days)", readonly=True)
    user_count = fields.Integer(string="People", readonly=True, help="How many different people asked, not who.")
    last_seen = fields.Date(readonly=True)
    example = fields.Text(readonly=True, help="What the request does, in words: never an answer or a value.")
    use_count = fields.Integer(string="Times Answered", readonly=True, copy=False, default=0)
    last_used = fields.Datetime(readonly=True)
    decided_by = fields.Many2one("res.users", readonly=True, ondelete="set null")
    decided_on = fields.Datetime(readonly=True)
    prompt_id = fields.Many2one("nudge.prompt", readonly=True, ondelete="set null")

    @api.depends("patterns")
    def _compute_patterns_text(self):
        for rule in self:
            rule.patterns_text = "\n".join(rule.patterns or [])

    @api.model
    def _check_manager(self):
        if not self.env.user.has_group("nudge.group_nudge_manager") and not self.env.is_superuser():
            raise AccessError(_("Only Nudge managers decide on the proposed rules."))

    # ------------------------------------------------------------------
    # Observing
    # ------------------------------------------------------------------
    @api.model
    def _min_count(self):
        return self.env["nudge.question"]._min_count()

    @api.model
    def _field_kind(self, model_name, field):
        """:return: "fixed" for a field whose value is part of the question (a state, a yes or no, a date),
        "free" for what the person says (a name, a record, a figure)"""
        name = str(field or "")
        if not model_name or model_name not in self.env or "." in name:
            return "free"
        model = self.env[model_name]
        if name not in model._fields:
            return "free"
        return "fixed" if model._fields[name].type in ("selection", "boolean", "date", "datetime") else "free"

    @api.model
    def _kind_of_tool(self, tool_name):
        """:return: "rule" for a tool that only reads and can be replayed, "prompt" for one that changes
        something, or None for any other"""
        tool = base.TOOLS.get(tool_name)
        if tool is None:
            return None
        if tool.kind not in ("read", "show"):
            return "prompt"
        return "rule" if tool_name in RULE_TOOLS else None

    @api.model
    def _turns(self, company, since):
        """:return: ``[(user message, [assistant messages of the turn])]`` of the questions of ``company`` said
        since ``since`` that nothing hid: no command, no file, no private mode, Talk or Agent"""
        Message = self.env["nudge.message"].sudo()
        # sudo: Nudge's own log, read by a job that keeps no text of it
        asked = Message.search([
            ("role", "=", "user"), ("create_date", ">=", since), ("conversation_id.company_id", "=", company.id),
            ("command", "=", False), ("attachment_name", "=", False), ("private_mode", "=", False),
            ("mode", "in", ("talk", "agent"))], order="id desc", limit=SCANNED_QUESTIONS)
        if not asked:
            return []
        thread = Message.search([
            ("conversation_id", "in", asked.conversation_id.ids), ("role", "in", ("user", "assistant")),
            ("id", ">=", min(asked.ids))], order="conversation_id, id")
        wanted, turns, current = set(asked.ids), [], None
        for message in thread:
            if message.role == "user":
                current = (message, []) if message.id in wanted else None
                if current:
                    turns.append(current)
            elif current and message.conversation_id == current[0].conversation_id:
                current[1].append(message)
        return turns

    @api.model
    def _group(self, company, since, today):
        """:return: ``{(fingerprint, tool, template hash): group}`` of what the AI answered in ``company`` since ``since``"""
        groups = {}
        for question, answers in self._turns(company, since):
            if any(answer.noai_intent or answer.private_mode for answer in answers):
                continue  # already answered by rules: nothing to save
            if not any(answer.state == "complete" and answer.provider_kind for answer in answers):
                continue
            calls = [call for answer in answers for call in answer.tool_calls or []]
            if len(calls) != 1 or not isinstance(calls[0], dict):
                continue
            tool, kind = calls[0].get("name"), self._kind_of_tool(calls[0].get("name"))
            if not kind:
                continue
            fitted = learned.fit(
                tool, calls[0].get("input"), question.content, question.create_date.date() if question.create_date else today,
                field_kind=self._field_kind, all_leaves=kind == "prompt")
            if not fitted:
                continue
            shape = learned.template_hash(fitted["template"]) if kind == "rule" else "-"
            group = groups.setdefault((fitted["key"], tool, shape), {
                "kind": kind, "tool": tool, "template": fitted["template"] if kind == "rule" else False,
                "slots": fitted["slots"], "patterns": Counter(), "users": set(), "times": 0, "last": None})
            group["patterns"][fitted["pattern"]] += 1
            group["users"].add(question.user_id.id)
            group["times"] += 1
            day = question.create_date.date()
            group["last"] = max(group["last"], day) if group["last"] else day
        return self._merge_readers(groups)

    @api.model
    def _merge_readers(self, groups):
        """One sentence the AI answered with a reading tool is one request, however it wrote the call each
        time (a count one time, a search the next, "=" or "contains" on a name): the variants of a
        sentence are counted together and the one used most often is the one that is proposed.
        :return: ``groups`` with the variants of each sentence merged into the most used one"""
        merged, variants = {}, {}
        for name, group in groups.items():
            if group["kind"] == "rule":
                variants.setdefault(name[0], []).append((name, group))
            else:
                merged[name] = group
        for found in variants.values():
            found.sort(key=lambda item: (item[1]["times"], item[1]["last"]), reverse=True)
            (name, best), others = found[0], found[1:]
            best = dict(best, patterns=Counter(best["patterns"]), users=set(best["users"]))
            for _other_name, other in others:
                # a variant that fills other slots would say a sentence the template cannot take
                if [slot["name"] for slot in other["slots"]] == [slot["name"] for slot in best["slots"]]:
                    best["patterns"].update(other["patterns"])
                best["users"] |= other["users"]
                best["times"] += other["times"]
                best["last"] = max(best["last"], other["last"])
            merged[name] = best
        return merged

    @api.model
    def _describe(self, tool_name, template):
        """:return: what the tool does with this template, in words and without any value"""
        if not template:
            return _("A request that changes data: kept as a prompt of the library.")
        parts = []
        model_name = template.get("model")
        label = self.env[model_name]._description if model_name in self.env else (model_name or "")
        for condition in template.get("conditions") or []:
            value = condition.get("value")
            shown = "{%s}" % value["$slot"] if isinstance(value, dict) and "$slot" in value else (
                _("today") if isinstance(value, dict) else str(value))
            parts.append("%s %s %s" % (condition.get("field"), condition.get("operator"), shown))
        what = {
            "aggregate_records": _("Counts or sums %s", label),
            "search_records": _("Lists %s", label),
            "open_screen": _("Opens a screen"),
            "filter_screen": _("Filters the screen of %s", label),
        }.get(tool_name, tool_name)
        return "%s%s" % (what, " (%s)" % "; ".join(parts) if parts else "")

    @api.model
    def _cron_observe(self):
        """Every night: the questions that come back, company by company. Nothing is asked of a provider."""
        Company = self.env["res.company"].sudo()
        for company in Company.search([]):
            try:
                with self.env.cr.savepoint():
                    self.with_company(company)._observe(company)
            except Exception:  # noqa: BLE001
                _logger.warning("Nudge: the recurring requests of a company could not be read", exc_info=True)
        return True

    @api.model
    def _observe(self, company):
        """Propose the groups of ``company`` that reached the threshold. :return: the proposals made or updated"""
        done = self.browse()
        if self.env["nudge.provider"]._is_private_mode():
            return done
        now = fields.Datetime.now()
        Param = self.env["ir.config_parameter"].sudo()
        # The turns said before this was switched on were never meant to be read for this: they are not
        started = Param.get_param(OBSERVE_FROM_PARAM)
        if not started:
            started = fields.Datetime.to_string(now - timedelta(days=WINDOW_DAYS))
            Param.set_param(OBSERVE_FROM_PARAM, started)
        since = max(now - timedelta(days=WINDOW_DAYS), fields.Datetime.to_datetime(started))
        Rule = self.sudo().with_context(active_test=False)
        # sudo: Nudge's own table, written by the job as infrastructure; the person who asked is never recorded
        for (key, tool, shape), group in self._group(company, since, fields.Date.context_today(self)).items():
            if group["times"] < self._min_count():
                continue
            patterns = [pattern for pattern, times in group["patterns"].most_common(MAX_PATTERNS)
                        if times >= MIN_PATTERN_TIMES]
            if not patterns:
                continue  # a different sentence each time: nothing stable to learn
            rule_key = "%s:%s:%s" % (key, tool, shape)
            values = {
                "patterns": patterns, "times": group["times"], "user_count": len(group["users"]), "last_seen": group["last"],
                "name": patterns[0][:200]}
            rule = Rule.search([("company_id", "=", company.id), ("key", "=", rule_key)], limit=1)
            if not rule and group["kind"] == "rule":
                # the AI wrote the call another way this time: still the request proposed (or decided) before
                rule = Rule.search([("company_id", "=", company.id), ("fingerprint", "=", key), ("kind", "=", "rule")],
                                   limit=1)
            if rule:
                if rule.state == "proposed":
                    rule.write(values)
                    done |= rule
                continue
            done |= Rule.create(dict(values, **{
                "company_id": company.id, "key": rule_key, "kind": group["kind"], "tool_name": tool,
                "template": group["template"], "slots": group["slots"], "fingerprint": key,
                "example": self._describe(tool, group["template"])}))
        return done

    # ------------------------------------------------------------------
    # Deciding
    # ------------------------------------------------------------------
    def action_accept(self):
        """A rule that reads answers the next identical request with no AI; one that writes is kept as a prompt"""
        self._check_manager()
        for rule in self.filtered(lambda r: r.state == "proposed"):
            if rule.kind == "rule":
                if rule.tool_name not in RULE_TOOLS or self._kind_of_tool(rule.tool_name) != "rule":
                    raise UserError(_("Only a tool that reads can answer on its own."))
                values = {}
            else:
                body = learned._SLOT.sub(lambda found: "{{%s}}" % found.group(1), rule.patterns[0])
                prompt = self.env["nudge.prompt"].create({
                    "name": rule.patterns[0][:80], "body": body, "mode": "agent", "scope": "company",
                    "company_id": rule.company_id.id, "origin": "ui",
                    "description": _("Asked again and again: kept for you to send, never done on its own.")})
                values = {"prompt_id": prompt.id}
            rule.sudo().write(dict(values, state="accepted", decided_by=self.env.user.id, decided_on=fields.Datetime.now()))
        return True

    def action_reject(self):
        """Hide the proposal: it is not proposed again"""
        self._check_manager()
        self.sudo().filtered(lambda r: r.state != "rejected").write(
            {"state": "rejected", "decided_by": self.env.user.id, "decided_on": fields.Datetime.now()})
        return True

    def action_reopen(self):
        """Bring a rejected proposal back to the list"""
        self._check_manager()
        self.sudo().filtered(lambda r: r.state == "rejected").write(
            {"state": "proposed", "decided_by": False, "decided_on": False})
        return True

    def action_observe_now(self):
        """Look for the questions that come back now, for the companies of the manager"""
        self._check_manager()
        for company in self.env.companies:
            self.with_company(company)._observe(company)
        return True

    # ------------------------------------------------------------------
    # Answering
    # ------------------------------------------------------------------
    @api.model
    def _matching(self, text):
        """:return: ``(rule, values)`` of the accepted rule of the company in use that says what ``text`` says, or
        ``(empty, {})``"""
        none = (self.browse(), {})
        # sudo: the rules are the company's and are read to decide; what they run is run as the person
        rules = self.sudo().search([
            ("company_id", "=", self.env.company.id), ("kind", "=", "rule"), ("state", "=", "accepted")],
            order="use_count desc, id", limit=MAX_RULES_READ)
        if not rules:
            return none
        key = fingerprint.of(text)
        for rule in rules:
            for pattern in rule.patterns or []:
                values = learned.match(pattern, rule.slots, text)
                if values is not None:
                    return rule, values
            if key and not rule.slots and key == rule.fingerprint:
                return rule, {}
        return none

    @api.model
    def _plan_for(self, text, screen, mode, noai):
        """:param noai: the ``nudge.noai`` model, for its Plan and its tool calls
        :return: the plan answering ``text`` through a rule, or None when no rule says it, the tool is not
        the person's to use, or it gives nothing"""
        from .nudge_noai import Plan  # noqa: PLC0415 - nudge_noai imports nothing of this file
        rule, values = self._matching(text)
        if not rule:
            return None
        tool = base.TOOLS.get(rule.tool_name)
        if tool is None or not tool.is_available(self.env, mode):
            _logger.info("Nudge: the tool of a learned rule is not open to this person")
            return None
        arguments = learned.fill(rule.template, values, fields.Date.context_today(self))
        if arguments is None:
            return None
        plan = Plan("", parsed=self._parsed(text))
        if tool.terminal:
            plan.calls = [(rule.tool_name, arguments)]
            plan.text = _("Done.")
        else:
            result = noai._call(rule.tool_name, arguments, screen, mode)
            if result.is_error:
                _logger.info("Nudge: a learned rule could not run its tool: %s", result.content[:200])
                return None
            plan.text = self._render(rule.tool_name, result)
            plan.events = [result.event] if result.event else []
        # Never waits and never fails, as the counters of the memory and of the answer cache: a rule another
        # answer is counting at this instant (SKIP LOCKED), or counted since this one began (a serialization
        # error under repeatable read), is not counted this time; the figure is an indication
        try:
            with self.env.cr.savepoint():
                self.env.cr.execute(
                    "UPDATE nudge_rule SET use_count = COALESCE(use_count, 0) + 1, "
                    "last_used = now() AT TIME ZONE 'UTC' "
                    "WHERE id IN (SELECT id FROM nudge_rule WHERE id = %s FOR UPDATE SKIP LOCKED)",
                    [rule.id], log_exceptions=False)
        except OperationalError:
            _logger.info("Nudge: rule %s counted by another answer at the same time, left uncounted", rule.id)
        self.env["nudge.rule"].invalidate_model(["use_count", "last_used"])
        return plan

    @api.model
    def _parsed(self, text):
        parsed = Parse(text)
        parsed.intent, parsed.confidence = "rule", 1.0
        return parsed

    @api.model
    def _render(self, tool_name, result):
        """:return: what a tool read, in words: figures and names, as the person reads them"""
        try:
            data = json.loads(result.content)
        except ValueError:
            return result.content
        model_name = data.get("model")
        label = self.env[model_name]._description if model_name in self.env else (model_name or "")
        if tool_name == "aggregate_records":
            rows = data.get("rows") or []
            if not rows:
                return _("I found nothing to count.")
            lines = []
            for row in rows[:SHOWN_ROWS]:
                names = [str(value) for key, value in row.items() if key not in data.get("measures", []) and key != "count"]
                figures = ", ".join("%s" % value for key, value in row.items() if key in data.get("measures", []) or key == "count")
                lines.append("%s: %s" % (" / ".join(names), figures) if names else "%s: %s" % (label, figures))
            text = "\n".join(lines)
            return "%s\n%s" % (text, _("Showing: %s.", data["showing"])) if data.get("showing") else text
        rows = data.get("records") or []
        names = [str(row.get("display_name") or row.get("name") or row.get("id")) for row in rows[:SHOWN_ROWS]]
        head = _("%(total)s %(what)s", total=data.get("total", len(rows)), what=label)
        return "%s: %s" % (head, ", ".join(names)) if names else _("I found nothing to list.")

    @api.autovacuum
    def _gc_stale(self):
        """Nothing here grows without end: proposals nobody decided in ninety days are dropped"""
        old = fields.Datetime.now() - timedelta(days=90)
        self.sudo().search([("state", "=", "proposed"), ("write_date", "<", old)]).unlink()
