# Content models and export schemas

[Back to the README](../README.md)

## In-memory content (schema version 2)

Read `section.text`, `page.text` or `block.text` for complete plain text without
traversing children or concatenating runs. These are computed, read-only views:
edits to runs/children are reflected immediately and the views are included in
JSON/XML. Repeated aggregate text at ancestor levels is intentional convenience,
not a request to render the same text again.

```python
from pyonenote import ContentBlock, TextRun

paragraph = ContentBlock(
    runs=[TextRun(text="Hello "), TextRun(text="world", style={"bold": True})],
    style={"font": "Calibri", "font_size": 11},
)
container = ContentBlock(kind="container", children=[paragraph])
print(container.text)       # Hello world
print(paragraph.text)       # Hello world
paragraph.runs[1].text = "reader"
print(container.text)       # Hello reader
plain = ContentBlock(text="A plain paragraph")  # convenience constructor
```

- `runs` contains typed `TextRun` objects (`text`, `style`), never structural blocks.
- `children` contains nested `ContentBlock` objects, never inline runs.
- `text` includes the block's runs and all structural descendants. Blocks are
  separated by newlines; table cells by tabs, including empty cells. No automatic
  whitespace trimming or text-based deduplication is performed.
- `inline_text` is a Python convenience property for only the block's own runs.
  It is not serialized; renderers use `runs` and then traverse `children`, never
  render the aggregate `text` in addition to its descendants.
- `source_object_id` points into `document.store.objects`. Original text
  properties remain there, rather than being copied into each block's
  `properties`. Other source metadata is still available on the block.
- `page.title_block` retains the original title subtree, including formatting,
  date and time. `page.blocks` contains the body; `page.content_blocks` is a
  Python view of title date/time blocks followed by the body. Exporters emit
  the page title once. Identical text at different source locations is retained.

Paragraph styles provide defaults for runs; explicit run values override them,
including `false` and the color value `"auto"`. Paragraph `alignment` is
`left`, `center` or `right`, and `direction` is `ltr` or `rtl`. `font_size`,
`space_before`, `space_after` and `line_spacing` are in points. Spacing converts
the documented half-inch float units; line spacing is a minimum where supported.
HTML/PDF, DOCX and ODT apply these supported styles; plain text cannot retain
formatting. Canvas offsets and other layout properties remain metadata.

The in-memory `Document` and debug JSON/XML retain `schema_version: 2`.
Default JSON/XML use the reading schema described below.
Existing consumers should use `runs` instead of looking for `kind="run"` in
`children`, and account for `text` being an aggregate rather than local text.
Loading older JSON migrates run children and plain text into runs. When explicit
`runs` are supplied, serialized `text` is treated as a derived view and ignored
on input; change `runs`/`children` to edit the content. Legacy title layout is
not reconstructed when loading old snapshots; reparse the original `.one`
file to obtain the new title/body separation. Original property `raw` bytes remain available in `Document`, Pickle and
debug JSON/XML.

## Migrating default JSON/XML to readable schema v3

Default exports have `schema_version: 3`, `view: "readable"`, ordered `sections`,
an `attachments` manifest (empty when there are no assets), and applicable
`diagnostics`. JSON and XML both use two-space indentation. XML declares
`schema_version="3"` on `onenote` and retains the typed `field`/`item` mapping.

```python
from pathlib import Path
from pyonenote import ReadableDocument

snapshot = ReadableDocument.model_validate_json(
    Path("exports/document.json").read_text(encoding="utf-8")
)
print(snapshot.sections[0].text)
```

`ReadableDocument` validates content snapshots, not complete parsed-store round
trips. Text is stored as a snapshot, not recomputed after edits. `Document`, its
live text properties, Pickle, and the HTML/Office/PDF/text renderers are unchanged.
Consumers needing raw properties, unknown fields or revision history should pass
`debug=True` / `--debug` and continue loading `Document`. Keep original source
files or debug exports for recovery of uninterpreted properties and canvas data.

Sections, pages and blocks retain full `text` summaries. Empty optional fields
are omitted; empty block text, meaningful `false`/zero values and explicit style
resets remain. IDs on sections/pages identify content; block `source_object_id`
and parser `properties` maps are absent. Styles stay inline.

For a block with one run, `text` and merged `style` replace `runs`. When that block
also has children, `inline_text` identifies its own text (even when empty).
Mixed-format blocks retain ordered `runs`, whose styles override block defaults.
To render a block, use its runs if present, otherwise its `inline_text` when
present, otherwise its `text` only when it has no children; then render children.
Do not render aggregate summaries alongside their descendants.

