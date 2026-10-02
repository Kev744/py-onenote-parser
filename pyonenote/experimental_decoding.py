"""Decode opt-in ink hypotheses while preserving original wire values."""

import math
import struct
from datetime import datetime, timedelta, timezone
from itertools import accumulate
from typing import Any
from uuid import UUID

from pyonenote.experimental_property_sets import (
    experimental_property,
    experimental_property_set,
)
from pyonenote.jcid import normalize_jcid
from pyonenote.models import (
    Diagnostic,
    ExperimentalStroke,
    OneObject,
    ParsedStore,
    Property,
)


def _varint(data: bytes, position: int) -> tuple[int, int]:
    """Read one bounded unsigned 64-bit LEB128 integer."""
    value = 0
    for index in range(10):
        if position >= len(data):
            raise ValueError("Truncated ink varint")
        byte = data[position]
        position += 1
        if index == 9 and byte > 1:
            raise ValueError("Ink varint exceeds 64 bits")
        value |= (byte & 0x7F) << (index * 7)
        if not byte & 0x80:
            return value, position
    raise ValueError("Unterminated ink varint")


def _path(data: bytes) -> list[int]:
    """Read counted, dimension-major sign/magnitude deltas."""
    header, position = _varint(data, 0)
    count = header >> 1
    if header & 1 or count > len(data) - position:
        raise ValueError("Invalid ink path count")
    deltas = []
    for _ in range(count):
        value, position = _varint(data, position)
        deltas.append(-(value >> 1) if value & 1 else value >> 1)
    if position != len(data):
        raise ValueError("Trailing bytes after ink path")
    return deltas


def _dimensions(data: bytes) -> list[dict[str, Any]]:
    """Decode GUIDs and signed ranges, retaining unresolved descriptor metadata."""
    if not data or len(data) % 32:
        raise ValueError("Ink dimensions require complete 32-byte descriptors")
    return [
        {
            "guid": str(UUID(bytes_le=data[start : start + 16])),
            "minimum": struct.unpack_from("<i", data, start + 16)[0],
            "maximum": struct.unpack_from("<i", data, start + 20)[0],
            "metadata_hex": "0x" + data[start + 24 : start + 32].hex().upper(),
        }
        for start in range(0, len(data), 32)
    ]


# Each branch names a distinct registered wire encoding.
# pylint: disable-next=too-many-return-statements
def _decode(prop: Property, encoding: str) -> Any:
    """Interpret a registered encoding without changing its source property."""
    if encoding == "float32_le":
        value = struct.unpack("<f", prop.raw)[0]
        if not math.isfinite(value):
            raise ValueError("Non-finite ink scalar")
        return value
    if encoding == "signed_varint_stream":
        return _path(prop.raw)
    if encoding == "dimension_descriptor[32]":
        return _dimensions(prop.raw)
    if encoding == "uint32_le[4]":
        return dict(
            zip(("xmin", "ymin", "xmax", "ymax"), struct.unpack("<4I", prop.raw))
        )
    if encoding == "guid_bytes_le_candidate":
        return str(UUID(bytes_le=prop.raw))
    if encoding == "filetime_le_candidate":
        ticks = struct.unpack("<Q", prop.raw)[0]
        instant = datetime(1601, 1, 1, tzinfo=timezone.utc) + timedelta(
            microseconds=ticks // 10
        )
        return {
            "ticks": ticks,
            "utc": instant.isoformat(),
            "encoding": "FILETIME_candidate",
        }
    if encoding == "colorref_uint32_le_candidate":
        packed = struct.unpack("<I", prop.raw)[0]
        return {
            "packed": packed,
            "rgb": "#" + prop.raw[:3].hex().upper(),
            "encoding": "COLORREF_candidate",
        }
    if encoding in {
        "uint8",
        "uint16_le",
        "uint32_le",
        "boolean_in_property_id",
        "object_id",
        "object_id_array",
    }:
        return prop.value
    raise ValueError(f"No experimental decoder for {encoding}")


