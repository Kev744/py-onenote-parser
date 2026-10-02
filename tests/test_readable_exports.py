"""Regression coverage for compact, content-preserving reading snapshots."""

import json
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import pytest

from pydantic import ValidationError

from pyonenote import OneNoteExtractor, ReadableDocument
from pyonenote.models import (
    Attachment,
    ContentBlock,
    Document,
    Page,
    ParsedStore,
    Section,
    TextRun,
)
from pyonenote.serialization import document_payload


def _document(*blocks: ContentBlock) -> Document:
    """Wrap independently built content in a minimal parsed document."""
    return Document(
        store=ParsedStore(format="revision"),
        sections=[
            Section(section_id="s", pages=[Page(page_id="p", blocks=list(blocks))])
        ],
    )


def _blocks(document: Document) -> list[dict[str, Any]]:
    """Read the projected body without depending on implementation helpers."""
    payload = document_payload(document)
    assert payload["schema_version"] == 3
    assert payload["view"] == "readable"
    assert "store" not in payload
    return payload["sections"][0]["pages"][0]["blocks"]  # type: ignore[no-any-return]


def test_uniform_run_compacts_and_preserves_resets() -> None:
    """Merge explicit false, zero, auto and empty-link overrides without loss."""
    block = ContentBlock(
        style={"bold": True, "space_before": 12, "color": "red", "href": "old"},
        runs=[
            TextRun(
                text="Unknown_0x123 is literal",
                style={"bold": False, "space_before": 0, "color": "auto", "href": ""},
            )
        ],
        source_object_id="raw-id",
        properties={"Unknown_0x123": "opaque"},
    )
    assert _blocks(_document(block)) == [
        {
            "kind": "paragraph",
            "text": block.text,
            "style": {"bold": False, "space_before": 0, "color": "auto", "href": ""},
        }
    ]


def test_mixed_runs_and_legitimate_repeated_text() -> None:
    """Keep ordered run overrides and repeated paragraphs at distinct locations."""
    block = ContentBlock(
        style={"bold": True},
        runs=[TextRun(text="repeat"), TextRun(text="repeat", style={"bold": False})],
    )
    projected = _blocks(_document(block, block.model_copy(deep=True)))
    assert len(projected) == 2
    assert projected[0] == projected[1]
    assert projected[0]["text"] == "repeatrepeat"
    assert projected[0]["runs"] == [
        {"text": "repeat"},
        {"text": "repeat", "style": {"bold": False}},
    ]
    assert projected[0]["style"] == {"bold": True}


def test_containers_and_empty_table_cells() -> None:
    """Collapse only transparent single-child containers and retain table shape."""
    paragraph = ContentBlock(text="cell")
    wrapper = ContentBlock(
        kind="container",
        children=[ContentBlock(kind="container", children=[paragraph])],
    )
    table = ContentBlock(
        kind="table",
        children=[
            ContentBlock(
                kind="row",
                children=[
                    ContentBlock(kind="cell"),
                    ContentBlock(kind="cell", children=[wrapper]),
                ],
            )
        ],
    )
    blocks = _blocks(
        _document(
            wrapper,
            table,
            ContentBlock(
                kind="list", children=[ContentBlock(kind="list_item", text="item")]
            ),
        )
    )
    assert blocks[0] == {"kind": "paragraph", "text": "cell"}
    assert blocks[1]["text"] == "\tcell"
    assert blocks[1]["children"][0]["children"][0] == {"kind": "cell", "text": ""}
    assert blocks[2]["kind"] == "list"
    assert blocks[2]["children"][0]["kind"] == "list_item"


@pytest.mark.parametrize(
    "extra",
    [
        {"style": {"bold": False}},
        {"attachment_id": "a"},
        {"text": "own"},
        {"properties": {"IsTitleDate": True}},
    ],
)
def test_meaningful_container_is_preserved(extra: dict[str, Any]) -> None:
    """Keep grouping with style, attachment, own text or a semantic role."""
    block = ContentBlock(
        kind="container", children=[ContentBlock(text="child")], **extra
    )
    result = _blocks(_document(block))[0]
    assert result["kind"] == "container"
    assert result["text"] == block.text
    if block.runs:
        assert result["inline_text"] == "own"
        assert "runs" not in result


