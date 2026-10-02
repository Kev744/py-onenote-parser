"""Regression tests for bounded revision-store decoding."""

from pathlib import Path
from hashlib import sha256
import struct
from uuid import UUID
from zlib import crc32

import pytest

from pyonenote.errors import ParseError, UnsupportedFormatError
from pyonenote.revision import parse_revision_store


def _node(node_id: int, body: bytes, base_type: int = 0) -> bytes:
    """Encode a full-width reference file node for a synthetic fixture."""
    return (
        struct.pack("<I", node_id | ((len(body) + 4) << 10) | (base_type << 27)) + body
    )


def _fixture(*, committed: int | None = None) -> bytes:
    """Build a deterministic revision with Unicode text and two references."""
    guid = UUID("12345678-1234-1234-1234-123456789abc")
    exguid = guid.bytes_le + struct.pack("<I", 1)
    text = "Café 世界".encode("utf-16-le")
    properties = struct.pack("<IIIHII", 0x80000002, 2, 3, 2, 0x1C001C22, 0x24001C20)
    properties += struct.pack("<I", len(text)) + text + struct.pack("<I", 2)
    nodes = [
        _node(0x00C, exguid),
        _node(0x01E, exguid + bytes(20) + struct.pack("<IH", 1, 0)),
        _node(0x022, b""),
        _node(0x024, struct.pack("<I", 0) + guid.bytes_le),
        _node(0x028, b""),
        _node(
            0x0A4, struct.pack("<QI IIBB", 1664, len(properties), 1, 0x6000E, 1, 1), 1
        ),
        _node(0x059, struct.pack("<II", 1, 1)),
        _node(0x01C, b""),
    ]
    header = bytearray(1024)
    header[:16] = UUID("7b5c52e4-d88c-4da7-aeb1-5378d02996d3").bytes_le
    header[48:64] = UUID("109add3f-911b-49f5-a5d0-1791edc8aed8").bytes_le
    struct.pack_into("<IIII", header, 64, 42, 42, 42, 42)
    struct.pack_into("<I", header, 96, 1)
    struct.pack_into("<QI", header, 160, 1536, 128)
    struct.pack_into("<QI", header, 172, 1024, 512)
    struct.pack_into("<Q", header, 196, 1664 + len(properties))
    fragment = bytearray(512)
    struct.pack_into("<QII", fragment, 0, 11841787174971765956, 2, 0)
    content = b"".join(nodes)
    fragment[16 : 16 + len(content)] = content
    struct.pack_into("<QIQ", fragment, 492, (1 << 64) - 1, 0, 10070635646201084491)
    entries = struct.pack("<II", 2, len(nodes) if committed is None else committed)
    transactions = entries + struct.pack("<II", 1, crc32(entries))
    return bytes(header + fragment) + transactions.ljust(128, b"\0") + properties


def test_decodes_objects_properties_and_distinct_references() -> None:
    """The decoder must preserve text and consume each ID exactly once."""
    store = parse_revision_store(_fixture())
    assert len(store.objects) == 1
    obj = next(iter(store.objects.values()))
    assert obj.jcid == 0x6000E
    assert obj.properties[0].value == "Café 世界"
    assert obj.references == [
        "12345678-1234-1234-1234-123456789abc:2",
        "12345678-1234-1234-1234-123456789abc:3",
    ]
    assert store.revisions[0].root_objects["1"] == obj.object_id


