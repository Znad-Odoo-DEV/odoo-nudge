import ast
import copy
import io
import zipfile
from types import SimpleNamespace

from lxml import etree

from odoo.tests import HttpCase, TransactionCase, new_test_user, tagged

from odoo.addons.nudge import tools
from odoo.addons.nudge.models.nudge_conversation import DEVELOPER_PARAM
from odoo.addons.nudge.tools import scaffold

from .common import NudgeCase

SPEC = {
    "name": "raghad_school",
    "title": "مدرسة رغد",
    "summary": "Courses and lessons",
    "models": [
        {
            "name": "raghad_school.course",
            "description": "Course",
            "fields": [
                {"name": "name", "type": "char", "string": "Name", "required": True},
                {"name": "level", "type": "selection", "selection": [
                    {"key": "basic", "label": "Basic"}, {"key": "advanced", "label": "Advanced"}]},
                {"name": "lesson_ids", "type": "one2many", "relation": "raghad_school.lesson", "inverse": "course_id"},
                {"name": "total_hours", "type": "integer", "compute": {
                    "depends": ["lesson_ids.hours"], "expression": "sum(record.lesson_ids.mapped('hours'))"}},
            ],
            "sql_constraints": [{"name": "name_uniq", "definition": "UNIQUE(name)", "message": "The name must be unique."}],
            "workflow": [{"key": "draft", "label": "Draft"}, {"key": "open", "label": "Open"}, {"key": "done", "label": "Done"}],
        },
        {
            "name": "raghad_school.lesson",
            "description": "Lesson",
            "fields": [
                {"name": "name", "type": "char", "required": True},
                {"name": "course_id", "type": "many2one", "relation": "raghad_school.course", "required": True},
                {"name": "hours", "type": "integer"},
                {"name": "fee", "type": "monetary"},
                {"name": "teacher_id", "type": "many2one", "relation": "res.partner"},
            ],
            "python_constraints": [{"fields": ["hours"], "condition": "record.hours < 0", "message": "Hours cannot be negative."}],
        },
    ],
    "menus": [{"name": "Courses", "model": "raghad_school.course"},
              {"name": "Lessons", "model": "raghad_school.lesson", "parent": "Courses"}],
}


def spec_copy():
    return copy.deepcopy(SPEC)


