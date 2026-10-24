"""@Nudge, written in the chatter of a record or in a channel of Discuss.

The question is answered in the background, as the person who wrote it, from
what they may read of the record and of the conversation: the fields every
reader of the record sees, the records it links to, the last messages. Nothing
else is looked up: the answer is read by the whole team. It is posted where
the question was, as an internal note on a record, as a message in a channel.
Nothing is ever changed from there.
"""
import logging
import re
import time
from datetime import timedelta

from lxml import html as lxml_html
from markupsafe import Markup, escape

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from odoo.addons.nudge.llm import loop
from odoo.addons.nudge.tools import base
from odoo.addons.nudge.tools.records import (
    SKIPPED_TYPES, Refused, cut, describe, get_model, get_records, model_label, usual_fields,
)

_logger = logging.getLogger(__name__)

# Seconds a pass of the cron answers before the rest waits for the next pass
CRON_SECONDS = 60
# An answer unfinished after this long is taken up again
ANSWERING_MINUTES = 10
MAX_FIELDS = 40
MAX_LINKED = 6
MAX_MESSAGES = 10
MAX_MESSAGE_CHARS = 600
MAX_QUESTION_CHARS = 2000
# Fields of how a record is followed and talked about, not of what it is
TECHNICAL = re.compile(r"^(message_|activity_|website_message|rating_|__|has_message|access_)")
CHANNEL = "discuss.channel"
# What is asked in a few words, and Nudge does at once: the history of the record summarised
HISTORY_ASK = re.compile(
    r"^(please\s+)?(summari[sz]e|recap|summary|catch me up|what happened|where are we|"
    r"give me (a )?(summary|recap)|لخص|لخّص|ملخص)"
    r"(\s+(this|it|that|here|everything|for me|with\s+\w+|the\s+(history|record|thread|conversation|customer|order|"
    r"project|ticket)|لي|هذا|هاد|هذه))*$",
    re.IGNORECASE)
REPLY_ASK = re.compile(
    r"^(please\s+)?((draft|write|prepare|compose)(\s+me)?(\s+(a|the|an))?(\s+(formal|friendly|short))?\s+"
    r"(reply|response|answer)|reply|اكتب(\s+لي)?\s+(رد|جواب)|"
    r"جهز(\s+لي)?\s+(رد|جواب))"
    r"(\s+(to\s+(this|the\s+\w+|them|him|her|it)|for\s+me|in\s+(a\s+)?(formal|friendly|short)(\s+tone)?|"
    r"(formal|friendly|short)(\s+tone)?|على\s+\S+|رسمي|ودي|"
    r"مختصر))*$",
    re.IGNORECASE)
TONE = re.compile(r"\b(formal|friendly|short)\b|(رسمي|ودي|مختصر)", re.IGNORECASE)
TONES = {"formal": "formal", "friendly": "friendly", "short": "short",
         "رسمي": "formal", "ودي": "friendly", "مختصر": "short"}
HEADING = re.compile(r"^#{1,6}\s+(.*)$")
LIST_ITEM = re.compile(r"^([-*•]|\d+[.)])\s+")


def text_of(body):
    """:return: the words of a message, without its markup"""
    if not body:
        return ""
    try:
        return " ".join(lxml_html.fromstring(f"<div>{body}</div>").text_content().split())
    except Exception:  # noqa: BLE001
        return " ".join(str(body).split())


def asked_for(text):
    """:return: ``(kind, tone)``: what the message asks in a few words, once @Nudge is taken off:
    ``"history"`` for the record summarised, ``"reply"`` for a reply to its customer drafted (with the
    tone asked, friendly by default); ``(None, None)`` for anything else, which is a question"""
    words = " ".join(re.sub(r"[.!?؟,;:]+", " ", (text or "").replace("@Nudge", "").replace("@nudge", "")).split())
    if not words:
        return None, None
    if HISTORY_ASK.match(words):
        return "history", None
    if REPLY_ASK.match(words):
        tone = TONE.search(words)
        return "reply", TONES.get(tone.group(0).lower(), "friendly") if tone else "friendly"
    return None, None


