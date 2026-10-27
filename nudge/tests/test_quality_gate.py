"""The quality gate of every Nudge release.

A fixed set of scenarios (``tests/scenarios``): a sentence in a mode on a screen, a scripted model
(the fake adapter: no provider, no Ollama, no network) and what must come out, said in facts that can
be checked: the tools called, the confirmation card shown before any write, the record before and
after the yes, nothing written without it, no secret in a request, no provider call in private mode,
a refusal for a user without the group, and the most tokens a request may take.

Each scenario is one test, so one failure never hides another. Every outcome is also collected, and
when the class ends a JSON report is written to the file named by ``NUDGE_GATE_REPORT`` (and logged
as one ``NUDGE_GATE_RESULT`` line); ``scripts/quality_gate.py`` runs the class and reads it to
compare a release with the one before.
"""
import json
import logging
import os
import time
from types import SimpleNamespace
from unittest.mock import patch

import requests

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.nudge.llm import base, loop, ollama, summary
from odoo.addons.nudge.llm.fake_adapter import FakeAdapter
from odoo.addons.nudge.models.nudge_conversation import ADMIN_PARAM, DEVELOPER_PARAM
from odoo.addons.nudge.tools import setup

from .common import NudgeCase, _no_server
from .scenarios import DEFAULT_BUDGET, SCENARIOS

_logger = logging.getLogger(__name__)

RESULTS = []
SECRETS = ("sk-test-secret",)


def tool_events(round_):
    events = [base.text_event(round_["text"])] if round_["text"] else []
    for number, (name, arguments) in enumerate(round_["calls"], 1):
        events.append(base.tool_use_event(f"call_{number}", name, arguments))
    reason = base.STOP_TOOL_USE if round_["calls"] else base.STOP_END
    return [*events, base.stop_event(reason)]


def request_size(call):
    """:return: about how many tokens one request to the model takes"""
    return summary.estimate_tokens(
        json.dumps([call["system"], call["turns"], call["tools"]], ensure_ascii=False, default=str))


