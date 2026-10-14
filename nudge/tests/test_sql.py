import json

import psycopg2.errors

from odoo.tests import TransactionCase, new_test_user, tagged
from odoo.tools import mute_logger

from odoo.addons.nudge import tools
from odoo.addons.nudge.llm.snapshot import HIDDEN
from odoo.addons.nudge.tools.sql import SQL_PARAM, TIMEOUT_MS, read_only

from .common import NudgeCase


@tagged("post_install", "-at_install")
class TestSql(NudgeCase, TransactionCase):
    """Reading the database with SQL, for administrators, reading only."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["ir.config_parameter"].sudo().set_param(SQL_PARAM, "yes")
        cls.admin = new_test_user(
            cls.env, login="nudge_sql_admin", groups="base.group_system,nudge.group_nudge_agent")
        cls.admin.company_ids = [(6, 0, cls.env["res.company"].sudo().search([]).ids)]
        cls.partners = cls.env["res.partner"].create([
            {"name": "Nudge Sql One"}, {"name": "Nudge Sql Two"},
        ])

    def query(self, text, user=None):
        return tools.base.call(
            self.env(user=user or self.admin), "run_sql",
            {"query": text, "purpose": "A test"}, mode="agent")

    def payload(self, text):
        result = self.query(text)
        self.assertFalse(result.is_error, result.content)
        return json.loads(result.content)

    def test_reads_rows(self):
        payload = self.payload(
            "select id, name from res_partner where name like 'Nudge Sql%' order by name")
        self.assertEqual(payload["columns"], ["id", "name"])
        self.assertEqual([row[1] for row in payload["rows"]], ["Nudge Sql One", "Nudge Sql Two"])
        self.assertEqual(payload["row_count"], 2)
        self.assertEqual(payload["purpose"], "A test")
        self.assertNotIn("note", payload)

    def test_joins_and_aggregates(self):
        payload = self.payload(
            "select c.name as country, count(*) as n from res_partner p "
            "join res_country c on c.id = p.country_id group by c.name order by n desc")
        self.assertEqual(payload["columns"], ["country", "n"])
        payload = self.payload(
            "with mine as (select id from res_partner where name like 'Nudge Sql%') "
            "select count(*) as n from mine")
        self.assertEqual(payload["rows"], [[2]])

    def test_rows_are_bounded(self):
        payload = self.payload("select p.id from res_partner p, res_partner q")
        self.assertEqual(payload["row_count"], 50)
        self.assertIn("first 50", payload["note"])

    def test_what_is_refused(self):
        for text, message in [
            ("update res_partner set name = 'x'", "SELECT"),
            ("select id from res_partner; select 2", "One statement"),
            ("select id from res_partner -- hi", "comment"),
            ("select id from res_partner for update", "reading only"),
            ("select id from res_partner for share", "lock"),
            ("select * from res_users", "no table"),
            ("select * from ir_config_parameter", "no table"),
            ("select * from nudge_provider", "no table"),
            ("select id from res_partner, res_users", "no table"),
            ("select id from res_partner where id in (select id from res_users)", "no table"),
            ("select * from pg_catalog.pg_tables", "catalog"),
            ("select id into tmp from res_partner", "INTO"),
            ("select pg_sleep(1) from res_partner", "PG_SLEEP"),
            ("select set_config('x', 'y', false) from res_partner", "SET_CONFIG"),
            ("select 1", "FROM a table"),
            # A function that runs a query of its own reads past the rule on tables
            ("select query_to_xml('select * from '||'res_us'||'ers', true, false, '') "
             "from res_partner limit 1", "may not call query_to_xml()"),
            ("select table_to_xml('res_users', true, false, '') from res_partner", "may not call table_to_xml()"),
            ("select xmlforest(name) from res_partner", "may not call xmlforest()"),
            ("select chr(114) from res_partner", "may not call chr()"),
            ("select pg_catalog.lower(name) from res_partner", "catalog"),
            # A name built out of pieces, or spelt so that it is not read as a word
            ("select 'res_us' || 'ers' as t from res_partner", "built with ||"),
            ("select name || '!' from res_partner", "built with ||"),
            ("select * from \"res_users\"", "no table"),
            ("select * from \"res_us\"\"ers\"", "quoted name"),
            ("select * from \"Res_Partner\"", "quoted name"),
        ]:
            result = self.query(text)
            self.assertTrue(result.is_error, text)
            self.assertIn(message, result.content, text)

        wrong = self.query("select nowhere from res_partner")
        self.assertTrue(wrong.is_error)
        self.assertIn("Postgres refused", wrong.content)
        # The transaction goes on after the refusal of Postgres
        self.assertEqual(self.partners[0].name, "Nudge Sql One")
        self.assertTrue(self.payload("select id from res_partner where id = %d" % self.partners[0].id)["rows"])

    def test_the_functions_of_a_report_are_called(self):
        self.assertGreaterEqual(self.payload("select count(*) from res_partner")["rows"][0][0], 2)
        payload = self.payload(
            "select upper(\"name\") as n, round(coalesce(partner_latitude, 0)::numeric, 2) as lat, "
            "to_char(create_date, 'YYYY') as y, date_trunc('month', now()) as m, "
            "row_number() over (order by id) as r from res_partner where name like 'Nudge Sql%' "
            "and id in (select id from res_partner) and exists (select 1 from res_partner) order by id")
        self.assertEqual(payload["row_count"], 2)
        self.assertEqual(payload["rows"][0][0], "NUDGE SQL ONE")

    def test_the_database_itself_keeps_it_read_only(self):
        # Were every word of a write to pass the checks, Postgres refuses it
        with self.assertRaises(psycopg2.errors.ReadOnlySqlTransaction), mute_logger("odoo.sql_db"):
            read_only(self.env.cr, "update res_partner set name = 'x' where id = %d" % self.partners[0].id)
        self.assertEqual(self.partners[0].name, "Nudge Sql One")
        with self.assertRaises(psycopg2.errors.ReadOnlySqlTransaction), mute_logger("odoo.sql_db"):
            read_only(self.env.cr, "delete from res_partner where id = %d" % self.partners[0].id)
        # The settings hold while the statement runs
        self.assertEqual(read_only(self.env.cr, "show transaction_read_only")[1], [("on",)])
        self.assertEqual(TIMEOUT_MS, 10000)
        self.assertEqual(read_only(self.env.cr, "show statement_timeout")[1], [("10s",)])
        # And go with the savepoint: the transaction of the request writes again
        self.env.cr.execute("show transaction_read_only")
        self.assertEqual(self.env.cr.fetchone(), ("off",))
        self.partners[0].name = "Nudge Sql One Still"
        self.partners[0].flush_recordset()
        self.assertEqual(
            read_only(self.env.cr, "select name from res_partner where id = %d" % self.partners[0].id)[1],
            [("Nudge Sql One Still",)])

    def test_secrets_are_hidden_by_their_column(self):
        payload = self.payload(
            "select name as password from res_partner where name = 'Nudge Sql One'")
        self.assertEqual(payload["rows"][0][0], HIDDEN)

    def test_secrets_are_hidden_by_their_value(self):
        # A key or a hash is hidden whatever the column is called, even wrapped in a text
        payload = self.payload(
            "select concat('key: ', 'sk-ant-abc') as a, 'AIzaSyAbc' as b, 'gsk_abc' as c, "
            "'$pbkdf2-sha512$abc' as d, '$2b$12$abc' as e, 'desk-top' as f, name as g "
            "from res_partner where name = 'Nudge Sql One'")
        self.assertEqual(payload["rows"][0], [HIDDEN, HIDDEN, HIDDEN, HIDDEN, HIDDEN, "desk-top", "Nudge Sql One"])

    def test_who_gets_it(self):
        def names(user, mode="agent"):
            return [tool.name for tool in tools.base.available(self.env(user=user), mode)]

        self.assertIn("run_sql", names(self.admin))
        self.assertNotIn("run_sql", names(self.admin, "talk"))
        self.assertNotIn("run_sql", names(self.agent), "The Agent group alone is not enough")
        self.assertNotIn("run_sql", names(self.manager), "Nor the Nudge manager")

        # Not allowed in every company: a query knows no company rule
        other = self.env["res.company"].sudo().create({"name": "Nudge Sql Co"})
        self.assertNotIn("run_sql", names(self.admin))
        refused = self.query("select id from res_partner")
        self.assertTrue(refused.is_error)
        self.admin.company_ids = [(4, other.id)]
        self.assertIn("run_sql", names(self.admin))

        self.env["ir.config_parameter"].sudo().set_param(SQL_PARAM, "no")
        self.assertNotIn("run_sql", names(self.admin), "Off unless an administrator says")

    def test_the_switch_in_the_settings(self):
        Settings = self.env["res.config.settings"]
        self.assertTrue(Settings.create({}).nudge_sql)
        settings = Settings.create({"nudge_sql": False})
        settings.set_values()
        self.assertFalse(self.env["nudge.conversation"]._is_sql_allowed())
