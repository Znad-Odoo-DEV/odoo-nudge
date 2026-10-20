from odoo import fields, models

DEFAULT_HOTKEY = "control+shift+space"
DEFAULT_DICTATION_HOTKEY = "alt+control+d"


class ResUsersSettings(models.Model):
    _inherit = "res.users.settings"

    # Combinations that neither the web client nor the browsers use. The
    # modifiers are written in the order the hotkey service expects.
    nudge_hotkey = fields.Selection(
        [
            ("control+shift+space", "Ctrl + Shift + Space"),
            ("alt+control+space", "Ctrl + Alt + Space"),
            ("alt+control+n", "Ctrl + Alt + N"),
            ("none", "No shortcut"),
        ],
        string="Nudge Shortcut", required=True, default=DEFAULT_HOTKEY,
    )
    # The home page as it was counted, for a few minutes (see tools/home.py): in the database, not in the
    # memory of a worker, so the workers of a server agree
    nudge_home_cache = fields.Json(copy=False, groups="base.group_user")
    nudge_share_screen = fields.Boolean(string="Share Screen with Nudge", default=True)
    # Switched by each person from their own menu: off, the avatar, the button
    # of the top bar and the shortcuts are gone until it is switched on again
    nudge_enabled = fields.Boolean(string="Nudge", default=True)
    nudge_dictation_hotkey = fields.Selection(
        [
            ("alt+control+d", "Ctrl + Alt + D"),
            ("control+shift+d", "Ctrl + Shift + D"),
            ("none", "No shortcut"),
        ],
        string="Nudge Dictation Shortcut", required=True, default=DEFAULT_DICTATION_HOTKEY,
    )
    nudge_voice_language = fields.Selection(
        [
            ("auto", "Language of the interface"),
            ("ar", "Arabic"),
            ("ar-SA", "Arabic (Saudi Arabia)"), ("ar-EG", "Arabic (Egypt)"), ("ar-AE", "Arabic (UAE)"),
            ("ar-SY", "Arabic (Syria)"), ("ar-IQ", "Arabic (Iraq)"), ("ar-JO", "Arabic (Jordan)"),
            ("ar-LB", "Arabic (Lebanon)"), ("ar-PS", "Arabic (Palestine)"), ("ar-KW", "Arabic (Kuwait)"),
            ("ar-QA", "Arabic (Qatar)"), ("ar-BH", "Arabic (Bahrain)"), ("ar-OM", "Arabic (Oman)"),
            ("ar-YE", "Arabic (Yemen)"), ("ar-SD", "Arabic (Sudan)"), ("ar-MA", "Arabic (Morocco)"),
            ("ar-DZ", "Arabic (Algeria)"), ("ar-TN", "Arabic (Tunisia)"),
            ("en", "English"),
            ("en-US", "English (United States)"), ("en-GB", "English (United Kingdom)"),
        ],
        string="Language Spoken to Nudge", required=True, default="auto",
    )
    nudge_speak_answers = fields.Boolean(string="Nudge Reads Its Answers Aloud", default=False)
    nudge_mode = fields.Selection(
        [("talk", "Talk"), ("agent", "Agent"), ("dev", "Developer")],
        string="Nudge Mode", required=True, default="talk",
        help="Talk: Nudge explains and shows. Agent: Nudge does the work, and asks before "
             "each change. Agent is for the people of the group Agent User.",
    )
    nudge_explain = fields.Selection(
        [
            ("ask", "Ask me each time"),
            ("screen", "On the screen, with the pointer"),
            ("text", "In writing, in the conversation"),
        ],
        string="How Nudge Explains", required=True, default="ask",
        help="For a question asked with the screen shared: whether Nudge asks each time, "
             "points on the screen, or writes the steps in the conversation.",
    )
    nudge_theme = fields.Selection(
        [
            ("violet", "Violet"),
            ("teal", "Teal"),
            ("amber", "Amber"),
            ("rose", "Rose"),
            ("slate", "Slate"),
        ],
        string="Nudge Colour", required=True, default="violet",
    )
    nudge_position = fields.Char(
        string="Nudge Position",
        help="Where the avatar was put on the screen, as fractions of its width and height. "
             "Empty puts it in the corner.",
    )
    nudge_validator_muted = fields.Char(
        string="Entry Warnings Muted",
        help="The checks of the warnings at entry time this person asked not to see, by key.")
