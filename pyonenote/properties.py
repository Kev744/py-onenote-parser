"""Decode MS-ONESTORE 2.6.6--2.6.9 property sets without losing raw data."""

from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from uuid import UUID

from pyonenote.binary import BinaryReader
from pyonenote.errors import ParseError
from pyonenote.models import Property
from pyonenote.experimental_decoding import decode_experimental_property
from pyonenote.property_catalog import PROPERTY_NAMES, property_name, property_names

__all__ = ["PROPERTY_NAMES", "property_name", "property_names", "decode_property_set"]

_UNICODE_NAMES = {
    "ListFont",
    "ConflictingUserName",
    "NoteTagLabel",
    "NextStyle",
    "Font",
    "RichEditTextUnicode",
    "CachedTitleString",
    "CachedTitleStringFromPage",
    "Author",
    "EmbeddedFileName",
    "SourceFilepath",
    "ImageFilename",
    "ImageAltText",
    "WzHyperlinkUrl",
    "ParagraphStyleId",
    "SectionDisplayName",
}


_INTEGER_NAMES = {
    "FileNameCRC",
    "OutlineElementChildLevel",
    "FontSize",
    "BodyTextAlignment",
    "LanguageID",
    "PageSize",
    "ListRestart",
    "LayoutCollisionPriority",
    "RichEditTextLangID",
    "Charset",
    "ListMSAAIndex",
    "RowCount",
    "ColumnCount",
    "SchemaRevisionInOrderToRead",
    "PageLevel",
    "UnderlineType",
    "ActionItemType",
    "NoteTagShape",
    "NoteTagPropertyStatus",
    "ActionItemStatus",
    "ActionItemSchemaVersion",
    "ParagraphAlignment",
    "DisplayedPageNumber",
    "ImageUploadState",
}
_FILETIME_NAMES = {"TopologyCreationTimeStamp", "LastModifiedTimeStamp"}
_GUID_NAMES = {
    "NotebookManagementEntityGuid",
    "FileIdentityGuid",
    "FileAncestorIdentityGuid",
}


def _filetime(value: int, offset: int) -> str:
    """Interpret MS-DTYP FILETIME with exact 100-nanosecond precision.

    :raises ParseError: If the timestamp exceeds Python's supported calendar.
    """
    seconds, fraction = divmod(value, 10_000_000)
    try:
        timestamp = datetime(1601, 1, 1, tzinfo=timezone.utc) + timedelta(
            seconds=seconds
        )
    except OverflowError as error:
        raise ParseError("FILETIME exceeds supported calendar range", offset) from error
    return timestamp.strftime("%Y-%m-%dT%H:%M:%S") + f".{fraction:07d}Z"


def _extended_ascii(
    value: bytes, has_unicode: bool
) -> tuple[str | None, Literal["decoded", "raw"]]:
    """Use the existing renderer's Windows Western text interpretation.

    :return: Readable text, or no interpretation for ignored/undefined bytes.
    """
    if has_unicode:
        return None, "raw"
    try:
        return value.decode("cp1252"), "decoded"
    except UnicodeDecodeError:
        return None, "raw"


def _interpret(
    prop: Property, offset: int, has_unicode: bool = False
) -> tuple[Any, Literal["decoded", "raw", "unknown"]]:
    """Interpret recognized values separately from their preserved wire values.

    :return: Semantic value and an honest semantic support state.
    """
    if not prop.known:
        return None, "unknown"
    if prop.name == "TextExtendedAscii":
        return _extended_ascii(prop.value, has_unicode)
    if prop.name in _GUID_NAMES:
        if not isinstance(prop.value, bytes) or len(prop.value) != 16:
            raise ParseError(f"{prop.name} requires 16 bytes", offset)
        return str(UUID(bytes_le=prop.value)), "decoded"
    if prop.name in _FILETIME_NAMES:
        return _filetime(prop.value, offset), "decoded"
    if (
        prop.name in _UNICODE_NAMES
        or prop.name in _INTEGER_NAMES
        or prop.type_id == 2
        or 8 <= prop.type_id <= 13
    ):
        return prop.value, "decoded"
    return None, "raw"