def decode_experimental_property(prop: Property, jcid: int) -> None:
    """Attach evidence and candidate values only for the explicitly known owner."""
    definition = experimental_property(jcid, prop.property_id)
    if definition is None:
        return
    prop.experimental = definition.model_copy(deep=True)
    if definition.evidence == "unresolved" or prop.interpretation == "decoded":
        return
    prop.name = definition.name
    prop.experimental_error = ""
    try:
        if prop.type_id != definition.wire_type:
            raise ValueError("Property wire type differs from experimental definition")
        prop.decoded_value = _decode(prop, definition.encoding)
        prop.interpretation = "experimental"
    except (ValueError, OverflowError, struct.error) as error:
        prop.decoded_value = None
        prop.interpretation = "invalid"
        prop.experimental_error = str(error)


def _diagnostic(store: ParsedStore, code: str, message: str) -> None:
    """Record an experimental issue once, including on repeated decoding."""
    diagnostic = Diagnostic(code=code, message=message)
    if diagnostic not in store.diagnostics:
        store.diagnostics.append(diagnostic)


def _check_links(store: ParsedStore, obj: OneObject) -> None:
    """Check candidate child/style types without traversing or expanding cycles."""
    for prop in obj.properties:
        definition = prop.experimental
        if definition is None or not definition.possible_child_jcids:
            continue
        targets = prop.value if isinstance(prop.value, list) else [prop.value]
        for target in targets:
            child = store.objects.get(target)
            allowed = {normalize_jcid(jcid) for jcid in definition.possible_child_jcids}
            if child is None or normalize_jcid(child.jcid) not in allowed:
                _diagnostic(
                    store,
                    "EXPERIMENTAL_REFERENCE",
                    f"{obj.object_id}: {prop.name} target {target} "
                    "is missing or has an unexpected JCID",
                )


def _stroke(store: ParsedStore, obj: OneObject) -> None:
    """Reconstruct current stroke channels using the referenced dimension order."""
    fields = {prop.property_id & 0x7FFFFFFF: prop for prop in obj.properties}
    path = fields.get(0x1C00340B)
    reference = fields.get(0x20003409)
    style = store.objects.get(reference.value) if reference else None
    dimensions = (
        next((p for p in style.properties if p.property_id == 0x1C00340A), None)
        if style
        else None
    )
    valid_style = style is not None and normalize_jcid(style.jcid) == normalize_jcid(
        0x120048
    )
    if (
        path is None
        or path.interpretation != "experimental"
        or not valid_style
        or dimensions is None
        or dimensions.interpretation != "experimental"
    ):
        _diagnostic(
            store,
            "EXPERIMENTAL_STROKE",
            f"{obj.object_id}: stroke needs a decoded path and valid referenced dimensions",
        )
        return
    deltas = path.decoded_value
    descriptors = dimensions.decoded_value
    if not deltas or len(deltas) % len(descriptors):
        _diagnostic(
            store,
            "EXPERIMENTAL_STROKE",
            f"{obj.object_id}: path count does not match dimension count",
        )
        return
    count = len(deltas) // len(descriptors)
    store.experimental_strokes[obj.object_id] = ExperimentalStroke(
        dimension_ids=[dimension["guid"] for dimension in descriptors],
        sample_count=count,
        channels=[
            list(accumulate(deltas[start : start + count]))
            for start in range(0, len(deltas), count)
        ],
    )


def decode_experimental_store(store: ParsedStore) -> ParsedStore:
    """Decode retained snapshots and derive strokes from the current resolved graph."""
    seen: set[int] = set()
    for objects in (store.objects, *store.revision_objects.values()):
        for obj in objects.values():
            if id(obj) in seen or experimental_property_set(obj.jcid) is None:
                continue
            seen.add(id(obj))
            obj.experimental = True
            for prop in obj.properties:
                decode_experimental_property(prop, obj.jcid)
    store.experimental_strokes.clear()
    for obj in store.objects.values():
        if not obj.experimental:
            continue
        for prop in obj.properties:
            if prop.experimental_error:
                _diagnostic(
                    store,
                    "EXPERIMENTAL_DECODE_ERROR",
                    f"{obj.object_id}: {prop.name}: {prop.experimental_error}",
                )
        _check_links(store, obj)
        if normalize_jcid(obj.jcid) == normalize_jcid(0x20047):
            _stroke(store, obj)
    return store
