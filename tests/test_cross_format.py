"""Equivalent revision/package wires must produce equivalent visible content."""

import struct
from uuid import UUID
from zlib import crc32

import pytest

from pyonenote.content import assemble_document
from pyonenote.models import Document
from pyonenote.package import parse_package_store
from pyonenote.revision import parse_revision_store
from tests.test_package import _compact, _element, _guid, _node as _package_node
from tests.test_revision import _fixture, _node as _revision_node


def _string(value: str) -> bytes:
    """Encode an MS-ONE variable-length Unicode property value."""
    encoded = value.encode("utf-16-le")
    return struct.pack("<I", len(encoded)) + encoded


def _properties(ids: list[int], values: list[bytes], refs: list[int]) -> bytes:
    """Encode the same property-set bytes for both storage formats."""
    return (
        struct.pack("<I", 0x80000000 | len(refs))
        + b"".join(struct.pack("<I", value) for value in refs)
        + struct.pack("<H", len(ids))
        + b"".join(struct.pack("<I", value) for value in ids)
        + b"".join(values)
    )


def _objects() -> list[tuple[int, int, bytes, list[int]]]:
    """Describe a page, a Unicode paragraph and an embedded image."""
    return [
        (
            1,
            0x6000B,
            _properties(
                [0x1C001CF3, 0x24001C20],
                [_string("Shared page"), struct.pack("<I", 2)],
                [2, 3],
            ),
            [2, 3],
        ),
        (2, 0x6000E, _properties([0x1C001C22], [_string("Café 世界")], []), []),
        (
            3,
            0x60011,
            _properties([0x1C001DD7, 0x20001C3F], [_string("sample.png"), b""], [4]),
            [4],
        ),
    ]


def _revision_wire() -> bytes:
    """Construct a committed revision independently from package framing."""
    data = bytearray(_fixture()[:1664])
    data[1040:1516] = bytes(476)
    guid = UUID("12345678-1234-1234-1234-123456789abc")
    exguid = guid.bytes_le + struct.pack("<I", 1)
    nodes = [
        _revision_node(0x00C, exguid),
        _revision_node(0x01E, exguid + bytes(20) + struct.pack("<IH", 1, 0)),
        _revision_node(0x022, b""),
        _revision_node(0x024, struct.pack("<I", 0) + guid.bytes_le),
        _revision_node(0x028, b""),
    ]
    for number, jcid, properties, refs in _objects():
        nodes.append(
            _revision_node(
                0x0A4,
                struct.pack(
                    "<QI IIBB", len(data), len(properties), number, jcid, bool(refs), 1
                ),
                1,
            )
        )
        data.extend(properties)
    attachment_guid = UUID(int=20)
    file_reference = f"<ifndf>{{{attachment_guid}}}"
    file_strings = struct.pack("<I", len(file_reference)) + file_reference.encode(
        "utf-16-le"
    )
    file_strings += struct.pack("<I", 4) + ".png".encode("utf-16-le")
    nodes.append(
        _revision_node(0x072, struct.pack("<IIB", 4, 0x80039, 1) + file_strings)
    )
    nodes.append(_revision_node(0x059, struct.pack("<II", 1, 1)))
    nodes.append(_revision_node(0x01C, b""))
    payload = b"attachment\0bytes"
    envelope = UUID("bde316e7-2665-4511-a4c4-8d4d0b7a9eac").bytes_le
    envelope += struct.pack("<Q", len(payload)) + bytes(12) + payload
    envelope += bytes(-(len(envelope) + 16) % 8)
    envelope += UUID("71fba722-0f79-4a0b-bb13-899256426b24").bytes_le
    nodes.append(
        _revision_node(
            0x094,
            struct.pack("<QI", len(data), len(envelope)) + attachment_guid.bytes_le,
            1,
        )
    )
    data.extend(envelope)
    content = b"".join(nodes)
    assert len(content) <= 476
    data[1040 : 1040 + len(content)] = content
    entries = struct.pack("<II", 2, len(nodes))
    data[1536:1552] = entries + struct.pack("<II", 1, crc32(entries))
    struct.pack_into("<Q", data, 196, len(data))
    return bytes(data)


