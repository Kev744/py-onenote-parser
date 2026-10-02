"""Decode revision stores using MS-ONESTORE sections 2.3 through 2.6.

References: https://learn.microsoft.com/en-us/openspecs/office_file_formats/
ms-onestore/ae670cd2-4b38-4b24-82d1-87cfb2cc3725
"""

from uuid import UUID
from zlib import crc32

from pydantic import BaseModel, Field

from pyonenote.binary import BinaryReader
from pyonenote.errors import ParseError, UnsupportedFormatError
from pyonenote.experimental_decoding import decode_experimental_store
from pyonenote.models import Attachment, Diagnostic, OneObject, ParsedStore, Revision
from pyonenote.properties import decode_property_set

REVISION_FORMAT = "109add3f-911b-49f5-a5d0-1791edc8aed8"
ONE_TYPE = "7b5c52e4-d88c-4da7-aeb1-5378d02996d3"
TOC_TYPE = "43ff2fa1-efd9-4c76-9ee2-10ea5722765f"
LIST_MAGIC = 11841787174971765956
LIST_FOOTER = 10070635646201084491
NIL_OFFSET = (1 << 64) - 1


class _Context(BaseModel):
    """Track IDs scoped to a revision and its nested object groups."""

    object_space_id: str = ""
    revision_id: str = ""
    group_id: str = ""
    manifest_offset: int = 0
    global_ids: dict[int, str] = Field(default_factory=dict)


def _guid(reader: BinaryReader) -> str:
    """Read one little-endian GUID."""
    return str(UUID(bytes_le=reader.read(16)))


def _extended_guid(reader: BinaryReader) -> str:
    """Read an ExtendedGUID in the common model's canonical notation."""
    return f"{_guid(reader)}:{reader.uint(4)}"


def _context_id(reader: BinaryReader) -> str:
    """Normalize an explicitly encoded default context to its common key."""
    value = _extended_guid(reader)
    return "" if value == "00000000-0000-0000-0000-000000000000:0" else value


def _compact_id(reader: BinaryReader, context: _Context) -> str:
    """Resolve one CompactID using the current global identification table."""
    value = reader.uint(4)
    index = value >> 8
    if index not in context.global_ids:
        raise ParseError(f"Undefined compact GUID index {index}", reader.tell() - 4)
    return f"{context.global_ids[index]}:{value & 255}"


def _chunk_reference(reader: BinaryReader, header: int) -> tuple[int, int]:
    """Decode a FileNodeChunkReference including compressed forms."""
    stp_format = (header >> 23) & 3
    cb_format = (header >> 25) & 3
    stp = reader.uint((8, 4, 2, 4)[stp_format])
    size = reader.uint((4, 8, 1, 2)[cb_format])
    return stp * (8 if stp_format >= 2 else 1), size * (8 if cb_format >= 2 else 1)


