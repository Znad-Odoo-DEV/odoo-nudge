"""Bring nudge/i18n/ar.po up to date with nudge/i18n/nudge.pot.

Export the template first, from the root of Odoo:

    odoo-bin i18n export nudge -c odoo.conf -d <database> -o <path>/nudge/i18n/nudge.pot

then, with the Python of Odoo (it has ``polib``):

    python scripts/update_ar_po.py [new_translations.json]

Translations already in ar.po are kept. The JSON file, ``{"text": "translation"}``,
gives the new ones and corrects the old ones. What is still missing is listed,
and the file is written all the same.
"""
import json
import pathlib
import sys

import polib

I18N = pathlib.Path(__file__).resolve().parent.parent / "nudge" / "i18n"
PLURAL_FORMS = (
    "nplurals=6; plural=n==0 ? 0 : n==1 ? 1 : n==2 ? 2 : "
    "n%100>=3 && n%100<=10 ? 3 : n%100>=11 && n%100<=99 ? 4 : 5;"
)


def main(arguments):
    known = {}
    current = I18N / "ar.po"
    if current.exists():
        known.update({
            entry.msgid: entry.msgstr for entry in polib.pofile(str(current)) if entry.msgstr
        })
    if arguments:
        known.update(json.loads(pathlib.Path(arguments[0]).read_text(encoding="utf-8")))

    catalog = polib.pofile(str(I18N / "nudge.pot"))
    catalog.metadata.update({
        "Language": "ar", "Language-Team": "Arabic", "Plural-Forms": PLURAL_FORMS,
    })
    missing = []
    for entry in catalog:
        entry.msgstr = known.get(entry.msgid, "")
        if not entry.msgstr:
            missing.append(entry.msgid)
        elif entry.msgid.count("%s") != entry.msgstr.count("%s"):
            raise SystemExit(f"Not the same number of %s: {entry.msgid!r}")
    catalog.save(str(current))

    print(f"{len(catalog)} texts, {len(catalog) - len(missing)} translated")
    for text in missing:
        print("missing:", json.dumps(text, ensure_ascii=False))
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
