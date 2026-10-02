"""Verify readable property values and lossless hexadecimal raw exports."""

import json
import struct

import pytest
from pydantic import ValidationError

from pyonenote.binary import BinaryReader
from pyonenote.models import Attachment, OneObject, Property
from pyonenote.properties import decode_property_set
from tests.test_properties import property_set


@pytest.mark.parametrize("text", ["Eliseu", "André Gomes", "", "A\x00B"])
def test_extended_ascii_value_is_readable(text: str) -> None:
    """Decode text without stripping bytes from a non-terminated string."""
    raw = text.encode("cp1252")
    source = property_set([0x1C003498], struct.pack("<I", len(raw)) + raw)
    prop = decode_property_set(BinaryReader(source))[0]
    assert prop.value == prop.decoded_value == text
    assert prop.interpretation == "decoded"
    assert prop.raw == raw
    assert json.loads(prop.model_dump_json())["raw"] == "0x" + raw.hex().upper()


def test_undefined_extended_ascii_byte_remains_lossless() -> None:
    """Keep undecodable bytes rather than replacing them with guessed text."""
    source = property_set([0x1C003498], struct.pack("<I", 1) + b"\x81")
    prop = decode_property_set(BinaryReader(source))[0]
    assert prop.value == prop.raw == b"\x81"
    assert prop.interpretation == "raw"


@pytest.mark.parametrize("raw", [b"", b"Eliseu", bytes(range(256))])
def test_property_hex_round_trip_and_python_bytes(raw: bytes) -> None:
    """Export every raw byte as explicit hex while retaining Python bytes."""
    prop = Property(property_id=0x1C003498, value="text", raw=raw)
    obj = OneObject(object_id="text", properties=[prop])
    exported = obj.model_dump_json()
    assert json.loads(exported)["properties"][0]["raw"] == "0x" + raw.hex().upper()
    assert OneObject.model_validate_json(exported).properties[0].raw == raw
    assert prop.model_dump()["raw"] == raw


def test_legacy_base64_raw_can_still_be_loaded() -> None:
    """Distinguish new prefixed hex from old Base64 without guessing."""
    prop = Property.model_validate_json('{"property_id":1,"raw":"RWxpc2V1"}')
    assert prop.raw == b"Eliseu"
    assert json.loads(prop.model_dump_json())["raw"] == "0x456C69736575"


def test_invalid_hex_is_rejected() -> None:
    """Reject broken hex rather than interpreting it as legacy Base64."""
    with pytest.raises(ValidationError):
        Property.model_validate({"property_id": 1, "raw": "0xXYZ"})


def test_attachment_encoding_is_not_changed() -> None:
    """Keep attachment payloads in the existing compact Base64 format."""
    attachment = Attachment(attachment_id="a", data=b"Eliseu")
    assert json.loads(attachment.model_dump_json())["data"] == "RWxpc2V1"


def test_unicode_property_leaves_extended_ascii_uninterpreted() -> None:
    """Ignore the undefined ASCII alternative when Unicode text exists."""
    text = "Hello".encode("utf-16-le")
    payload = struct.pack("<I", 1) + b"\x81" + struct.pack("<I", len(text)) + text
    properties = decode_property_set(
        BinaryReader(property_set([0x1C003498, 0x1C001C22], payload))
    )
    assert properties[0].value == properties[0].raw == b"\x81"
    assert properties[0].interpretation == "raw"
    assert properties[1].value == "Hello"