def _package_wire(properties_first: bool = False) -> bytes:
    """Construct partitioned package objects for the same document."""
    declarations = b""
    values = b""
    for number, jcid, properties, refs in _objects():
        for partition, content in ((4, struct.pack("<I", jcid)), (1, properties))[
            :: -1 if properties_first else 1
        ]:
            references = refs if partition == 1 else []
            declarations += _package_node(
                0x18,
                _guid(number)
                + _compact(partition)
                + _compact(len(content))
                + _compact(len(references))
                + b"\0",
            )
            reference_data = _compact(len(references)) + b"".join(
                _guid(ref) for ref in references
            )
            values += _package_node(
                0x16, reference_data + b"\0" + _compact(len(content)) + content
            )
    declarations += _package_node(5, _guid(4) + _guid(20) + _compact(2) + b"\0\0")
    values += _package_node(0x1C, b"\0\0" + _guid(20))
    group = _element(
        10,
        5,
        _package_node(0x1D, children=declarations)
        + _package_node(0x1E, children=values),
    )
    revision = _element(
        11,
        4,
        _package_node(0x1A, _guid(12) + b"\0")
        + _package_node(0x0A, _guid(13) + _guid(1))
        + _package_node(0x19, _guid(10)),
    )
    blob = _element(20, 10, _package_node(2, b"attachment\0bytes"))
    prefix = (
        UUID("7b5c52e4-d88c-4da7-aeb1-5378d02996d3").bytes_le
        + UUID(int=3).bytes_le * 2
        + UUID("638de92f-a6d4-4bc1-9a36-b3fc2511a5b7").bytes_le
        + bytes(4)
    )
    return prefix + _package_node(
        0x7A,
        _guid(30) + UUID("1f937cb4-b26f-445f-b9f8-17e20160e461").bytes_le,
        _package_node(0x15, b"\0", _element(30, 1, b"") + group + revision + blob),
    )


def _visible(
    document: Document,
) -> tuple[str, str, list[tuple[str, str]], list[tuple[str, bytes]]]:
    """Normalize visible content while deliberately excluding storage IDs."""
    section = document.sections[0]
    page = section.pages[0]
    return (
        section.title,
        page.title,
        [(block.kind, block.text) for block in page.blocks],
        [
            (attachment.file_name, attachment.data)
            for attachment in document.store.attachments.values()
        ],
    )


def test_equivalent_revision_and_package_content() -> None:
    """Both binary pipelines must converge on titles, text and exact assets."""
    revision = assemble_document(parse_revision_store(_revision_wire()), "Shared.one")
    package = assemble_document(parse_package_store(_package_wire()), "Shared.one")
    expected = (
        "Shared",
        "Shared page",
        [("paragraph", "Café 世界"), ("image", "")],
        [("sample.png", b"attachment\0bytes")],
    )
    assert _visible(revision) == _visible(package) == expected
    assert not revision.store.diagnostics
    assert not package.store.diagnostics


@pytest.mark.parametrize("properties_first", [False, True])
def test_owner_specific_names_in_both_stores(properties_first: bool) -> None:
    """Resolve aliases even when a package's property partition precedes its JCID."""
    stores = [
        parse_revision_store(_revision_wire()),
        parse_package_store(_package_wire(properties_first=properties_first)),
    ]
    for store in stores:
        snapshots = [store.objects, *store.revision_objects.values()]
        for objects in snapshots:
            page = next(obj for obj in objects.values() if obj.jcid == 0x6000B)
            prop = next(
                prop for prop in page.properties if prop.property_id == 0x24001C20
            )
            assert prop.name == "ElementChildNodesOfPage"
            assert prop.known
            assert page.jcid_name == "jcidPageNode"