Only single-child `container` wrappers without own runs, style, attachments or
semantic roles collapse. Multi-child groups, lists/items, tables/rows/cells and
empty cells remain. Repeated text at different locations is retained.
`title_block` retains the formatted title subtree with `roles: ["title"]`;
date/time nodes use `title_date` / `title_time` roles instead of property flags.
Read the title, then title date/time subtrees in their original order, then body
`blocks`. The page `text` already supplies that reading order.

Each manifest entry has `attachment_id`, original `file_name`, `content_type`,
and a portable relative `path` under `assets/`. Blocks reference `attachment_id`.
The file at `path` holds original bytes once per attachment; readable JSON/XML
contains no Base64 payload. Move the document and its assets directory together.

## Property names and object recognition

The catalog includes all 144 property names (135 distinct IDs) in the
[MS-ONE property table](https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-one/e9bf7da8-7aab-4668-be5e-e0c421175e3c),
all [documented property-set JCIDs](https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-one/73d98105-f194-4c05-a795-09840a6d24bf),
and both documented file-data JCIDs. Shared property IDs use their owning
object's JCID to select a name; `name_aliases` retains all published alternatives.
Without a matching owner, child-node properties retain their generic names.

Three additional properties observed in package headers are documented outside
that table, in [MS-ONESTORE Header Cell, section 2.7.2](https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-onestore/07a5dc4d-0d97-4a4c-ab69-aa7957d7115c):
`FileNameCRC` (`0x14001D93`), `FileIdentityGuid` (`0x1C001D94`), and
`FileAncestorIdentityGuid` (`0x1C001D95`). The CRC is an unsigned integer; the two
GUIDs receive readable UUID interpretations while retaining their original bytes.

For supported properties, `value` contains the decoded semantic value, including
readable text, GUID strings and UTC FILETIME strings. `decoded_value` remains
as a compatibility alias for supported interpretations. Original `raw` bytes
remain Python bytes in memory and serialize to uppercase hexadecimal with a
`0x` prefix in JSON/XML (for example, `0x456C69736575` for `Eliseu`).
The `known`, `decoded_value` and `interpretation` fields distinguish catalog
recognition from semantic decoding: `decoded` means a supported interpretation,
`raw` means a recognized but uninterpreted value, and `unknown` means an
identifier absent from the catalog. Malformed supported payloads raise
`ParseError`; experimental decoding also uses `invalid` for malformed candidate payloads.
Uninterpreted properties retain their wire-level `value`; opaque byte values
remain Base64 in JSON, with the exact bytes also available in hexadecimal `raw`.

`TextExtendedAscii` uses the renderer's existing Windows-1252 interpretation.
Undefined bytes remain uninterpreted rather than being replaced. When
`RichEditTextUnicode` is present, it takes precedence and the ASCII alternative
is left uninterpreted, as required by MS-ONE.

For example, `0x1C001C30` is `NotebookManagementEntityGuid`, whose
`value` is a canonical UUID. `0x18001C65` is
`TopologyCreationTimeStamp`: `value` contains a UTC timestamp with seven
fractional digits. The exact integer FILETIME can be recovered from `raw`
using `int.from_bytes(prop.raw, "little")`.
`LastModifiedTimeStamp` receives the same lossless interpretation.
Uninterpreted float bit patterns and structured binary data are not presented
as semantically decoded.

Objects expose `jcid_name`, `jcid_aliases` and `jcid_known` in structural
exports. Shared JCIDs retain all alternatives instead of guessing a subtype.
Recognition does not imply visual rendering support: `UNKNOWN_JCID` identifies
an undocumented visible object, while `UNSUPPORTED_CONTENT` identifies a
recognized object without a renderer. Unknown properties and objects remain
in the parsed model and Pickle; JSON/XML include them only with `--debug`.
Default JSON/XML preserve interpreted reading content. Unknown structural
fields and diagnostics with `UNKNOWN_` codes remain debug-only; literal note
text is never filtered by wording. Other diagnostics appear at the top level.

## Experimental ink property sets

`pyonenote.property_catalog.EXPERIMENTAL_PROPERTY_SETS` contains opt-in research
metadata for drawing containers, strokes, position, color and pen attributes,
including possible child JCIDs and evidence levels. Enable decoding with
`OneNoteExtractor(experimental=True)` or `--experimental`; combine it with
`--debug` to export candidate values, preserved bytes and reconstructed stroke
channels in JSON/XML. Default parsing is unchanged. This does not enable ink
rendering or promote candidates into the documented property catalog. See the
[experimental ink investigation](experimental_ink.md) for exact hexadecimal
IDs, fixture observations, source links and unresolved pencil/brush semantics.
