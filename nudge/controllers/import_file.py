"""A file matched to the fields of a model and tried, without any AI.

The file is read for this request only and kept nowhere; the person's own
rights apply to the model and to the importer of Odoo. Nothing is saved.
"""
import json

from werkzeug.exceptions import Forbidden

from odoo import http
from odoo.exceptions import AccessError, UserError
from odoo.http import Response, request

from ..llm import attachments
from ..tools import import_match
from ..tools.records import Refused, get_model
from .chat import UPLOAD_CAP, NudgeChat

CSV_NAME_LIMIT = 80


class NudgeImportFile(http.Controller):

    @http.route("/nudge/import/file", type="http", auth="user", methods=["POST"], max_content_length=UPLOAD_CAP)
    def import_file(self, file=None, model=None, step="match", mapping=None, sheet=None, **kwargs):
        """:param file: the Excel or CSV file
        :param model: technical name of the model; without it the likely models come back
        :param step: ``match`` (the mapping), ``check`` (the mapping and the dry run) or
            ``csv`` (the file again with the matched fields as its header row)
        :param mapping: JSON ``[{"index": column, "field": path}]`` the person changed
        :param sheet: the sheet of an Excel file with several
        """
        env = request.env
        if not env.user.has_group("nudge.group_nudge_user"):
            raise Forbidden()
        try:
            upload = NudgeChat._read_upload(file)
            if upload is None:
                raise UserError(env._("Attach the Excel or CSV file first."))
            name, data, mimetype = upload
            kind = attachments.kind_of(name, mimetype)
            if kind != "sheet" and not (kind == "text" and name.lower().endswith((".csv", ".tsv"))):
                raise UserError(env._("Only an Excel or CSV file can be matched to fields."))
            try:
                changes = json.loads(mapping) if mapping else []
            except ValueError:
                changes = []
            if not isinstance(changes, list):
                changes = []
            changes = [entry for entry in changes if isinstance(entry, dict)]
            if step == "csv":
                model_ = get_model(env, model or "", "create")
                content = import_match.corrected_csv(env, model_, name, data, mimetype, changes, sheet or "")
                base = name.rsplit(".", 1)[0][:CSV_NAME_LIMIT] or "import"
                headers = [("Content-Type", "text/csv; charset=utf-8"),
                           ("Content-Disposition", http.content_disposition(f"{base}_matched.csv"))]
                return Response(content, headers=headers)
            result = import_match.card(env, model or "", name, data, mimetype, changes, sheet or "",
                                       check=step == "check")
        except Refused as exc:
            return self._json({"error": str(exc)})
        except (UserError, AccessError) as exc:
            if isinstance(exc, AccessError):
                return self._json({"error": env._("This is not open to you.")})
            return self._json({"error": str(exc)})
        return self._json(result)

    @staticmethod
    def _json(payload):
        return Response(json.dumps(payload, ensure_ascii=False), headers=[
            ("Content-Type", "application/json; charset=utf-8"), ("Cache-Control", "no-store")])
