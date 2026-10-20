import re

from werkzeug.exceptions import Forbidden, NotFound

from odoo import http, release
from odoo.exceptions import AccessError, MissingError, UserError
from odoo.http import request


class StepListController(http.Controller):

    @http.route("/nudge/step/<int:step_id>/open", type="http", auth="user", methods=["GET"])
    def open_list(self, step_id):
        """The link under a list delivered by a task: the list the step looked
        at, filtered the same, for the one who opens it. The step is read as
        that person (the record rules of the log), and the list is an ordinary
        window action, so what they see is what their own rights allow."""
        step = request.env["nudge.step"].browse(step_id)
        try:
            step.check_access("read") if hasattr(step, "check_access") else step.check_access_rights("read")
            step.exists().ensure_one()
            values = step.action_open_list()
        except (AccessError, UserError):
            raise Forbidden() from None
        except (MissingError, ValueError):
            raise NotFound() from None
        # One server action of the module, run as the person with the step as active_id:
        # it builds the window action on the fly, so nothing is stored per step
        action = request.env.ref("nudge.nudge_step_list_action").sudo().id
        if release.version_info[0] >= 18:
            return request.redirect(f"/odoo/{step_id}/action-{action}")
        return request.redirect(f"/web#action={action}&active_id={step_id}")


class ModuleScaffoldController(http.Controller):

    @http.route("/nudge/step/<int:step_id>/module.zip", type="http", auth="user", methods=["GET"])
    def download(self, step_id):
        """The module Nudge drafted for a developer, built again in memory from
        the spec kept in the step they said yes to: nothing is stored, nothing
        is written on the server. Their own step, and Developer mode."""
        from ..tools import scaffold  # noqa: PLC0415
        env = request.env
        if not env["nudge.conversation"]._can_develop():
            raise Forbidden()
        step = env["nudge.step"].browse(step_id)
        try:
            step.check_access("read") if hasattr(step, "check_access") else step.check_access_rights("read")
            step.exists().ensure_one()
        except (AccessError, UserError):
            raise Forbidden() from None
        except (MissingError, ValueError):
            raise NotFound() from None
        if step.user_id != env.user:
            # A manager may read the log of the others, but the module is the property of whoever asked for it
            raise Forbidden()
        values = step.values or {}
        if step.tool != scaffold.SCAFFOLD_TOOL or step.state != "done" or not values.get("spec"):
            raise NotFound()
        files, problems = scaffold.build_and_check(values["spec"], values["version"])
        if problems:
            raise NotFound()
        name = re.sub(r"[^a-z0-9_]", "", str(values["spec"]["name"])) or "module"
        try:
            archive = scaffold.make_zip(files)
        except ValueError:
            raise NotFound() from None
        return request.make_response(archive, headers=[
            ("Content-Type", "application/zip"),
            ("Content-Disposition", f'attachment; filename="{name}.zip"'),
            ("X-Content-Type-Options", "nosniff"),
        ])
