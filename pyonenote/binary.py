"""Bounded, little-endian binary reading built on :mod:`struct`."""

import struct
from typing import Any

from pyonenote.errors import ParseError


class BinaryReader:
    """Read a fixed byte region while retaining absolute source offsets."""

    def __init__(self, data: bytes, *, offset: int = 0) -> None:
        """Initialize a reader over ``data`` at the given source offset."""
        self.data = data
        self.offset = offset
        self.position = 0

    @property
    def remaining(self) -> int:
        """Return the number of bytes left in this region."""
        return len(self.data) - self.position

    def tell(self) -> int:
        """Return the absolute source offset of the next byte."""
        return self.offset + self.position

    def seek(self, position: int) -> None:
        """Seek within this region, rejecting offsets outside it."""
        if not 0 <= position <= len(self.data):
            raise ParseError("Seek outside record", self.tell())
        self.position = position

    def read(self, size: int) -> bytes:
        """Read exactly ``size`` bytes or raise :class:`ParseError`."""
        if size < 0 or size > self.remaining:
            raise ParseError(
                f"Invalid read of {size} bytes; {self.remaining} remain", self.tell()
            )
        start = self.position
        self.position += size
        return self.data[start : self.position]

    def uint(self, size: int) -> int:
        """Read an unsigned little-endian integer of the specified width."""
        return int.from_bytes(self.read(size), "little")

    def unpack(self, fmt: str) -> tuple[Any, ...]:
        """Unpack a bounded record with an explicit :mod:`struct` format."""
        return struct.unpack(fmt, self.read(struct.calcsize(fmt)))

    def subreader(self, size: int) -> "BinaryReader":
        """Consume a record and return a reader restricted to its bytes."""
        offset = self.tell()
        return BinaryReader(self.read(size), offset=offset)
