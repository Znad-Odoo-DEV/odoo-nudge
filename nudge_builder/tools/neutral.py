"""A page of projects that names none: what the template says of its sample projects is dropped when the person
did not tick "Show our project names" (the template's names are placeholders, never the company's)."""
import copy as copymod

# Blocks that name no project: they stay
KEEP = ("s_title", "s_call_to_action", "s_numbers", "s_text_block")


def without_project_names(spec):
    """:param dict spec: a page of a template
    :return: the page without its sample projects: the blocks that show a project (a picture with a name and a
        few words) become one block that speaks of the work in general, from the page's own title and
        description, in each language the template has them in. Other pages are returned as they are."""
    if spec.get("kind") != "projects":
        return spec
    blocks, general = [], None
    for block in spec.get("blocks") or []:
        short = block["snippet"].split(".")[-1]
        if short in KEEP or block.get("kind"):  # a block of the company's own records is theirs
            blocks.append(block)
            continue
        if general is None:
            general = _general_block(spec, block)
            if general:
                blocks.append(general)
    return {**spec, "blocks": blocks or list(spec.get("blocks") or [])[:1]}


def _general_block(spec, block):
    names = spec.get("name") or {}
    description = (spec.get("seo") or {}).get("description") or {}
    copy = {}
    for code, name in names.items():
        text = description.get(code) or description.get("en")
        if text:
            copy[code] = {"title": name, "text": text}
    if "en" not in copy:
        return None
    new = {"snippet": "s_text_image", "copy": copy}
    if block.get("image"):
        new["image"] = block["image"]
    return copymod.deepcopy(new)
