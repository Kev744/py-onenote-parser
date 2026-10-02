"""Decode package stores using MS-ONESTORE 2.8 and MS-FSSHTTPB 2.2.1.

The stream tree preserves compound boundaries before interpreting data elements.
"""

from uuid import UUID

from pydantic import BaseModel, Field

from .binary import BinaryReader
from .errors import ParseError
from .experimental_decoding import decode_experimental_store
from .models import Attachment, Diagnostic, OneObject, ParsedStore, Revision
from .properties import decode_property_set
from .property_catalog import property_name

PACKAGE_FORMAT = UUID("638de92f-a6d4-4bc1-9a36-b3fc2511a5b7")
FILE_TYPE = UUID("7b5c52e4-d88c-4da7-aeb1-5378d02996d3")
NULL_ID = f"{UUID(int=0)}:0"
DEFAULT_CONTEXT = "84defab9-aaa3-4a0d-a3a8-520c77ac7073:1"
CELL_SCHEMAS = {
    "1f937cb4-b26f-445f-b9f8-17e20160e461",  # OneNote section (.one).
    "e4dbfd38-e5c7-408b-a8a1-0e7b421e1f5f",  # Notebook TOC (.onetoc2).
}


class StreamObject(BaseModel):
    """Represent a bounded stream object and its compound children."""

    type_id: int
    payload: bytes
    offset: int
    compound: bool = False
    header_width: int = 0
    end_width: int = 0
    children: list["StreamObject"] = Field(default_factory=list)


class DataElement(BaseModel):
    """Associate a data element identity with its typed stream records."""

    element_id: str
    type_id: int
    records: list[StreamObject]


def read_compact_uint(reader: BinaryReader) -> int:
    """Read an unsigned compact integer per MS-FSSHTTPB 2.2.1.1."""
    first = reader.uint(1)
    if first == 0:
        return 0
    if first == 0x80:
        return reader.uint(8)
    for width in range(1, 8):
        if first & ((1 << width) - 1) == 1 << (width - 1):
            return (first | (reader.uint(width - 1) << 8)) >> width
    raise ParseError("Invalid compact integer", reader.tell() - 1)


def read_exguid(reader: BinaryReader) -> str:
    """Read the canonical ``uuid:integer`` extended GUID identity."""
    first = reader.uint(1)
    if first == 0:
        return NULL_ID
    for mask, marker, width, shift in (
        (7, 4, 1, 3),
        (63, 32, 2, 6),
        (127, 64, 3, 7),
        (255, 128, 5, 8),
    ):
        if first & mask == marker:
            number = (first | (reader.uint(width - 1) << 8)) >> shift
            return f"{UUID(bytes_le=reader.read(16))}:{number}"
    raise ParseError("Invalid extended GUID", reader.tell() - 1)


def _serial_number(reader: BinaryReader) -> None:
    """Consume a null or GUID-qualified serial number."""
    marker = reader.uint(1)
    if marker == 128:
        reader.read(24)
    elif marker != 0:
        raise ParseError("Invalid serial number", reader.tell() - 1)


def _cell_id(reader: BinaryReader) -> tuple[str, str]:
    """Return ``(object_space, context)`` from the wire context/space pair."""
    context = read_exguid(reader)
    object_space = read_exguid(reader)
    return object_space, "" if context == DEFAULT_CONTEXT else context


def _exguid_array(reader: BinaryReader) -> list[str]:
    """Read an array with a count bounded by the remaining bytes."""
    count = read_compact_uint(reader)
    if count > reader.remaining:
        raise ParseError("Extended GUID count exceeds payload", reader.tell())
    return [read_exguid(reader) for _ in range(count)]


def _header(reader: BinaryReader) -> tuple[int, bool, int, bool]:
    """Read type, compound flag, payload length, and end-marker flag."""
    first = reader.uint(1)
    marker = first & 3
    if marker in (1, 3):
        encoded = first | (reader.uint(1) << 8) if marker == 3 else first
        return encoded >> 2, False, 0, True
    width = 2 if marker == 0 else 4
    encoded = first | (reader.uint(width - 1) << 8)
    type_id = (encoded >> 3) & (0x3F if width == 2 else 0x3FFF)
    length = encoded >> (9 if width == 2 else 17)
    if width == 4 and length == 0x7FFF:
        length = read_compact_uint(reader)
    return type_id, bool(encoded & 4), length, False


