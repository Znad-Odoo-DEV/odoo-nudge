# The recipe index

The Marketplace (Nudge / Configuration / Recipes / Marketplace) lists the recipes of one or more
indexes. An index is one JSON file, hosted by anyone on a static site (GitHub Pages, any web
server). Nudge only reads it, over HTTPS, with a limit of 10 seconds and 2 MB per file.
**Publish** (same menu) writes an index and its recipe files into a ZIP, ready to upload.

## Format

The file is a JSON object with a list of recipes (a bare list of recipes is read too):

```json
{
  "format": 1,
  "updated": "2026-10-17",
  "recipes": [
    {
      "key": "raghad-sales-tours",
      "name": {"en": "Sales tours", "ar": "جولات المبيعات"},
      "description": {"en": "Four tours that teach a new salesperson the quotation.", "ar": "أربع جولات تعلّم مندوب المبيعات الجديد عرض السعر."},
      "kind": "tour",
      "apps": ["sale"],
      "odoo": ["17.0", "18.0", "19.0"],
      "author": "Ebrahim",
      "licence": "LGPL-3",
      "url": "recipes/raghad-sales-tours.json",
      "sha256": "9f2c...64 hexadecimal characters...",
      "size": 18342,
      "updated": "2026-10-17"
    }
  ]
}
```

| Field | Meaning |
| --- | --- |
| `key` | Lower case letters, digits, `.`, `_`, `-`; up to 64. Names the recipe for good: an update keeps it. |
| `name`, `description` | `{"en": ..., "ar": ...}`; a plain string is taken as English. Shown in the language of the person. |
| `kind` | `tour`, `rules`, `lexicon`, `answers`, `prompts` or `pack` (a mix). Any other value is refused. |
| `apps` | Technical names of the modules the recipe needs. A card shows the ones not installed here. |
| `odoo` | Series the recipe is for (`19.0`). Empty means any. A recipe for another series is marked and cannot be installed. |
| `author`, `licence` | Free text. |
| `url` | The recipe file. Relative to the index, or absolute. **HTTPS only.** |
| `sha256` | sha256 of the recipe file, 64 hexadecimal characters. A file that does not match is never installed. |
| `size` | Bytes, for information. The cap of 2 MB applies to what is read. |
| `updated` | `YYYY-MM-DD`. When it is later than the date installed here, the card says **Update available**. |

An entry that breaks a rule (bad `sha256`, unknown `kind`, an address that is not HTTPS, a key listed twice)
is refused and named in the result of the refresh; the others are listed.

## The recipe file

The file `url` points to is a normal Nudge recipe (the output of **Export**): a version header, the
items and a checksum. **Install** downloads it, compares its sha256 with the index, reads it, and opens the
import window: preview, untick what you do not want, import. Everything is made as you, in your company, and
Undo archives it. A source that is not trusted cannot bring files with its recipes.

## Hosting

Put `index.json` and the `recipes` folder from the ZIP together on any static site and add the address of
`index.json` under **Recipe Sources**. Nothing is uploaded by Nudge. The default source is
`https://znad-odoo-dev.github.io/nudge/recipes/index.json`; when it does not exist yet, the Marketplace says
"No index yet" and nothing else changes.

## Private mode

Fetching an index is not an AI call, but it leaves the server. In private mode it is refused unless an
administrator turns on **Allow fetching recipe indexes** (Settings, Nudge).
