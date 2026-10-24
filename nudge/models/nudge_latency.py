import pytz

import odoo
from odoo import _, api, fields, models
from odoo.exceptions import AccessError

# A median or a 95th percentile has no sum: the groups of a pivot show the mean of the rows below
if odoo.release.version_info[0] >= 18:
    MEAN = {"aggregator": "avg"}
else:
    MEAN = {"group_operator": "avg"}

# Columns of nudge_message that other features fill: read when they exist, NULL until then, so
# nothing is shown for them before there is data. Time to the first line of the answer
# (streaming) and the wait in the queue (the waiting room, NP-04): add the Integer field with
# one of these names (milliseconds) to nudge.message and the panel shows it on the next update.
HOOK_COLUMNS = ("first_line_ms", "queue_wait_ms")
# What the ledger tells of each answer: the provider, the tier that gave it and what that saved (S6)
USAGE_BY_MESSAGE = (
    "SELECT message_id, MIN(provider_id) AS provider_id, MIN(tier) AS tier, SUM(cost_saved) AS cost_saved "
    "FROM nudge_usage WHERE message_id IS NOT NULL GROUP BY message_id")
# The same for the answer ``m`` alone, through the index of message_id
USAGE_OF_MESSAGE = (
    "SELECT MIN(provider_id) AS provider_id, MIN(tier) AS tier, SUM(cost_saved) AS cost_saved "
    "FROM nudge_usage WHERE message_id = m.id")


