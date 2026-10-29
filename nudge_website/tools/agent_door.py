"""The bubble answered by agents, when the website has published website bindings.

Nothing here reads company data. A public visitor, and a customer logged in to the portal, is handed to
``nudge.agent._answer_public``, which runs as the service user of the binding and reads only its allow-list;
a logged-in employee is handed to ``agent.with_user(user)._answer``, as themselves, and only if the agent is
open to them.
The ``sudo()`` below reads the agents' own configuration (which agent, which binding), never data.
"""
import logging

from odoo import fields
from odoo.exceptions import AccessError, UserError

from . import assistant

_logger = logging.getLogger(__name__)

CONV_KEY = "nudge_website_agent_conv"
BINDING_KEY = "nudge_website_binding"
# The channels (binding ids) whose agent the visitor agreed may hand them to a person: the "Talk to a person" form
# ticked while talking to that agent
CONSENT_KEY = "nudge_website_consent"


def consented(session_value):
    """:return: the binding ids a visitor gave their consent for, from what the session keeps"""
    if not isinstance(session_value, (list, tuple)):
        return []
    return [value for value in session_value if isinstance(value, int)]


def spent_today(env, company):
    """:return: the tokens spent today on the visitors of the websites of ``company`` (``assistant.spent_today``)"""
    return assistant.spent_today(env, company)


def website_bindings(env, website):
    """:return: the published website bindings of the published agents that work in the company of the website"""
    bindings = env["nudge.agent.binding"].sudo().search([("channel", "=", "website"), ("published", "=", True)])
    company = website.company_id
    return bindings.filtered(
        lambda b: b.agent_id.active and b.agent_id.state == "published"
        and (not b.agent_id.company_ids or company in b.agent_id.company_ids))


def _pick(env, text, bindings):
    if len(bindings) <= 1:
        return bindings
    picked = env["nudge.agent"].sudo()._front_desk(text, bindings)
    return picked if picked and picked in bindings else bindings[:1]


def _shape(result):
    return {
        "text": result.get("text") or "", "products": [], "links": [],
        "answered": not result.get("error"), "used_ai": result.get("source") == "ai", "busy": False}


def serve(env, website, user, text, logged_in, visitor_key, lang=None, memory=None, refusal="", consent=()):
    """:param memory: a dict kept in the session, ``{agent id: conversation id}``, for a logged-in employee
    :param consent: the bindings (ids) the visitor agreed may hand them to a person (the "Talk to a person" form),
        passed to the engine for its hand_to_person on that channel only
    :return: the answer in the shape of ``assistant.answer`` (plus ``binding_id``), or None when no agent
        serves this visitor and the plain assistant answers as before"""
    bindings = website_bindings(env, website)
    if not bindings:
        return None
    Agent = env["nudge.agent"]
    # A customer logged in to the portal holds no Nudge right of their own (the engine refuses them in _answer):
    # they are answered like a visitor, as the service user, within the channel's models
    if logged_in and not user.share:
        usable = bindings.filtered(lambda b: b.agent_id._can_use(user))
        if not usable:
            return {"text": refusal, "products": [], "links": [], "answered": False, "used_ai": False,
                    "busy": False}
        # A service binding answers as its service user only: an employee asks through a binding of their own
        # (office hours, hand-over), or without one when the agent is bound here for visitors only
        own = usable.filtered(lambda b: b.acts_as == "person")
        binding = _pick(env, text, own or usable)
        agent = Agent.browse(binding.agent_id.id).with_user(user)
        conversation = None
        if memory is not None and memory.get(str(agent.id)):
            conversation = env["nudge.conversation"].with_user(user).browse(memory[str(agent.id)]).exists()
        try:
            result = agent._answer(text, lang=lang, conversation=conversation or None,
                                   binding=binding if binding.acts_as == "person" else None)
        except (AccessError, UserError) as error:
            result = {"text": str(error.args[0]) if error.args else str(error), "error": True}
        if memory is not None and result.get("conversation_id"):
            memory[str(agent.id)] = result["conversation_id"]
    else:
        usable = bindings.filtered(lambda b: b.acts_as == "service" and b.service_user_id)
        if not usable:
            return None
        company = website.company_id
        budget = company.sudo().nudge_public_budget
        if not budget or spent_today(env, company) >= budget:
            # The AI budget of the day for the visitors is spent (or none is given): the plain assistant answers,
            # without AI, from the products and the files, as it does past it
            return None
        binding = _pick(env, text, usable)
        try:
            # One of the places of the AI of the website, as the plain assistant takes: visitors never hold more
            with assistant.ai_slot(env):
                # sudo: the receiver only carries the method; the turn runs as the service user of the binding, in
                # the company of the website
                result = Agent.browse(binding.agent_id.id).sudo()._answer_public(
                    text, binding, lang=lang, conversation=None, visitor_key=visitor_key,
                    consent=binding.id in consented(consent), company=company)
        except assistant.Busy:
            return {"text": env._("Many people are asking right now. Please try again in a minute: %s",
                                  assistant.contact_url(website)),
                    "products": [], "links": [], "answered": False, "used_ai": False, "busy": True,
                    "binding_id": binding.id}
        except (AccessError, UserError):
            # A visitor is never shown what went wrong inside
            _logger.info("Nudge website: an agent could not answer a visitor", exc_info=True)
            result = {"text": env._("I cannot answer this here. Please ask in other words, or ask to talk to a "
                                    "person."), "error": True}
    answer = _shape(result)
    answer["binding_id"] = binding.id
    return answer


def hand_over(env, binding_id, website, summary, name, email, consent, logged_in, lead=None):
    """Tells the agent's escalation person, through the engine's ``_hand_to_person``, when the binding the
    visitor was talking to has one. The consent is the visitor's, passed as given.

    :param lead: the ``crm.lead`` the form just made: the activity goes on it, never on a second lead
    :return: True when the engine took the request"""
    binding = env["nudge.agent.binding"].sudo().browse(binding_id or 0).exists()
    if (not binding or binding.channel != "website" or not binding.published or not binding.escalation_user_id
            or binding not in website_bindings(env, website)):
        return False
    method = getattr(env["nudge.agent"].sudo(), "_hand_to_person", None)
    if method is None:
        return False
    try:
        method(binding, summary, consent=bool(consent), visitor_name=name, visitor_email=email,
               public=not logged_in, lead=lead)
    except (AccessError, UserError):
        _logger.info("Nudge website: the hand-over to a person was refused", exc_info=True)
        return False
    return True