@pytest.mark.parametrize("node_id", [0x0A4, 0x0A5, 0x0C4, 0x0C5])
def test_all_modern_property_declaration_variants(node_id: int) -> None:
    """Small/large and read-only declarations expose the same property data."""
    data = bytearray(_fixture())
    position = 1040
    nodes: list[bytes] = []
    for index in range(8):
        size = (struct.unpack_from("<I", data, position)[0] >> 10) & 8191
        node = bytes(data[position : position + size])
        if index == 5:
            body = node[4:-1] + (
                struct.pack("<I", 1) if node_id in (0x0A5, 0x0C5) else b"\1"
            )
            if node_id in (0x0C4, 0x0C5):
                body += bytes(16)
            node = _node(node_id, body, 1)
        nodes.append(node)
        position += size
    data[1040:1516] = b"".join(nodes).ljust(476, b"\0")
    obj = next(iter(parse_revision_store(bytes(data)).objects.values()))
    assert obj.properties[0].value == "Café 世界"


def test_ignores_uncommitted_nodes() -> None:
    """Transactions delimit readable nodes even when extra bytes are present."""
    assert not parse_revision_store(_fixture(committed=5)).objects


def _append_revision(data: bytes, *, activate: bool) -> bytes:
    """Append a second revision whose role is assigned independently."""
    result = bytearray(data)
    guid = UUID("12345678-1234-1234-1234-123456789abc")
    revision_id = guid.bytes_le + struct.pack("<I", 2)
    text = "Second".encode("utf-16-le")
    properties = struct.pack("<IHI", 0x80000000, 1, 0x1C001C22)
    properties += struct.pack("<I", len(text)) + text
    position = 1040
    for _ in range(8):
        position += (struct.unpack_from("<I", result, position)[0] >> 10) & 8191
    nodes = [
        _node(0x01E, revision_id + bytes(20) + struct.pack("<IH", 4, 0)),
        _node(0x022, b""),
        _node(0x024, struct.pack("<I", 0) + guid.bytes_le),
        _node(0x028, b""),
        _node(
            0x0A4,
            struct.pack("<QI IIBB", len(result), len(properties), 1, 0x6000E, 0, 1),
            1,
        ),
        _node(0x01C, b""),
    ]
    if activate:
        nodes.append(_node(0x05C, revision_id + struct.pack("<I", 1)))
    content = b"".join(nodes)
    result[position : position + len(content)] = content
    struct.pack_into("<I", result, 1540, 8 + len(nodes))
    struct.pack_into("<I", result, 1548, crc32(result[1536:1544]))
    result.extend(properties)
    struct.pack_into("<Q", result, 196, len(result))
    return bytes(result)


@pytest.mark.parametrize("activate,expected", [(False, "Café 世界"), (True, "Second")])
def test_current_revision_uses_role_assignments(activate: bool, expected: str) -> None:
    """Only active role-one revisions supply current object content."""
    store = parse_revision_store(_append_revision(_fixture(), activate=activate))
    assert next(iter(store.objects.values())).properties[0].value == expected
    assert len(store.revision_objects) == 2


def test_transaction_crc_mismatch_is_reported() -> None:
    """Advisory transaction checksums produce a visible diagnostic."""
    data = bytearray(_fixture())
    data[1548] ^= 1
    store = parse_revision_store(bytes(data))
    assert any(item.code == "transaction_crc_mismatch" for item in store.diagnostics)


def test_duplicate_revision_ids_are_rejected() -> None:
    """Duplicate declarations cannot silently overwrite a revision snapshot."""
    data = bytearray(_append_revision(_fixture(), activate=False))
    position = 1040
    for _ in range(8):
        position += (struct.unpack_from("<I", data, position)[0] >> 10) & 8191
    struct.pack_into("<I", data, position + 20, 1)
    with pytest.raises(ParseError, match="Duplicate revision"):
        parse_revision_store(bytes(data))


