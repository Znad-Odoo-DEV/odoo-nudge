import textwrap

from odoo.tests import TransactionCase, tagged

from odoo.addons.nudge import tools
from odoo.addons.nudge.knowledge import rst
from odoo.addons.nudge.llm import prompt
from odoo.addons.nudge.models.nudge_doc import CORPUS_PARAM

from .common import NudgeCase

SAMPLE = textwrap.dedent("""\
    .. meta::
       :description: How quotations are
                     made.

    =================
    Create quotations
    =================

    In Odoo **Sales**, quotations are *sent* to customers.

    .. _sales/quotations/settings:

    Quotation settings
    ==================

    Navigate to :menuselection:`Sales app --> Configuration -->
    Settings`, then :guilabel:`Quotations & Orders`.

    .. image:: create_quotations/settings.png
       :align: center
       :alt: The settings.

    - :guilabel:`Online Signature`: Request a signature. It spans
      two lines.
    - :guilabel:`Online Payment`: Request a payment.

    .. note::
       See :doc:`the templates <quote_template>` and :ref:`sales/quotations/settings`.

    Order lines
    -----------

    .. list-table::
       :header-rows: 1

       * - Column
         - Use
       * - Product
         - What is sold.

    .. tabs::

       .. tab:: Structure

          Pick a product.

    In code
    -------

    Use :func:`~odoo.api.depends`, as in::

        @api.depends('value')
        def _compute_total(self):
            for record in self:
                record.total = record.value * 2

    .. code-block:: python

        total = fields.Float(compute='_compute_total')

    .. automethod:: odoo.models.Model.search
    """)


class TestReader(TransactionCase):

    def test_page_is_read_as_sections(self):
        page = rst.read_page(SAMPLE)
        self.assertEqual(page["title"], "Create quotations")
        self.assertEqual(page["description"], "How quotations are made.")
        headings = [section["heading"] for section in page["sections"]]
        self.assertEqual(headings, [
            "", "Quotation settings", "Quotation settings ▸ Order lines", "Quotation settings ▸ In code",
        ])

        intro, settings, lines, code = page["sections"]
        self.assertIn(
            "Use depends, as in:\n\n@api.depends('value')\ndef _compute_total(self):\n"
            "    for record in self:\n        record.total = record.value * 2",
            code["text"], "A literal block keeps its code and its indentation")
        self.assertIn("total = fields.Float(compute='_compute_total')", code["text"])
        self.assertNotIn("automethod", code["text"], "What Sphinx draws from the source is not here")
        self.assertNotIn("Model.search", code["text"])
        self.assertEqual(intro["text"], "In Odoo Sales, quotations are sent to customers.")
        self.assertEqual(settings["anchor"], "sales/quotations/settings",
                         "The label written before the heading is its anchor")
        self.assertIn("Navigate to Sales app ▸ Configuration ▸ Settings, then Quotations & Orders.",
                      settings["text"], "A role may span two lines")
        self.assertIn("- Online Signature: Request a signature. It spans two lines.", settings["text"])
        self.assertIn("Note:\nSee the templates and settings.", settings["text"])
        self.assertNotIn("settings.png", settings["text"], "Images are left out")
        self.assertNotIn(":guilabel:", settings["text"])
        self.assertEqual(lines["anchor"], "order-lines", "Sphinx makes the anchor from the heading")
        self.assertIn("Column: Use\nProduct: What is sold.", lines["text"], "Tables become lines")
        self.assertIn("Structure:\nPick a product.", lines["text"], "A tab is labelled by its title")

    def test_long_section_is_split(self):
        paragraphs = "\n\n".join(f"Paragraph {index}. " + "word " * 200 for index in range(4))
        page = rst.read_page("=====\nTitle\n=====\n\nLong\n====\n\n" + paragraphs + "\n")
        sections = page["sections"]
        self.assertGreater(len(sections), 1)
        for section in sections:
            self.assertEqual(section["heading"], "Long")
            self.assertLessEqual(len(section["text"]), rst.MAX_PASSAGE_CHARS)
        self.assertIn("Paragraph 0.", sections[0]["text"])
        self.assertIn("Paragraph 3.", sections[-1]["text"])


