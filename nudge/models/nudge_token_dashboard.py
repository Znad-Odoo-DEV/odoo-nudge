from odoo import api, fields, models

# What each line of the usage ledger is, for the dashboard: an answer from a call to a provider, an answer
# that cost no call at all (a line with zero tokens), or something else (speech, a list, a translation)
SOURCES = [
    ("ai", "AI Call"), ("memory", "Remembered Answer"), ("cache", "Answer Cache"),
    ("noai", "Understood Without AI"), ("other", "Other")]


class NudgeTokenDashboard(models.Model):
    """What the answers of Nudge cost in tokens, one row per day, mode, role, provider, model, company
    and source of the answer. Aggregates only, read from the ledger of usage: no message text is read.
    A line of a call to a provider counts as one answer (an Agent answer with several rounds writes one
    line per round); the answers that cost no call (remembered, cached, understood without AI) are
    lines with zero tokens. Speech, lists and translations are "Other" and are not answers."""
    _name = "nudge.token.dashboard"
    _description = "Nudge Token Dashboard"
    _auto = False
    _order = "date desc, id"

    date = fields.Date(readonly=True)
    mode = fields.Selection(
        [("talk", "Talk"), ("agent", "Agent"), ("dev", "Developer")], readonly=True)
    role = fields.Selection(
        [("user", "Nudge User"), ("agent", "Agent User"), ("manager", "Manager"), ("admin", "Administrator")],
        string="Role", readonly=True, help="The highest Nudge role the person holds.")
    source = fields.Selection(SOURCES, string="Answer Source", readonly=True)
    provider_id = fields.Many2one("nudge.provider", string="Provider", readonly=True)
    model_name = fields.Char(string="Model", readonly=True)
    company_id = fields.Many2one("res.company", readonly=True)
    # Nudge Agents: who answered, an agent or Nudge itself (empty); the rehearsals of Try it are left out
    agent_id = fields.Many2one("nudge.agent", string="Agent", readonly=True)
    answers = fields.Integer(readonly=True, help="Answers: calls to a provider and answers given without one.")
    ai_answers = fields.Integer(string="Answers by AI", readonly=True)
    no_ai_answers = fields.Integer(
        string="Answers Without AI", readonly=True, help="Remembered, cached or understood without any AI.")
    input_tokens = fields.Integer(readonly=True)
    output_tokens = fields.Integer(readonly=True)
    cache_read_tokens = fields.Integer(readonly=True)
    cache_write_tokens = fields.Integer(readonly=True)
    tokens = fields.Integer(readonly=True)
    cost = fields.Float(digits=(12, 6), readonly=True)
    tool_calls = fields.Integer(readonly=True, help="Tools the model called in these answers.")

    def init(self):
        cr = self.env.cr
        cr.execute(f"DROP VIEW IF EXISTS {self._table} CASCADE")
        # The mode of the answer; a line with no mode of its own (remembered, cached) takes that of the
        # question before it, then of the run, and a line of a conversation is Talk at the least.
        # The role is the highest of the Nudge groups, or the Administrator of Odoo.
        cr.execute(f"""
            CREATE VIEW {self._table} AS (
                SELECT
                    MIN(u.id) AS id,
                    u.date AS date,
                    COALESCE(m.mode, q.mode,
                             CASE WHEN r.id IS NULL THEN NULL WHEN r.is_developer THEN 'dev' ELSE 'agent' END,
                             CASE WHEN u.conversation_id IS NULL THEN NULL ELSE 'talk' END) AS mode,
                    CASE WHEN g.is_admin THEN 'admin' WHEN g.is_manager THEN 'manager'
                         WHEN g.is_agent THEN 'agent' ELSE 'user' END AS role,
                    CASE u.kind WHEN 'chat' THEN 'ai' WHEN 'memory' THEN 'memory' WHEN 'cache' THEN 'cache'
                                WHEN 'noai' THEN 'noai' ELSE 'other' END AS source,
                    u.provider_id AS provider_id,
                    u.model_name AS model_name,
                    u.company_id AS company_id,
                    u.agent_id AS agent_id,
                    COUNT(*) FILTER (WHERE u.kind IN ('chat', 'memory', 'cache', 'noai')) AS answers,
                    COUNT(*) FILTER (WHERE u.kind = 'chat') AS ai_answers,
                    COUNT(*) FILTER (WHERE u.kind IN ('memory', 'cache', 'noai')) AS no_ai_answers,
                    COALESCE(SUM(u.input_tokens), 0)::int AS input_tokens,
                    COALESCE(SUM(u.output_tokens), 0)::int AS output_tokens,
                    COALESCE(SUM(u.cache_read_tokens), 0)::int AS cache_read_tokens,
                    COALESCE(SUM(u.cache_write_tokens), 0)::int AS cache_write_tokens,
                    COALESCE(SUM(u.tokens), 0)::int AS tokens,
                    COALESCE(SUM(u.cost), 0) AS cost,
                    COALESCE(SUM(CASE WHEN jsonb_typeof(m.tool_calls) = 'array'
                                       AND u.id = (SELECT MIN(f.id) FROM nudge_usage f WHERE f.message_id = m.id)
                                      THEN jsonb_array_length(m.tool_calls) ELSE 0 END), 0)::int AS tool_calls
                FROM nudge_usage u
                LEFT JOIN nudge_message m ON m.id = u.message_id
                LEFT JOIN nudge_conversation c ON c.id = u.conversation_id
                LEFT JOIN nudge_run r ON r.id = c.run_id
                LEFT JOIN LATERAL (
                    SELECT qm.mode FROM nudge_message qm
                    WHERE qm.conversation_id = u.conversation_id AND qm.role = 'user'
                      AND qm.id < COALESCE(u.message_id, 2147483647)
                    ORDER BY qm.id DESC LIMIT 1) q ON m.mode IS NULL AND u.conversation_id IS NOT NULL
                LEFT JOIN LATERAL (
                    SELECT COALESCE(bool_or(d.module = 'base' AND d.name = 'group_system'), FALSE) AS is_admin,
                           COALESCE(bool_or(d.module = 'nudge' AND d.name = 'group_nudge_manager'), FALSE) AS is_manager,
                           COALESCE(bool_or(d.module = 'nudge' AND d.name = 'group_nudge_agent'), FALSE) AS is_agent
                    FROM res_groups_users_rel x
                    JOIN ir_model_data d ON d.model = 'res.groups' AND d.res_id = x.gid
                    WHERE x.uid = u.user_id) g ON TRUE
                -- Nudge Agents: Try it is never billed as real
                WHERE NOT COALESCE(u.rehearsal, FALSE)
                GROUP BY u.date, 3, 4, 5, u.provider_id, u.model_name, u.company_id, u.agent_id
            )""")

    @api.model
    def summary(self, date_from, date_to):
        """The figures of the dashboard over a period, exact (ratios of sums, not means of ratios): the
        whole, and per mode, role, source and provider. Only the rows the person may read are counted.

        :param date_from: first day, included (a date or ``YYYY-MM-DD``)
        :param date_to: last day, included
        :return: ``{"total": row, "by_mode": [rows], "by_role": [...], "by_source": [...],
            "by_provider": [...]}``; a row has its ``name``, answers, ai_answers, no_ai_answers, the four
            token counts, tokens, cost, tool_calls and the ratios ``tokens_per_answer``,
            ``cache_hit_rate`` (cache read / (input + cache read)), ``no_ai_share`` and
            ``tool_calls_per_answer``; a ratio is None when there is nothing to divide by"""
        self.env.flush_all()  # the view reads what the transaction wrote
        rows = self.search([("date", ">=", str(date_from)), ("date", "<=", str(date_to))])
        labels = {
            key: dict(self._fields[key]._description_selection(self.env)) for key in ("mode", "role", "source")}

        def figures(name, subset):
            total = {key: sum(subset.mapped(key)) for key in (
                "answers", "ai_answers", "no_ai_answers", "input_tokens", "output_tokens",
                "cache_read_tokens", "cache_write_tokens", "tokens", "cost", "tool_calls")}
            readable = total["input_tokens"] + total["cache_read_tokens"]
            answers = total["answers"]
            total.update(
                name=name,
                tokens_per_answer=total["tokens"] / answers if answers else None,
                cache_hit_rate=total["cache_read_tokens"] / readable if readable else None,
                no_ai_share=total["no_ai_answers"] / answers if answers else None,
                tool_calls_per_answer=total["tool_calls"] / answers if answers else None)
            return total

        def by(key, label):
            values = sorted({row[key] for row in rows}, key=lambda value: str(value))
            return [figures(label(value), rows.filtered(lambda row, v=value: row[key] == v)) for value in values]

        return {
            "total": figures("", rows),
            "by_mode": by("mode", lambda value: labels["mode"].get(value, "")),
            "by_role": by("role", lambda value: labels["role"].get(value, "")),
            "by_source": by("source", lambda value: labels["source"].get(value, "")),
            "by_provider": by("provider_id", lambda value: value.display_name or ""),
        }
