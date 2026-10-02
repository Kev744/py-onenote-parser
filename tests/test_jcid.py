"""Verify documented object recognition independently of rendering support."""

import pytest

from pyonenote.content import assemble_document
from pyonenote.jcid import JCID_NAMES, jcid_names, normalize_jcid
from pyonenote.models import OneObject, ParsedStore, Property


def test_complete_jcid_catalog_and_shared_ids() -> None:
    """Retain every documented property-set and file-data object name."""
    assert len(JCID_NAMES) == 28
    assert sum(len(names) for names in JCID_NAMES.values()) == 30
    assert jcid_names(0x00120001) == (
        "jcidReadOnlyPersistablePropertyContainerForAuthor",
    )
    assert jcid_names(0x00020001) == (
        "jcidPersistablePropertyContainerForTOC",
        "jcidPersistablePropertyContainerForTOCSection",
    )
    assert jcid_names(0x00080039) == ("jcidPictureContainer14",)
    assert jcid_names(0x00120051) == ()


@pytest.mark.parametrize("ignored", [0, 0x00040000, 0x80000000, 0xFFE40000])
def test_jcid_ignores_only_undefined_bits(ignored: int) -> None:
    """Ignore reserved/graph bits while retaining meaningful storage flags."""
    assert normalize_jcid(0x0002000E | ignored) == 0x0002000E
    assert jcid_names(0x0002000E | ignored) == ("jcidRichTextOENode",)
    for wrong_flags in (0, 0x10000, 0x80000, 0x120000):
        assert jcid_names(wrong_flags | 0x0E | ignored) == ()


def test_object_metadata_tracks_late_partition_assignment() -> None:
    """Serialize recognition from the current JCID, including late changes."""
    obj = OneObject(object_id="object")
    assert not obj.jcid_known
    obj.jcid = 0x00020030
    assert obj.jcid_name == "jcidPageMetaData"
    assert obj.jcid_aliases == ["jcidPageMetaData"]
    assert obj.model_dump()["jcid_known"] is True
    obj.jcid = 0x0012004D
    assert len(obj.jcid_aliases) == 2
    restored = OneObject.model_validate_json(obj.model_dump_json())
    assert restored.jcid_aliases == obj.jcid_aliases
    obj.jcid = 0x00120051
    assert obj.jcid_name == "Unknown_0x00120051"
    assert not obj.jcid_known


@pytest.mark.parametrize(
    "jcid,code",
    [(0x00020030, "UNSUPPORTED_CONTENT"), (0x0008000E, "UNKNOWN_JCID")],
)
def test_known_unsupported_and_unknown_objects_are_distinct(
    jcid: int, code: str
) -> None:
    """Do not infer rich-text support from a colliding low JCID index."""
    page = OneObject(
        object_id="page",
        jcid=0x6000B,
        properties=[Property(property_id=0x24001C20, value=["child"])],
    )
    child = OneObject(object_id="child", jcid=jcid)
    store = ParsedStore(format="revision", objects={"page": page, "child": child})
    document = assemble_document(store)
    assert document.sections[0].pages[0].blocks[0].kind == "unsupported"
    assert [diagnostic.code for diagnostic in store.diagnostics] == [code]
    assert child.jcid_name in store.diagnostics[0].message


def test_documented_relationship_is_not_reported_unknown() -> None:
    """Recognize note-tag definitions without treating them as visible edges."""
    page = OneObject(
        object_id="page",
        jcid=0x6000B,
        properties=[Property(property_id=0x20003488, value="tag")],
    )
    store = ParsedStore(format="revision", objects={"page": page})
    assemble_document(store)
    assert not store.diagnostics