@tagged("post_install", "-at_install")
class TestDocumentation(NudgeCase, TransactionCase):

    def test_the_documentation_comes_with_the_module(self):
        Page = self.env["nudge.doc.page"]
        self.assertGreater(Page.search_count([]), 900)
        page = Page.search([("path", "=", "sales/sales/sales_quotations/create_quotations")])
        self.assertEqual(page.title, "Create quotations")
        self.assertEqual(page.app, "sales")
        self.assertEqual(page.category, "sales")
        self.assertIn(" sale ", page.modules)
        self.assertEqual(
            page.url,
            "https://www.odoo.com/documentation/19.0/applications/sales/sales/sales_quotations/"
            "create_quotations.html")
        self.assertGreater(page.passage_count, 3)
        self.assertGreater(self.env["nudge.doc.passage"].search_count([]), 5000)

        # The developer documentation, about the framework rather than an app
        self.assertGreater(Page.search_count([("category", "=", "developer")]), 100)
        orm = Page.search([("path", "=", "developer/reference/backend/orm")])
        self.assertEqual(orm.title, "ORM API")
        self.assertEqual(orm.app, "reference")
        self.assertEqual(orm.url, "https://www.odoo.com/documentation/19.0/developer/reference/backend/orm.html")
        self.assertFalse(orm.modules, "Not about an app: never offered for a screen")
        computed = orm.passage_ids.filtered(lambda passage: "Computed Fields" in (passage.heading or ""))
        self.assertIn("@api.depends('value', 'tax')\ndef _compute_total(self):", computed[0].body,
                      "Code is kept as written")
        self.env.cr.execute("SELECT count(*) FROM nudge_doc_passage WHERE search_vector IS NULL")
        self.assertEqual(self.env.cr.fetchone()[0], 0, "Postgres keeps the text vector itself")
        self.assertTrue(Page._is_loaded())

    def test_search_finds_the_right_pages(self):
        Passage = self.env["nudge.doc.passage"]
        found = Passage._search_text("quotation template")
        self.assertTrue(found)
        self.assertEqual(found[0].app, "sales")
        self.assertIn("template", found[0].body.lower())

        found = Passage._search_text("reordering rules")
        self.assertEqual(found[0].page_id.category, "inventory_and_mrp")

        found = Passage._search_text("reordering rules", app="inventory")
        self.assertTrue(found)
        self.assertEqual(set(found.mapped("app")), {"inventory"})

        found = Passage._search_text("computed field depends")
        self.assertEqual(found[0].page_id.category, "developer", "Technical words find the developer docs")

        found = Passage._search_text("record rules", app="reference")
        self.assertTrue(found)
        self.assertEqual(set(found.mapped("app")), {"reference"})

        self.assertFalse(Passage._search_text(""))
        self.assertFalse(Passage._search_text("   "))
        self.assertFalse(Passage._search_text("كيف حالك"), "Other scripts with no known word find nothing, quietly")
        self.assertFalse(Passage._search_text("xqzv wprtk"))

    def test_the_screen_puts_its_app_first(self):
        Passage = self.env["nudge.doc.passage"]
        # Studio's approval rules match "approval" best; on a Time Off screen,
        # the approvals of Time Off come first
        plain = Passage._search_text("approval", limit=3)
        boosted = Passage._search_text("approval", modules=["hr_holidays"], limit=3)
        self.assertTrue(plain and boosted)
        self.assertNotIn(" hr_holidays ", plain[0].modules or "")
        self.assertIn(" hr_holidays ", boosted[0].modules)

    def test_passages_are_rendered_with_their_source(self):
        found = self.env["nudge.doc.passage"]._search_text("quotation template", limit=2)
        text = found._render()
        self.assertTrue(text.startswith("## "))
        self.assertIn("Source: https://www.odoo.com/documentation/19.0/applications/", text)
        self.assertEqual(text.count("Source: "), len(found))
        short = found._render(max_chars=300)
        self.assertLessEqual(len(short), 300)
        self.assertEqual(short.count("Source: "), 1, "At least one passage, cut to the size")

    def test_the_tool(self):
        env = self.env(user=self.user_a)
        for mode in ("talk", "agent"):
            self.assertIn("search_odoo_documentation",
                          [tool.name for tool in tools.base.available(env, mode)])

        result = tools.base.call(env, "search_odoo_documentation",
                                 {"query": "quotation template", "app": "sales"})
        self.assertFalse(result.is_error)
        self.assertIn("Source: https://www.odoo.com/documentation/19.0/applications/sales/", result.content)

        result = tools.base.call(env, "search_odoo_documentation", {"query": "xqzv wprtk"})
        self.assertFalse(result.is_error)
        self.assertIn("Nothing in the documentation matches", result.content)

        result = tools.base.call(env, "search_odoo_documentation", {"query": "x", "app": "nowhere"})
        self.assertTrue(result.is_error, "The schema keeps the app to the known ones")

    def test_pages_of_the_app_on_the_screen(self):
        Page = self.env["nudge.doc.page"]
        self.assertEqual(Page._modules_of_model("res.partner"), ["base"])
        self.assertEqual(Page._modules_of_model("nowhere.model"), [])
        hints = Page._hint_for_model("res.partner")
        self.assertTrue(hints)
        self.assertLessEqual(len(hints), 6)
        for hint in hints:
            self.assertEqual(set(hint), {"title", "url"})
        self.assertEqual(Page._hint_for_model("nudge.doc.page"), [],
                         "Nothing is said about an app without documentation")
        self.assertEqual(Page._hint_for_model(None), [])

        values = self.env["nudge.conversation"]._chat_context_values(model_name="res.partner")
        self.assertEqual(values["documentation"], hints)
        self.assertNotIn("documentation", self.env["nudge.conversation"]._chat_context_values())

    def test_loading_again_only_when_the_file_changed(self):
        Page = self.env["nudge.doc.page"]
        count = Page.search_count([])
        self.assertFalse(Page._load_bundled(), "The same file is not loaded twice")
        Param = self.env["ir.config_parameter"].sudo()
        Param.set_param(CORPUS_PARAM, "stale")
        self.env["nudge.profile"]._cron_refresh()
        self.assertEqual(Page.search_count([]), count, "The cron loads it again when it changed")
        self.assertTrue(Page._is_loaded())
        self.assertIn("pages of the official documentation of Odoo 19.0", Page._summary())

    def test_the_model_is_told(self):
        self.assertIn("search_odoo_documentation", prompt.SYSTEM_PERSONA)
        self.assertIn('"documentation"', prompt.SYSTEM_PERSONA)
