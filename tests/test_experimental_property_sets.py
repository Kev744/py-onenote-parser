"""Check experimental ink identities against real bytes without promoting support."""

import struct
from itertools import accumulate
from pathlib import Path

import pytest

from pyonenote import OneNoteExtractor
from pyonenote.binary import BinaryReader
from pyonenote.experimental_property_sets import (
    ExperimentalPropertySet,
    experimental_property,
    experimental_property_set,
)
from pyonenote.jcid import jcid_names
from pyonenote.models import ParsedStore
from pyonenote.properties import decode_property_set
from pyonenote.property_catalog import EXPERIMENTAL_PROPERTY_SETS, property_names


@pytest.fixture(name="ink_store", scope="module")
def ink_store_fixture() -> ParsedStore:
    """Parse the supplied revision fixture without enabling an ink decoder."""
    source = Path(__file__).resolve().parents[1] / "Equipe Euro 2016.one"
    return OneNoteExtractor().parse(source).store


def test_registry_encodes_ink_relationships() -> None:
    """Distinguish structural children from shared stroke-style references."""
    expected = [
        (0x60014, 0x20003415, 0x2003B, "child"),
        (0x60014, 0x24001C1F, 0x60014, "child"),
        (0x2003B, 0x24003416, 0x20047, "child"),
        (0x20047, 0x20003409, 0x120048, "style"),
    ]
    for jcid, identifier, target, relationship in expected:
        prop = experimental_property(jcid, identifier)
        assert prop is not None
        assert prop.possible_child_jcids == (target,)
        assert prop.relationship == relationship
    assert set(EXPERIMENTAL_PROPERTY_SETS) == {0x60014, 0x2003B, 0x20047, 0x120048}


def test_real_ink_edges_match_registry(ink_store: ParsedStore) -> None:
    """Check every observed ink reference against its claimed possible target type."""
    edges = 0
    for obj in ink_store.objects.values():
        record = experimental_property_set(obj.jcid)
        if record is None:
            continue
        for prop in obj.properties:
            candidate = experimental_property(obj.jcid, prop.property_id)
            assert candidate is not None, (hex(obj.jcid), hex(prop.property_id))
            if candidate.possible_child_jcids:
                refs = prop.value if isinstance(prop.value, list) else [prop.value]
                for identifier in refs:
                    assert (
                        ink_store.objects[identifier].jcid
                        in candidate.possible_child_jcids
                    )
                    edges += 1
    assert edges > 0


def test_observed_position_and_pen_values(ink_store: ParsedStore) -> None:
    """Verify floating layout, padded extents and unmodified packed color bytes."""
    for obj in ink_store.objects.values():
        values = {prop.property_id & 0x7FFFFFFF: prop for prop in obj.properties}
        if obj.jcid == 0x60014:
            layout = {
                identifier: struct.unpack("<f", values[identifier].raw)[0]
                for identifier in range(0x1400349E, 0x140034A6)
            }
            for start, offset, extent, padded in [
                (0x1400349E, 0x140034A2, 0x140034A0, 0x140034A4),
                (0x1400349F, 0x140034A3, 0x140034A1, 0x140034A5),
            ]:
                assert layout[padded] == pytest.approx(
                    layout[extent] + 2 * (layout[start] - layout[offset]), abs=3e-6
                )
        if obj.jcid == 0x120048:
            assert values[0x1400340F].raw == bytes.fromhex("E7122500")
            assert values[0x1400340F].value == 0x002512E7
            for identifier in (0x1400340C, 0x1400340D):
                assert struct.unpack("<f", values[identifier].raw)[0] == pytest.approx(
                    100
                )
            assert 0x0C003412 not in values  # No observed pen-tip / brush enum.
            assert len(values[0x1C00340A].raw) in (64, 96)


@pytest.mark.parametrize("graph_bits", [0, 0x40000, 0x80000000])
def test_lookup_preserves_meaningful_jcid_flags(graph_bits: int) -> None:
    """Accept ignored graph/reserved bits without conflating different object kinds."""
    record = experimental_property_set(0x20014 | graph_bits)
    assert record is not None and record.name == "InkContainer"
    assert experimental_property_set(0x80014 | graph_bits) is None


