"""Regress confirmed MS-ONESTORE 2.7.2 header-cell property identities."""

from pathlib import Path
import struct
from uuid import UUID

import pytest

from pyonenote.binary import BinaryReader
from pyonenote.errors import ParseError
from pyonenote.package import parse_package_store
from pyonenote.properties import decode_property_set
from pyonenote.property_catalog import property_name
from tests.test_properties import property_set

_HEADER_NAMES = {
    0x14001D93: "FileNameCRC",
    0x1C001D94: "FileIdentityGuid",
    0x1C001D95: "FileAncestorIdentityGuid",
}


@pytest.mark.parametrize("identifier,name", list(_HEADER_NAMES.items()))
def test_header_cell_catalog_names(identifier: int, name: str) -> None:
    """Recognize only the header fields confirmed in the official table."""
    assert property_name(identifier) == name


@pytest.mark.parametrize("identifier", [0x1C001D94, 0x1C001D95])
def test_header_guid_keeps_wire_bytes(identifier: int) -> None:
    """Interpret little-endian GUIDs without replacing their binary values."""
    guid = UUID("12345678-1234-5678-90ab-cdef01234567")
    payload = property_set([identifier], struct.pack("<I", 16) + guid.bytes_le)
    prop = decode_property_set(BinaryReader(payload))[0]
    assert prop.name == _HEADER_NAMES[identifier]
    assert prop.known and prop.interpretation == "decoded"
    assert prop.decoded_value == str(guid)
    assert prop.value == str(guid)
    assert prop.raw == guid.bytes_le


def test_filename_crc_is_an_unsigned_integer() -> None:
    """Preserve all four CRC bytes, including a set high bit."""
    raw = struct.pack("<I", 0xFEDCBA98)
    prop = decode_property_set(BinaryReader(property_set([0x14001D93], raw)))[0]
    assert prop.name == "FileNameCRC"
    assert prop.value == prop.decoded_value == 0xFEDCBA98
    assert prop.raw == raw
    assert prop.known and prop.interpretation == "decoded"


@pytest.mark.parametrize("identifier", [0x1C001D94, 0x1C001D95])
def test_malformed_header_guid_has_absolute_offset(identifier: int) -> None:
    """Report invalid GUID length at the original data offset."""
    payload = property_set([identifier], struct.pack("<I", 15) + bytes(15))
    with pytest.raises(ParseError, match="requires 16 bytes") as failure:
        decode_property_set(BinaryReader(payload, offset=100))
    assert failure.value.offset == 110


def test_real_package_header_fields_and_history_are_recognized() -> None:
    """Name header properties in the current package and retained snapshots."""
    source = Path(__file__).resolve().parents[1] / "Section sans titre.one"
    store = parse_package_store(source.read_bytes())
    observed = set()
    for objects in [store.objects, *store.revision_objects.values()]:
        for obj in objects.values():
            for prop in obj.properties:
                if prop.property_id in _HEADER_NAMES:
                    observed.add(prop.property_id)
                    assert prop.name == _HEADER_NAMES[prop.property_id]
                    assert prop.name_aliases == [prop.name]
                    assert prop.known and prop.interpretation == "decoded"
                    if prop.type_id == 7:
                        assert prop.value == prop.decoded_value
                        assert prop.decoded_value == str(UUID(bytes_le=prop.raw))
                    else:
                        assert prop.decoded_value == int.from_bytes(prop.raw, "little")
    assert observed == set(_HEADER_NAMES)


@pytest.mark.parametrize("identifier", [0x20001CE2, 0x240034D8, 0x1400348B])
def test_unconfirmed_sample_fields_remain_unknown(identifier: int) -> None:
    """Do not guess names for fields absent from the checked specifications."""
    assert property_name(identifier) == f"Unknown_0x{identifier:08X}"