def test_explicit_and_implicit_default_context_share_one_role_slot() -> None:
    """The last default-context role declaration wins across wire variants."""
    data = bytearray(_append_revision(_fixture(), activate=False))
    guid = UUID("12345678-1234-1234-1234-123456789abc")
    nodes = _node(0x05D, guid.bytes_le + struct.pack("<II", 2, 1) + bytes(20))
    nodes += _node(0x05C, guid.bytes_le + struct.pack("<II", 1, 1))
    position = 1040
    for _ in range(14):
        position += (struct.unpack_from("<I", data, position)[0] >> 10) & 8191
    data[position : position + len(nodes)] = nodes
    struct.pack_into("<I", data, 1540, 16)
    struct.pack_into("<I", data, 1548, crc32(data[1536:1544]))
    obj = next(iter(parse_revision_store(bytes(data)).objects.values()))
    assert obj.properties[0].value == "Café 世界"


@pytest.mark.parametrize("offset", [1024, 1528])
def test_rejects_damaged_fragment_markers(offset: int) -> None:
    """The parser validates both fragment envelope markers."""
    data = bytearray(_fixture())
    data[offset] ^= 255
    with pytest.raises(ParseError, match="fragment"):
        parse_revision_store(bytes(data))


def test_rejects_file_node_cycle() -> None:
    """A committed continuation cannot point back to an ancestor fragment."""
    data = bytearray(_fixture())
    struct.pack_into("<I", data, 1040, 255 | (4 << 10))
    struct.pack_into("<QI", data, 1516, 1024, 512)
    with pytest.raises(ParseError, match="cyclic"):
        parse_revision_store(bytes(data))


def test_limits_repeated_reference_expansion() -> None:
    """A tiny acyclic file must not cause exponentially repeated parsing."""
    depth = 15
    data = bytearray(_fixture()[:1024])
    entries = b""
    for level in range(depth):
        fragment = bytearray(128)
        struct.pack_into("<QII", fragment, 0, 11841787174971765956, level + 2, 0)
        if level == depth - 1:
            nodes = _node(0x028, b"")
            count = 1
        else:
            node = _node(0x010, struct.pack("<QI", 1024 + (level + 1) * 128, 128), 2)
            nodes = node + node
            count = 2
        fragment[16 : 16 + len(nodes)] = nodes
        struct.pack_into("<QIQ", fragment, 108, (1 << 64) - 1, 0, 10070635646201084491)
        data.extend(fragment)
        entries += struct.pack("<II", level + 2, count)
    transactions = entries + struct.pack("<II", 1, crc32(entries))
    struct.pack_into("<QI", data, 160, len(data), len(transactions))
    struct.pack_into("<QI", data, 172, 1024, 128)
    data.extend(transactions)
    struct.pack_into("<Q", data, 196, len(data))
    with pytest.raises(ParseError, match="work limit"):
        parse_revision_store(bytes(data))


def test_rejects_impossible_object_chunk() -> None:
    """Object references must remain inside the source file."""
    data = bytearray(_fixture())
    position = 1040
    for _ in range(5):
        position += (struct.unpack_from("<I", data, position)[0] >> 10) & 8191
    struct.pack_into("<Q", data, position + 4, len(data) + 1)
    with pytest.raises(ParseError, match="outside"):
        parse_revision_store(bytes(data))


def test_preserves_non_property_binary_object() -> None:
    """Opaque JCIDs retain their bytes for loss-preserving structured export."""
    data = bytearray(_fixture())
    position = 1040
    for _ in range(5):
        position += (struct.unpack_from("<I", data, position)[0] >> 10) & 8191
    struct.pack_into("<I", data, position + 20, 0x10001)
    store = parse_revision_store(bytes(data))
    assert next(iter(store.objects.values())).raw_data == bytes(data[1664:])
    assert store.diagnostics[0].code == "binary_object"


def test_context_stream_flag_belongs_to_object_space_header() -> None:
    """Context streams are announced by OSIDs, not the preceding OID header."""
    properties = struct.pack(
        "<IIIIIHII", 0, 0x40000001, 2, 1, 3, 2, 0x28000001, 0x30000002
    )
    data = bytearray(_fixture()[:1664]) + properties
    position = 1040
    for _ in range(5):
        position += (struct.unpack_from("<I", data, position)[0] >> 10) & 8191
    struct.pack_into("<I", data, position + 12, len(properties))
    struct.pack_into("<Q", data, 196, len(data))
    obj = next(iter(parse_revision_store(bytes(data)).objects.values()))
    assert [prop.value for prop in obj.properties] == [
        "12345678-1234-1234-1234-123456789abc:2",
        "12345678-1234-1234-1234-123456789abc:3",
    ]