def read_stream_object(reader: BinaryReader, depth: int = 0) -> StreamObject:
    """Read a balanced stream tree with a fixed nesting limit."""
    if depth > 64:
        raise ParseError("Stream nesting exceeds 64", reader.tell())
    start = reader.position
    type_id, compound, length, is_end = _header(reader)
    if is_end:
        raise ParseError("Unexpected stream end", reader.tell())
    node = StreamObject(
        type_id=type_id,
        offset=reader.tell(),
        header_width=min(4, reader.position - start),
        compound=compound,
        payload=reader.read(length),
    )
    if compound:
        while True:
            saved = reader.position
            child_type, _, _, child_end = _header(reader)
            if child_end:
                if child_type != type_id:
                    raise ParseError("Mismatched compound end", reader.tell())
                node.end_width = reader.position - saved
                break
            reader.seek(saved)
            node.children.append(read_stream_object(reader, depth + 1))
    return node


def _payload(node: StreamObject) -> BinaryReader:
    """Create a reader retaining the source offset of an object payload."""
    return BinaryReader(node.payload, offset=node.offset)


def _elements(package: StreamObject) -> list[DataElement]:
    """Decode the common data-element prefix of a complete package."""
    if (
        package.type_id != 0x15
        or not package.compound
        or package.header_width != 2
        or package.end_width != 1
        or len(package.payload) != 1
    ):
        raise ParseError("Expected data element package", package.offset)
    # Reserved values MUST be ignored; the one-byte field itself is required.
    elements = []
    for child in package.children:
        if (
            child.type_id != 1
            or not child.compound
            or child.header_width != 2
            or child.end_width != 1
        ):
            raise ParseError("Expected data element", child.offset)
        reader = _payload(child)
        element_id = read_exguid(reader)
        _serial_number(reader)
        type_id = read_compact_uint(reader)
        if reader.remaining:
            raise ParseError("Unconsumed data element prefix", reader.tell())
        elements.append(
            DataElement(element_id=element_id, type_id=type_id, records=child.children)
        )
    return elements


def _property_streams(
    reader: BinaryReader, object_ids: list[str], cells: list[tuple[str, str]]
) -> tuple[list[str], list[str], list[str]]:
    """Consume compact-ID tables while using FSSHTTPB resolved identities."""
    header = reader.uint(4)
    count = header & 0xFFFFFF
    if count != len(object_ids):
        raise ParseError("Property/object reference count mismatch", reader.tell())
    reader.read(count * 4)
    spaces: list[str] = []
    contexts: list[str] = []
    if not header & 0x80000000:
        header = reader.uint(4)
        count = header & 0xFFFFFF
        if count > len(cells):
            raise ParseError("Property/cell reference count mismatch", reader.tell())
        reader.read(count * 4)
        spaces = [cell[0] for cell in cells[:count]]
        if header & 0x40000000:
            count = reader.uint(4) & 0xFFFFFF
            if count > len(cells):
                raise ParseError(
                    "Property/context reference count mismatch", reader.tell()
                )
            reader.read(count * 4)
            contexts = [cell[1] for cell in cells[:count]]
    return object_ids, spaces, contexts


