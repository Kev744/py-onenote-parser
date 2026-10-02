"""Exercise documented property wire types and shared reference cursors."""

import struct
import base64
from uuid import UUID

import pytest

from pyonenote.binary import BinaryReader
from pyonenote.errors import ParseError
from pyonenote.properties import decode_property_set


def property_set(ids: list[int], payload: bytes = b"") -> bytes:
    """Build a property set fixture.

    :return: Count, identifiers and data in wire order.
    """
    return (
        struct.pack("<H", len(ids))
        + b"".join(struct.pack("<I", item) for item in ids)
        + payload
    )


def test_unicode_boolean_and_unknown_binary() -> None:
    """Decode known text without losing unknown bytes."""
    raw = "Équipe ⚽".encode("utf-16-le")
    source = property_set(
        [0x88001C04, 0x1C001C22, 0x1C000999],
        struct.pack("<I", len(raw)) + raw + b"\x02\x00\x00\x00\xff\xfe",
    )
    result = decode_property_set(BinaryReader(source))
    assert result[0].name == "Bold"
    assert result[0].value is True
    assert result[1].value == "Équipe ⚽"
    assert result[1].raw == raw
    assert result[2].value == b"\xff\xfe"


def test_reference_cursors_are_shared_by_nested_sets() -> None:
    """Keep independent streams and nested reference order."""
    nested = property_set([0x20000001, 0x28000002, 0x30000003])
    source = property_set(
        [0x24000001, 0x44000001, 0x20000002], struct.pack("<I", 2) + nested
    )
    result = decode_property_set(
        BinaryReader(source), ["a", "b", "c", "d"], ["space"], ["context"]
    )
    assert result[0].value == ["a", "b"]
    assert [prop.value for prop in result[1].value] == ["c", "space", "context"]
    assert result[2].value == "d"


def test_property_arrays_and_empty_arrays() -> None:
    """Decode arrays containing complete nested property sets."""
    source = property_set(
        [0x40000001, 0x40000002],
        struct.pack("<II", 2, 0x44000000)
        + property_set([0x0C000001], b"\x04")
        + property_set([])
        + struct.pack("<I", 0),
    )
    result = decode_property_set(BinaryReader(source))
    assert result[0].value[0][0].value == 4
    assert result[0].value[1] == []
    assert result[1].value == []


@pytest.mark.parametrize(
    "source",
    [
        property_set([0x24000001], struct.pack("<I", 1)),
        property_set([0x78000001]),
        property_set([0x1C001C22], struct.pack("<I", 3) + b"abc"),
        property_set([0x40000001], struct.pack("<II", 1, 0x14000001)),
    ],
)
def test_invalid_property_data_is_rejected(source: bytes) -> None:
    """Reject missing references, unsupported types and malformed strings."""
    with pytest.raises(ParseError):
        decode_property_set(BinaryReader(source, offset=100))


def test_every_truncated_prefix_is_rejected() -> None:
    """Enforce bounds at every byte of a variable length property."""
    source = property_set([0x1C001C22], struct.pack("<I", 4) + b"a\x00b\x00")
    for size in range(len(source)):
        with pytest.raises(ParseError):
            decode_property_set(BinaryReader(source[:size]))


def test_unused_references_and_excessive_nesting_are_rejected() -> None:
    """Enforce exact stream consumption and bound recursive property decoding."""
    with pytest.raises(ParseError, match="Unused"):
        decode_property_set(BinaryReader(property_set([])), ["unused"])
    nested = property_set([])
    for _ in range(66):
        nested = property_set([0x44000001], nested)
    with pytest.raises(ParseError, match="nesting"):
        decode_property_set(BinaryReader(nested))


def test_known_guid_and_filetime_keep_original_values() -> None:
    """Expose interpreted GUIDs/timestamps while preserving all original bytes."""
    identifier = UUID("12345678-1234-5678-90ab-cdef01234567")
    timestamp = 116444736000000001
    source = property_set(
        [0x1C001C30, 0x18001C65, 0x18001D77],
        struct.pack("<I", 16) + identifier.bytes_le + struct.pack("<QQ", timestamp, 0),
    )
    properties = decode_property_set(BinaryReader(source))
    guid = properties[0]
    created = properties[1]
    modified = properties[2]
    assert guid.name == "NotebookManagementEntityGuid"
    assert guid.name_aliases == ["NotebookManagementEntityGuid"]
    assert guid.known is True
    assert guid.interpretation == "decoded"
    assert guid.raw == identifier.bytes_le
    assert guid.value == str(identifier)
    assert guid.decoded_value == str(identifier)
    assert created.value == "1970-01-01T00:00:00.0000001Z"
    assert created.raw == struct.pack("<Q", timestamp)
    assert created.decoded_value == "1970-01-01T00:00:00.0000001Z"
    assert modified.decoded_value == "1601-01-01T00:00:00.0000000Z"


