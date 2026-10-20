import json
import re

from markupsafe import Markup, escape

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from ..tools import base, commands, history
from ..tools.records import Refused, cut, get_model, get_records

TONES = ("formal", "friendly", "short")
DEFAULT_TONE = "friendly"
THREAD_MESSAGES = 8
THREAD_MESSAGE_CHARS = 900
CONTEXT_BUDGET = 5000
FILES_CHARS = 2500
FILE_PASSAGES = 3
FILE_WORDS = 6
QUESTION = "Write the reply to the customer's last message. Tone: %s."
LANGUAGE_OF_THE_CUSTOMER = "the language of the customer's last message"


def text_to_html(text):
    """:return: the paragraphs of a plain text as HTML, nothing in it taken for markup"""
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", (text or "").strip()) if part.strip()]
    return Markup("").join(
        Markup("<p>%s</p>") % Markup("<br/>").join(escape(line) for line in part.splitlines())
        for part in paragraphs)


def clean_draft(text):
    """:return: what the model wrote, without the fences or Markdown marks a model adds"""
    text = (text or "").strip()
    text = re.sub(r"^```[a-z]*\n|\n```$", "", text)
    return re.sub(r"\*\*(.+?)\*\*", r"\1", text).strip()


class NudgeReply(models.AbstractModel):
    """A reply to the customer of a record, drafted from the thread.

    The draft is only text: it is shown in a window to read, change and send
    (nudge_mail), and Nudge never sends it. Only what the customer can read of
    the thread goes to the model: the notes between employees stay out."""
    _name = "nudge.reply"
    _description = "Nudge Reply Draft"

    # ------------------------------------------------------------------
    # The customer's message
    # ------------------------------------------------------------------
    @api.model
    def _is_incoming(self, message):
        """:return: whether the message was written by someone outside the company, to be answered"""
        nudge = self.env["ir.model.data"]._xmlid_to_res_id("nudge_discuss.partner_nudge", raise_if_not_found=False)
        bot = self.env["ir.model.data"]._xmlid_to_res_id("base.partner_root", raise_if_not_found=False)
        author = message.author_id
        return (
            message.message_type in ("email", "comment") and not history.is_internal(message)
            and author.id not in (nudge, bot) and (not author or author.partner_share))

    @api.model
    def _messages(self, record, limit=THREAD_MESSAGES):
        """:return: the messages of the thread the customer can read, newest first, as this person reads them"""
        found = self.env["mail.message"].search([
            ("model", "=", record._name), ("res_id", "=", record.id), ("message_type", "in", ("email", "comment")),
        ], order="id desc", limit=limit * 3)
        return found.filtered(lambda message: not history.is_internal(message))[:limit]

    @api.model
    def _incoming(self, record):
        """:return: the last message of the customer, or an empty one"""
        for message in self._messages(record, limit=THREAD_MESSAGES):
            if self._is_incoming(message):
                return message
        return self.env["mail.message"]

    @api.model
    def state(self, model, res_id):
        """For the panel: whether there is a customer message on this record to draft a reply to.
        :return: ``{"can": bool}``"""
        try:
            if "nudge.email.review" not in self.env or not res_id or model not in self.env:
                return {"can": False}
            record = get_records(get_model(self.env, model), [res_id])
            return {"can": hasattr(record, "message_ids") and bool(self._incoming(record))}
        except (AccessError, Refused, UserError):
            return {"can": False}

    # ------------------------------------------------------------------
    # The draft
    # ------------------------------------------------------------------
    @api.model
    def _thread_rows(self, messages):
        """:return: the thread, oldest first"""
        rows = []
        for message in reversed(messages):
            text = cut(history.plain(message.body), THREAD_MESSAGE_CHARS)
            if not text:
                continue
            rows.append({
                "date": fields.Datetime.to_string(message.date),
                "from": message.author_id.display_name or message.email_from or "",
                "side": "customer" if self._is_incoming(message) else "us",
                "subject": cut(message.subject or "", 120),
                "text": text,
            })
        return rows

    @api.model
    def _company_files(self, incoming):
        """:return: passages of the company's files about what the customer wrote, as data; empty if none"""
        Source = self.env["nudge.source"]
        if not Source._for_person():
            return ""
        words = []
        for word in re.findall(r"\w{4,}", f"{incoming.subject or ''} {history.plain(incoming.body)[:400]}"):
            if word.lower() not in words:
                words.append(word.lower())
        passages = self.env["nudge.source.passage"]._search_text(" or ".join(words[:FILE_WORDS]), limit=FILE_PASSAGES)
        if not passages:
            return ""
        return f'<data source="company_files">{passages._render(FILES_CHARS).replace("<", "&lt;")}</data>'

    @api.model
    def _recipient(self, record, incoming):
        """:return: whom the reply goes to: whoever wrote, else the contact of the record"""
        author = incoming.author_id
        if author.email:
            return author.email_formatted
        if incoming.email_from:
            return incoming.email_from
        partner = record.partner_id if "partner_id" in record._fields else self.env["res.partner"]
        return partner.email_formatted or ""

    @api.model
    def _subject(self, record, incoming):
        subject = (incoming.subject or record.display_name or "").strip()
        return subject if re.match(r"(?i)^(re|ref|رد)\s*:", subject) else f"Re: {subject}"

    @api.model
    def _draft(self, record, tone=DEFAULT_TONE, refresh=False):
        """:param record: a record the person may read, with a message of the customer in its chatter
        :return: ``{"text", "subject", "to", "cached"}``: the reply drafted, as plain text; nothing is sent
        :raise UserError: when there is nothing to answer"""
        record.ensure_one()
        record.check_access("read")
        tone = tone if tone in TONES else DEFAULT_TONE
        incoming = self._incoming(record)
        if not incoming:
            raise UserError(self.env._("There is no message from the customer on this record to answer."))
        kind = f"reply:{tone}"
        History = self.env["nudge.history"]
        if not refresh:
            kept = History._kept(record, kind)
            if kept:
                return dict(json.loads(kept.data or "{}"), text=kept.text, cached=True)
        sections, omitted = history.fit(history.gather_context(self.env, record), CONTEXT_BUDGET)
        payload = {
            "kind": record._description,
            "record": record.display_name,
            "today": fields.Date.context_today(self.env.user).isoformat(),
            "thread": self._thread_rows(self._messages(record)),
            **{name: rows for name, rows in sections.items() if rows},
        }
        if omitted:
            payload["omitted"] = omitted
        block = '<data source="reply_thread">%s</data>' % base.dump(payload).replace("<", "&lt;")
        files = self._company_files(incoming)
        language = "Arabic" if commands.language_of(history.plain(incoming.body)) == "Arabic" \
            else LANGUAGE_OF_THE_CUSTOMER
        text = clean_draft(History._ask(
            "reply", QUESTION % tone, "\n".join(part for part in (block, files) if part),
            f"{record.display_name}: {QUESTION % tone}", answer_in=language))
        details = {"subject": self._subject(record, incoming), "to": self._recipient(record, incoming),
                   "incoming_id": incoming.id}
        History._keep(record, kind, text, data=json.dumps(details))
        return dict(details, text=text, cached=False)
