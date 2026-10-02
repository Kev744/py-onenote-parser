# pyOneNote

A Python library and command-line tool for reading Microsoft OneNote files and
exporting their text, structure and attachments. Use it to turn notes into readable
documents or inspect the underlying binary objects without modifying the source.

## What it includes

- **Binary parsers** for modern OneNote revision and package stores, plus saved
  FSSHTTP/FSSHTTPB responses.
- **Pydantic models** for sections, pages, formatted text, lists, tables and
  attachments, with raw properties and revision history available for inspection.
- **Nine export formats:** JSON, XML, Pickle, HTML, Markdown, TXT, PDF, DOCX and ODT.
- **Experimental ink decoding** for stroke coordinates and candidate pen properties.
  Drawing rendering is not yet supported.

## Quick start

Requires **Python 3.10+** and [uv](https://docs.astral.sh/uv/getting-started/installation/).
From the repository root:

```sh
uv sync --locked
uv run pyonenote "notes.one" --output exports/notes
```

The default output is readable JSON and HTML. Select formats with repeated
`--format` options, or use `--format all`:

```sh
uv run pyonenote "notes.one" --output exports/documents --format markdown --format docx
uv run pyonenote "notes.one" --output exports/inspection --format json --debug
```

| Option | Purpose |
| --- | --- |
| `--format`, `-f` | Select an export format; `all` selects all nine |
| `--debug` | Include the complete parsed store in JSON/XML |
| `--experimental` | Decode tentative ink properties; combine with `--debug` to inspect them |
| `--overwrite` | Allow replacing existing output files |
| `--max-bytes` | Limit input size; default: 512 MiB |
| `--timeout` | Set the HTTP timeout; default: 30 seconds |

The source may also be a direct HTTP(S) download URL. This does not sign into
Microsoft accounts or synchronize notebooks. Run `uv run pyonenote --help` for
all arguments. Choose a fresh output folder to avoid mixing old and new assets.

**PDF setup:** WeasyPrint needs native libraries such as Pango. Follow its
[installation guide](https://doc.courtbouillon.org/weasyprint/stable/first_steps.html),
especially on Windows; set `WEASYPRINT_DLL_DIRECTORIES` when needed. Other export
formats do not require those PDF libraries.

## Python usage

```python
from pyonenote import OneNoteExtractor

extractor = OneNoteExtractor()
document = extractor.parse("notes.one")  # Also accepts a Path or binary stream.
# document = extractor.parse_url("https://example.org/notes.one")

for section in document.sections:
    for page in section.pages:
        print(page.title, page.text)

result = extractor.export(document, "exports/python", ["json", "html", "docx"])
print(result.files)

for diagnostic in document.store.diagnostics:
    print(diagnostic.code, diagnostic.message)
```

`document.sections` provides interpreted content; `document.store` retains
objects, properties, revisions and original attachment bytes. Parsing and export
errors are available from `pyonenote.errors`.

## Exports and attachments

| Output | Contents |
| --- | --- |
| JSON / XML | Readable schema-v3 snapshots; complete schema-v2 documents with `--debug` |
| HTML / Markdown / TXT | One document per section |
| PDF | Reflowed pages with current attachments embedded |
| DOCX / ODT | Editable text, formatting, tables, images and attachment links |
| Pickle | Complete Python model snapshot; load only trusted files |

`assets/` contains the original, byte-identical files and images used by current
pages. File icons and unreferenced historical attachments are excluded. Their
bytes remain available in the parsed store, debug JSON/XML and Pickle. Keep the
assets directory beside exported documents so relative links continue to work.

Load default JSON with `ReadableDocument.model_validate_json(...)`; load debug
JSON with `Document.model_validate_json(...)`, both imported from `pyonenote`.
Readable exports are content snapshots, not complete parsed-store round trips.
See [content models and schema migration](docs/content_models.md) for details.

## Compatibility and limits

The parsers are tested with generated fixtures and real revision/package files.
Exports preserve interpreted content, but reflow it: exact canvas positioning,
overlap and drawing appearance are not reproduced. Legacy OneNote 2003/2007
formats, legacy `.onetoc2` declaration variants and encrypted content are
unsupported. Diagnostics report unsupported or incomplete structures.

Ink decoding is opt-in through `--experimental` or
`OneNoteExtractor(experimental=True)`. Its interpretations remain tentative;
see the [ink investigation](docs/experimental_ink.md) for field IDs and evidence.

## Development

```sh
uv sync --locked
uv run pytest
uv run black --check pyonenote tests
uv run mypy pyonenote tests
uv run pylint pyonenote tests
```

Use test-driven changes and include a minimal, shareable reproduction when
reporting parsing issues. Some integration tests require the local `.one`
fixtures. Their expected counts depend on fixture contents; changing a notebook
can invalidate those assertions.

## Credits and references

- **Microsoft Open Specifications:** [MS-ONE](https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-one/73d22548-a613-4350-8c23-07d15576be50)
  describes note content; [MS-ONESTORE](https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-onestore/ae670cd2-4b38-4b24-82d1-87cfb2cc3725)
  describes storage. [MS-FSSHTTP](https://learn.microsoft.com/en-us/openspecs/sharepoint_protocols/ms-fsshttp/05fa7efd-48ed-48d5-8d85-77995e17cc81)
  and [MS-FSSHTTPB](https://learn.microsoft.com/en-us/openspecs/sharepoint_protocols/ms-fsshttpb/ac629d63-60a1-49b2-9db2-fa3c19971cc9)
  inform saved-response parsing.
- **Experimental ink research:** [onenote.rs](https://github.com/msiemens/onenote.rs)
  and [one2html](https://github.com/msiemens/one2html) by msiemens provided reference
  implementations. Pinned sources and uncertainties are recorded in the ink guide.
- **Open-source foundations:** [Pydantic](https://docs.pydantic.dev/),
  [WeasyPrint](https://weasyprint.org/), [pypdf](https://pypdf.readthedocs.io/),
  [python-docx](https://python-docx.readthedocs.io/),
  [odfpy](https://github.com/eea/odfpy) and
  [markdownify](https://github.com/matthewwithanm/python-markdownify).

pyOneNote is an independent project, not affiliated with Microsoft.

## License

[MIT](LICENSE). Third-party dependencies and referenced documentation retain
their respective licenses.