# Each branch below corresponds to one MS-FSSHTTPB object-data wire variant.
# Keeping declaration/data validation adjacent makes their invariants reviewable.
# pylint: disable=too-many-locals,too-many-branches,too-many-statements
def _group(element: DataElement, store: ParsedStore, blobs: dict[str, bytes]) -> None:
    """Join declaration/data pairs and combine OneNote object partitions."""
    declarations = next(
        (record.children for record in element.records if record.type_id == 0x1D), []
    )
    values = next(
        (record.children for record in element.records if record.type_id == 0x1E), []
    )
    if len(declarations) != len(values):
        raise ParseError("Object declaration/data count mismatch")
    group_ids: list[str] = []
    for declaration, value in zip(declarations, values):
        reader = _payload(declaration)
        object_id = read_exguid(reader)
        declared_blob = read_exguid(reader) if declaration.type_id == 5 else ""
        partition = read_compact_uint(reader)
        declared_size = (
            read_compact_uint(reader) if declaration.type_id == 0x18 else None
        )
        reference_count = read_compact_uint(reader)
        cell_count = read_compact_uint(reader)
        if reader.remaining:
            raise ParseError("Unconsumed object declaration", reader.tell())
        reader = _payload(value)
        object_ids = _exguid_array(reader)
        count = read_compact_uint(reader)
        if count > reader.remaining // 2:
            raise ParseError("Cell count exceeds payload", reader.tell())
        cells = [_cell_id(reader) for _ in range(count)]
        if len(object_ids) != reference_count or len(cells) != cell_count:
            raise ParseError("Object reference count mismatch", reader.tell())
        obj = store.objects.setdefault(object_id, OneObject(object_id=object_id))
        if object_id not in group_ids:
            group_ids.append(object_id)
        if value.type_id == 0x1C:
            blob_id = read_exguid(reader)
            if declared_blob and blob_id != declared_blob:
                raise ParseError("Blob declaration/reference mismatch", reader.tell())
            if blob_id in blobs:
                store.attachments[object_id] = Attachment(
                    attachment_id=blob_id, object_id=object_id, data=blobs[blob_id]
                )
            else:
                store.diagnostics.append(
                    Diagnostic(code="missing_blob", message=f"Missing blob {blob_id}")
                )
        elif value.type_id == 3:
            read_compact_uint(reader)
            store.diagnostics.append(
                Diagnostic(
                    code="excluded_object_data",
                    message=f"Object data excluded: {object_id}",
                )
            )
        elif value.type_id == 0x16:
            size = read_compact_uint(reader)
            if declared_size is not None and declared_size != size:
                raise ParseError("Object data size mismatch", reader.tell())
            data = reader.subreader(size)
            if partition == 4:
                obj.jcid = data.uint(4)
            elif partition == 1:
                streams = _property_streams(data, object_ids, cells)
                obj.properties = decode_property_set(
                    data,
                    object_ids=streams[0],
                    object_space_ids=streams[1],
                    context_ids=streams[2],
                    jcid=obj.jcid,
                )
                obj.references = object_ids
                if data.remaining <= 7:
                    if any(data.read(data.remaining)):
                        raise ParseError("Nonzero property padding", data.tell())
            else:
                store.diagnostics.append(
                    Diagnostic(
                        code="unknown_partition",
                        message=f"Object {object_id}: partition {partition}",
                    )
                )
                data.read(data.remaining)
            if data.remaining:
                raise ParseError("Unconsumed object partition", data.tell())
        else:
            raise ParseError("Unexpected object group data type", value.offset)
        if reader.remaining:
            raise ParseError("Unconsumed object data", reader.tell())
    # Partition order is not a semantic ordering: the JCID may arrive last.
    for object_id in group_ids:
        obj = store.objects[object_id]
        for prop in obj.properties:
            prop.name = property_name(prop.property_id, obj.jcid)
    store.object_groups[element.element_id] = group_ids


def _reassemble_fragments(
    elements: list[DataElement], store: ParsedStore
) -> list[DataElement]:
    """Reassemble bounded, nonoverlapping fragments into complete elements."""
    groups: dict[str, list[tuple[int, int, bytes]]] = {}
    result = [element for element in elements if element.type_id != 6]
    for element in elements:
        if element.type_id != 6:
            continue
        for record in element.records:
            if record.type_id != 0x6A:
                raise ParseError("Invalid fragment record", record.offset)
            reader = _payload(record)
            fragment_id = read_exguid(reader)
            total = read_compact_uint(reader)
            start = read_compact_uint(reader)
            size = read_compact_uint(reader)
            content = reader.read(size)
            if reader.remaining or start + size > total:
                raise ParseError("Fragment exceeds its declared bounds", reader.tell())
            groups.setdefault(fragment_id, []).append((start, total, content))
    for fragment_id, chunks in groups.items():
        position = 0
        total = chunks[0][1]
        parts = []
        for start, expected_total, content in sorted(chunks):
            if expected_total != total or start < position:
                raise ParseError("Overlapping or inconsistent data element fragments")
            if start != position:
                break
            parts.append(content)
            position += len(content)
        if position != total:
            store.diagnostics.append(
                Diagnostic(
                    code="incomplete_fragment",
                    message=f"Incomplete data element {fragment_id}",
                )
            )
            continue
        reader = BinaryReader(b"".join(parts))
        node = read_stream_object(reader)
        if reader.remaining:
            raise ParseError("Unconsumed reassembled fragment", reader.tell())
        decoded = _elements(
            StreamObject(
                type_id=0x15,
                payload=b"\0",
                offset=0,
                children=[node],
                compound=True,
                header_width=2,
                end_width=1,
            )
        )
        if decoded[0].type_id == 6 or decoded[0].element_id != fragment_id:
            raise ParseError("Reassembled fragment identity or type mismatch")
        result.extend(decoded)
    return result