def test_known_raw_unknown_and_scalar_support_are_distinct() -> None:
    """Do not claim semantic decoding for floating bit patterns or opaque blobs."""
    source = property_set(
        [0x88001C04, 0x14001D57, 0x14001C01, 0x1C001D66, 0x14000099],
        struct.pack("<III", 4, 0x3F800000, 2) + b"\xff\x00" + struct.pack("<I", 42),
    )
    properties = decode_property_set(BinaryReader(source))
    bold = properties[0]
    row_count = properties[1]
    width = properties[2]
    table_widths = properties[3]
    unknown = properties[4]
    assert bold.decoded_value is True and bold.interpretation == "decoded"
    assert row_count.decoded_value == 4 and row_count.interpretation == "decoded"
    assert width.value == 0x3F800000 and width.decoded_value is None
    assert width.known and width.interpretation == "raw"
    assert table_widths.known and table_widths.interpretation == "raw"
    assert table_widths.value == b"\xff\x00"
    assert unknown.name == "Unknown_0x14000099"
    assert not unknown.known and unknown.interpretation == "unknown"


def test_contextual_owner_applies_only_to_top_level_properties() -> None:
    """Keep nested set aliases generic rather than inheriting a parent JCID."""
    nested = property_set([0x24001C20], struct.pack("<I", 0))
    source = property_set([0x24001C20, 0x44000001], struct.pack("<I", 0) + nested)
    properties = decode_property_set(BinaryReader(source), [], [], [], jcid=0x60007)
    outer = properties[0]
    child = properties[1]
    assert outer.name == "ElementChildNodesOfSection"
    assert len(outer.name_aliases) == 9
    assert child.value[0].name == "ElementChildNodes"


@pytest.mark.parametrize("identifier", [0x1C001C52, 0x1C001D9E, 0x1C003468, 0x1C00348A])
def test_additional_documented_strings(identifier: int) -> None:
    """Interpret verified WzInAtom fields as Unicode strings."""
    raw = "Label\x00".encode("utf-16-le")
    prop = decode_property_set(
        BinaryReader(property_set([identifier], struct.pack("<I", len(raw)) + raw))
    )[0]
    assert prop.value == prop.decoded_value == "Label"
    assert prop.raw == raw and prop.interpretation == "decoded"


@pytest.mark.parametrize(
    "source",
    [
        property_set([0x1C001C30], struct.pack("<I", 15) + bytes(15)),
        property_set([0x18001C65], struct.pack("<Q", 2**64 - 1)),
    ],
)
def test_invalid_semantic_payloads_have_absolute_offsets(source: bytes) -> None:
    """Reject malformed GUIDs and calendar overflow with absolute offsets."""
    with pytest.raises(ParseError) as failure:
        decode_property_set(BinaryReader(source, offset=200))
    assert failure.value.offset >= 206


def test_user_reported_base64_values_are_named_and_interpreted() -> None:
    """Regress the exact GUID and FILETIME bytes reported as unknown."""
    guid_data = base64.b64decode("zXj1SzuHQ06NamJ8NdIf4w==")
    time_data = base64.b64decode("4FWrzyiF2wE=")
    source = property_set(
        [0x1C001C30, 0x18001C65],
        struct.pack("<I", len(guid_data)) + guid_data + time_data,
    )
    properties = decode_property_set(BinaryReader(source))
    assert properties[0].name == "NotebookManagementEntityGuid"
    assert properties[0].decoded_value == "4bf578cd-873b-4e43-8d6a-627c35d21fe3"
    assert properties[0].raw == guid_data
    assert properties[1].name == "TopologyCreationTimeStamp"
    assert properties[1].value == "2025-02-22T12:53:46.6860000Z"
    assert properties[1].decoded_value == "2025-02-22T12:53:46.6860000Z"
    assert properties[1].raw == time_data
