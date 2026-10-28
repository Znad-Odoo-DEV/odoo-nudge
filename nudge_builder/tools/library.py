"""The template library: the JSON files of data/templates, read and checked."""
import json
import logging
import os

from odoo.tools.misc import file_path

from . import schema

_logger = logging.getLogger(__name__)
FOLDER = "nudge_builder/data/templates"
FALLBACK = "generic"


def _folder():
    return file_path(FOLDER)


def _extra_files(extra):
    """:return: ``{key: path}`` of the template files a plugin gave (``extra``: absolute paths); a key of the
    library itself is never replaced"""
    own = {name[:-5] for name in os.listdir(_folder()) if name.endswith(".json")}
    files = {}
    for path in extra or ():
        key = os.path.basename(path)[:-5]
        if path.endswith(".json") and key not in own:
            files.setdefault(key, path)
    return files


def keys(extra=()):
    """:return: the keys of every template file (and of the plugin files ``extra``), the fallback first"""
    names = sorted({name[:-5] for name in os.listdir(_folder()) if name.endswith(".json")} | set(_extra_files(extra)))
    if FALLBACK in names:
        names.remove(FALLBACK)
        names.insert(0, FALLBACK)
    return names


def load(key, extra=()):
    """:return: the template ``key``, checked
    :raise ValueError: when it is not in the library or is not valid"""
    path = os.path.join(_folder(), f"{key}.json")
    if not os.path.isfile(path) and key in _extra_files(extra):
        path = _extra_files(extra)[key]
    if not os.path.isfile(path) or os.path.basename(path) != f"{key}.json":
        raise ValueError(f"No template {key}")
    with open(path, encoding="utf-8") as handle:
        return schema.validate_template(json.load(handle))


def all_templates(extra=()):
    """:return: ``{key: template}`` of the valid ones; a faulty file is logged and left out"""
    found = {}
    for key in keys(extra):
        try:
            found[key] = load(key, extra)
        except (ValueError, OSError) as exc:
            _logger.warning("Nudge Builder: template %s skipped: %s", key, exc)
    return found


def pick(text, extra=()):
    """:param text: what the person said their business is, in any language
    :return: the key of the template whose words match it best, the fallback otherwise"""
    text = (text or "").lower()
    if not text.strip():
        return FALLBACK
    best, best_score = FALLBACK, 0
    for key, template in all_templates(extra).items():
        score = sum(len(word) for word in [key.replace("_", " ")] + list(template.get("suggest") or [])
                    if word and word.lower() in text)
        if score > best_score:
            best, best_score = key, score
    return best
