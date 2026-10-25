{
    "name": "Nudge",
    "version": "17.0.1.59.3",
    "category": "Productivity",
    "summary": "An AI companion that lives in the Odoo web client",
    "description": """
Nudge
=====
A small, friendly AI companion inside the Odoo web client.

It sees the screen you are on when you ask, points at the buttons and fields
to use, takes you to the right screen, and knows how this database is built:
its custom modules, the fields they add, how each app is configured.

Talk to it instead of typing, and dictate in the fields of Odoo, in Arabic and
in English.

In Agent mode it does the work: it looks up records, creates and changes them,
presses buttons, with your rights, and asks you before each change. What it
did is kept in a log. Long work is done in the background: you are told when
it is done, and when your answer is needed.

Works with any OpenAI-compatible AI provider (Gemini, Groq, OpenRouter, a local
Ollama) and with Anthropic Claude.

No Python package is required. Some optional ones add a feature when they are
installed on the server: anthropic (the Claude provider), onnxruntime,
tokenizers and numpy (search by meaning, on your own server), phonenumbers (the
phone format warning) and the program ffmpeg (voice notes and meeting audio).
Without them the rest works and the feature says what is missing.
""",
    "author": "Ibrahim Elzenad",
    "website": "https://znad-odoo-dev.github.io",
    "support": "https://github.com/Znad-Odoo-DEV/odoo-nudge/issues",
    "license": "OPL-1",
    "images": ["static/description/banner.png"],
    "depends": ["web", "bus"],
    "data": [
        "security/nudge_security.xml",
        "security/nudge_auditor_security.xml",
        "security/ir.model.access.csv",
        "security/nudge_tour_security.xml",
        "security/nudge_health_security.xml",
        "security/nudge_report_security.xml",
        "security/nudge_note_security.xml",
        "security/nudge_credential_security.xml",
        "security/nudge_prompt_security.xml",
        "data/nudge_provider_data.xml",
        "data/nudge_cron_data.xml",
        "data/nudge_compliance_cron.xml",
        "data/nudge_recipe_market_data.xml",
        "data/nudge_recipe_publisher_data.xml",
        "data/nudge_tour_data.xml",
        "data/nudge_coach_data.xml",
        "data/nudge_latency_data.xml",
        "views/nudge_provider_views.xml",
        "views/nudge_conversation_views.xml",
        "views/nudge_run_views.xml",
        "views/nudge_task_views.xml",
        "views/nudge_usage_views.xml",
        "views/nudge_profile_views.xml",
        "views/nudge_doc_views.xml",
        "views/nudge_source_views.xml",
        "views/nudge_memory_views.xml",
        "views/nudge_translation_views.xml",
        "views/nudge_question_views.xml",
        "views/nudge_validator_views.xml",
        "views/nudge_setup_wizard_views.xml",
        "views/nudge_latency_views.xml",
        "views/res_config_settings_views.xml",
        "views/nudge_menus.xml",
        "views/nudge_answer_cache_views.xml",
        "data/nudge_answer_cache_cron.xml",
        "views/nudge_latency_menu.xml",
        "views/nudge_setup_wizard_menu.xml",
        "views/nudge_home_views.xml",
        "views/nudge_tour_views.xml",
        "views/nudge_health_views.xml",
        "views/nudge_report_views.xml",
        "views/nudge_recipe_views.xml",
        "views/nudge_coach_views.xml",
        "views/nudge_recipe_market_views.xml",
        "views/nudge_recipe_catalogue_views.xml",
        "views/nudge_compliance_feed_views.xml",
        "views/nudge_prompt_views.xml",
        "data/nudge_prompt_data.xml",
        "data/nudge_auditor_cron.xml",
        "views/nudge_built_views.xml",
        "views/nudge_slot_views.xml",
        "views/nudge_auditor_views.xml",
        "views/nudge_coverage_views.xml",
        "data/nudge_rule_cron.xml",
        "views/nudge_rule_views.xml",
        "security/nudge_skill_security.xml",
        "data/nudge_skill_data.xml",
        "data/nudge_skill_data_np03.xml",
        "views/nudge_skill_views.xml",
        "views/res_config_settings_plan_views.xml",
        "views/nudge_plugin_views.xml",
        "views/nudge_memory_menu.xml",
        "views/nudge_provider_tier_views.xml",
        "views/nudge_screens_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "nudge/static/src/**/*",
            # Don't include dark mode files in light mode
            ("remove", "nudge/static/src/**/*.dark.scss"),
        ],
        "web.assets_web_dark": [
            "nudge/static/src/**/*.dark.scss",
        ],
        "web.assets_tests": [
            "nudge/static/tests/tours/**/*",
        ],
    },
    "post_init_hook": "post_init_hook",
    "uninstall_hook": "uninstall_hook",
    "application": True,
    "installable": True,
}