def _normalize(elements: list[DataElement], store: ParsedStore) -> ParsedStore:
    """Resolve element maps, object groups, revisions, and attachments."""
    elements = _reassemble_fragments(elements, store)
    index_id = store.header.get("storage_index_id")
    if index_id is not None and not any(
        element.element_id == index_id and element.type_id == 1 for element in elements
    ):
        raise ParseError("Package references a missing storage index")
    blobs = {
        element.element_id: record.payload
        for element in elements
        if element.type_id == 10
        for record in element.records
        if record.type_id == 2
    }
    cell_mapping: dict[str, tuple[str, str]] = {}
    current_revisions: dict[str, str] = {}
    revision_groups: dict[str, list[str]] = {}
    group_objects: dict[str, dict[str, OneObject]] = {}
    for element in elements:
        if element.type_id == 5:
            group_store = ParsedStore(format="package")
            _group(element, group_store, blobs)
            group_objects[element.element_id] = group_store.objects
            store.object_groups.update(group_store.object_groups)
            store.attachments.update(group_store.attachments)
            store.diagnostics.extend(group_store.diagnostics)
        elif element.type_id == 1:
            if index_id is not None and element.element_id != index_id:
                continue
            for record in element.records:
                reader = _payload(record)
                if record.type_id == 0x0E:
                    cell = _cell_id(reader)
                    cell_mapping[read_exguid(reader)] = cell
                    _serial_number(reader)
                elif record.type_id in (0x11, 0x0D):
                    read_exguid(reader)
                    if record.type_id == 0x0D:
                        read_exguid(reader)
                    _serial_number(reader)
                else:
                    raise ParseError("Unknown storage index record", record.offset)
                if reader.remaining:
                    raise ParseError("Unconsumed storage index record", reader.tell())
        elif element.type_id == 3:
            for record in element.records:
                if record.type_id == 0x0B:
                    current_revisions[element.element_id] = read_exguid(
                        _payload(record)
                    )
        elif element.type_id == 4:
            revision = Revision(revision_id=element.element_id)
            groups: list[str] = []
            for record in element.records:
                reader = _payload(record)
                if record.type_id == 0x1A:
                    revision.revision_id = read_exguid(reader)
                    base_id = read_exguid(reader)
                    revision.dependent_revision_id = (
                        "" if base_id == NULL_ID else base_id
                    )
                elif record.type_id == 0x0A:
                    root_id = read_exguid(reader)
                    revision.root_objects[root_id] = read_exguid(reader)
                elif record.type_id == 0x19:
                    groups.append(read_exguid(reader))
                else:
                    raise ParseError("Unknown revision manifest record", record.offset)
                if reader.remaining:
                    raise ParseError(
                        "Unconsumed revision manifest record", reader.tell()
                    )
            store.revisions.append(revision)
            revision_groups[revision.revision_id] = groups
        elif element.type_id not in (2, 10):
            store.diagnostics.append(
                Diagnostic(
                    code="unsupported_data_element",
                    message=f"Unsupported data element type {element.type_id}",
                )
            )
    spaces = {
        revision_id: cell_mapping.get(cell_id, ("", ""))
        for cell_id, revision_id in current_revisions.items()
    }
    for revision in store.revisions:
        revision.object_space_id, revision.context_id = spaces.get(
            revision.revision_id, ("", "")
        )
        revision.role = 2 if revision.context_id else 1
        for group_id in revision_groups[revision.revision_id]:
            if group_id not in store.object_groups:
                store.diagnostics.append(
                    Diagnostic(
                        code="missing_object_group",
                        message=f"Missing object group {group_id}",
                    )
                )
            revision.objects.extend(store.object_groups.get(group_id, []))
    _resolve_revisions(store, revision_groups, group_objects)
    heads = [revision_id for revision_id, space in spaces.items() if not space[1]] or [
        revision.revision_id for revision in store.revisions
    ]
    for revision_id in heads:
        store.objects.update(store.revision_objects.get(revision_id, {}))
    store.header["current_revisions"] = {
        space[0]: revision_id for revision_id, space in spaces.items() if not space[1]
    }
    store.header["current_cells"] = {
        f"{space[0]}/{space[1]}": revision_id for revision_id, space in spaces.items()
    }
    # A package can hold unattached object groups, useful for incremental captures.
    if not store.revisions:
        for objects in group_objects.values():
            store.objects.update(objects)
    for object_id, attachment in store.attachments.items():
        obj = store.objects.get(object_id)
        extension = (
            next(
                (
                    prop.value
                    for prop in obj.properties
                    if prop.property_id == 0x1C003424
                ),
                "",
            )
            if obj
            else ""
        )
        if isinstance(extension, bytes):
            extension = extension.decode("utf-16-le").rstrip("\0")
        attachment.file_name = object_id.replace(":", "_") + (
            str(extension)
            if str(extension).startswith(".")
            else "." + str(extension) if extension else ".bin"
        )
    return store