def test_boolean_lookup_preserves_type_and_value() -> None:
    """Mask the Boolean payload bit but never accept malformed wire types."""
    false_prop = experimental_property(0x120048, 0x08003411)
    assert false_prop is not None and false_prop.name == "InkIgnorePressure"
    assert experimental_property(0x120048, 0x88003411) == false_prop
    assert experimental_property(0x120048, 0x80003411) is None
    assert experimental_property(0x20047, 0x08003411) is None


def test_experimental_hypotheses_do_not_change_official_decoding() -> None:
    """Retain raw data and unknown status even after importing experimental metadata."""
    raw = bytes.fromhex("E7122500")
    wire = struct.pack("<HI", 1, 0x1400340F) + raw
    prop = decode_property_set(BinaryReader(wire), jcid=0x120048)[0]
    assert prop.name == "Unknown_0x1400340F"
    assert prop.raw == raw and prop.value == 0x002512E7
    assert not prop.known and prop.interpretation == "unknown"
    assert not property_names(0x1400340F)
    assert not jcid_names(0x60014)


def test_registry_keeps_uncertainty_and_wire_types_explicit() -> None:
    """Keep inferred geometry and absent pen fields distinguishable from observations."""
    for record in EXPERIMENTAL_PROPERTY_SETS.values():
        assert record.sources
        assert (
            ExperimentalPropertySet.model_validate_json(record.model_dump_json())
            == record
        )
        for identifier, prop in record.properties.items():
            assert identifier >> 31 == 0
            assert (identifier >> 26) & 31 == prop.wire_type
            assert prop.notes
    assert (
        EXPERIMENTAL_PROPERTY_SETS[0x60014].properties[0x140034A4].evidence
        == "inferred"
    )
    assert (
        EXPERIMENTAL_PROPERTY_SETS[0x120048].properties[0x0C003412].evidence
        == "source_only"
    )
    assert (
        EXPERIMENTAL_PROPERTY_SETS[0x20047].properties[0x0C003610].evidence
        == "unresolved"
    )


def _signed_ink_values(data: bytes) -> list[int]:
    """Independently inspect bounded sign/magnitude varints in the fixture."""
    reader = BinaryReader(data)
    values = []
    while reader.remaining:
        value = 0
        for shift in range(0, 70, 7):
            byte = reader.uint(1)
            value |= (byte & 127) << shift
            if byte < 128:
                break
        else:
            pytest.fail("Ink varint exceeds 64-bit framing")
        values.append(-(value >> 1) if value & 1 else value >> 1)
    assert values[0] == len(values) - 1
    return values[1:]


def test_stroke_bounds_corroborate_layout_hypothesis(ink_store: ParsedStore) -> None:
    """Check coordinate deltas against independently stored container rectangles."""
    for container in ink_store.objects.values():
        if container.jcid != 0x60014:
            continue
        properties = {p.property_id: p for p in container.properties}
        data = ink_store.objects[properties[0x20003415].value]
        refs = next(p.value for p in data.properties if p.property_id == 0x24003416)
        assert len(refs) == 1
        stroke = {p.property_id: p for p in ink_store.objects[refs[0]].properties}
        style = ink_store.objects[stroke[0x20003409].value]
        dimensions = next(
            p.raw for p in style.properties if p.property_id == 0x1C00340A
        )
        values = _signed_ink_values(stroke[0x1C00340B].raw)
        count, remainder = divmod(len(values), len(dimensions) // 32)
        assert remainder == 0 and count > 0
        for axis in (0, 1):
            coordinates = list(accumulate(values[axis * count : (axis + 1) * count]))
            start = struct.unpack("<f", properties[0x1400349E + axis].raw)[0]
            extent = struct.unpack("<f", properties[0x140034A0 + axis].raw)[0]
            assert start == pytest.approx((min(coordinates) - 1) / 1270, abs=2e-6)
            assert extent == pytest.approx(
                (max(coordinates) - min(coordinates) + 2) / 1270, abs=2e-6
            )
