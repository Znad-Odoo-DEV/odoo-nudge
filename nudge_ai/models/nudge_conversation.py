"""What the company told its AI agents is told to Nudge too."""
from odoo import models

MAX_AGENTS = 10
MAX_TOPICS = 5
MAX_PROMPT_CHARS = 600
MAX_TOPIC_CHARS = 300
MAX_BLOCK_CHARS = 4000


def _cut(text, size):
    text = " ".join((text or "").split())
    return text if len(text) <= size else text[:size - 1] + "…"


class NudgeConversation(models.Model):
    _inherit = "nudge.conversation"

    def _chat_overview(self):
        overview = super()._chat_overview()
        agents = self._ai_agents_text()
        return f"{overview}\n\n{agents}" if agents else overview

    def _ai_agents_text(self):
        """:return: the agents of the AI app, their instructions and topics,
        as text for the model; "" when there is none"""
        agents = self.env["ai.agent"].search([], limit=MAX_AGENTS)
        if not agents:
            return ""
        lines = [
            "## Agents of the AI app",
            "What this company told its own AI agents: follow it where it applies, and name the "
            "agent when you do. search_company_sources reads the documents they were given.",
        ]
        size = sum(len(line) for line in lines)
        for agent in agents:
            parts = [f"- {agent.name}"]
            if agent.subtitle:
                parts.append(f" ({_cut(agent.subtitle, 120)})")
            if agent.system_prompt:
                parts.append(f": {_cut(agent.system_prompt, MAX_PROMPT_CHARS)}")
            entry = "".join(parts)
            for topic in agent.topic_ids[:MAX_TOPICS]:
                if topic.instructions:
                    entry += f"\n  Topic {topic.name}: {_cut(topic.instructions, MAX_TOPIC_CHARS)}"
            if size + len(entry) > MAX_BLOCK_CHARS:
                break
            lines.append(entry)
            size += len(entry)
        return "\n".join(lines)
