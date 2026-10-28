"""The plugin points of the Website Builder: what another module may add without editing this one.

A plugin module depends on ``nudge_builder``, ``_inherit``s this abstract model and extends the three
methods below (always ``super()`` first, so several plugins add up)::

    class MyPlugin(models.AbstractModel):
        _inherit = "nudge.builder.plugin"

        @api.model
        def _plugin_kinds(self):
            return {**super()._plugin_kinds(), "my_kind": {"model": "my.model", ...}}

The registry is the Odoo registry itself: a plugin counts for a database only when its module is installed
there, and with no plugin installed every method returns nothing, so the builder behaves as it always did.
Nothing a plugin gives is trusted: a faulty entry is logged and left out, it never stops a build.

``_plugin_kinds``: ``{key: spec}``, a kind of content (the section of a page that shows records). The key is
lower case, may not be one of the core's kinds and is what a template block names (``{"kind": key}``). A spec:

- ``model`` (required): the model of the records. It carries ``site_id`` (Many2one to the site), ``published``,
  ``featured`` and ``name``, which is what inheriting ``nudge.builder.content`` gives. A kind whose model is not
  in the registry is ignored.
- ``domain``: extra domain on that model (default none). A model may serve more than one kind.
- ``layouts``: the three layouts of Classic, Bold and Soft (default grid, list, showcase), among those of
  ``tools/content_sections.py``: grid, list, showcase, carousel.
- ``snippet``: the Odoo snippet the section stands for (default s_text_block).
- ``item(site, record, item, lang)``: adds to the plain dict the renderer reads (``meta`` is a list of words for
  the facts line, ``actions`` a list of ``{"url", "text"}`` buttons); returns the dict. The AI is never given more
  than the record's name: it writes from the records' facts only and never invents one.
- ``render(items, layout, direction, title, text, cx, uid)``: the lxml section; default
  ``content_sections.render_kind``. Return None when there is nothing to show: a section with no records is not placed.
- ``labels(env)``: ``{key: translated word}`` added to ``cx["labels"]``, the words the renderer writes itself.

``_plugin_checks``: a list of ``{"group", "label", "function", "texts"}`` for Site Check. ``group`` is a new key
(also added with ``selection_add`` to ``check_group`` of ``nudge.builder.check.finding``), ``function(env, website,
pages, **options)`` returns findings made with ``tools.checks.finding``, ``label`` is the translated name of the
group and ``texts(env, count, extra)`` returns ``{finding key: sentence}``.

``_plugin_templates``: a list of absolute paths of template JSON files (schema 1, see ``tools/schema.py``). A block
of a template may name a plugin's kind; the template may carry ``"listing_mode": true`` to show the price,
bedrooms and area fields of a project like the real estate template does.
"""
import logging
import os
import re

from odoo import api, models

from .nudge_builder_content import ALL_KINDS

_logger = logging.getLogger(__name__)

KEY = re.compile(r"^[a-z][a-z0-9_]{1,30}$")
KNOWN_LAYOUTS = ("grid", "list", "showcase", "carousel")
DEFAULT_LAYOUTS = ("grid", "list", "showcase")


class NudgeBuilderPlugin(models.AbstractModel):
    _name = "nudge.builder.plugin"
    _description = "Nudge Builder plugin points"

    # ------------------------------------------------------------------ what a plugin extends
    @api.model
    def _plugin_kinds(self):
        """:return: ``{key: spec}`` of the kinds of content the installed plugins add"""
        return {}

    @api.model
    def _plugin_checks(self):
        """:return: the checks of Site Check the installed plugins add"""
        return []

    @api.model
    def _plugin_templates(self):
        """:return: the absolute paths of the template files the installed plugins add"""
        return []

    # ------------------------------------------------------------------ what the builder reads (validated)
    @api.model
    def plugin_kinds(self):
        """:return: ``{key: spec}`` of the valid kinds, every spec complete"""
        found = {}
        for key, spec in (self._plugin_kinds() or {}).items():
            layouts = tuple((spec or {}).get("layouts") or DEFAULT_LAYOUTS)
            problem = None
            if not KEY.fullmatch(str(key)) or key in ALL_KINDS or key in ("contact", "hours", "quote", "whatsapp"):
                problem = "the key is not allowed"
            elif not isinstance(spec, dict) or spec.get("model") not in self.env:
                problem = "its model is not there"
            elif len(layouts) != 3 or any(layout not in KNOWN_LAYOUTS for layout in layouts):
                problem = "the layouts must be three of %s" % ", ".join(KNOWN_LAYOUTS)
            elif key in found:
                problem = "two plugins give the same key"
            if problem:
                _logger.warning("Nudge Builder plugin kind %r skipped: %s", key, problem)
                continue
            found[key] = {"domain": [], "snippet": "s_text_block", "item": None, "render": None, "labels": None,
                          **spec, "layouts": layouts, "key": key}
        return found

    @api.model
    def plugin_checks(self):
        """:return: the valid checks; a group of the core cannot be replaced"""
        from ..tools import checks as core_checks
        found = []
        for check in self._plugin_checks() or []:
            group = check.get("group") if isinstance(check, dict) else None
            if (not isinstance(group, str) or not KEY.fullmatch(group) or group in core_checks.GROUPS
                    or not callable(check.get("function")) or any(group == other["group"] for other in found)):
                _logger.warning("Nudge Builder plugin check %r skipped", group)
                continue
            found.append(check)
        return found

    @api.model
    def plugin_template_paths(self):
        """:return: the paths of the template files that exist"""
        return [path for path in (self._plugin_templates() or []) if path and os.path.isfile(path)]
