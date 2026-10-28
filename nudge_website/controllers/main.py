"""The doors of the assistant of the website.

Public: anyone may knock, nobody is trusted. Each door checks that the
assistant is on for this website and language, counts the visitor's address
(as a hash) against the limit, reads their message as text only, and answers
through ``tools.assistant``, in the environment of whoever is there: the public
user of the site, or the logged-in customer with their own rights.
"""
import re
import time

from odoo import _, http, release
from odoo.http import request
from odoo.tools import plaintext2html

from ..tools import agent_door, assistant

# The type of a route that speaks JSON changed its name in Odoo 19
JSON = "jsonrpc" if release.version_info[0] >= 19 else "json"
MAX_TURN_CHARS = 500
MAX_NAME = 100
MAX_EMAIL = 200
MAX_MESSAGE = 1000
MAX_BODY = 6000
EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}$")
# A robot fills the field a person does not see, or sends the form sooner than a person can fill it in
HONEYPOT = "homepage"
MIN_FILL_SECONDS = 3
SHOWN_KEY = "nudge_website_handover_shown"
_clock = time.time


class NudgeWebsite(http.Controller):

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _site():
        """:return: the website when its assistant is on in the language of the visitor"""
        website = request.website
        lang = request.env.lang or (request.lang.code if request.lang else None)
        if not website or not website.nudge_assistant_on(lang):
            return None
        return website

    @staticmethod
    def _is_logged_in():
        return not request.env.user._is_public()

    def _gate(self):
        """:return: ``(website, refusal)``; the refusal is the answer to give instead"""
        website = self._site()
        if not website:
            return None, {"error": "off", "text": _("The assistant is not available.")}
        return website, None

    @staticmethod
    def _who():
        """:return: ``(ip_hash, session_hash)``. The address is the one Odoo reports, which a
        proxy header changes only when ``proxy_mode`` is on; the session is the cookie's."""
        Message = request.env["nudge.web.message"]
        ip_hash = Message._hash_address(request.httprequest.remote_addr or "")
        sid = getattr(request.session, "sid", "") or ""
        return ip_hash, (Message._hash_address(f"session:{sid}") if sid else "")

    @staticmethod
    def _limited(website):
        return {
            "error": "limited",
            "text": _("You have asked a lot in a short time. Please wait a few minutes and try again."),
            "contact": assistant.contact_url(website)}

    # ------------------------------------------------------------------
    # Doors
    # ------------------------------------------------------------------
    # csrf=False on purpose: these doors are for visitors with no login, they change nothing
    # but the log (and one request the visitor asked for), and a page of another site can
    # neither read the answer nor act as the visitor beyond what anyone may do here.
    @http.route("/nudge_website/ask", type=JSON, auth="public", website=True, methods=["POST"], csrf=False)
    def ask(self, message=None, **kwargs):
        """The answer to one message. No record is created, nothing is changed."""
        website, refusal = self._gate()
        if refusal:
            return refusal
        env = request.env
        text = message if isinstance(message, str) else ""
        text = " ".join(text.split())[:assistant.MAX_QUESTION]
        if len(text) < 2:
            return {"error": "empty", "text": _("Please write your question.")}
        user = env.user
        ip_hash, session_hash = self._who()
        # The place is taken, and made visible to the other requests, before the answer costs anything
        line = env["nudge.web.message"]._reserve(website, user, ip_hash, session_hash, text)
        if not line:
            return self._limited(website)
        env.cr.commit()
        logged_in = self._is_logged_in()
        # With published website bindings the agents answer; without, the assistant as before
        memory = dict(request.session.get(agent_door.CONV_KEY) or {}) if logged_in else None
        result = agent_door.serve(
            env, website, user, text, logged_in, session_hash,
            lang=env.lang or (request.lang.code if request.lang else None), memory=memory,
            refusal=_("This assistant is not open to you."),
            consent=agent_door.consented(request.session.get(agent_door.CONSENT_KEY)))
        if result is None:
            result = assistant.answer(env, website, user, text, logged_in)
        else:
            request.session[agent_door.BINDING_KEY] = result.pop("binding_id", 0)
            if memory is not None:
                request.session[agent_door.CONV_KEY] = memory
        line.sudo().write({
            "answered": result["answered"], "used_ai": result["used_ai"],
            "answer": " ".join((result["text"] or "").split())[:500]})
        result["contact"] = assistant.contact_url(website)
        return result

    @http.route("/nudge_website/handover", type=JSON, auth="public", website=True, methods=["POST"], csrf=False)
    def handover(self, name=None, email=None, message=None, consent=False, **kwargs):
        """"Talk to a person": the live chat when someone is there, otherwise the contact
        form, or a request left for the company. The conversation goes with the
        request only if the visitor said yes, and it is the one Nudge stored itself:
        nothing the browser sends as a transcript is read."""
        website, refusal = self._gate()
        if refusal:
            return refusal
        env = request.env
        ip_hash, session_hash = self._who()
        line = env["nudge.web.message"]._reserve(
            website, env.user, ip_hash, session_hash, "handover", kind="handover")
        if not line:
            return self._limited(website)
        env.cr.commit()
        line.sudo().write({"answered": True})
        contact = assistant.contact_url(website)
        name = " ".join(str(name or "").split())[:MAX_NAME]
        email = str(email or "").strip()[:MAX_EMAIL]
        if not (name and EMAIL.match(email)):
            # The form is offered now: the request that follows is timed from here
            request.session[SHOWN_KEY] = _clock()
            channel_url = self._livechat_url(website)
            if channel_url:
                return {"type": "livechat", "url": channel_url}
            return {"type": "contact", "url": contact}
        text = " ".join(str(message or "").split())[:MAX_MESSAGE]
        lines = []
        if consent is True or consent == "true":
            for role, said in env["nudge.web.message"]._transcript(session_hash):
                who = _("Visitor") if role == "user" else _("Assistant")
                lines.append(f"{who}: {said[:MAX_TURN_CHARS]}")
        if "crm.lead" not in env.registry:
            return {"type": "contact", "url": contact}
        thanks = {"type": "lead", "text": _("Thank you. Someone will write to you soon.")}
        shown = request.session.get(SHOWN_KEY)
        if (str(kwargs.get(HONEYPOT) or "").strip() or not isinstance(shown, (int, float))
                or _clock() - shown < MIN_FILL_SECONDS):
            # A robot is thanked as a person is, so it cannot tell, and nothing is made
            return thanks
        body = "\n\n".join(part for part in (text, "\n".join(lines)) if part)[:MAX_BODY]
        # sudo: a visitor has no right on leads. This is the only record the assistant
        # creates, asked for by the visitor, with fields chosen here and no others. The
        # text is escaped; the salesperson is not the public user, so nobody is assigned
        values = {
            "name": _("Website assistant: %s", name),
            "contact_name": name, "email_from": email, "user_id": False,
            "description": plaintext2html(body),
        }
        if "website_id" in env["crm.lead"]._fields:
            values["website_id"] = website.id
        # The company of the website, not the one of the public user
        values["company_id"] = website.company_id.id
        lead = env["crm.lead"].sudo().create(values)
        agreed = consent is True or consent == "true"
        talked_to = request.session.get(agent_door.BINDING_KEY)
        if agreed and isinstance(talked_to, int) and talked_to:
            # A person, past the checks above: the agent they were talking to may hand them to a person from now
            # on (hand_to_person), that agent only
            request.session[agent_door.CONSENT_KEY] = sorted(
                set(agent_door.consented(request.session.get(agent_door.CONSENT_KEY))) | {talked_to})
        # The agent the visitor was talking to tells its escalation person too, on the lead just made; the
        # transcript goes only with consent
        agent_door.hand_over(
            env, talked_to, website, body, name, email, agreed, self._is_logged_in(), lead=lead)
        return thanks

    @staticmethod
    def _livechat_url(website):
        env = request.env
        if "im_livechat.channel" not in env.registry:
            return ""
        try:
            for channel in env["im_livechat.channel"].search([], limit=5):
                operators = channel.available_operator_ids if "available_operator_ids" in channel._fields else True
                if operators and getattr(channel, "web_page", ""):
                    return channel.web_page
        except Exception:  # noqa: BLE001 - no live chat for this visitor: the contact form answers
            return ""
        return ""
