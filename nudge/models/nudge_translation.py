"""Translating a module with the AI, with a person to review every term.

Settings people (administrators) pick an installed module and a language. Nudge lists the
terms of the module that have no translation yet (the list Odoo's own export finds), asks the
provider for them in batches, checks that the placeholders came back unchanged and that the
words of the glossary were left alone, and shows the result in a grid to accept or refuse term
by term. Accepted terms are written with Odoo's own loader; the .po file of the module can be
downloaded as well, which is the only place the strings of the code can live.
Nothing here deletes a translation."""
import json
import logging
import time

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from ..llm import base as llm_base
from ..llm import oneshot
from .nudge_run import register_job
from ..tools import translation as helper
from ..tools.deadline import until
from ..tools.records import Refused

_logger = logging.getLogger(__name__)

BATCH_SECONDS = 90
MAX_TRIES = 2


class NudgeTranslation(models.TransientModel):
    _name = "nudge.translation"
    _description = "Nudge Module Translation"

    module_id = fields.Many2one(
        "ir.module.module", string="Module", domain=[("state", "=", "installed")], required=True)
    lang_id = fields.Many2one(
        "res.lang", string="Language", required=True, context={"active_test": False},
        help="Any language. One that is not active yet is added when the terms are listed.")
    glossary = fields.Text(
        default=lambda self: self.env.company.nudge_translation_glossary,
        help="Terms that must not change in a translation, one per line: product names, brands. "
             "\"Odoo\" and \"Nudge\" always stay, and so does the name of the module.")
    state = fields.Selection([("setup", "Setup"), ("listed", "Listed")], default="setup", required=True)
    line_ids = fields.One2many("nudge.translation.line", "wizard_id", string="Terms")
    estimate_text = fields.Text(readonly=True)
    note = fields.Text(readonly=True)
    total_count = fields.Integer(compute="_compute_counts")
    todo_count = fields.Integer(compute="_compute_counts")
    proposed_count = fields.Integer(compute="_compute_counts")
    accepted_count = fields.Integer(compute="_compute_counts")
    applied_count = fields.Integer(compute="_compute_counts")

    @api.depends("line_ids.state")
    def _compute_counts(self):
        for wizard in self:
            states = [line.state for line in wizard.line_ids]
            wizard.total_count = len(states)
            wizard.todo_count = states.count("todo")
            wizard.proposed_count = states.count("proposed")
            wizard.accepted_count = states.count("accepted")
            wizard.applied_count = states.count("applied")

    # ------------------------------------------------------------------
    # Who, and what
    # ------------------------------------------------------------------
    def _check_admin(self):
        if not self.env.user.has_group("base.group_system"):
            raise AccessError(_("Only administrators translate a module."))

    def _reopen(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window", "res_model": self._name, "res_id": self.id,
            "view_mode": "form", "views": [(False, "form")], "target": "current",
        }

    def _glossary_terms(self):
        self.ensure_one()
        return helper.glossary_terms(self.glossary, self.module_id.name, self.module_id.shortdesc)

    def _language_name(self):
        return "%s (%s)" % (self.lang_id.name, self.lang_id.code)

    def _provider(self):
        """:return: the provider, or an empty recordset when there is none or private mode refuses it"""
        Provider = self.env["nudge.provider"].sudo()
        provider = Provider._get_for_mode("agent") or Provider._get_default()
        if not provider or not provider.is_configured or not provider.active:
            return Provider.browse()
        try:
            provider._check_private()
        except UserError:
            return Provider.browse()
        return provider

    def _problem_text(self, source, text):
        """:return: why ``text`` cannot stand for ``source``, in words; empty when it can"""
        words = []
        for code, detail in helper.problems(source, text, self._glossary_terms()):
            if code == "empty":
                words.append(_("Empty."))
            elif code == "missing":
                words.append(_("Missing from the translation: %s", detail))
            elif code == "extra":
                words.append(_("Not in the source: %s", detail))
            else:
                words.append(_("The term %s must stay as it is.", detail))
        return " ".join(words)

    # ------------------------------------------------------------------
    # Listing
    # ------------------------------------------------------------------
    def action_list(self):
        """List the terms of the module without a translation in the language, and say what
        translating them would cost"""
        self.ensure_one()
        self._check_admin()
        if self.line_ids:
            raise UserError(_("The terms are listed already."))
        code = self.lang_id.code
        if not self.lang_id.active:
            # Odoo reads translations of active languages only; adding one is what the person asked for
            self.env["res.lang"]._activate_lang(code)
        rows = helper.export_rows(self.env.cr, self.module_id.name, code)
        terms = [term for term in helper.group_terms(rows, self.module_id.name) if not term["translation"]]
        # The terms a record holds first: they are the ones that are written to the database
        terms.sort(key=lambda term: term["kind"] != "db")
        omitted = max(0, len(terms) - helper.MAX_TERMS)
        terms = terms[:helper.MAX_TERMS]
        self.env["nudge.translation.line"].create([{
            "wizard_id": self.id, "sequence": index, "source": term["source"], "kind": term["kind"],
            "refs": json.dumps(term["refs"], ensure_ascii=False),
        } for index, term in enumerate(terms)])
        note = _(
            "%(count)s terms of %(module)s have no %(language)s translation.",
            count=len(terms) + omitted, module=self.module_id.shortdesc or self.module_id.name,
            language=self._language_name())
        if omitted:
            note += " " + _(
                "The first %(count)s are listed; list again once they are done to get the others.",
                count=helper.MAX_TERMS)
        self.write({"state": "listed", "note": note, "estimate_text": self._estimate_text(terms)})
        return self._reopen()

    def _estimate_text(self, terms):
        if not terms:
            return _("There is nothing to translate.")
        figures = helper.estimate([(index, term["source"]) for index, term in enumerate(terms)])
        total = figures["input_tokens"] + figures["output_tokens"]
        text = _(
            "About %(tokens)s tokens in %(batches)s requests to the provider.",
            tokens=total, batches=figures["batches"])
        provider = self._provider()
        if not provider:
            if self.env["nudge.provider"]._is_private_mode():
                return text + " " + _(
                    "Private mode is on: nothing is sent to a provider that does not run on your own server.")
            return text + " " + _("No AI provider is set up yet (Settings, Nudge).")
        usage = {"input_tokens": figures["input_tokens"], "output_tokens": figures["output_tokens"],
                 "cache_read_tokens": 0, "cache_write_tokens": 0}
        cost = provider._compute_cost(usage)
        if cost:
            text += " " + _("About %s at the prices of the provider.", round(cost, 4))
        tokens_left, cost_left = self.env["nudge.usage"]._headroom()
        if (tokens_left is not None and total > tokens_left) or (
                cost_left is not None and cost and cost > cost_left):
            text += " " + _(
                "That is more than your budget allows today: Nudge translates what the budget leaves and stops.")
        return text

    # ------------------------------------------------------------------
    # Asking the provider
    # ------------------------------------------------------------------
    def action_translate(self):
        """Ask the provider for the terms in the background: a request that waited for every batch would
        outlive its worker. The person is told when it is done, and Stop on the notice ends it between two
        batches; the terms already done stay in the grid."""
        self.ensure_one()
        self._check_admin()
        from ..tools import bulk  # noqa: PLC0415
        try:
            bulk.get_provider(self.env)
        except Refused as error:
            raise UserError(str(error)) from error
        if (self.glossary or "") != (self.env.company.nudge_translation_glossary or ""):
            self.env.company.nudge_translation_glossary = self.glossary
        Run = self.env["nudge.run"]
        if not Run._job_open("translate", self.id):
            Run._start_job("translate", _(
                "Translate %(module)s into %(language)s", module=self.module_id.shortdesc or self.module_id.name,
                language=self.lang_id.name), {"id": self.id})
        self.note = _("The terms are being translated in the background. Nudge tells you when it is done.")
        return self._reopen()

    def _translate_piece(self, run=None, deadline=None):
        """Ask the provider for the next terms, batch by batch, each committed when it is done, until the
        time of the piece is up (``deadline``, monotonic) or the terms are all asked.

        :return: whether there is nothing more to ask"""
        self.ensure_one()
        from ..tools import bulk  # noqa: PLC0415
        try:
            provider = bulk.get_provider(self.env)
        except Refused as error:
            raise UserError(str(error)) from error
        Line = self.env["nudge.translation.line"]
        todo = self.line_ids.filtered(lambda line: line.state == "todo")
        system = helper.system_prompt(self._language_name(), self._glossary_terms())
        notes = []
        for batch in helper.batches([(line.id, line.source) for line in todo]):
            if run and (run._job_stopped() or (deadline and time.monotonic() > deadline - BATCH_SECONDS / 3)):
                return False  # the next piece goes on from the terms still to ask
            try:
                self.env["nudge.usage"]._check_budget()
            except UserError as error:
                notes.append(str(error))
                break
            lines = Line.browse([key for key, _text in batch])
            rows = [{"id": key, "text": text} for key, text in batch]
            started = time.monotonic()
            try:
                answer, usage, model = oneshot.ask(
                    provider._get_adapter(), system, helper.frame(rows), deadline=until(BATCH_SECONDS))
            except llm_base.AdapterError as error:
                _logger.info("Nudge: a translation batch failed: %s", error)
                notes.append(_("The provider failed: %s", str(error)))
                break
            if not any(usage.values()):
                # A provider that does not count: about four characters to a token
                usage = dict(usage, input_tokens=len(system + helper.frame(rows)) // 4,
                             output_tokens=len(answer) // 4)
            self.env["nudge.usage"]._add(
                "translation", usage, provider._compute_cost(usage), self.env.user, self.env.company,
                provider_id=provider.id, model_name=model or provider.model_name)
            found = helper.read_answers(answer, [line.id for line in lines])
            for line in lines:
                text = found.get(line.id)
                tries = line.tries + 1
                if not text:
                    line.write({"tries": tries} if tries < MAX_TRIES else {
                        "tries": tries, "state": "rejected",
                        "issue": _("The provider gave no translation for this term.")})
                    continue
                issue = self._problem_text(line.source, text)
                line.write({"translation": text, "tries": tries, "issue": issue,
                            "state": "rejected" if issue else "proposed"})
            self._batch_done(run)
        remaining = len(self.line_ids.filtered(lambda line: line.state == "todo"))
        if notes:
            notes.append(_("Nothing is lost: the terms that are done stay in the grid."))
        elif remaining:
            return False
        self.note = " ".join(notes) or _("All the terms were asked. Review them, then write them.")
        if run:
            run._say(self.note)
        return True

    def _batch_done(self, run):
        """What a batch did is kept, and the progress is told to the one who waits"""
        if not run:
            return
        left = len(self.line_ids.filtered(lambda line: line.state == "todo"))
        run.progress = _("%(done)s of %(total)s terms asked", done=len(self.line_ids) - left, total=len(self.line_ids))
        run._tell("progress")
        run._commit()

    # ------------------------------------------------------------------
    # Review and writing
    # ------------------------------------------------------------------
    def action_accept_all(self):
        self.ensure_one()
        self._check_admin()
        self.line_ids.filtered(lambda line: line.state == "proposed" and not line.issue).write({"state": "accepted"})
        return self._reopen()

    def action_apply(self):
        """Write the accepted terms into the database, with Odoo's loader"""
        self.ensure_one()
        self._check_admin()
        accepted = self.line_ids.filtered(lambda line: line.state == "accepted")
        if not accepted:
            raise UserError(_("Accept at least one term first."))
        for line in accepted:
            issue = self._problem_text(line.source, line.translation)
            if issue:
                raise UserError(_(
                    "“%(term)s” cannot be written: %(issue)s", term=line.source[:60], issue=issue))
        code = self.lang_id.code
        rows = []
        for line in accepted:
            rows += [(module, ttype, name, res_id, line.source, line.translation, comments)
                     for module, ttype, name, res_id, comments in line._refs()]
        helper.load_po(self.env.cr, helper.po_file(code, rows), code)
        self.env.invalidate_all()
        written = len(accepted.filtered(lambda line: line.kind == "db"))
        accepted.write({"state": "applied"})
        self.note = _(
            "%(db)s terms are written to the database. %(code)s terms belong to the code of the module: "
            "they are in the .po file to download.", db=written, code=len(accepted) - written)
        return self._reopen()

    def _po_rows(self):
        """:return: every term of the module as Odoo exports it, with the translations in the
        database and the ones accepted here"""
        code = self.lang_id.code
        mine = {line.source: line.translation for line in self.line_ids
                if line.state in ("accepted", "applied") and line.translation}
        rows = helper.export_rows(self.env.cr, self.module_id.name, code)
        return [(module, ttype, name, res_id, source, mine.get(source) or value, comments)
                for module, ttype, name, res_id, source, value, comments in rows]

    def action_download(self):
        """The .po file of the module in the language: what is translated already and what was accepted"""
        self.ensure_one()
        self._check_admin()
        content = helper.po_file(self.lang_id.code, self._po_rows())
        attachment = self.env["ir.attachment"].create({
            "name": "%s.po" % self.lang_id.code, "raw": content, "mimetype": "text/x-gettext-translation",
            "res_model": self._name, "res_id": self.id})
        return {"type": "ir.actions.act_url", "url": "/web/content/%s?download=true" % attachment.id,
                "target": "self"}


class NudgeTranslationLine(models.TransientModel):
    _name = "nudge.translation.line"
    _description = "Nudge Module Translation Term"
    _order = "sequence, id"

    wizard_id = fields.Many2one("nudge.translation", required=True, ondelete="cascade", index=True)
    sequence = fields.Integer()
    source = fields.Text(readonly=True)
    translation = fields.Text(string="Proposed")
    kind = fields.Selection(
        [("db", "Database"), ("code", "Code")], default="code", readonly=True,
        help="Database: a record holds the term and it is written there. Code: the term belongs to the "
             "code of the module and is only in the .po file.")
    state = fields.Selection(
        [("todo", "To Translate"), ("proposed", "Proposed"), ("accepted", "Accepted"),
         ("rejected", "Rejected"), ("applied", "Written")], default="todo", required=True)
    issue = fields.Char(readonly=True)
    tries = fields.Integer(readonly=True)
    refs = fields.Text(readonly=True)

    def _refs(self):
        return json.loads(self.refs or "[]")

    def write(self, values):
        # Changed by hand: it is checked again, and waits for a decision again
        if "translation" in values and "state" not in values and "issue" not in values:
            result = True
            for line in self:
                issue = line.wizard_id._problem_text(line.source, values["translation"])
                state = "rejected" if issue else "proposed"
                result = super(NudgeTranslationLine, line).write(dict(values, issue=issue, state=state))
            return result
        return super().write(values)

    def action_accept(self):
        for line in self:
            line.wizard_id._check_admin()
            issue = line.wizard_id._problem_text(line.source, line.translation)
            if issue:
                raise UserError(_("This translation cannot be accepted: %s", issue))
            line.state = "accepted"

    def action_reject(self):
        self.wizard_id._check_admin()
        self.state = "rejected"


register_job("translate", "nudge.translation", "_translate_piece")