def test_title_formatting_metadata_and_reading_order() -> None:
    """Retain the title tree with explicit date/time roles and full summaries."""
    document = _document(ContentBlock(text="Title"), ContentBlock(text="body"))
    page = document.sections[0].pages[0]
    page.title = "Title"
    page.title_block = ContentBlock(
        kind="container",
        children=[
            ContentBlock(text="Title", style={"font_size": 20}),
            ContentBlock(text="1 October", properties={"IsTitleDate": True}),
            ContentBlock(text="09:00", properties={"IsTitleTime": True}),
        ],
    )
    projected = document_payload(document)["sections"][0]
    result = projected["pages"][0]
    assert projected["text"] == document.sections[0].text
    assert result["text"] == "Title\n1 October\n09:00\nTitle\nbody"
    title = result["title_block"]
    assert title["roles"] == ["title"]
    assert title["children"][0]["style"] == {"font_size": 20}
    assert title["children"][1]["roles"] == ["title_date"]
    assert title["children"][2]["roles"] == ["title_time"]
    assert "properties" not in json.dumps(result)


def _xml_payload(element: ElementTree.Element) -> Any:
    """Decode the existing typed XML mapping independently of the exporter."""
    kind = element.attrib["type"]
    if kind == "object":
        return {child.attrib["name"]: _xml_payload(child) for child in element}
    if kind == "array":
        return [_xml_payload(child) for child in element]
    value = element.text or ""
    if kind == "bool":
        return value == "True"
    if kind == "int":
        return int(value)
    if kind == "float":
        return float(value)
    return None if kind == "null" else value


def test_public_model_xml_and_asset_round_trips(tmp_path: Path) -> None:
    """Validate snapshots, equivalent XML and safe byte-identical external assets."""
    document = _document(ContentBlock(kind="attachment", attachment_id="a"))
    document.store.attachments["a"] = Attachment(
        attachment_id="a", file_name="../../CON.bin", data=b"\xff\x00original"
    )
    OneNoteExtractor.export(document, tmp_path, ["json", "xml"])
    raw = (tmp_path / "document.json").read_text(encoding="utf-8")
    payload = json.loads(raw)
    restored = ReadableDocument.model_validate_json(raw)
    assert json.loads(restored.model_dump_json()) == payload
    xml = ElementTree.parse(tmp_path / "document.xml").getroot()
    assert xml.attrib["schema_version"] == "3"
    assert _xml_payload(xml) == payload
    asset = payload["attachments"][0]
    assert set(asset) == {"attachment_id", "file_name", "content_type", "path"}
    assert asset["attachment_id"] == "a"
    path = tmp_path / asset["path"]
    assert asset["path"].startswith("assets/")
    assert path.resolve().is_relative_to(tmp_path.resolve())
    assert path.read_bytes() == document.store.attachments["a"].data
    assert len(list((tmp_path / "assets").iterdir())) == 1
    assert "data" not in asset and "store" not in payload


