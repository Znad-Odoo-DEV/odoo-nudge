"""The shape of a quality-gate scenario.

A scenario is a sentence a person types, the mode and screen it is typed in, what a scripted model
answers (the fake adapter plays it, so no provider, Ollama or network is needed), and what must
come out: the tools called, the confirmation card, the record before and after the yes, what must
not happen, and the most tokens a request may take. Everything is data, so a release can be
compared with the previous one scenario by scenario.
"""
from dataclasses import dataclass, field

TALK, AGENT, DEV = "talk", "agent", "dev"
MODES = (TALK, AGENT, DEV)
LANGUAGES = ("en", "ar", "arabizi")

# Largest request (system + turns + tools, about 4 characters a token) a scenario may send, by mode.
# A scenario may set its own. Measured on 19.0.1.58.0 (largest request: talk 18.7k, agent 29.5k, dev 47.6k) plus about 8% room: a release that makes a
# request heavier than this fails the gate until the budget is raised on purpose.
DEFAULT_BUDGET = {TALK: 19500, AGENT: 32000, DEV: 52000}


def round_(text="", *calls):
    """One request to the model: some words, then calls to tools ``(name, arguments)``. A round
    with no call ends the answer."""
    return {"text": text, "calls": list(calls)}


@dataclass
class Scenario:
    id: str
    domain: str                 # the skill (np03_skills.md) or the cross-cutting concern
    lang: str                   # en | ar | arabizi
    mode: str                   # talk | agent | dev
    sentence: str
    # What the scripted model answers, as a function of the fixtures: list of ``round_``
    script: object = None
    user: str = "agent"         # fixture user: agent, user_a, outsider, admin, manager
    typos: bool = False         # the sentence is written with slips on purpose
    screen: object = None       # function of the fixtures -> the snapshot the browser sends, or None
    setup: object = None        # function of (case, fixtures) run before the request
    # --- expected outcome
    tools: tuple = ()           # tools that must be called, in this order (others may be between)
    tool_errors: tuple = ()     # tools whose call may fail (the sentence cannot be done on this screen)
    card: object = None         # {"kind":, "title":, "lines": {field-or-label: text}} shown before any write
    decision: str = ""          # "confirm" | "decline" | "" (the person says nothing)
    written: object = None      # after a yes: {"ref": fixture name, "values": {...}} or {"model","domain","values"}
    unchanged: bool = False     # whatever was said, no record may have changed at the end
    refusal: str = ""           # the request is refused (UserError) and the text holds this
    no_card: bool = False       # no confirmation card may appear at all
    private: str = ""           # "outside" | "local": private mode on, with a provider outside or on our server
    max_tokens: int = 0         # budget; 0 = the default of the mode
    must_not_prompt: tuple = () # texts that must not be in any request, beside the secrets always checked
    needs: tuple = ()           # apps the scenario builds on; without them it is skipped, never counted
    tags: tuple = field(default_factory=tuple)
