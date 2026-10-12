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

from lxml import html as lxml_html
from markupsafe import Markup, escape

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from odoo.addons.nudge.llm import loop
from odoo.addons.nudge.tools import base
from odoo.addons.nudge.tools.records import (
    SKIPPED_TYPES, Refused, cut, describe, get_model, get_records, model_label, usual_fields,
)

_logger = logging.getLogger(__name__)

MAX_FIELDS = 40
MAX_LINKED = 6
MAX_MESSAGES = 10
MAX_MESSAGE_CHARS = 600
MAX_QUESTION_CHARS = 2000
# Fields of how a record is followed and talked about, not of what it is
TECHNICAL = re.compile(r"^(message_|activity_|website_message|rating_|__|has_message|access_)")
CHANNEL = "discuss.channel"


def text_of(body):
    """:return: the words of a message, without its markup"""
    if not body:
        return ""
    try:
        return " ".join(lxml_html.fromstring(f"<div>{body}</div>").text_content().split())
    except Exception:  # noqa: BLE001
        return " ".join(str(body).split())


def to_html(text):
    """Simple Markdown, as the model writes it, to the HTML of a message:
    paragraphs, lists, bold, code, links."""
    def inline(line):
        line = str(escape(line))
        line = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", line)
        line = re.sub(r"`([^`]+)`", r"<code>\1</code>", line)
        return re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2" target="_blank">\1</a>', line)

    parts, items, kind = [], [], None

    def close():
        nonlocal items, kind
        if items:
            parts.append(f"<{kind}>" + "".join(f"<li>{item}</li>" for item in items) + f"</{kind}>")
        items, kind = [], None

    for raw in (text or "").splitlines():
        line = raw.strip()
        bullet = re.match(r"^[-*•]\s+(.*)$", line)
        number = re.match(r"^\d+[.)]\s+(.*)$", line)
        if bullet or number:
            wanted = "ul" if bullet else "ol"
            if kind and kind != wanted:
                close()
            kind = wanted
            items.append(inline((bullet or number).group(1)))
            continue
        close()
        if line:
            heading = re.match(r"^#{1,6}\s+(.*)$", line)
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
        [("queued", "Waiting"), ("done", "Answered"), ("refused", "Not for Them"), ("failed", "Failed")],
        default="queued", required=True, index=True)
    answer_id = fields.Many2one("mail.message", string="Answer", ondelete="set null")
    conversation_id = fields.Many2one("nudge.conversation", ondelete="set null")
    error = fields.Char()

    @api.model
    def _queue(self, message, thread):
        user = message.author_id.user_ids[:1]
        if message.author_id == self.env.user.partner_id:
            user = self.env.user
        self.create({
            "message_id": message.id,
            "model": thread._name,
            "res_id": thread.id,
            "user_id": user.id,
        })
        self.env.ref("nudge_discuss.cron_mentions").sudo()._trigger()

    @api.model
    def _cron_answer(self):
        for mention in self.search([("state", "=", "queued")], order="id", limit=20):
            mention._answer()
            if not self.env.context.get("nudge_keep_transaction"):
                self.env.cr.commit()

    # ------------------------------------------------------------------
    # Answered as the person who asked
    # ------------------------------------------------------------------
    def _as_person(self):
        user = self.user_id
        context = dict(user.with_user(user).context_get())
        context["allowed_company_ids"] = user.company_ids.ids
        return self.env(user=user.id, su=False, context=context)

    def _lang(self):
        """:return: the language of the person who asked: what Nudge posts is
        read by them, not by the background job that posts it"""
        return (self.user_id.lang or self.message_id.author_id.lang
                or self.env.company.partner_id.lang or self.env.lang or "en_US")

    def _answer(self):
        self.ensure_one()
        user = self.user_id
        told = self.with_context(lang=self._lang()).env
        if not user or not user._is_internal() or not user.has_group("nudge.group_nudge_user"):
            self.state = "refused"
            self._post(told._("Nudge is not open to the person who asked."), notify=False)
            return
        env = self._as_person()
        try:
            with self.env.cr.savepoint():
                context = self._context_of(env)
                question = cut(text_of(self.message_id.body).replace("@Nudge", "").strip(), MAX_QUESTION_CHARS)
                question = question or _("Summarise this for me.")
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
                    raise UserError(last.error_message or _("Nudge had no answer."))
                self.write({"conversation_id": conversation.id})
            self.answer_id = self._post(answer.content)
            self.state = "done"
        except (UserError, AccessError, Refused) as error:
            self.write({"state": "failed", "error": cut(str(error), 300)})
            self._post(told._("Nudge could not answer: %s", error))
        except Exception as error:  # noqa: BLE001
            _logger.exception("Nudge: mention %s failed", self.id)
            self.write({"state": "failed", "error": cut(str(error), 300)})

    def _context_of(self, env):
        """:return: ``{"title", "block"}``: what the person may read of the
        record and of the conversation, as a block of the message"""
        if self.model == CHANNEL:
            channel = env[CHANNEL].browse(self.res_id).exists()
            if not channel:
                raise UserError(_("This channel no longer exists."))
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