class _RevisionParser:  # pylint: disable=too-many-instance-attributes
    """Read bounded chunks and project committed objects into a ParsedStore."""

    def __init__(self, data: bytes) -> None:
        """Initialize parser-local state without opening or modifying files."""
        self.data = data
        self.store = ParsedStore(format="revision")
        self.committed_counts: dict[int, int] = {}
        self.revisions: dict[str, Revision] = {}
        self.global_tables: dict[str, dict[int, str]] = {}
        self.revision_objects: dict[str, dict[str, OneObject]] = {}
        self.roles: dict[tuple[str, str, int], str] = {}
        self.seen_revisions: set[tuple[int, str]] = set()
        self.active_lists: set[int] = set()
        self.attachment_links: list[tuple[str, str, str]] = []
        self.node_budget = max(4096, len(data) * 4)

    def _reader(self, offset: int, size: int) -> BinaryReader:
        """Read a file interval, rejecting overflow and invalid references."""
        if offset < 0 or size < 0 or offset + size > len(self.data):
            raise ParseError("Chunk reference lies outside the file", offset)
        return BinaryReader(self.data[offset : offset + size], offset=offset)

    def parse(self) -> ParsedStore:
        """Validate the header and decode committed lists and attachments."""
        header = self._reader(0, 1024)
        file_type = _guid(header)
        file_id = _guid(header)
        header.read(16)
        file_format = _guid(header)
        versions = [header.uint(4) for _ in range(4)]
        if file_format != REVISION_FORMAT or file_type not in (ONE_TYPE, TOC_TYPE):
            raise UnsupportedFormatError("Not a supported OneNote revision store", 0)
        expected_version = 42 if file_type == ONE_TYPE else 27
        if any(version != expected_version for version in versions):
            raise UnsupportedFormatError(
                f"Revision format versions {versions} are unsupported; expected {expected_version}",
                64,
            )
        header.seek(96)
        transactions = header.uint(4)
        header.seek(160)
        transaction_ref = (header.uint(8), header.uint(4))
        root_ref = (header.uint(8), header.uint(4))
        header.seek(196)
        expected_length = header.uint(8)
        if expected_length > len(self.data):
            raise ParseError("File is shorter than its declared length", 196)
        self.store.header = {
            "file_type": file_type,
            "file_id": file_id,
            "file_format": file_format,
            "format_version": versions[0],
            "transactions": transactions,
            "expected_length": expected_length,
        }
        self._read_transactions(transaction_ref, transactions)
        self._read_list(root_ref, _Context())
        self._resolve_dependencies()
        for object_id, attachment_id, extension in self.attachment_links:
            attachment = self.store.attachments.get(attachment_id)
            if attachment is None:
                self.store.diagnostics.append(
                    Diagnostic(
                        code="external_attachment",
                        message=f"Attachment {attachment_id} for {object_id} is not embedded",
                    )
                )
                continue
            attachment.object_id = object_id
            if not attachment.file_name:
                attachment.file_name = attachment_id + (
                    extension
                    if extension.startswith(".")
                    else f".{extension}" if extension else ""
                )
        return self.store

    def _read_transactions(self, reference: tuple[int, int], count: int) -> None:
        """Apply only the transaction prefix committed by the file header."""
        if count == 0:
            raise ParseError("Revision store has no committed transaction", 96)
        seen: set[int] = set()
        completed = 0
        running_crc = 0
        transaction_crc = 0
        while completed < count:
            offset, size = reference
            if offset in seen:
                raise ParseError("Cycle in transaction log", offset)
            seen.add(offset)
            reader = self._reader(offset, size)
            # A nonfinal fragment reserves its last twelve bytes for its link.
            while reader.remaining >= 8:
                if reader.remaining == 12:
                    break
                entry_offset = reader.tell()
                list_id, node_count = reader.uint(4), reader.uint(4)
                entry_bytes = self.data[entry_offset : entry_offset + 8]
                if list_id == 1:
                    if self.store.header[
                        "file_type"
                    ] == ONE_TYPE and node_count not in (running_crc, transaction_crc):
                        self.store.diagnostics.append(
                            Diagnostic(
                                code="transaction_crc_mismatch",
                                message=f"Advisory CRC differs for transaction {completed + 1}",
                                offset=entry_offset,
                            )
                        )
                    transaction_crc = 0
                    completed += 1
                    if completed == count:
                        return
                elif list_id == 0:
                    raise ParseError(
                        "Invalid transaction list identifier", reader.tell() - 8
                    )
                else:
                    self.committed_counts[list_id] = node_count
                    transaction_crc = crc32(entry_bytes, transaction_crc)
                running_crc = crc32(entry_bytes, running_crc)
            if reader.remaining != 12:
                raise ParseError(
                    "Transaction fragment has no continuation", reader.tell()
                )
            reference = (reader.uint(8), reader.uint(4))

    def _read_list(self, reference: tuple[int, int], context: _Context) -> None:
        """Visit a linked file-node list with cycle and commit-count checks."""
        # The local state mirrors the fragment envelope and its commit boundary.
        # pylint: disable=too-many-locals,too-many-branches
        first_offset = reference[0]
        if first_offset in self.active_lists or len(self.active_lists) > 128:
            raise ParseError(
                "Cyclic or excessively nested file-node lists", first_offset
            )
        self.active_lists.add(first_offset)
        seen: set[int] = set()
        consumed = 0
        expected_list_id: int | None = None
        expected_sequence = 0
        try:
            while True:
                offset, size = reference
                if offset in seen or size < 36:
                    raise ParseError("Invalid or cyclic list fragment", offset)
                seen.add(offset)
                reader = self._reader(offset, size)
                magic, list_id, sequence = (
                    reader.uint(8),
                    reader.uint(4),
                    reader.uint(4),
                )
                if magic != LIST_MAGIC or sequence != expected_sequence:
                    raise ParseError("Invalid file-node fragment header", offset)
                if expected_list_id is not None and list_id != expected_list_id:
                    raise ParseError("File-node list identity changed", offset + 8)
                expected_list_id = list_id
                expected_sequence += 1
                if list_id not in self.committed_counts:
                    raise ParseError(
                        "Referenced list has no committed transaction", offset
                    )
                limit = self.committed_counts[list_id]
                trailer = self._reader(offset + size - 20, 20)
                next_ref = (trailer.uint(8), trailer.uint(4))
                if trailer.uint(8) != LIST_FOOTER:
                    raise ParseError(
                        "Invalid file-node fragment footer", offset + size - 8
                    )
                while consumed < limit and reader.position + 4 <= size - 20:
                    self.node_budget -= 1
                    if self.node_budget < 0:
                        raise ParseError(
                            "File-node traversal work limit exceeded", reader.tell()
                        )
                    node_offset = reader.tell()
                    header = reader.uint(4)
                    node_id = header & 1023
                    node_size = (header >> 10) & 8191
                    if node_id == 255:
                        consumed += 1
                        break
                    if node_size < 4 or reader.position + node_size - 4 > size - 20:
                        raise ParseError("Invalid file-node size", node_offset)
                    body = reader.subreader(node_size - 4)
                    consumed += 1
                    self._read_node(node_id, header, body, context)
                if consumed >= limit:
                    return
                if next_ref == (NIL_OFFSET, 0):
                    raise ParseError(
                        "File-node list ends before its committed count", offset
                    )
                reference = next_ref
        finally:
            self.active_lists.remove(first_offset)

    def _read_node(
        self, node_id: int, header: int, body: BinaryReader, context: _Context
    ) -> None:
        """Decode one file node and follow only documented list references."""
        # Dispatch follows the enumerated MS-ONESTORE FileNode alternatives.
        # pylint: disable=too-many-branches,too-many-statements
        base_type = (header >> 27) & 15
        reference = _chunk_reference(body, header) if base_type in (1, 2) else None
        if node_id == 0x004:
            self.store.header["root_object_space_id"] = _extended_guid(body)
        elif node_id in (0x00C, 0x014):
            context.object_space_id = _extended_guid(body)
            if node_id == 0x014:
                context.manifest_offset = body.offset
                self.roles = {
                    key: value
                    for key, value in self.roles.items()
                    if key[0] != context.object_space_id
                }
        elif node_id in (0x008, 0x010, 0x090, 0x0B0):
            child = context.model_copy(deep=True)
            if node_id == 0x008:
                child.object_space_id = _extended_guid(body)
            elif node_id == 0x0B0:
                child.group_id = _extended_guid(body)
            if reference is None:
                raise ParseError("Missing file-node list reference", body.tell())
            self._read_list(reference, child)
        elif node_id in (0x01B, 0x01E, 0x01F):
            self._start_revision(node_id, body, context)
        elif node_id == 0x01C:
            self.global_tables[context.revision_id] = dict(context.global_ids)
        elif node_id in (0x021, 0x022):
            context.global_ids = {}
        elif node_id == 0x024:
            index = body.uint(4)
            context.global_ids[index] = _guid(body)
        elif node_id in (0x025, 0x026):
            self._map_global_ids(node_id, body, context)
        elif node_id in (0x059, 0x05A):
            object_id = (
                _compact_id(body, context) if node_id == 0x059 else _extended_guid(body)
            )
            role = str(body.uint(4))
            self._revision(context).root_objects[role] = object_id
        elif node_id in (0x05C, 0x05D):
            revision_id = _extended_guid(body)
            role_id = body.uint(4)
            context_id = _context_id(body) if node_id == 0x05D else ""
            self.roles[(context.object_space_id, context_id, role_id)] = revision_id
        elif node_id == 0x0B4:
            context.group_id = _extended_guid(body)
        elif node_id in (0x0A4, 0x0A5, 0x0C4, 0x0C5):
            if reference is None:
                raise ParseError("Missing object property chunk", body.tell())
            self._read_object(node_id, body, context, reference)
        elif node_id in (0x072, 0x073):
            self._read_file_declaration(node_id, body, context)
        elif node_id == 0x094:
            if reference is None:
                raise ParseError("Missing attachment chunk", body.tell())
            self._read_attachment(_guid(body), reference)
        elif node_id == 0x07C:
            raise UnsupportedFormatError(
                "Encrypted OneNote object spaces are unsupported", body.tell()
            )
        elif node_id not in (0x028, 0x084, 0x08C, 0x0B8, 0x0C2):
            raise UnsupportedFormatError(
                f"Unsupported revision file-node type 0x{node_id:03x}", body.tell()
            )

    def _start_revision(
        self, node_id: int, body: BinaryReader, context: _Context
    ) -> None:
        """Declare a revision and associate its role and optional context."""
        context.revision_id = _extended_guid(body)
        identity = (context.manifest_offset, context.revision_id)
        if identity in self.seen_revisions:
            raise ParseError(
                "Duplicate revision identity in a manifest", body.tell() - 20
            )
        self.seen_revisions.add(identity)
        dependency = _extended_guid(body)
        if node_id == 0x01B:
            body.read(8)
        role_id, encryption = body.uint(4), body.uint(2)
        if encryption:
            raise UnsupportedFormatError(
                "Encrypted revision object data is unsupported", body.tell() - 2
            )
        context_id = _context_id(body) if node_id == 0x01F else ""
        revision = Revision(
            revision_id=context.revision_id,
            object_space_id=context.object_space_id,
            dependent_revision_id=dependency,
            role=role_id,
            context_id=context_id,
        )
        self.revisions[context.revision_id] = revision
        self.revision_objects[context.revision_id] = {}
        self.store.revisions.append(revision)
        self.roles[(context.object_space_id, context_id, role_id)] = context.revision_id
        context.global_ids = {}

    def _revision(self, context: _Context) -> Revision:
        """Require an enclosing revision for object declarations and roots."""
        if context.revision_id not in self.revisions:
            raise ParseError("Object encountered outside a revision")
        return self.revisions[context.revision_id]

    def _map_global_ids(
        self, node_id: int, body: BinaryReader, context: _Context
    ) -> None:
        """Resolve dependency-table mappings used by table-of-contents files."""
        dependency = self._revision(context).dependent_revision_id
        previous = self.global_tables.get(dependency, {})
        if node_id == 0x025:
            source, target, count = body.uint(4), body.uint(4), 1
        else:
            source, count, target = body.uint(4), body.uint(4), body.uint(4)
        if count > len(previous):
            raise ParseError(
                "Global identification mapping exceeds source table", body.tell()
            )
        for delta in range(count):
            if source + delta not in previous:
                raise ParseError("Missing dependency GUID mapping", body.tell())
            context.global_ids[target + delta] = previous[source + delta]

    def _read_id_stream(
        self, reader: BinaryReader, context: _Context
    ) -> tuple[int, list[str]]:
        """Read an object, object-space or context CompactID stream."""
        header = reader.uint(4)
        count = header & 0xFFFFFF
        if count > reader.remaining // 4:
            raise ParseError(
                "Identifier stream exceeds its object chunk", reader.tell()
            )
        return header, [_compact_id(reader, context) for _ in range(count)]

    def _read_object(
        self,
        node_id: int,
        body: BinaryReader,
        context: _Context,
        reference: tuple[int, int],
    ) -> None:
        """Decode a property object and retain its revision and group identity."""
        object_id = _compact_id(body, context)
        jcid = body.uint(4)
        body.uint(1)
        body.uint(4 if node_id in (0x0A5, 0x0C5) else 1)
        if node_id in (0x0C4, 0x0C5):
            body.read(16)
        obj = OneObject(
            object_id=object_id,
            jcid=jcid,
            revision_id=context.revision_id,
            object_space_id=context.object_space_id,
        )
        reader = self._reader(*reference)
        if jcid & 0x20000:
            flags, object_ids = self._read_id_stream(reader, context)
            space_ids: list[str] = []
            context_ids: list[str] = []
            if not flags & 0x80000000:
                space_flags, space_ids = self._read_id_stream(reader, context)
                if space_flags & 0x40000000:
                    _, context_ids = self._read_id_stream(reader, context)
            obj.properties = decode_property_set(
                reader, object_ids, space_ids, context_ids, jcid=jcid
            )
            obj.references = object_ids
            if reader.remaining > 7 or any(reader.read(reader.remaining)):
                raise ParseError("Invalid property-set padding", reader.tell())
        else:
            obj.raw_data = reader.read(reader.remaining)
            self.store.diagnostics.append(
                Diagnostic(
                    code="binary_object",
                    message=f"Object {object_id} has non-property JCID 0x{jcid:x}",
                    offset=reference[0],
                )
            )
        self.revision_objects[context.revision_id][object_id] = obj
        self._revision(context).objects.append(object_id)
        if context.group_id:
            self.store.object_groups.setdefault(context.group_id, []).append(object_id)

    def _read_file_declaration(
        self, node_id: int, body: BinaryReader, context: _Context
    ) -> None:
        """Associate a file-data object with its embedded-file identity."""
        object_id = _compact_id(body, context)
        jcid = body.uint(4)
        body.uint(1 if node_id == 0x072 else 4)
        reference = self._read_string(body)
        extension = self._read_string(body)
        attachment_id = reference.removeprefix("<ifndf>").strip("{}").lower()
        self.attachment_links.append((object_id, attachment_id, extension))
        self.revision_objects[context.revision_id][object_id] = OneObject(
            object_id=object_id,
            jcid=jcid,
            revision_id=context.revision_id,
            object_space_id=context.object_space_id,
        )
        self._revision(context).objects.append(object_id)

    @staticmethod
    def _read_string(reader: BinaryReader) -> str:
        """Decode an explicitly sized UTF-16LE StringInStorageBuffer."""
        count = reader.uint(4)
        try:
            return reader.read(count * 2).decode("utf-16-le").rstrip("\0")
        except UnicodeDecodeError as error:
            raise ParseError("Invalid UTF-16 storage string", reader.tell()) from error

    def _read_attachment(self, attachment_id: str, reference: tuple[int, int]) -> None:
        """Validate the file-data envelope and preserve its exact payload."""
        reader = self._reader(*reference)
        if _guid(reader) != "bde316e7-2665-4511-a4c4-8d4d0b7a9eac":
            raise ParseError("Invalid embedded file header", reference[0])
        length = reader.uint(8)
        reader.read(12)
        if length > reader.remaining - 16:
            raise ParseError("Embedded payload overlaps its footer", reader.tell())
        payload = reader.read(length)
        reader.seek(reference[1] - 16)
        if _guid(reader) != "71fba722-0f79-4a0b-bb13-899256426b24":
            raise ParseError("Invalid embedded file footer", reader.tell() - 16)
        self.store.attachments[attachment_id] = Attachment(
            attachment_id=attachment_id, data=payload
        )

    def _resolve_dependencies(self) -> None:
        """Resolve revision snapshots and select the active default contexts."""
        resolved: set[str] = set()
        for revision in self.store.revisions:
            chain: list[Revision] = []
            current = revision
            seen: set[str] = set()
            while current.revision_id not in resolved:
                if current.revision_id in seen:
                    raise ParseError("Cycle in revision dependencies")
                seen.add(current.revision_id)
                chain.append(current)
                dependency = current.dependent_revision_id
                if dependency in ("", "00000000-0000-0000-0000-000000000000:0"):
                    break
                if dependency not in self.revisions:
                    raise ParseError(f"Missing dependency revision {dependency}")
                current = self.revisions[dependency]
            for current in reversed(chain):
                parent = self.revisions.get(current.dependent_revision_id)
                if parent is not None:
                    current.root_objects = parent.root_objects | current.root_objects
                    self.revision_objects[current.revision_id] = (
                        self.revision_objects[parent.revision_id]
                        | self.revision_objects[current.revision_id]
                    )
                current.objects = list(self.revision_objects[current.revision_id])
                resolved.add(current.revision_id)
        self.store.revision_objects = self.revision_objects
        active: dict[str, str] = {}
        for (space_id, context_id, role_id), revision_id in self.roles.items():
            if role_id == 1 and context_id in (
                "",
                "00000000-0000-0000-0000-000000000000:0",
            ):
                if revision_id not in self.revision_objects:
                    raise ParseError(f"Active revision {revision_id} is missing")
                active[space_id] = revision_id
                self.store.objects.update(self.revision_objects[revision_id])
        self.store.header["current_revisions"] = active


def parse_revision_store(data: bytes, *, experimental: bool = False) -> ParsedStore:
    """Decode a modern revision store from immutable bytes.

    :param experimental: Enable evidence-labelled ink decoding.
    :param data: Complete contents of a ``.one`` or ``.onetoc2`` file.
    :raises ParseError: The file contains truncated or inconsistent structures.
    :raises UnsupportedFormatError: Its format or encryption is unsupported.
    """
    store = _RevisionParser(data).parse()
    return decode_experimental_store(store) if experimental else store
