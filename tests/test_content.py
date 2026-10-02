"""Exercise page hierarchy, rich text and attachment interpretation."""

import struct
from pathlib import Path

import pytest

from pyonenote.errors import ParseError
from pyonenote.content import assemble_document
from pyonenote.models import (
    Attachment,
    ContentBlock,
    OneObject,
    ParsedStore,
    Property,
    Revision,
)
from pyonenote.properties import PROPERTY_NAMES
from pyonenote.package import parse_package_store
from pyonenote.revision import parse_revision_store


def prop(identifier: int, value: object) -> Property:
    """Make one interpreted fixture property.

    :return: A property carrying its documented name.
    """
    return Property(
        property_id=identifier, name=PROPERTY_NAMES[identifier], value=value
    )


def fixture_store() -> ParsedStore:
    """Create a section with ordered pages, text and an image.

    :return: An in-memory model independent of either parser.
    """
    objects = [
        OneObject(
            object_id="section", jcid=0x60007, properties=[prop(0x24001C20, ["series"])]
        ),
        OneObject(
            object_id="series",
            jcid=0x60008,
            properties=[prop(0x2C001D63, ["space-b", "space-a"])],
        ),
        OneObject(object_id="a", object_space_id="space-a", jcid=0x6000B),
        OneObject(
            object_id="b",
            object_space_id="space-b",
            jcid=0x6000B,
            properties=[
                prop(0x24001C20, ["text", "image"]),
                prop(0x1C001CF3, "Équipe"),
            ],
        ),
        OneObject(
            object_id="text",
            jcid=0x6000E,
            properties=[
                prop(0x1C001C22, "Hello 🌍!"),
                prop(0x1C001E12, struct.pack("<I", 6)),
                prop(0x24001E13, ["plain", "bold"]),
            ],
        ),
        OneObject(object_id="plain", jcid=0x12004D),
        OneObject(object_id="bold", jcid=0x12004D, properties=[prop(0x08001C04, True)]),
        OneObject(
            object_id="image",
            jcid=0x60011,
            properties=[prop(0x20001C3F, "blob"), prop(0x1C001DD7, "photo.png")],
        ),
    ]
    return ParsedStore(
        format="revision",
        objects={obj.object_id: obj for obj in objects},
        attachments={
            "asset": Attachment(
                attachment_id="asset", object_id="blob", data=b"picture"
            )
        },
    )


def test_hierarchy_order_rich_runs_and_attachments() -> None:
    """Preserve page order, Unicode run boundaries and asset association."""
    store = fixture_store()
    document = assemble_document(store, "Notebook.one")
    section = document.sections[0]
    assert section.title == "Notebook"
    assert [page.page_id for page in section.pages] == ["b", "a"]
    assert section.pages[0].title == "Équipe"
    paragraph, image = section.pages[0].blocks
    assert paragraph.text == "Hello 🌍!"
    assert [run.text for run in paragraph.runs] == ["Hello ", "🌍!"]
    assert paragraph.children == []
    assert paragraph.runs[1].style["bold"] is True
    assert image.kind == "image"
    assert image.attachment_id == "asset"
    assert store.attachments["asset"].file_name == "photo.png"


def test_content_cycle_is_rejected() -> None:
    """Reject cyclic visible content rather than recurse forever."""
    store = fixture_store()
    store.objects["text"].properties.append(prop(0x24001C20, ["text"]))
    with pytest.raises(ParseError, match="[Cc]ycle"):
        assemble_document(store)


def test_missing_and_unsupported_content_has_diagnostics() -> None:
    """Retain unsupported content while reporting missing references."""
    store = fixture_store()
    store.objects["text"].jcid = 0x600FF
    store.objects["b"].properties[0].value.append("missing")
    document = assemble_document(store)
    assert document.sections[0].pages[0].blocks[0].kind == "unsupported"
    assert {item.code for item in store.diagnostics} >= {
        "UNKNOWN_JCID",
        "MISSING_OBJECT",
    }


def test_unicode_takes_precedence_over_extended_ascii() -> None:
    """Prefer Unicode even when both text properties occur."""
    store = fixture_store()
    store.objects["text"].properties.insert(0, prop(0x1C003498, b"wrong"))
    document = assemble_document(store)
    assert document.sections[0].pages[0].blocks[0].text == "Hello 🌍!"


def test_numbered_list_and_table_structure() -> None:
    """Preserve list semantics and nested table cell content."""
    store = fixture_store()
    store.objects["b"].properties[0].value = ["item", "table"]
    store.objects = store.objects | (
        {
            "item": OneObject(
                object_id="item",
                jcid=0x6000D,
                properties=[prop(0x24001C1F, ["text"]), prop(0x24001C26, ["number"])],
            ),
            "number": OneObject(
                object_id="number",
                jcid=0x60012,
                properties=[
                    Property(property_id=0x1C001C1A, value=b"\x02\x00\xfd\xff.\x00")
                ],
            ),
            "table": OneObject(
                object_id="table", jcid=0x60022, properties=[prop(0x24001C20, ["row"])]
            ),
            "row": OneObject(
                object_id="row", jcid=0x60023, properties=[prop(0x24001C20, ["cell"])]
            ),
            "cell": OneObject(
                object_id="cell", jcid=0x60024, properties=[prop(0x24001C20, ["text"])]
            ),
        }
    )
    page = assemble_document(store).sections[0].pages[0]
    assert page.blocks[0].kind == "list"
    assert page.blocks[0].style["ordered"] is True
    assert page.blocks[0].children[0].kind == "list_item"
    assert page.blocks[1].kind == "table"
    assert page.blocks[1].children[0].kind == "row"
    assert page.blocks[1].children[0].children[0].kind == "cell"