def _goes_on(rest):
    """:return: whether a list goes on after a blank line: the next line is an
    item, or indented under the item above"""
    following = next((raw for raw in rest if raw.strip()), "")
    return bool(following) and (following[:1].isspace() or bool(LIST_ITEM.match(following.strip())))


def to_html(text):
    """Simple Markdown, as the model writes it, to the HTML of a message:
    paragraphs, lists, bold, code, links."""
    def inline(line):
        line = str(escape(line))
        line = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", line)
        # *italic* and _italic_ kept their marks in the channel; never inside a
        # word or a sum: file_name, 2*3*4
        line = re.sub(r"(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])", r"<i>\1</i>", line)
        line = re.sub(r"(?<!\w)_(?=\S)([^_\n]+?)(?<=\S)_(?!\w)", r"<i>\1</i>", line)
        line = re.sub(r"`([^`]+)`", r"<code>\1</code>", line)
        line = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2" target="_blank">\1</a>', line)
        # An address inside Odoo opens in the same tab: the "Show me" of a summary
        return re.sub(r"\[([^\]]+)\]\((/odoo/[A-Za-z0-9_./-]+)\)", r'<a href="\2">\1</a>', line)

    parts, items, kind, start = [], [], None, 1

    def close():
        nonlocal items, kind
        if items:
            # A list that goes on after a paragraph keeps the number written
            opening = f'<ol start="{start}">' if kind == "ol" and start > 1 else f"<{kind}>"
            parts.append(opening + "".join(f"<li>{item}</li>" for item in items) + f"</{kind}>")
        items, kind = [], None

    lines = (text or "").splitlines()
    for index, raw in enumerate(lines):
        line = raw.strip()
        bullet = re.match(r"^[-*•]\s+(.*)$", line)
        number = re.match(r"^(\d+)[.)]\s+(.*)$", line)
        if bullet or number:
            wanted = "ul" if bullet else "ol"
            if kind and kind != wanted:
                close()
            if not kind:
                start = int(number.group(1)) if number else 1
            kind = wanted
            items.append(inline(bullet.group(1) if bullet else number.group(2)))
            continue
        # "1. Enable", "   Go to: …", "", "2. Add" made three lists, each numbered
        # from 1: an indented line goes on with the item above it, across a blank line too
        if items and line and raw[:1].isspace() and not HEADING.match(line):
            items[-1] += "<br>" + inline(line)
            continue
        if items and not line and _goes_on(lines[index + 1:]):
            continue
        close()
        if line:
            heading = HEADING.match(line)
            parts.append(f"<p><b>{inline(heading.group(1))}</b></p>" if heading else f"<p>{inline(line)}</p>")
    close()
    return Markup("".join(parts))


