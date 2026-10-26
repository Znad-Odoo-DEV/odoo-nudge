"""Rules coverage: the sentences people asked that the rules could not answer.

The counted questions (``nudge.question``) of the last seven days are read by the
same rules that answer the Agent without any AI, with the words the company
added. Those the rules do not understand are listed, the most asked first, with
one click to teach the rules the word (a command word of the company) or to keep
an answer for the sentence. Only the redacted sample of a question is read, never
who asked, and no provider is called. A word is added by a Nudge manager, who
is told what the sentence means once it is added."""
import json
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from ..llm import fingerprint
from ..tools import noai_commands, skill_router
from ..tools.noai_commands import grammar, normalise

WINDOW_DAYS = 7
SCANNED = 300
SHOWN = 30
MIN_WORD_CHARS = 2


class NudgeCoverage(models.TransientModel):
    _name = "nudge.coverage"
    _description = "Sentences the Rules Missed"
    _order = "times desc, id"
    _rec_name = "sample"

    question_id = fields.Many2one("nudge.question", readonly=True, ondelete="cascade")
    company_id = fields.Many2one("res.company", readonly=True)
    sample = fields.Char(string="Sentence", readonly=True)
    times = fields.Integer(string="Last 7 Days", readonly=True)
    phrase = fields.Char(
        string="Word or Phrase",
        help="The word the rules did not know. Nudge will understand it like the words it knows.")
    intent = fields.Selection(
        lambda self: self.env["nudge.lexicon.entry"]._intents(), string="Means",
        help="The word of Nudge's own list this one means the same as.")
    state = fields.Selection([("missed", "Missed"), ("added", "Word Added")], default="missed", readonly=True)
    result = fields.Char(string="Now Understood As", readonly=True)

    @api.model
    def _check_manager(self):
        if not self.env.user.has_group("nudge.group_nudge_manager") and not self.env.is_superuser():
            raise AccessError(_("Only Nudge managers improve the words Nudge understands."))

    @api.model
    def _too_generic(self, phrase):
        """:return: whether ``phrase`` is one word that says nothing of the work (“do”, “the”, “make”): as a
        command word it would turn most sentences into that command"""
        words = normalise.tokenize(phrase)
        return len(words) == 1 and (
            words[0].raw.lower() in skill_router.STOP or words[0].norm in skill_router.STOP)

    @api.model
    def _unknown_word(self, text):
        """:return: the first word of ``text`` that nothing in the lexicon explains, as written, or ''"""
        tokens = normalise.tokenize(text)
        for span in grammar.scan(tokens):
            if span.cats or span.j - span.i != 1:
                continue
            word = tokens[span.i].raw
            if (len(word) >= MIN_WORD_CHARS and not word.isdigit() and "@" not in word
                    and not self._too_generic(word)):
                return word
        return ""

    @api.model
    def _guess_intent(self, parsed, text):
        """:return: the word of Nudge's list a missed sentence most likely means, or '' when it is not clear:
        its nearest reading, or "open" for a sentence that names a kind of record"""
        for candidate in parsed.candidates or []:
            if candidate.get("intent") in noai_commands.lexicon.CATEGORIES:
                return candidate["intent"]
        spans = grammar.scan(normalise.tokenize(text))
        if any(span.cats & set(grammar.KINDS) for span in spans):
            return "open"
        return ""

    @api.model
    def _missed(self, company_ids):
        """:return: ``[(question, times in the window, parsed)]`` of the sentences the rules do not understand,
        the most asked first"""
        today = fields.Date.context_today(self)
        first = (today - timedelta(days=WINDOW_DAYS - 1)).isoformat()
        rows = self.env["nudge.question"].search([
            ("company_id", "in", company_ids), ("status", "not in", ("muted", "covered")),
            ("last_asked", ">=", fields.Datetime.now() - timedelta(days=WINDOW_DAYS)),
        ], order="recent_count desc, id", limit=SCANNED)
        out = []
        Rule = self.env["nudge.rule"]
        with self.env["nudge.noai"]._company_words():
            for row in rows:
                try:
                    days = json.loads(row.daily or "{}")
                except ValueError:
                    days = {}
                times = sum(int(number) for day, number in days.items() if day >= first)
                if not times or not row.sample:
                    continue
                parsed = noai_commands.parse(row.sample, {})
                if parsed.intent and parsed.confidence >= noai_commands.THRESHOLD:
                    continue
                # S9: a rule the managers accepted from the requests that came back answers it
                if Rule.with_company(row.company_id)._matching(row.sample)[0]:
                    continue
                out.append((row, times, parsed))
        out.sort(key=lambda item: (-item[1], item[0].id))
        return out[:SHOWN]

    @api.model
    def action_open(self):
        """The page: the missed sentences of the last seven days, counted now, for the manager who opens it"""
        self._check_manager()
        # The lines are the manager's own and short-lived: the previous ones are dropped
        self.search([("create_uid", "=", self.env.uid)]).unlink()
        for question, times, parsed in self._missed(self.env.companies.ids):
            text = fingerprint.sample(question.sample)
            self.create({
                "question_id": question.id, "company_id": question.company_id.id, "sample": text, "times": times,
                "phrase": self._unknown_word(text), "intent": self._guess_intent(parsed, text) or False})
        action = self.env["ir.actions.actions"]._for_xml_id("nudge.nudge_coverage_action")
        action["domain"] = [("create_uid", "=", self.env.uid)]
        return action

    def action_add_entry(self):
        """Teach the rules the word: a command word of the company, then say what the sentence means now"""
        self.ensure_one()
        self._check_manager()
        phrase = " ".join((self.phrase or "").split())
        if not phrase or not self.intent:
            raise UserError(_("Write the word and choose what it means."))
        if self._too_generic(phrase):
            raise UserError(_(
                "“%s” is too common a word to teach: write the word that only this request uses.", phrase))
        Entry = self.env["nudge.lexicon.entry"]
        key = normalise.phrase_key(phrase)
        known = Entry.search([("company_id", "=", self.company_id.id), ("intent", "=", self.intent)]).filtered(
            lambda entry: normalise.phrase_key(entry.synonym) == key)
        if not known:
            Entry.create({"synonym": phrase, "intent": self.intent, "company_id": self.company_id.id})
        with self.env["nudge.noai"]._company_words():
            parsed = noai_commands.parse(self.sample, {})
        sure = bool(parsed.intent and parsed.confidence >= noai_commands.THRESHOLD)
        result = self.env["nudge.noai"]._describe(parsed) if sure else _("Not yet: it needs more words")
        self.write({"state": "added", "result": result})
        return {
            "type": "ir.actions.client", "tag": "display_notification",
            "params": {
                "title": _("Word added"), "type": "success" if sure else "warning", "sticky": False,
                "message": _("“%(sentence)s” now means: %(result)s", sentence=self.sample, result=result),
                "next": {"type": "ir.actions.client", "tag": "soft_reload"}},
        }

    def action_keep_answer(self):
        """Save an answer for the sentence: the form of a remembered answer, as the recurring questions do"""
        self.ensure_one()
        self._check_manager()
        return self.question_id.action_keep_answer()