@tagged("post_install", "-at_install")
class TestQualityGate(NudgeCase, TransactionCase):
    """One test per scenario of ``tests/scenarios``, named ``test_<id>``."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        # The administrator of a real database holds the manager group of every app it has
        groups = ["nudge.group_nudge_agent", "base.group_system", "base.group_partner_manager"]
        groups += [xmlid for xmlid in ("purchase.group_purchase_manager", "sales_team.group_sale_manager",
                                       "account.group_account_manager")
                   if env.ref(xmlid, raise_if_not_found=False)]
        cls.admin = new_test_user(env, login="nudge_gate_admin", groups=",".join(groups))
        # An administrator may reach the setup, and this database is not set up yet
        env["ir.config_parameter"].sudo().set_param(ADMIN_PARAM, "yes")
        env["ir.config_parameter"].sudo().set_param(setup.PARAM_DONE, "no")
        cls.fixtures = SimpleNamespace(
            raghad=env["res.partner"].create({"name": "Raghad", "city": "Nablus", "function": "Manager"}),
            ebrahim=env["res.partner"].create({"name": "Ebrahim", "city": "Hebron", "email": "ebrahim@your-company.example"}),
            company=env["res.partner"].create({"name": "your company", "is_company": True}),
            agent_id=cls.agent.id,
            system_group_id=env.ref("base.group_system").id,
        )

    @classmethod
    def tearDownClass(cls):
        try:
            report = {"results": sorted(RESULTS, key=lambda one: one["id"])}
            RESULTS.clear()
            path = os.environ.get("NUDGE_GATE_REPORT")
            if report["results"]:
                _logger.info("NUDGE_GATE_RESULT %s", json.dumps(report, ensure_ascii=False))
                if path:
                    with open(path, "w", encoding="utf-8") as handle:
                        json.dump(report, handle, ensure_ascii=False, indent=1)
        finally:
            super().tearDownClass()

    # ------------------------------------------------------------------------------------ helpers
    def snapshot(self):
        """What the scenarios promise not to change when nothing was said yes to"""
        env, fx = self.env, self.fixtures
        def row(partner):
            partner = partner.sudo()
            return (partner.name, partner.city, partner.email, partner.function, partner.comment or "")
        return {
            "partners": env["res.partner"].sudo().with_context(active_test=False).search_count([]),
            "raghad": row(fx.raghad), "ebrahim": row(fx.ebrahim),
            "agent_groups": sorted(self.agent.sudo().group_ids.ids) if "group_ids" in self.agent._fields
            else sorted(self.agent.sudo().groups_id.ids),
            "company": env.company.name,
            "tasks": env["nudge.task"].sudo().search_count([]),
            "automations": env["base.automation"].sudo().search_count([]) if "base.automation" in env else 0,
            "fields": env["ir.model.fields"].sudo().search_count([("state", "=", "manual")]),
            "users": env["res.users"].sudo().with_context(active_test=False).search_count([]),
        }

    def prompts_of(self, adapter):
        return json.dumps([[call["system"], call["turns"], call["tools"]] for call in adapter.calls],
                          ensure_ascii=False, default=str)

    def tools_called(self, conversation, adapter):
        names = []
        for call in adapter.calls:
            for turn in call["turns"]:
                for tool_call in turn.get("tool_calls") or []:
                    names.append(tool_call["name"])
        names += conversation.sudo().run_ids.step_ids.mapped("tool")
        return names

    def tool_errors(self, adapter):
        found = []
        for call in adapter.calls:
            for turn in call["turns"]:
                for result in turn.get("results") or []:
                    if result.get("is_error"):
                        found.append((result["name"], result["content"][:160]))
        return found

    def open_run(self, scenario, user):
        Conversation = self.env["nudge.conversation"].with_user(user)
        conversation = Conversation._chat_open()
        screen = scenario.screen(self.fixtures) if scenario.screen else None
        job = conversation._chat_prepare(scenario.sentence, snapshot=screen, mode=scenario.mode)
        return conversation, list(loop.run(job, conversation._chat_record))

    def has_line(self, card, key, text):
        for line in card.get("lines") or []:
            if key in (line.get("field"), line.get("label")) and text in json.dumps(line, ensure_ascii=False):
                return True
        return False

    # ----------------------------------------------------------------------------------- the run
    def play(self, scenario):
        missing = [name for name in scenario.needs if not self.env["ir.module.module"].sudo().search_count(
            [("name", "=", name), ("state", "=", "installed")])]
        if missing:
            RESULTS.append({"id": scenario.id, "domain": scenario.domain, "lang": scenario.lang,
                            "mode": scenario.mode, "skipped": "needs " + ", ".join(missing)})
            self.skipTest("needs " + ", ".join(missing))
        started = time.monotonic()
        failures = []
        record = {
            "id": scenario.id, "domain": scenario.domain, "lang": scenario.lang, "mode": scenario.mode,
            "typos": scenario.typos, "tokens": 0, "requests": 0, "passed": False, "failures": failures,
            "seconds": 0.0,
        }
        try:
            self.run_scenario(scenario, failures, record)
        except Exception as error:  # a crash is a failed scenario with its reason, never a hidden one
            failures.append(f"crashed: {type(error).__name__}: {str(error)[:300]}")
            raise
        finally:
            record["passed"] = not failures
            record["seconds"] = round(time.monotonic() - started, 2)
            RESULTS.append(record)
        if failures:
            self.fail(f"{scenario.id} ({scenario.domain}): " + "; ".join(failures))

    def run_scenario(self, scenario, failures, record):
        fx = self.fixtures
        user = self.admin if scenario.user == "admin" else getattr(self, scenario.user)
        if scenario.mode == "dev" or scenario.user == "admin":
            self.env["ir.config_parameter"].sudo().set_param(DEVELOPER_PARAM, "yes")
        if scenario.setup:
            scenario.setup(self, fx)
        # The setup never installs an app from the gate
        self.patch(setup, "install_modules", lambda env, names: names)
        before = self.snapshot()
        script = [tool_events(one) for one in scenario.script(fx)] if scenario.script else []

        if scenario.private:
            return self.run_private(scenario, user, script, failures, record, before)

        adapter = self.use_adapter(script=script)
        if scenario.refusal:
            try:
                self.open_run(scenario, user)
            except UserError as error:
                if scenario.refusal != "*" and scenario.refusal not in str(error):
                    failures.append(f"refusal says {str(error)[:120]!r}, expected {scenario.refusal!r}")
            else:
                failures.append("the request was not refused")
            if adapter.calls:
                failures.append("the provider was called for a refused user")
            self.check_unchanged(before, failures)
            return

        conversation, events = self.open_run(scenario, user)
        cards = [event for event in events if event["type"] == "confirm"]
        # What was said, done or not
        if scenario.no_card and cards:
            failures.append("a confirmation card appeared where none was expected")
        if scenario.card:
            if not cards:
                failures.append("no confirmation card before the write")
            else:
                self.check_card(scenario.card, cards[0], failures)
        if cards:
            if self.snapshot() != before:
                failures.append("a record changed before the yes")
            if events[-1]["type"] != "waiting":
                failures.append(f"the request did not wait for the person (ended with {events[-1]['type']})")
        self.check_calls(scenario, conversation, adapter, failures, record)

        if cards and scenario.decision:
            self.decide(conversation, scenario.decision)
            if scenario.decision == "confirm":
                self.check_written(scenario, failures, before)
        if scenario.unchanged or scenario.decision == "decline" or (cards and not scenario.decision):
            self.check_unchanged(before, failures)

    def check_calls(self, scenario, conversation, adapter, failures, record):
        called = self.tools_called(conversation, adapter)
        position = 0
        for name in scenario.tools:
            try:
                position = called.index(name, position) + 1
            except ValueError:
                failures.append(f"tool {name} was not called (called: {called})")
        for name, content in self.tool_errors(adapter):
            if name not in scenario.tool_errors:
                failures.append(f"tool {name} failed: {content}")
        text = self.prompts_of(adapter)
        for secret in (*SECRETS, *scenario.must_not_prompt):
            if secret in text:
                failures.append(f"the secret {secret[:12]}... is in a request to the model")
        sizes = [request_size(call) for call in adapter.calls]
        record["requests"] = len(sizes)
        record["tokens"] = max(sizes or [0])
        budget = scenario.max_tokens or DEFAULT_BUDGET[scenario.mode]
        record["budget"] = budget
        if record["tokens"] > budget:
            failures.append(f"a request takes {record['tokens']} tokens, the budget is {budget}")

    def check_card(self, expected, card, failures):
        if expected.get("kind") and card.get("kind") != expected["kind"]:
            failures.append(f"the card is a {card.get('kind')!r}, expected {expected['kind']!r}")
        if expected.get("title") and expected["title"] not in card.get("title", ""):
            failures.append(f"the card title {card.get('title')!r} lacks {expected['title']!r}")
        for key, text in (expected.get("lines") or {}).items():
            if not self.has_line(card, key, text):
                failures.append(f"the card shows no {key} = {text}")
        if expected.get("plan_total") and (card.get("plan") or {}).get("total") != expected["plan_total"]:
            failures.append(f"the plan on the card covers {(card.get('plan') or {}).get('total')} record(s), "
                            f"expected {expected['plan_total']}")
        for key in set(expected) - {"kind", "title", "lines", "plan_total"}:
            if card.get(key) != expected[key]:
                failures.append(f"the card has {key} = {card.get(key)!r}, expected {expected[key]!r}")
        if card.get("state") != "pending":
            failures.append(f"the card is {card.get('state')!r}, not pending")

    def check_unchanged(self, before, failures):
        after = self.snapshot()
        changed = sorted(key for key in before if before[key] != after[key])
        if changed:
            failures.append("changed without a yes: " + ", ".join(changed))

    def check_written(self, scenario, failures, before):
        spec = scenario.written
        if not spec:
            return
        if spec.get("ref"):
            record = getattr(self.fixtures, spec["ref"]).sudo()
            record.invalidate_recordset()
            records = record
        else:
            records = self.env[spec["model"]].sudo().with_context(active_test=False).search(spec["domain"])
            if len(records) != spec.get("count", 1):
                failures.append(f"{len(records)} {spec['model']} record(s) after the yes, expected {spec.get('count', 1)}")
                return
        for field, value in spec.get("values", {}).items():
            for record in records:
                if record[field] != value:
                    failures.append(f"{spec.get('model', 'record')}.{field} is {record[field]!r} after the yes, expected {value!r}")

    def run_private(self, scenario, user, script, failures, record, before):
        """Private mode: nothing leaves for a provider that is not on our server"""
        self.env.company.nudge_private_mode = True
        Provider = self.env["nudge.provider"]
        network = []

        def spy(*args, **kwargs):
            network.append(args[:2])
            raise requests.exceptions.ConnectionError("no network in the gate")
        # pinned_http.get/post/head all go through its request(): the outbound calls of Nudge's own tools
        patches = [patch("requests.request", spy), patch("requests.post", spy), patch("requests.get", spy),
                   patch("odoo.addons.nudge.tools.pinned_http.request", spy),
                   patch.object(ollama.outbound, "request", _no_server)]
        adapter = FakeAdapter(script=script)
        built = []

        def make(config):
            built.append(config.base_url)
            return adapter
        patches.append(patch.object(base, "get_adapter", make))
        if scenario.private == "local":
            Provider.create({
                "name": "Our Ollama", "kind": "openai_compatible", "base_url": "http://localhost:11434/v1",
                "model_name": "qwen", "requires_api_key": False, "runs_locally": True, "sequence": 50})
        for one in patches:
            one.start()
            self.addCleanup(one.stop)
        try:
            conversation, events = self.open_run(scenario, user)
        except UserError as error:
            if not scenario.refusal:
                failures.append(f"refused: {str(error)[:120]}")
            elif scenario.refusal not in str(error):
                failures.append(f"refusal says {str(error)[:120]!r}, expected {scenario.refusal!r}")
            events = []
        else:
            if scenario.refusal:
                kinds = [event.get("code") or event["type"] for event in events]
                failures.append(f"private mode did not stop the request ({kinds[-3:]})")
        if scenario.private == "outside":
            if adapter.calls or built:
                failures.append("a provider outside our server was called in private mode")
        else:
            if not adapter.calls:
                failures.append("the provider on our server was not used")
            if any("localhost" not in url for url in built):
                failures.append(f"a provider other than ours was built: {built}")
            record["requests"] = len(adapter.calls)
            record["tokens"] = max([request_size(call) for call in adapter.calls] or [0])
        if network:
            failures.append(f"{len(network)} network call(s) in private mode")
        self.check_unchanged(before, failures)


def _add(scenario):
    def test(self):
        self.play(scenario)
    test.__name__ = f"test_{scenario.id.replace('-', '_')}"
    test.__doc__ = f"{scenario.domain} / {scenario.mode} / {scenario.lang}: {scenario.sentence}"
    setattr(TestQualityGate, test.__name__, test)


for _scenario in SCENARIOS:
    _add(_scenario)