@tagged("post_install", "-at_install")
class TestScaffold(NudgeCase, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin = new_test_user(
            cls.env, login="nudge_scaffold_admin",
            groups="nudge.group_nudge_agent,base.group_system,base.group_partner_manager")
        cls.env["ir.config_parameter"].sudo().set_param(DEVELOPER_PARAM, "yes")

    def dev_env(self, user=None):
        return self.env(user=user or self.admin, context={"nudge_mode": "dev"})

    def run_tool(self, arguments, user=None):
        return tools.base.call(self.dev_env(user), "prepare_module_scaffold", arguments, None, "dev")

    def built(self, version="19.0", spec=None):
        spec = scaffold.read_spec(self.env, spec or spec_copy(), version)
        return spec, scaffold.build_files(spec, version)

    def test_two_models_and_a_one2many_give_a_zip_that_passes_its_checks(self):
        card = self.run_tool({"spec": spec_copy(), "version": "19.0"})
        self.assertFalse(card.is_error, card.content)
        labels = {line["label"]: line["value"] for line in card.pending["preview"]["lines"]}
        self.assertIn("raghad_school.course", labels)
        self.assertIn("lesson_ids (one2many → raghad_school.lesson)", labels["raghad_school.course"])
        self.assertIn("Not available", labels["Try it here"])
        self.assertEqual(labels["Server"], "Nothing is written on the server")
        step = SimpleNamespace(id=7, values=card.pending["values"], res_model="nudge.run", res_ids=[])
        done = tools.base.TOOLS["prepare_module_scaffold"].execute(self.dev_env(), step)
        self.assertFalse(done.is_error, done.content)
        self.assertIn("/nudge/step/7/module.zip", done.content)
        files, problems = scaffold.build_and_check(card.pending["values"]["spec"], "19.0")
        self.assertEqual(problems, [])
        data = scaffold.make_zip(files)
        self.assertEqual(data, scaffold.make_zip(scaffold.build_files(card.pending["values"]["spec"], "19.0")),
                         "The same spec gives the same ZIP")
        archive = zipfile.ZipFile(io.BytesIO(data))
        names = set(archive.namelist())
        for expected in ("__manifest__.py", "models/course.py", "models/lesson.py", "security/security.xml",
                         "security/ir.model.access.csv", "views/course_views.xml", "views/menus.xml",
                         "data/demo.xml", "i18n/ar.po", "tests/test_raghad_school.py"):
            self.assertIn("raghad_school/" + expected, names)
        for name in names:
            text = archive.read(name).decode()
            if name.endswith(".py"):
                compile(text, name, "exec")
            elif name.endswith(".xml"):
                etree.fromstring(text.encode())
        manifest = ast.literal_eval(archive.read("raghad_school/__manifest__.py").decode())
        self.assertEqual(manifest["version"], "19.0.1.0.0")
        self.assertIn("mail", manifest["depends"])
        self.assertIn("Raghad", archive.read("raghad_school/data/demo.xml").decode())
        self.assertIn('msgid "مدرسة رغد"', archive.read("raghad_school/i18n/ar.po").decode())

    def test_a_duplicate_field_is_refused_naming_it(self):
        spec = spec_copy()
        spec["models"][1]["fields"].append({"name": "hours", "type": "float"})
        refused = self.run_tool({"spec": spec})
        self.assertTrue(refused.is_error)
        self.assertIn("The field hours is twice in the model raghad_school.lesson", refused.content)

    def test_other_wrong_specs_are_refused_with_what_is_wrong(self):
        def bad_inverse(spec):
            spec["models"][0]["fields"][2] = {
                "name": "lesson_ids", "type": "one2many", "relation": "raghad_school.lesson", "inverse": "nope"}

        cases = [
            (lambda s: s["models"][0]["fields"].append({"name": "Bad Name", "type": "char"}), "Bad Name"),
            (lambda s: s["models"][1]["fields"].append(
                {"name": "x_ref", "type": "many2one", "relation": "nothing.here"}), "nothing.here"),
            (bad_inverse, "nope"),
            (lambda s: s["models"][1].__setitem__("name", "res.partner"), "res.partner"),
            (lambda s: s.__setitem__("name", "Raghad School"), "snake_case"),
            (lambda s: s["menus"].append({"name": "X", "model": "ghost.model"}), "ghost.model"),
        ]
        for change, told in cases:
            spec = spec_copy()
            change(spec)
            refused = self.run_tool({"spec": spec})
            self.assertTrue(refused.is_error, told)
            self.assertIn(told, refused.content)

    def test_versions_differ_where_odoo_differs(self):
        spec, v17 = self.built("17.0")
        _spec, v18 = self.built("18.0")
        _spec, v19 = self.built("19.0")
        view = "raghad_school/views/course_views.xml"
        self.assertIn("<tree>", v17[view])
        self.assertNotIn("<list>", v17[view])
        self.assertIn("oe_chatter", v17[view])
        for files in (v18, v19):
            self.assertIn("<list>", files[view])
            self.assertNotIn("<tree>", files[view])
            self.assertIn("<chatter/>", files[view])
        self.assertIn('<group expand="0" string="Group By">', v17["raghad_school/views/lesson_views.xml"])
        self.assertIn('<group expand="0" string="Group By">', v18["raghad_school/views/lesson_views.xml"])
        self.assertNotIn("expand=", v19["raghad_school/views/lesson_views.xml"])
        model = "raghad_school/models/course.py"
        self.assertIn("_sql_constraints", v17[model])
        self.assertIn("_sql_constraints", v18[model])
        self.assertIn("models.Constraint(", v19[model])
        self.assertNotIn("_sql_constraints", v19[model])
        security = "raghad_school/security/security.xml"
        self.assertIn('name="users"', v17[security])
        self.assertIn('name="users"', v18[security])
        self.assertIn('name="user_ids"', v19[security])
        self.assertIn("res.groups.privilege", v19[security])
        self.assertNotIn("res.groups.privilege", v18[security])
        self.assertIn("category_id", v17[security])
        self.assertIn('"17.0.1.0.0"', v17["raghad_school/__manifest__.py"])
        for files in (v17, v18, v19):
            self.assertEqual(scaffold.check_files(spec, files), [])

    def test_the_static_checks_catch_a_broken_module(self):
        spec, files = self.built()
        broken = dict(files)
        broken["raghad_school/models/lesson.py"] += "\ndef (:\n"
        self.assertTrue(any("lesson.py" in p for p in scaffold.check_files(spec, broken)))
        broken = dict(files)
        broken["raghad_school/views/lesson_views.xml"] = broken["raghad_school/views/lesson_views.xml"].replace(
            '<field name="hours"/>', '<field name="hours_ghost"/>', 1)
        self.assertTrue(any("hours_ghost" in p for p in scaffold.check_files(spec, broken)))
        broken = dict(files)
        broken["raghad_school/security/ir.model.access.csv"] = "\n".join(
            line for line in files["raghad_school/security/ir.model.access.csv"].splitlines()
            if "lesson" not in line) + "\n"
        self.assertTrue(any("no access rule for raghad_school.lesson" in p for p in scaffold.check_files(spec, broken)))
        broken = dict(files)
        broken["raghad_school/views/menus.xml"] = broken["raghad_school/views/menus.xml"].replace(
            "</odoo>", '<menuitem id="menu_courses" name="Again"/></odoo>')
        self.assertTrue(any("xml id menu_courses" in p for p in scaffold.check_files(spec, broken)))
        broken = dict(files)
        broken["raghad_school/__manifest__.py"] = broken["raghad_school/__manifest__.py"].replace(
            '"license"', '"licence_typo"')
        self.assertTrue(any("licence_typo" in p for p in scaffold.check_files(spec, broken)))

    def test_nothing_is_stored_on_the_server(self):
        count = (lambda: (self.env["ir.attachment"].sudo().search_count([]),
                          self.env["ir.module.module"].sudo().search_count([])))
        before = count()
        card = self.run_tool({"spec": spec_copy()})
        step = SimpleNamespace(values=card.pending["values"], res_model="nudge.run", res_ids=[])
        tools.base.TOOLS["prepare_module_scaffold"].execute(self.dev_env(), step)
        self.assertEqual(before, count())

    def test_a_non_developer_is_refused(self):
        refused = self.run_tool({"spec": spec_copy()}, user=self.agent)
        self.assertTrue(refused.is_error)
        offered = [tool.name for tool in tools.base.available(self.env(user=self.agent), "dev")]
        self.assertNotIn("prepare_module_scaffold", offered)
        self.env["ir.config_parameter"].sudo().set_param(DEVELOPER_PARAM, "no")
        self.assertTrue(self.run_tool({"spec": spec_copy()}).is_error)

    def test_the_provider_writes_the_spec_and_the_yes_gives_the_link(self):
        from odoo.addons.nudge.llm import loop  # noqa: PLC0415

        from .test_agent import asking, call, saying  # noqa: PLC0415

        adapter = self.use_adapter(script=[
            asking(call("prepare_module_scaffold", {"spec": spec_copy(), "version": "18.0"})),
            saying("Done."),
        ])
        conversation = self.env["nudge.conversation"].with_user(self.admin)._chat_open()
        job = conversation._chat_prepare("بدي موديول لمدرسة رغد فيه دورات ودروس", mode="dev")
        list(loop.run(job, conversation._chat_record))
        self.decide(conversation)
        told = adapter.calls[1]["turns"][-1]["results"][0]["content"]
        self.assertIn("/module.zip", told)
        self.assertIn("Nothing was written on the server", told)


@tagged("post_install", "-at_install")
class TestScaffoldDownload(HttpCase):

    def test_the_zip_is_built_again_for_its_owner_only(self):
        self.env["ir.config_parameter"].sudo().set_param(DEVELOPER_PARAM, "yes")
        admin = new_test_user(self.env, login="nudge_scaffold_dl", password="nudge_scaffold_dl",
                              groups="nudge.group_nudge_agent,base.group_system")
        new_test_user(self.env, login="nudge_scaffold_other", password="nudge_scaffold_other",
                      groups="nudge.group_nudge_agent,base.group_system")
        conversation = self.env["nudge.conversation"].with_user(admin)._chat_open()
        run = self.env["nudge.run"].sudo().create({
            "name": "Module", "conversation_id": conversation.id, "user_id": admin.id, "is_developer": True})
        spec = scaffold.read_spec(self.env, spec_copy(), "19.0")
        step = self.env["nudge.step"].sudo().create({
            "run_id": run.id, "tool": scaffold.SCAFFOLD_TOOL, "kind": "show", "state": "done",
            "values": {"spec": spec, "version": "19.0"}})
        self.env.cr.flush()
        self.authenticate("nudge_scaffold_dl", "nudge_scaffold_dl")
        response = self.url_open(f"/nudge/step/{step.id}/module.zip")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Content-Type"], "application/zip")
        self.assertIn("raghad_school/__manifest__.py", zipfile.ZipFile(io.BytesIO(response.content)).namelist())
        self.authenticate("nudge_scaffold_other", "nudge_scaffold_other")
        self.assertEqual(self.url_open(f"/nudge/step/{step.id}/module.zip").status_code, 403)


@tagged("post_install", "-at_install")
class TestScaffoldSafety(TransactionCase):

    def test_a_zip_never_holds_a_path_that_climbs_or_starts_at_the_root(self):
        for path in ("../evil.py", "/etc/cron.d/x", "a/../../b.py", "C:/x.py", "a//b.py", "a\\b.py"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                scaffold.make_zip({path: "x"})
        self.assertTrue(scaffold.make_zip({"raghad_school/models/course.py": "x"}))

    def test_a_name_with_a_trailing_newline_is_not_a_name(self):
        self.assertFalse(scaffold.SNAKE.fullmatch("course"+chr(10)))
        self.assertTrue(scaffold.SNAKE.fullmatch("course"))

