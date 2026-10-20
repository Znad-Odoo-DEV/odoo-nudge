import base64

from odoo.tests import new_test_user

from odoo.addons.nudge.llm.fake_adapter import FakeAdapter


class WebsiteCase:
    """Mixin: a website with the assistant on, a public file, a published
    product and a customer of the portal, all named after Raghad or Ebrahim."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.website = cls.env["website"].search([], limit=1)
        cls.website.nudge_assistant = True
        cls.public_user = cls.website.user_id
        cls.public_env = cls.env(user=cls.public_user)
        Provider = cls.env["nudge.provider"]
        Provider.search([]).active = False
        cls.provider = Provider.create({
            "name": "Test provider", "kind": "openai_compatible", "base_url": "https://llm.invalid/v1",
            "api_key": "sk-test-secret", "model_name": "test-model"})
        cls.public_file = cls.make_file("Raghad Shipping Policy", "Shipping to Dubai takes three days. Returns within thirty days.", True)
        cls.private_file = cls.make_file("Ebrahim Salary Policy", "Salary scale of the engineers is confidential.", False)
        if "website_published" in cls.env["product.template"]._fields:
            cls.chair = cls.env["product.template"].create({
                "name": "Raghad Oak Chair", "list_price": 120.0, "website_published": True, "sale_ok": True})
            cls.hidden = cls.env["product.template"].create({
                "name": "Ebrahim Secret Chair", "list_price": 99.0, "website_published": False, "sale_ok": True})
        cls.portal_user = new_test_user(cls.env, login="portal_raghad", groups="base.group_portal")
        cls.portal_user.partner_id.write({"name": "Raghad Customer", "email": "raghad@nudge.example"})

    @classmethod
    def make_file(cls, name, text, public):
        return cls.env["nudge.source"].create({
            "name": name, "file_name": f"{name}.txt", "is_public": public,
            "file": base64.b64encode(text.encode())})

    def use_adapter(self, **kwargs):
        adapter = FakeAdapter(**kwargs)
        self.patch(type(self.env["nudge.provider"]), "_get_adapter", lambda provider: adapter)
        return adapter

    def need(self, model):
        if model not in self.env:
            self.skipTest(f"{model} is not installed")