class NudgeLatencyReport(models.Model):
    """How fast and how dear the answers are, one row per day, mode, provider, person and
    company. Aggregates only: no message text is read. The percentiles are those of the
    answers of the row (an answer that cost no call, like a remembered one, has no duration
    and is not counted in them)."""
    _name = "nudge.latency.report"
    _description = "Nudge Latency and Cost"
    _auto = False
    _order = "date desc, id"

    date = fields.Date(readonly=True)
    mode = fields.Selection(
        [("talk", "Talk"), ("agent", "Agent"), ("dev", "Developer")], readonly=True)
    provider_id = fields.Many2one("nudge.provider", string="Provider", readonly=True)
    user_id = fields.Many2one("res.users", string="Person", readonly=True)
    company_id = fields.Many2one("res.company", readonly=True)
    # S6: which model answered, and what the economy tier saved (from the ledger of usage)
    tier = fields.Selection([("main", "Main Model"), ("economy", "Economy Model")], readonly=True)
    cost_saved = fields.Float(
        string="Saved", digits=(12, 6), readonly=True,
        help="What the economy tier saved: its tokens at the prices of the model of the provider, less "
             "what they cost. An estimate.")
    answers = fields.Integer(readonly=True)
    timed_answers = fields.Integer(
        string="Timed Answers", readonly=True, help="Answers that called the provider and have a duration.")
    errors = fields.Integer(readonly=True)
    refusals = fields.Integer(readonly=True)
    tokens = fields.Integer(readonly=True)
    cost = fields.Float(digits=(12, 6), readonly=True)
    duration_p50 = fields.Float(string="Duration p50 (ms)", readonly=True, **MEAN)
    duration_p95 = fields.Float(string="Duration p95 (ms)", readonly=True, **MEAN)
    duration_avg = fields.Float(string="Duration Mean (ms)", readonly=True, **MEAN)
    first_line_samples = fields.Integer(
        string="First Line Samples", readonly=True, help="Answers with a time to the first line.")
    first_line_p50 = fields.Float(string="First Line p50 (ms)", readonly=True, **MEAN)
    first_line_p95 = fields.Float(string="First Line p95 (ms)", readonly=True, **MEAN)
    queue_wait_samples = fields.Integer(
        string="Queue Wait Samples", readonly=True, help="Answers that waited in the queue.")
    queue_wait_p50 = fields.Float(string="Queue Wait p50 (ms)", readonly=True, **MEAN)
    queue_wait_p95 = fields.Float(string="Queue Wait p95 (ms)", readonly=True, **MEAN)

    @api.model
    def _hook_sql(self, column):
        """:return: the (samples, p50, p95) expressions of a column other features fill, or
        NULLs while no model has it"""
        self.env.cr.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = 'nudge_message' AND column_name = %s",
            (column,))
        if not self.env.cr.fetchone():
            return "0", "NULL::float", "NULL::float"
        value = f"m.{column}"
        timed = f"FILTER (WHERE {value} > 0)"
        return (
            f"COUNT(*) {timed}",
            f"percentile_cont(0.5) WITHIN GROUP (ORDER BY {value}) {timed}",
            f"percentile_cont(0.95) WITHIN GROUP (ORDER BY {value}) {timed}")

    def init(self):
        cr = self.env.cr
        # The view is rebuilt on every update: a column added by another feature is picked up
        cr.execute(f"DROP VIEW IF EXISTS {self._table} CASCADE")
        first = self._hook_sql("first_line_ms")
        queue = self._hook_sql("queue_wait_ms")
        # The id of a row is the first answer of its group: it stays while people chat (a number
        # over the groups changed between the search and the read of a list). With no numbering
        # over the whole, the filters of a read (the last 30 days of the menu, the companies)
        # reach the answers, and only theirs are grouped and looked up in the ledger
        cr.execute(f"""
            CREATE VIEW {self._table} AS (
                SELECT
                    MIN(m.id) AS id,
                    (m.create_date AT TIME ZONE 'UTC')::date AS date,
                    m.mode AS mode,
                    u.provider_id AS provider_id,
                    c.user_id AS user_id,
                    c.company_id AS company_id,
                    u.tier AS tier,
                    COUNT(*) AS answers,
                    COUNT(*) FILTER (WHERE m.duration_ms > 0) AS timed_answers,
                    COUNT(*) FILTER (WHERE m.state = 'error') AS errors,
                    COUNT(*) FILTER (WHERE m.state = 'refused') AS refusals,
                    COALESCE(SUM(COALESCE(m.input_tokens, 0) + COALESCE(m.output_tokens, 0)
                                 + COALESCE(m.cache_read_tokens, 0) + COALESCE(m.cache_write_tokens, 0)), 0)::int AS tokens,
                    COALESCE(SUM(m.cost), 0) AS cost,
                    COALESCE(SUM(u.cost_saved), 0) AS cost_saved,
                    percentile_cont(0.5) WITHIN GROUP (ORDER BY m.duration_ms)
                        FILTER (WHERE m.duration_ms > 0) AS duration_p50,
                    percentile_cont(0.95) WITHIN GROUP (ORDER BY m.duration_ms)
                        FILTER (WHERE m.duration_ms > 0) AS duration_p95,
                    AVG(m.duration_ms) FILTER (WHERE m.duration_ms > 0) AS duration_avg,
                    {first[0]} AS first_line_samples,
                    {first[1]} AS first_line_p50,
                    {first[2]} AS first_line_p95,
                    {queue[0]} AS queue_wait_samples,
                    {queue[1]} AS queue_wait_p50,
                    {queue[2]} AS queue_wait_p95
                FROM nudge_message m
                JOIN nudge_conversation c ON c.id = m.conversation_id
                LEFT JOIN LATERAL ({USAGE_OF_MESSAGE}) u ON TRUE
                WHERE m.role = 'assistant'
                GROUP BY 2, 3, 4, 5, 6, 7
            )""")

    @api.model
    def panel(self, date_from, date_to):
        """The numbers of the panel over a period, exact (the percentiles are those of every
        answer of the period, not a mean of the daily ones): per mode, per provider, per
        person, per company and per day.

        :param date_from: first day, included (a date or ``YYYY-MM-DD``)
        :param date_to: last day, included
        :return: ``{"by_mode": [...], "by_provider": [...], "by_user": [...], "by_company": [...],
            "by_day": [...], "by_tier": [...]}``; a row has its name, answers, errors, refusals, tokens,
            cost, ``cost_saved`` (S6),
            ``duration_p50``, ``duration_p95``, and the first line and queue wait ones, which
            are None until the answers carry them"""
        if not self.env.user.has_group("nudge.group_nudge_manager"):
            raise AccessError(_("The latency and cost of Nudge are for its managers."))
        first = self._hook_sql("first_line_ms")
        queue = self._hook_sql("queue_wait_ms")
        self.env.flush_all()  # the raw queries below read what the transaction wrote
        companies = self.env.companies.ids
        # A translated name is stored as json, one entry per language
        provider_name = "p.name->>'en_US'" if self.env["nudge.provider"]._fields["name"].translate else "p.name"
        # The days of the period are the manager's days, as the dates they chose: in Dubai the evening
        # of the 24th is already the 25th. The zone is one of pytz's names, never free text
        tz = self.env.context.get("tz") or self.env.user.tz or "UTC"
        if tz not in pytz.all_timezones_set:
            tz = "UTC"
        local_day = f"((m.create_date AT TIME ZONE 'UTC') AT TIME ZONE '{tz}')::date"

        def group(label_sql, join=""):
            self.env.cr.execute(f"""
                SELECT {label_sql} AS label,
                       COUNT(*), COUNT(*) FILTER (WHERE m.state = 'error'),
                       COUNT(*) FILTER (WHERE m.state = 'refused'),
                       COALESCE(SUM(COALESCE(m.input_tokens, 0) + COALESCE(m.output_tokens, 0)
                                    + COALESCE(m.cache_read_tokens, 0) + COALESCE(m.cache_write_tokens, 0)), 0),
                       COALESCE(SUM(m.cost), 0), COALESCE(SUM(u.cost_saved), 0),
                       percentile_cont(0.5) WITHIN GROUP (ORDER BY m.duration_ms)
                           FILTER (WHERE m.duration_ms > 0),
                       percentile_cont(0.95) WITHIN GROUP (ORDER BY m.duration_ms)
                           FILTER (WHERE m.duration_ms > 0),
                       {first[1]}, {first[2]}, {queue[1]}, {queue[2]}
                FROM nudge_message m
                JOIN nudge_conversation c ON c.id = m.conversation_id
                LEFT JOIN ({USAGE_BY_MESSAGE}) u ON u.message_id = m.id
                {join}
                WHERE m.role = 'assistant'
                  AND {local_day} BETWEEN %s AND %s
                  AND (c.company_id IS NULL OR c.company_id = ANY(%s))
                GROUP BY 1 ORDER BY 1""", (str(date_from), str(date_to), companies))
            keys = ("name", "answers", "errors", "refusals", "tokens", "cost", "cost_saved",
                    "duration_p50", "duration_p95", "first_line_p50", "first_line_p95",
                    "queue_wait_p50", "queue_wait_p95")
            return [dict(zip(keys, row)) for row in self.env.cr.fetchall()]

        return {
            "by_mode": group("COALESCE(m.mode, '')"),
            "by_provider": group(f"COALESCE({provider_name}, '')", "LEFT JOIN nudge_provider p ON p.id = u.provider_id"),
            "by_user": group(
                "COALESCE(pa.name, '')",
                "LEFT JOIN res_users ru ON ru.id = c.user_id LEFT JOIN res_partner pa ON pa.id = ru.partner_id"),
            "by_company": group("COALESCE(co.name, '')", "LEFT JOIN res_company co ON co.id = c.company_id"),
            "by_day": group(f"{local_day}::text"),
            # S6: the model of the provider and its economy tier, side by side
            "by_tier": group("COALESCE(u.tier, '')"),
        }