def test_projection_does_not_serialize_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the default projection independent of full document serialization."""

    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        """Reject attempts to serialize the parsed store or document."""
        raise AssertionError("Full store serialization is forbidden")

    document = _document(ContentBlock(text="readable"))
    monkeypatch.setattr(Document, "model_dump_json", forbidden)
    monkeypatch.setattr(Document, "model_dump", forbidden)
    monkeypatch.setattr(ParsedStore, "model_dump", forbidden)
    monkeypatch.setattr(ParsedStore, "model_dump_json", forbidden)
    assert _blocks(document)[0]["text"] == "readable"


def test_empty_inline_text_with_children_is_explicit() -> None:
    """Distinguish an empty local run from a container with only descendants."""
    block = ContentBlock(runs=[TextRun(text="")], children=[ContentBlock(text="child")])
    projected = _blocks(_document(block))[0]
    assert projected["text"] == "\nchild"
    assert projected["inline_text"] == ""


@pytest.mark.parametrize(
    "path", ["../a", "/assets/a", "assets/../a", "assets/a/b", "C:/a"]
)
def test_readable_model_rejects_unsafe_asset_paths(path: str) -> None:
    """Reject manifests whose assets escape the documented relative layout."""
    with pytest.raises(ValidationError):
        ReadableDocument.model_validate(
            {
                "sections": [],
                "attachments": [
                    {
                        "attachment_id": "a",
                        "file_name": "a",
                        "content_type": "text/plain",
                        "path": path,
                    }
                ],
            }
        )


def _restore_block(value: dict[str, Any]) -> ContentBlock:
    """Reconstruct interpreted content to compare text and effective formatting."""
    children = [_restore_block(child) for child in value.get("children", [])]
    if "runs" in value:
        runs = [TextRun(**run) for run in value["runs"]]
    elif "inline_text" in value or not children:
        runs = [TextRun(text=value.get("inline_text", value["text"]))]
    else:
        runs = []
    return ContentBlock(
        kind=value["kind"],
        runs=runs,
        children=children,
        style=value.get("style", {}),
        attachment_id=value.get("attachment_id", ""),
    )


def _formatted_text(block: ContentBlock) -> list[tuple[str, dict[str, Any]]]:
    """Flatten visible runs in order with their effective style for comparison."""
    return [(run.text, block.style | run.style) for run in block.runs if run.text] + [
        item for child in block.children for item in _formatted_text(child)
    ]


@pytest.mark.parametrize(
    "filename,json_before,xml_before",
    [
        ("Equipe Euro 2016.one", 11846405, 19583266),
        ("Section sans titre.one", 463251, 772037),
    ],
)
def test_real_readable_exports(
    filename: str, json_before: int, xml_before: int, tmp_path: Path
) -> None:
    """Preserve both fixtures' reading content at under 10% of prior default sizes."""
    document = OneNoteExtractor().parse(Path(__file__).resolve().parents[1] / filename)
    original = document.model_dump_json()
    OneNoteExtractor.export(document, tmp_path, ["json", "xml"])
    json_path = tmp_path / "document.json"
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    restored = ReadableDocument.model_validate(payload)
    assert restored.model_dump(mode="json") == payload
    assert (
        _xml_payload(ElementTree.parse(tmp_path / "document.xml").getroot()) == payload
    )
    assert [s.text for s in restored.sections] == [s.text for s in document.sections]
    _verify_sections(document, payload)
    for asset in payload["attachments"]:
        assert (tmp_path / asset["path"]).read_bytes() == document.store.attachments[
            asset["attachment_id"]
        ].data
    assert "store" not in payload
    assert json_path.stat().st_size < json_before * 0.1
    assert (tmp_path / "document.xml").stat().st_size < xml_before * 0.1
    assert document.model_dump_json() == original


def _verify_sections(document: Document, payload: dict[str, Any]) -> None:
    """Compare page order, aggregate text and every effective inline style."""
    for source_section, section in zip(document.sections, payload["sections"]):
        assert len(section.get("pages", [])) == len(source_section.pages)
        for source_page, page in zip(source_section.pages, section.get("pages", [])):
            assert page["text"] == source_page.text
            assert page.get("title", "") == source_page.title
            originals = list(source_page.blocks)
            projected = list(page.get("blocks", []))
            if source_page.title_block is not None:
                originals.insert(0, source_page.title_block)
                projected.insert(0, page["title_block"])
            assert len(originals) == len(projected)
            for source_block, block in zip(originals, projected):
                reconstructed = _restore_block(block)
                assert reconstructed.text == source_block.text
                assert _formatted_text(reconstructed) == _formatted_text(source_block)