class NudgeMention(models.Model):
    _name = "nudge.mention"
    _description = "Nudge Mention"
    _order = "id desc"

    message_id = fields.Many2one("mail.message", string="Question", required=True, ondelete="cascade")
    model = fields.Char(required=True)
    res_id = fields.Integer(string="Record ID", required=True)
    user_id = fields.Many2one("res.users", string="Asked By", ondelete="cascade")
    state = fields.Selection(
        [("queued", "Waiting"), ("answering", "Answering"), ("done", "Answered"), ("refused", "Not for Them"), ("failed", "Failed")],
        default="queued", required=True, index=True)
    answer_id = fields.Many2one("mail.message", string="Answer", ondelete="set null")
    conversation_id = fields.Many2one("nudge.conversation", ondelete="set null")
    error = fields.Char()

    @api.model
    def _queue(self, message, thread, user):
        self.create({
            "message_id": message.id,
            "model": thread._name,
            "res_id": thread.id,
            "user_id": user.id,
        })
        self.env.ref("nudge_discuss.cron_mentions").sudo()._trigger()

    @api.model
    def _cron_answer(self):
        keep = self.env.context.get("nudge_keep_transaction")
        # A worker killed in the middle of an answer leaves the mention "answering": it goes
        # back to the queue after a while, instead of blocking the front of it
        old = fields.Datetime.now() - timedelta(minutes=ANSWERING_MINUTES)
        self.search([("state", "=", "answering"), ("write_date", "<", old)]).write({"state": "queued"})
        started = time.monotonic()
        while True:
            mention = self.search([("state", "=", "queued")], order="id", limit=1)
            if not mention:
                break
            # Marked and committed before the (slow) answer, so a kill does not repeat it first
            mention.state = "answering"
            if not keep:
                self.env.cr.commit()
            mention._answer()
            if not keep:
                self.env["ir.cron"]._commit_progress(1)
            if time.monotonic() - started > CRON_SECONDS:
                self.env.ref("nudge_discuss.cron_mentions").sudo()._trigger()
                break

    # ------------------------------------------------------------------
    # Answered as the person who asked
    # ------------------------------------------------------------------
    def _as_person(self):
        user = self.user_id
        context = dict(user.with_user(user).context_get())
        # All their companies, the one of the record first (then their own): env.company is what the answer
        # reads totals and currencies in
        record = self.env[self.model].sudo().browse(self.res_id) if self.model in self.env else None
        company = record.exists().company_id if record is not None and "company_id" in record._fields else None
        first = (company or self.env["res.company"]) & user.company_ids
        context["allowed_company_ids"] = (first | user.company_id | user.company_ids).ids
        return self.env(user=user.id, su=False, context=context)

    def _lang(self):
        """:return: the language of the person who asked: what Nudge posts is
        read by them, not by the background job that posts it"""
        return (self.user_id.lang or self.message_id.author_id.lang
                or self.env.company.partner_id.lang or self.env.lang or "en_US")

    def _told(self, error=None):
        """:return: what Nudge posts when it does not answer; called on the
        mention in the language of the one who asked"""
        if error is None:
            return self.env._("Nudge is not open to the person who asked.")
        return self.env._("Nudge could not answer: %s", error)

    def _answer(self):
        self.ensure_one()
        user = self.user_id
        told = self.with_context(lang=self._lang())
        if not user or not user._is_internal() or not user.has_group("nudge.group_nudge_user"):
            self.state = "refused"
            self._post(told._told(), notify=False)
            return
        env = self._as_person()
        try:
            with self.env.cr.savepoint():
                kind, tone = asked_for(text_of(self.message_id.body)) if self.model != CHANNEL else (None, None)
                if kind:
                    # A few words that ask for one thing: Nudge does it, as the person, and says it here
                    record = get_records(get_model(env, self.model), [self.res_id], "read")
                    self.answer_id = self._post(self._done(env, kind, tone, record))
                    self.state = "done"
                    return
                context = self._context_of(env)
                question = cut(text_of(self.message_id.body).replace("@Nudge", "").strip(), MAX_QUESTION_CHARS)
                question = question or env._("Summarise this for me.")
                conversation = env["nudge.conversation"].create({"name": cut(f"@Nudge: {context['title']}", 60)})
                job = conversation._chat_prepare(question, focus="mention", extra=context["block"])
                for _event in loop.run(job, conversation._chat_record):
                    pass
                answer = env["nudge.message"].sudo().search([
                    ("conversation_id", "=", conversation.id),
                    ("role", "=", "assistant"),
                    ("content", "!=", False),
                ], order="id desc", limit=1)
                if not answer.content:
                    last = env["nudge.message"].sudo().search(
                        [("conversation_id", "=", conversation.id)], order="id desc", limit=1)
                    raise UserError(last.error_message or env._("Nudge had no answer."))
                self.write({"conversation_id": conversation.id})
            self.answer_id = self._post(answer.content)
            self.state = "done"
        except (UserError, AccessError, Refused) as error:
            self.write({"state": "failed", "error": cut(str(error), 300)})
            self._post(told._told(error))
        except Exception as error:  # noqa: BLE001
            _logger.exception("Nudge: mention %s failed", self.id)
            self.write({"state": "failed", "error": cut(str(error), 300)})

    def _done(self, env, kind, tone, record):
        """:return: what is posted for a request made in a few words: the history of the record
        summarised, or the draft of a reply to its customer. A draft is only text here, and a hint at
        where it is read and sent: Nudge never sends it."""
        if kind == "history":
            return env["nudge.history"]._summary(record)["text"]
        draft = env["nudge.reply"]._draft(record, tone)
        hint = (env._("To edit it and send it, open Nudge on this record and press Draft a reply. It is sent only "
                      "when you press Send.") if "nudge.email.review" in env
                else env._("Copy it into your reply: Nudge never sends it."))
        return "\n\n".join((
            env._("**Draft of a reply, not sent**"),
            env._("To: %(to)s", to=draft["to"]) + "\n" + env._("Subject: %(subject)s", subject=draft["subject"]),
            draft["text"],
            hint,
        ))

    def _context_of(self, env):
        """:return: ``{"title", "block"}``: what the person may read of the
        record and of the conversation, as a block of the message"""
        if self.model == CHANNEL:
            channel = env[CHANNEL].browse(self.res_id).exists()
            if not channel:
                raise UserError(env._("This channel no longer exists."))
            payload = {"channel": channel.display_name, "messages": self._messages(env)}
            title = channel.display_name
        else:
            model = get_model(env, self.model)
            record = get_records(model, [self.res_id], "read")
            payload = {
                "kind": model_label(model),
                "record": record.display_name,
                "fields": self._fields_of(env, record),
                "linked": self._linked(env, record),
                "messages": self._messages(env),
            }
            title = record.display_name
        text = base.dump(payload).replace("</nudge_record", "<\\/nudge_record")
        return {"title": title or "", "block": f"<nudge_record>\n{text}\n</nudge_record>"}

    @staticmethod
    def _shared_fields(record):
        """:return: the fields every reader of the record sees: no group keeps them"""
        available = record.fields_get(attributes=["string", "type"])
        names = []
        for name, field in record._fields.items():
            if (name in available and not field.groups and field.type not in SKIPPED_TYPES
                    and field.type != "one2many" and name != "id" and not TECHNICAL.match(name)):
                names.append(name)
        usual = [name for name in usual_fields(record) if name in names]
        ordered = usual + [name for name in names if name not in usual]
        return ordered[:MAX_FIELDS], available

    def _fields_of(self, env, record):
        names, available = self._shared_fields(record)
        row = describe(env, record, names)[0]
        return {available[name]["string"]: row[name] for name in names
                if name in row and row[name] not in (False, None, "", [])}

    def _linked(self, env, record):
        """:return: the records it points to, as the person reads them"""
        linked = {}
        names, available = self._shared_fields(record)
        for name in names:
            field = record._fields[name]
            if field.type != "many2one" or not record[name] or len(linked) >= MAX_LINKED:
                continue
            target = record[name]
            try:
                shown = [field_name for field_name in usual_fields(target) if not target._fields[field_name].groups]
                linked[available[name]["string"]] = describe(env, target, shown)[0]
            except (AccessError, Refused, UserError):
                continue
        return linked

    def _messages(self, env):
        """:return: the last messages of the conversation, the oldest first"""
        messages = env["mail.message"].search([
            ("model", "=", self.model),
            ("res_id", "=", self.res_id),
            ("message_type", "in", ("comment", "email")),
            ("id", "!=", self.message_id.id),
        ], order="id desc", limit=MAX_MESSAGES)
        return [{
            "from": message.author_id.display_name or "",
            "date": fields.Datetime.to_string(message.date),
            "text": cut(text_of(message.body), MAX_MESSAGE_CHARS),
        } for message in reversed(messages) if text_of(message.body)]

    def _post(self, text, notify=True):
        """Post the answer where the question was, as Nudge.
        :return: the message posted"""
        nudge = self.env.ref("nudge_discuss.partner_nudge")
        thread = self.env[self.model].sudo().browse(self.res_id).exists()
        if not thread:
            return self.env["mail.message"]
        body = to_html(text)
        if self.model == CHANNEL:
            return thread.message_post(
                body=body, author_id=nudge.id, message_type="comment", subtype_xmlid="mail.mt_comment")
        asker = self.message_id.author_id
        return thread.with_context(mail_create_nosubscribe=True).message_post(
            body=body, author_id=nudge.id, message_type="comment", subtype_xmlid="mail.mt_note",
            partner_ids=asker.ids if notify and asker else [], parent_id=self.message_id.id)