def test_generated_embedded_attachment_is_byte_exact() -> None:
    """The embedded-data envelope must not leak padding or footer bytes."""
    data = bytearray(_fixture())
    payload = b"\x89PNG\r\n\x1a\n\x00\xfftest payload"
    attachment_id = UUID("abcdef01-2345-6789-abcd-0123456789ab")
    envelope = UUID("bde316e7-2665-4511-a4c4-8d4d0b7a9eac").bytes_le
    envelope += struct.pack("<Q", len(payload)) + bytes(12) + payload
    envelope += bytes(-(len(envelope) + 16) % 8)
    envelope += UUID("71fba722-0f79-4a0b-bb13-899256426b24").bytes_le
    position = 1040
    for _ in range(8):
        position += (struct.unpack_from("<I", data, position)[0] >> 10) & 8191
    node = _node(
        0x094, struct.pack("<QI", len(data), len(envelope)) + attachment_id.bytes_le, 1
    )
    data[position : position + len(node)] = node
    data.extend(envelope)
    struct.pack_into("<Q", data, 196, len(data))
    struct.pack_into("<I", data, 1540, 9)
    struct.pack_into("<I", data, 1548, crc32(data[1536:1544]))
    store = parse_revision_store(bytes(data))
    assert store.attachments[str(attachment_id)].data == payload


def test_encrypted_revision_is_explicitly_unsupported() -> None:
    """Encrypted object bytes must never be interpreted as clear properties."""
    data = bytearray(_fixture())
    struct.pack_into("<H", data, 1040 + 24 + 4 + 44, 2)
    with pytest.raises(UnsupportedFormatError, match="Encrypted"):
        parse_revision_store(bytes(data))


@pytest.mark.parametrize("length", [0, 63, 1023, 1100])
def test_rejects_truncated_input(length: int) -> None:
    """Malformed input must fail with a structured parser exception."""
    with pytest.raises(ParseError):
        parse_revision_store(_fixture()[:length])


def test_rejects_legacy_format_explicitly() -> None:
    """Unknown historical formats must not be mistaken for modern stores."""
    data = bytearray(_fixture())
    struct.pack_into("<I", data, 64, 11)
    with pytest.raises(UnsupportedFormatError):
        parse_revision_store(bytes(data))


def test_real_revision_sample() -> None:
    """The supplied section must yield text objects and binary attachments."""
    sample = Path(__file__).parents[1] / "Equipe Euro 2016.one"
    if not sample.exists():
        pytest.skip("Local sample not distributed")
    store = parse_revision_store(sample.read_bytes())
    assert store.format == "revision"
    assert store.revisions
    assert any(obj.jcid == 0x6000B for obj in store.objects.values())
    assert any(
        prop.name in ("RichEditTextUnicode", "TextExtendedAscii") and prop.value
        for obj in store.objects.values()
        for prop in obj.properties
    )
    assert store.attachments
    assert all(attachment.data for attachment in store.attachments.values())
    assert len(store.objects) == 138
    assert len(store.revisions) == 7
    assert any(
        prop.name == "CachedTitleString" and prop.value == "Equipe Euro 2016"
        for obj in store.objects.values()
        for prop in obj.properties
    )
    attachment = next(iter(store.attachments.values()))
    assert len(attachment.data) == 150426
    assert sha256(attachment.data).hexdigest() == (
        "e3026445518aaf13603f173a5a0db7e6fcb4d1133c0576e364d3e409ba40664b"
    )
    assert not store.diagnostics
