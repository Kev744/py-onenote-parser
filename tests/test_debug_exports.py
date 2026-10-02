"""Require explicit debug opt-in for unknown JSON/XML structures."""

import json
import pickle
from pathlib import Path

import pytest

from pyonenote import OneNoteExtractor
from pyonenote.cli import main
from pyonenote.models import (
    ContentBlock,
    Diagnostic,
    Document,
    OneObject,
    Page,
    ParsedStore,
    Property,
    Revision,
    Section,
)


def _document() -> Document:
    """Build unknown fields at every structural and semantic nesting level."""
    unknown = Property(
        property_id=0x1C000999,
        name="Unknown_0x1C000999",
        type_id=7,
        value=b"secret-unknown",
        raw=b"secret-unknown",
        interpretation="unknown",
    )
    known = Property(
        property_id=0x1C001C22,
        name="RichEditTextUnicode",
        type_id=7,
        value="Unknown_0x12345678 is literal note text",
        known=True,
    )
    nested = Property(
        property_id=0x40003499,
        name="TextRunData",
        type_id=16,
        value=[[known, unknown]],
        known=True,
    )
    page = OneObject(
        object_id="page", jcid=0x6000B, properties=[known, unknown, nested]
    )
    opaque = OneObject(object_id="opaque", jcid=0x120051, properties=[unknown])
    return Document(
        store=ParsedStore(
            format="package",
            objects={"page": page, "opaque": opaque},
            revisions=[Revision(revision_id="r", objects=["page", "opaque"])],
            revision_objects={"r": {"page": page, "opaque": opaque}},
            diagnostics=[
                Diagnostic(
                    code="UNKNOWN_CONTENT_PROPERTY", message="unknown-field detail"
                ),
                Diagnostic(code="MISSING_ATTACHMENT", message="Keep this warning"),
            ],
        ),
        sections=[
            Section(
                section_id="s",
                pages=[
                    Page(
                        page_id="page",
                        properties={"Unknown_0x1C000999": "hidden", "PageWidth": 4},
                        blocks=[
                            ContentBlock(
                                text=known.value,
                                properties={
                                    "Unknown_0x1C000999": "hidden",
                                    "Bold": True,
                                },
                            )
                        ],
                    )
                ],
            )
        ],
    )


def test_default_filters_unknowns_without_mutating_source(tmp_path: Path) -> None:
    """Filter current/history/nested properties without erasing literal text."""
    document = _document()
    original = document.model_dump_json()
    OneNoteExtractor.export(document, tmp_path, ["json", "xml", "pickle"])
    payload = json.loads((tmp_path / "document.json").read_text(encoding="utf-8"))
    for path in (tmp_path / "document.json", tmp_path / "document.xml"):
        text = path.read_text(encoding="utf-8")
        assert "Unknown_0x1C000999" not in text
        assert "Unknown_0x00120051" not in text
        assert "unknown-field detail" not in text
        assert "Unknown_0x12345678 is literal note text" in text
        assert "Keep this warning" in text
    assert "store" not in payload
    assert "properties" not in payload["sections"][0]["pages"][0]
    assert payload["diagnostics"] == [
        {"code": "MISSING_ATTACHMENT", "message": "Keep this warning"}
    ]
    assert document.model_dump_json() == original
    assert pickle.loads((tmp_path / "document.pickle").read_bytes()) == document


def test_debug_retains_complete_structures(tmp_path: Path) -> None:
    """Expose the complete loss-preserving document only on explicit request."""
    document = _document()
    OneNoteExtractor.export(document, tmp_path, ["json", "xml"], debug=True)
    payload = json.loads((tmp_path / "document.json").read_text(encoding="utf-8"))
    assert payload == json.loads(document.model_dump_json())
    assert "Unknown_0x1C000999" in (tmp_path / "document.xml").read_text(
        encoding="utf-8"
    )


@pytest.mark.parametrize("debug", [False, True])
@pytest.mark.parametrize(
    "filename,unknown_name",
    [
        ("Section sans titre.one", "Unknown_0x240034D8"),
        ("Equipe Euro 2016.one", "Unknown_0x20001CE2"),
    ],
)
def test_cli_debug_controls_json_and_xml(
    debug: bool, filename: str, unknown_name: str, tmp_path: Path
) -> None:
    """Exercise argparse and both export paths with both real storage formats."""
    source = Path(__file__).resolve().parents[1] / filename
    args = [
        str(source),
        "--output",
        str(tmp_path),
        "--format",
        "json",
        "--format",
        "xml",
    ]
    if debug:
        args.append("--debug")
    assert main(args) == 0
    for suffix in ("json", "xml"):
        text = (tmp_path / f"document.{suffix}").read_text(encoding="utf-8")
        assert (unknown_name in text) is debug
        if filename == "Section sans titre.one":
            assert ("Unknown_0x00120051" in text) is debug