def test_consecutive_list_items_share_numbering_and_restart() -> None:
    """Group sibling lists without losing an explicit numbering restart."""
    store = fixture_store()
    store.objects["b"].properties[0].value = ["first", "second", "restart", "after"]
    for identifier in ("first", "second", "restart", "after"):
        store.objects[identifier] = OneObject(
            object_id=identifier,
            jcid=0x6000D,
            properties=[
                prop(0x24001C1F, ["text"]),
                prop(
                    0x24001C26,
                    ["restart-format" if identifier == "restart" else "number"],
                ),
            ],
        )
    for identifier in ("number", "restart-format"):
        store.objects[identifier] = OneObject(
            object_id=identifier,
            jcid=0x60012,
            properties=[prop(0x1C001C1A, b"\x03\x00\xfd\xff\x00\x00.\x00")],
        )
    store.objects["restart-format"].properties.append(prop(0x14001CB7, 5))
    blocks = assemble_document(store).sections[0].pages[0].blocks
    assert len(blocks) == 2
    assert len(blocks[0].children) == 2
    assert blocks[1].style["start"] == 5
    assert len(blocks[1].children) == 2


def test_paragraph_defaults_link_and_run_override() -> None:
    """Apply paragraph defaults while letting runs override explicit styles."""
    store = fixture_store()
    store.objects["text"].properties.append(prop(0x2000342C, "default"))
    store.objects["default"] = OneObject(
        object_id="default",
        jcid=0x12004D,
        properties=[
            prop(0x08001C04, True),
            prop(0x10001C0B, 24),
            prop(0x14001C0C, 0xFF000000),
        ],
    )
    store.objects["plain"].properties = [
        prop(0x08001C04, False),
        prop(0x1C001E20, "https://example.org"),
    ]
    paragraph = assemble_document(store).sections[0].pages[0].blocks[0]
    assert paragraph.style == {"bold": True, "font_size": 12, "color": "auto"}
    assert paragraph.runs[0].style == {"bold": False, "href": "https://example.org"}


def test_unknown_reference_property_reports_uninterpreted_relationship() -> None:
    """Make omitted undocumented graph relationships visible to callers."""
    store = fixture_store()
    store.objects["b"].properties.append(
        Property(property_id=0x240034D8, type_id=9, value=["text"])
    )
    assemble_document(store)
    assert any(item.code == "UNKNOWN_CONTENT_PROPERTY" for item in store.diagnostics)


def test_selected_current_revision_controls_object_space_root() -> None:
    """Ignore a later historical root when an explicit current revision exists."""
    store = fixture_store()
    store.revisions = [
        Revision(
            revision_id="current", object_space_id="space-b", root_objects={"1": "b"}
        ),
        Revision(
            revision_id="history", object_space_id="space-b", root_objects={"1": "a"}
        ),
    ]
    store.header["current_revisions"] = {"space-b": "current"}
    assert [page.page_id for page in assemble_document(store).sections[0].pages] == [
        "b",
        "a",
    ]


@pytest.mark.parametrize("indices", [struct.pack("<I", 7), struct.pack("<II", 1, 1)])
def test_invalid_utf16_run_boundaries_preserve_full_text(indices: bytes) -> None:
    """Reject surrogate splits and repeated run offsets without losing text."""
    store = fixture_store()
    store.objects["text"].properties[1].value = indices
    store.objects["text"].properties[2].value = ["plain"] * (len(indices) // 4 + 1)
    paragraph = assemble_document(store).sections[0].pages[0].blocks[0]
    assert paragraph.text == "Hello 🌍!"
    assert paragraph.children == []
    assert [run.text for run in paragraph.runs] == [paragraph.text]
    assert any(item.code == "INVALID_TEXT_RUNS" for item in store.diagnostics)


def _flatten(blocks: list[ContentBlock]) -> list[ContentBlock]:
    """Collect content blocks in pre-order.

    :return: Nodes including rich-text run children.
    """
    return [node for block in blocks for node in [block] + _flatten(block.children)]


def test_real_revision_content_and_asset() -> None:
    """Check the real sample's player text, visible title and image filename."""
    source = Path(__file__).resolve().parents[1] / "Equipe Euro 2016.one"
    store = parse_revision_store(source.read_bytes())
    document = assemble_document(store, source.name)
    assert len(document.sections) == 1
    page = document.sections[0].pages[0]
    assert page.title == "Equipe Euro 2016"
    blocks = _flatten(page.blocks)
    assert "Cristiano Ronaldo" in [block.text for block in blocks]
    assert "André Gomes" in [block.text for block in blocks]
    image = next(block for block in blocks if block.kind == "image")
    assert store.attachments[image.attachment_id].file_name == "Portugal_Logo.png"
    assert store.attachments[image.attachment_id].data.startswith(b"\x89PNG\r\n\x1a\n")
    assert not any(
        item.code in {"MISSING_OBJECT", "MISSING_ATTACHMENT", "INVALID_TEXT_RUNS"}
        for item in store.diagnostics
    )


def test_real_package_content_reports_undocumented_relationships() -> None:
    """Assemble the real current page while exposing undocumented content gaps."""
    source = Path(__file__).resolve().parents[1] / "Section sans titre.one"
    store = parse_package_store(source.read_bytes())
    document = assemble_document(store, source.name)
    assert len(document.sections) == 1
    assert document.sections[0].pages[0].title == "history"
    assert any(
        item.code == "UNKNOWN_CONTENT_PROPERTY" and "0x240034D8" in item.message
        for item in store.diagnostics
    )
