from odoo import SUPERUSER_ID, api

# Provider, the address it must still have, how it listens, with which model
SPEECH = {
    "nudge.provider_gemini": (
        "https://generativelanguage.googleapis.com", "chat", "gemini-3.1-flash-lite"),
    "nudge.provider_groq": ("https://api.groq.com", "endpoint", "whisper-large-v3"),
}


def migrate(cr, version):
    """The providers that come with Nudge are data an upgrade leaves alone.
    Those that can listen are told how, unless someone decided otherwise or
    made another provider of them."""
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    for xml_id, (address, mode, model) in SPEECH.items():
        provider = env.ref(xml_id, raise_if_not_found=False)
        if not provider or provider.stt_mode != "none" or provider.stt_model:
            continue
        if (provider.base_url or "").startswith(address):
            provider.write({"stt_mode": mode, "stt_model": model})
