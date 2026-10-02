"""Regression tests for bounded binary operations and shared models."""

import pytest

from pyonenote.binary import BinaryReader
from pyonenote.errors import ParseError
from pyonenote.models import Attachment, ParsedStore


def test_reader_bounds_and_absolute_offsets() -> None:
    """Report the absolute location of truncated input."""
    reader = BinaryReader(b"\x01\x02\x03", offset=10)
    assert reader.uint(2) == 513
    assert reader.tell() == 12
    with pytest.raises(ParseError, match="12"):
        reader.read(2)
    assert reader.remaining == 1


def test_subreader_cannot_escape_its_record() -> None:
    """Keep children inside their declared record boundaries."""
    reader = BinaryReader(b"abcd", offset=8)
    child = reader.subreader(2)
    assert child.read(2) == b"ab"
    assert reader.read(2) == b"cd"
    with pytest.raises(ParseError):
        child.seek(3)
    with pytest.raises(ParseError):
        child.read(-1)


def test_unpack_and_seek() -> None:
    """Decode explicitly little-endian values without moving on errors."""
    reader = BinaryReader(b"\x01\x00\x02\x00")
    assert reader.unpack("<HH") == (1, 2)
    reader.seek(0)
    assert reader.position == 0
    assert reader.uint(2) == 1


def test_structural_bytes_round_trip() -> None:
    """Serialize arbitrary attachment bytes as base64, without text loss."""
    store = ParsedStore(
        format="revision",
        attachments={"image": Attachment(attachment_id="image", data=b"\xff\x00\x80")},
    )
    recovered = ParsedStore.model_validate_json(store.model_dump_json())
    assert recovered.attachments["image"].data == b"\xff\x00\x80"
