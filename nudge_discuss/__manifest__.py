{
    "name": "Nudge: Discuss",
    "summary": "@Nudge in the chatter of any record and in the channels of Discuss: answered as an internal note",
    "description": """
Write @Nudge in the chatter of any record, or in a channel of Discuss, and ask:
"@Nudge summarise this", "@Nudge who has to approve it?". Nudge answers there,
in a few seconds, as an internal note the other employees read too.

It answers as the person who asked, from what they may read of the record:
its fields, the records it links to, the last messages. It looks up nothing
else, so that an answer read by the whole team holds nothing taken from
elsewhere; only the official documentation of Odoo may be searched. Nothing
is changed from the chatter: for that, Nudge points to its panel, in Agent
mode, where every change waits for a yes.

Installed by itself when Nudge and Discuss are both there.
""",
    "category": "Productivity",
    "version": "17.0.1.0.1",
    "author": "Ibrahim Elzenad",
    "license": "OPL-1",
    "images": ["static/description/banner.png"],
    "depends": ["nudge", "mail"],
    "data": [
        "security/ir.model.access.csv",
        "data/nudge_discuss_data.xml",
    ],
    "auto_install": True,
    "installable": True,
}