def _resolve_revisions(
    store: ParsedStore,
    revision_groups: dict[str, list[str]],
    group_objects: dict[str, dict[str, OneObject]],
) -> None:
    """Resolve base chains without exposing historical overrides as current."""
    revisions = {revision.revision_id: revision for revision in store.revisions}

    def resolve(revision_id: str, ancestors: frozenset[str]) -> dict[str, OneObject]:
        """Build a revision snapshot with bounded dependency traversal."""
        if revision_id in store.revision_objects:
            return store.revision_objects[revision_id]
        if revision_id in ancestors or len(ancestors) >= 64:
            raise ParseError("Cyclic or excessive revision dependency chain")
        revision = revisions[revision_id]
        objects: dict[str, OneObject] = {}
        if revision.dependent_revision_id in revisions:
            base = revisions[revision.dependent_revision_id]
            if not base.object_space_id:
                base.object_space_id = revision.object_space_id
                base.context_id = revision.context_id
            objects.update(
                {
                    key: obj.model_copy(deep=True)
                    for key, obj in resolve(
                        base.revision_id, ancestors | {revision_id}
                    ).items()
                }
            )
            revision.root_objects = {**base.root_objects, **revision.root_objects}
        elif revision.dependent_revision_id:
            store.diagnostics.append(
                Diagnostic(
                    code="missing_base_revision",
                    message=f"Missing base revision {revision.dependent_revision_id}",
                )
            )
        for group_id in revision_groups[revision_id]:
            for object_id, source in group_objects.get(group_id, {}).items():
                obj = source.model_copy(deep=True)
                obj.revision_id = revision_id
                obj.object_space_id = revision.object_space_id
                objects[object_id] = obj
        revision.objects = list(objects)
        store.revision_objects[revision_id] = objects
        return objects

    for revision in sorted(
        store.revisions, key=lambda item: not bool(item.object_space_id)
    ):
        resolve(revision.revision_id, frozenset())


def parse_package_store(data: bytes, *, experimental: bool = False) -> ParsedStore:
    """Parse a complete package store and normalize its OneNote objects."""
    reader = BinaryReader(data)
    if UUID(bytes_le=reader.read(16)) != FILE_TYPE:
        raise ParseError("Not a OneNote package store")
    file_id = str(UUID(bytes_le=reader.read(16)))
    legacy_version = str(UUID(bytes_le=reader.read(16)))
    if UUID(bytes_le=reader.read(16)) != PACKAGE_FORMAT:
        raise ParseError("Unknown package format", 48)
    reader.read(4)
    wrapper = read_stream_object(reader)
    if (
        wrapper.type_id != 0x7A
        or not wrapper.compound
        or wrapper.header_width != 4
        or wrapper.end_width != 2
        or len(wrapper.children) != 1
    ):
        raise ParseError("Invalid package wrapper", reader.tell())
    padding = reader.remaining
    if any(reader.read(padding)):
        raise ParseError("Nonzero bytes after package end", reader.tell() - padding)
    prefix = _payload(wrapper)
    index_id = read_exguid(prefix)
    schema_id = str(UUID(bytes_le=prefix.read(16)))
    if schema_id not in CELL_SCHEMAS:
        raise ParseError("Unsupported OneNote package cell schema", prefix.tell() - 16)
    if prefix.remaining:
        raise ParseError("Invalid package prefix", prefix.tell())
    store = ParsedStore(
        format="package",
        header={
            "file_id": file_id,
            "legacy_version": legacy_version,
            "storage_index_id": index_id,
            "schema_id": schema_id,
            "trailing_zero_padding": padding,
        },
    )
    store = _normalize(_elements(wrapper.children[0]), store)
    return decode_experimental_store(store) if experimental else store


def parse_data_element_packages(
    packages: list[StreamObject], *, experimental: bool = False
) -> ParsedStore:
    """Normalize packages extracted from saved binary protocol responses."""
    elements = [element for package in packages for element in _elements(package)]
    store = _normalize(
        elements, ParsedStore(format="package", header={"transport": "fsshttpb"})
    )
    return decode_experimental_store(store) if experimental else store