class _PropertyDecoder:
    """Share reference stream positions across recursively nested sets."""

    def __init__(
        self, reader: BinaryReader, streams: list[list[str]], jcid: int | None = None
    ) -> None:
        """Initialize a decoder.

        :param streams: Object, object-space and context reference streams.
        """
        self.reader = reader
        self.streams = streams
        self.positions = [0, 0, 0]
        self.jcid = jcid

    def decode(self, depth: int = 0) -> list[Property]:
        """Read one property set.

        :raises ParseError: If a property exceeds its data boundaries.
        """
        if depth > 64:
            raise ParseError("Property nesting exceeds 64 levels", self.reader.tell())
        identifiers = [self.reader.uint(4) for _ in range(self.reader.uint(2))]
        result = []
        for identifier in identifiers:
            type_id = (identifier >> 26) & 31
            name = property_name(identifier, self.jcid if depth == 0 else None)
            aliases = property_names(identifier)
            value, raw = self.read_value(identifier, type_id, depth)
            if name in _UNICODE_NAMES and isinstance(value, bytes):
                try:
                    value = value.decode("utf-16-le").rstrip("\x00")
                except UnicodeDecodeError as error:
                    raise ParseError(
                        "Invalid UTF-16 property", self.reader.tell() - len(raw)
                    ) from error
            prop = Property(
                property_id=identifier,
                name=name,
                type_id=type_id,
                value=value,
                raw=raw,
                name_aliases=list(aliases),
                known=bool(aliases),
            )
            prop.decoded_value, prop.interpretation = _interpret(
                prop, self.reader.tell() - len(raw), 0x1C001C22 in identifiers
            )
            if prop.interpretation == "decoded":
                prop.value = prop.decoded_value
            result.append(prop)
        return result

    def read_value(
        self, identifier: int, type_id: int, depth: int
    ) -> tuple[Any, bytes]:
        """Read a wire value.

        :return: Decoded value and its original scalar data bytes.
        """
        if type_id == 1:
            return None, b""
        if type_id == 2:
            return bool(identifier >> 31), b""
        if 3 <= type_id <= 7:
            size = self.reader.uint(4) if type_id == 7 else 1 << (type_id - 3)
            raw = self.reader.read(size)
            return (raw if type_id == 7 else int.from_bytes(raw, "little")), raw
        if 8 <= type_id <= 13:
            return self.read_references(type_id), b""
        if type_id == 16:
            return self.read_array(depth), b""
        if type_id == 17:
            return self.decode(depth + 1), b""
        raise ParseError(
            f"Unknown property wire type 0x{type_id:02X}", self.reader.tell()
        )

    def read_references(self, type_id: int) -> str | list[str]:
        """Consume references from the selected independent stream.

        :return: A single identifier or an ordered identifier array.
        """
        stream_index = (type_id - 8) // 2
        count = self.reader.uint(4) if type_id % 2 else 1
        start = self.positions[stream_index]
        if count > len(self.streams[stream_index]) - start:
            raise ParseError(
                "Property references exceed reference stream", self.reader.tell()
            )
        self.positions[stream_index] += count
        values = self.streams[stream_index][start : start + count]
        return values if type_id % 2 else values[0]

    def read_array(self, depth: int) -> list[list[Property]]:
        """Decode the documented array-of-property-sets representation.

        :return: Nested property sets in wire order.
        """
        count = self.reader.uint(4)
        if count == 0:
            return []
        if (self.reader.uint(4) >> 26) & 31 != 17:
            raise ParseError(
                "Property array must contain property sets", self.reader.tell() - 4
            )
        if count > self.reader.remaining // 2:
            raise ParseError(
                "Property array exceeds available data", self.reader.tell()
            )
        return [self.decode(depth + 1) for _ in range(count)]


def decode_property_set(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    reader: BinaryReader,
    object_ids: list[str] | None = None,
    object_space_ids: list[str] | None = None,
    context_ids: list[str] | None = None,
    *,
    jcid: int | None = None,
    experimental: bool = False,
) -> list[Property]:
    """Decode a property set beginning at its two-byte count.

    :param reader: Reader positioned at ``PropertySet.cProperties``.
    :param object_ids: Resolved identifiers in OIDs stream order.
    :param object_space_ids: Resolved identifiers in OSIDs stream order.
    :param context_ids: Resolved identifiers in ContextIDs stream order.
    :param jcid: Optional owning JCID for top-level contextual aliases only.
    :param experimental: Decode registered ink candidates for the top-level owner.
    :return: Properties preserving identifiers, types and raw scalar data.
    """
    decoder = _PropertyDecoder(
        reader, [object_ids or [], object_space_ids or [], context_ids or []], jcid
    )
    properties = decoder.decode()
    if decoder.positions != [len(stream) for stream in decoder.streams]:
        raise ParseError(
            "Unused references remain after the property set", reader.tell()
        )
    if experimental and jcid is not None:
        for prop in properties:
            decode_experimental_property(prop, jcid)
    return properties
