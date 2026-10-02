"""Exercise opt-in ink decoding through binary, transport and export interfaces."""

import io
from base64 import b64encode
import json
import struct
from pathlib import Path
from uuid import UUID

import pytest

from pyonenote import OneNoteExtractor
from pyonenote.binary import BinaryReader
from pyonenote.cli import main
from pyonenote.models import ParsedStore
from pyonenote.experimental_decoding import decode_experimental_store
from pyonenote.fsshttp import parse_fsshttp_response
from pyonenote.package import parse_package_store
from pyonenote.properties import decode_property_set
from pyonenote.revision import parse_revision_store
from tests.test_package import _compact, _element, _guid, _node
from tests.test_properties import property_set


def test_explicit_scalar_decoding_preserves_wire_values() -> None:
    """Decode floating pen size, color and true/false flags without losing bytes."""
    wire = property_set(
        [0x1400340D, 0x1400340F, 0x88003411, 0x08003410, 0x0C003412],
        bytes.fromhex("0100C842E712250001"),
    )
    normal = decode_property_set(BinaryReader(wire), jcid=0x120048)
    decoded = decode_property_set(BinaryReader(wire), jcid=0x120048, experimental=True)
    assert [p.raw for p in normal] == [p.raw for p in decoded]
    assert [p.value for p in normal] == [p.value for p in decoded]
    assert [p.name for p in decoded] == [
        "InkWidth",
        "InkColor",
        "InkIgnorePressure",
        "InkFitToCurveCandidate",
        "InkPenTip",
    ]
    assert decoded[0].decoded_value == pytest.approx(100)
    assert decoded[1].decoded_value == {
        "packed": 0x002512E7,
        "rgb": "#E71225",
        "encoding": "COLORREF_candidate",
    }
    assert decoded[2].decoded_value is True
    assert decoded[3].decoded_value is False
    assert decoded[4].decoded_value == 1
    assert all(p.interpretation == "experimental" and not p.known for p in decoded)
    assert all(p.experimental is not None for p in decoded)
    assert all(p.experimental is None for p in normal)


def test_owner_scope_and_unresolved_fields() -> None:
    """Never infer nested owners or give unresolved flags a decoded meaning."""
    wire = property_set([0x14001D4E], struct.pack("<I", 1))
    unresolved = decode_property_set(
        BinaryReader(wire), jcid=0x60014, experimental=True
    )[0]
    assert unresolved.interpretation == "unknown" and unresolved.decoded_value is None
    assert unresolved.experimental is not None
    nested = property_set(
        [0x44000001], property_set([0x1400340D], struct.pack("<f", 100))
    )
    result = decode_property_set(BinaryReader(nested), jcid=0x120048, experimental=True)
    assert result[0].value[0].experimental is None
    wrong_owner = decode_property_set(
        BinaryReader(wire), jcid=0x80014, experimental=True
    )[0]
    assert wrong_owner.experimental is None


@pytest.mark.parametrize(
    "payload",
    [b"", b"\x80", b"\x04\x02", b"\x02\x80", b"\x02" + b"\xff" * 10, b"\x00\x00"],
)
def test_malformed_paths_remain_raw(payload: bytes) -> None:
    """Bound length prefixes and varints without failing ordinary binary preservation."""
    wire = property_set([0x1C00340B], struct.pack("<I", len(payload)) + payload)
    prop = decode_property_set(BinaryReader(wire), jcid=0x20047, experimental=True)[0]
    assert prop.raw == prop.value == payload
    assert prop.interpretation == "invalid"
    assert prop.experimental_error and prop.decoded_value is None


@pytest.mark.parametrize(
    "identifier,payload",
    [
        (0x1C00340A, bytes(31)),
        (0x1C003418, bytes(15)),
        (0x1C00341A, bytes(15)),
        (0x1C00341D, bytes(7)),
    ],
)
def test_malformed_structured_fields_are_not_guessed(
    identifier: int, payload: bytes
) -> None:
    """Reject incomplete descriptors, boxes, GUIDs and candidate timestamps."""
    owner = {0x1C00340A: 0x120048, 0x1C003418: 0x2003B}.get(identifier, 0x20047)
    wire = property_set([identifier], struct.pack("<I", len(payload)) + payload)
    prop = decode_property_set(BinaryReader(wire), jcid=owner, experimental=True)[0]
    assert prop.interpretation == "invalid" and prop.raw == payload


def _ink_package(properties_first: bool) -> bytes:
    """Build a complete synthetic ink chain with two points and a shared style."""
    dimensions = b"".join(
        UUID(guid).bytes_le + struct.pack("<ii", -100, 100) + bytes(8)
        for guid in [
            "598a6a8f-52c0-4ba0-93af-af357411a561",
            "b53f9f75-04e0-4498-a7ee-c30dbb5a9011",
        ]
    )
    objects = [
        (1, 0x6000B, property_set([0x24001C20], struct.pack("<I", 1)), [2]),
        (2, 0x60014, property_set([0x20003415]), [3]),
        (3, 0x2003B, property_set([0x24003416], struct.pack("<I", 1)), [4]),
        (
            4,
            0x20047,
            property_set(
                [0x1C00340B, 0x20003409],
                struct.pack("<I", 5) + bytes([8, 20, 5, 40, 6]),
            ),
            [5],
        ),
        (
            5,
            0x120048,
            property_set(
                [0x1C00340A, 0x1400340D],
                struct.pack("<I", len(dimensions))
                + dimensions
                + struct.pack("<f", 100),
            ),
            [],
        ),
    ]
    declarations = b""
    values = b""
    for number, jcid, props, refs in objects:
        for partition, content in (
            (4, struct.pack("<I", jcid)),
            (
                1,
                struct.pack("<I", 0x80000000 | len(refs))
                + bytes(4 * len(refs))
                + props,
            ),
        )[:: -1 if properties_first else 1]:
            references = refs if partition == 1 else []
            declarations += _node(
                0x18,
                _guid(number)
                + _compact(partition)
                + _compact(len(content))
                + _compact(len(references))
                + b"\0",
            )
            values += _node(
                0x16,
                _compact(len(references))
                + b"".join(_guid(r) for r in references)
                + b"\0"
                + _compact(len(content))
                + content,
            )
    group = _element(
        10, 5, _node(0x1D, children=declarations) + _node(0x1E, children=values)
    )
    revision = _element(
        11,
        4,
        _node(0x1A, _guid(12) + b"\0")
        + _node(0x0A, _guid(13) + _guid(1))
        + _node(0x19, _guid(10)),
    )
    prefix = (
        UUID("7b5c52e4-d88c-4da7-aeb1-5378d02996d3").bytes_le
        + UUID(int=3).bytes_le * 2
        + UUID("638de92f-a6d4-4bc1-9a36-b3fc2511a5b7").bytes_le
        + bytes(4)
    )
    return prefix + _node(
        0x7A,
        _guid(30) + UUID("1f937cb4-b26f-445f-b9f8-17e20160e461").bytes_le,
        _node(0x15, b"\0", _element(30, 1, b"") + group + revision),
    )


@pytest.mark.parametrize("properties_first", [False, True])
def test_package_decodes_after_owner_partition(properties_first: bool) -> None:
    """Resolve ink fields correctly even when properties precede their JCID."""
    payload = _ink_package(properties_first)
    normal = parse_package_store(payload)
    assert not normal.experimental_strokes
    store = parse_package_store(payload, experimental=True)
    stroke = next(iter(store.experimental_strokes.values()))
    assert stroke.channels == [[10, 8], [20, 23]]
    assert stroke.sample_count == 2
    assert {obj.jcid_name for obj in store.objects.values()} >= {
        "InkContainer",
        "InkDataNode",
        "InkStrokeNode",
        "StrokePropertiesNode",
    }
    snapshots = [store.objects, *store.revision_objects.values()]
    for objects in snapshots:
        style = next(obj for obj in objects.values() if obj.jcid == 0x120048)
        assert (
            next(p for p in style.properties if p.name == "InkWidth").decoded_value
            == 100
        )
    assert not store.diagnostics
    restored = ParsedStore.model_validate_json(store.model_dump_json())
    assert restored.experimental_strokes == store.experimental_strokes
    assert (
        OneNoteExtractor(experimental=True)
        .parse(io.BytesIO(payload))
        .store.experimental_strokes
    )


def test_revision_and_cli_experimental_export(tmp_path: Path) -> None:
    """Decode the real binary and expose candidates in a complete debug export."""
    source = Path(__file__).resolve().parents[1] / "Equipe Euro 2016.one"
    store = parse_revision_store(source.read_bytes(), experimental=True)
    assert store.experimental_strokes
    assert all(
        stroke.sample_count > 0 for stroke in store.experimental_strokes.values()
    )
    assert not any(
        item.code == "EXPERIMENTAL_DECODE_ERROR" for item in store.diagnostics
    )
    assert (
        main(
            [
                str(source),
                "--output",
                str(tmp_path),
                "--format",
                "json",
                "--format",
                "xml",
                "--experimental",
                "--debug",
            ]
        )
        == 0
    )
    data = json.loads((tmp_path / "document.json").read_text(encoding="utf-8"))
    assert data["store"]["experimental_strokes"]
    assert 'name="experimental_strokes"' in (tmp_path / "document.xml").read_text(
        encoding="utf-8"
    )
    style = next(o for o in data["store"]["objects"].values() if o["jcid"] == 0x120048)
    color = next(p for p in style["properties"] if p["name"] == "InkColor")
    assert color["raw"] == "0xE7122500"
    assert color["decoded_value"]["rgb"] == "#E71225"
    assert color["interpretation"] == "experimental" and not color["known"]


@pytest.mark.parametrize("xml", [False, True])
def test_saved_response_enables_experimental_decoding(xml: bool) -> None:
    """Propagate the option through binary and SOAP transports and the extractor."""
    data = (
        b"\x0c\0\x0b\0"
        + (0x9B069439F329CF9D).to_bytes(8, "little")
        + _node(0x62, b"\0", _ink_package(True)[105:-2])
    )
    if xml:
        data = (
            b'<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
            b'<s:Body><SubResponse ErrorCode="Success"><SubResponseData>'
            + b64encode(data)
            + b"</SubResponseData></SubResponse></s:Body></s:Envelope>"
        )
    assert not parse_fsshttp_response(data).experimental_strokes
    store = parse_fsshttp_response(data, experimental=True)
    assert next(iter(store.experimental_strokes.values())).channels == [
        [10, 8],
        [20, 23],
    ]
    assert (
        OneNoteExtractor(experimental=True)
        .parse(io.BytesIO(data))
        .store.experimental_strokes
        == store.experimental_strokes
    )


@pytest.mark.parametrize(
    "problem", ["missing_style", "wrong_style", "dimensions", "count"]
)
def test_broken_stroke_graph_is_retained(problem: str) -> None:
    """Preserve bad links or payloads with diagnostics and no invented coordinates."""
    store = parse_package_store(_ink_package(False))
    style = next(obj for obj in store.objects.values() if obj.jcid == 0x120048)
    if problem == "missing_style":
        del store.objects[style.object_id]
    elif problem == "wrong_style":
        style.jcid = 0x60014
    elif problem == "dimensions":
        style.properties[0].raw = bytes(31)
    else:
        stroke = next(obj for obj in store.objects.values() if obj.jcid == 0x20047)
        stroke.properties[0].raw = bytes([6, 20, 5, 40])
    decode_experimental_store(store)
    assert not store.experimental_strokes
    assert any(d.code == "EXPERIMENTAL_STROKE" for d in store.diagnostics)
    if problem in {"missing_style", "wrong_style"}:
        assert any(d.code == "EXPERIMENTAL_REFERENCE" for d in store.diagnostics)
    if problem == "dimensions":
        assert any(d.code == "EXPERIMENTAL_DECODE_ERROR" for d in store.diagnostics)
    snapshot = store.model_dump_json()
    decode_experimental_store(store)
    assert store.model_dump_json() == snapshot


def test_cyclic_container_references_are_not_expanded() -> None:
    """Retain cyclic candidate child graphs without recursion or duplication."""
    store = parse_package_store(_ink_package(False))
    container = next(obj for obj in store.objects.values() if obj.jcid == 0x60014)
    container.properties = decode_property_set(
        BinaryReader(property_set([0x24001C1F], struct.pack("<I", 1))),
        [container.object_id],
        jcid=container.jcid,
    )
    decode_experimental_store(store)
    assert not store.diagnostics
    assert container.properties[0].decoded_value == [container.object_id]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_experimental_scalars_remain_raw(value: float) -> None:
    """Avoid exporting non-finite geometry as an interpreted number."""
    raw = struct.pack("<f", value)
    prop = decode_property_set(
        BinaryReader(property_set([0x1400340D], raw)), jcid=0x120048, experimental=True
    )[0]
    assert prop.raw == raw and prop.interpretation == "invalid"
    assert prop.decoded_value is None


def test_candidate_structures_keep_exact_values() -> None:
    """Preserve GUID endianness, timestamp ticks and bounding-box component order."""
    guid = UUID("598a6a8f-52c0-4ba0-93af-af357411a561")
    wire = property_set(
        [0x1C00341A, 0x1C00341D],
        struct.pack("<I", 16) + guid.bytes_le + struct.pack("<IQ", 8, 11),
    )
    props = decode_property_set(BinaryReader(wire), jcid=0x20047, experimental=True)
    assert props[0].decoded_value == str(guid)
    assert props[1].decoded_value["ticks"] == 11
    assert props[1].decoded_value["utc"] == "1601-01-01T00:00:00.000001+00:00"
    box = decode_property_set(
        BinaryReader(property_set([0x1C003418], struct.pack("<5I", 16, 1, 2, 3, 4))),
        jcid=0x2003B,
        experimental=True,
    )[0]
    assert box.decoded_value == {"xmin": 1, "ymin": 2, "xmax": 3, "ymax": 4}


def test_decoded_channels_match_stored_container_geometry() -> None:
    """Compare production coordinate decoding with independent binary rectangles."""
    source = Path(__file__).resolve().parents[1] / "Equipe Euro 2016.one"
    store = parse_revision_store(source.read_bytes(), experimental=True)
    for container in store.objects.values():
        if container.jcid != 0x60014:
            continue
        fields = {p.property_id: p for p in container.properties}
        data = store.objects[fields[0x20003415].value]
        stroke_ids = next(
            p.value for p in data.properties if p.property_id == 0x24003416
        )
        for stroke_id in stroke_ids:
            stroke = store.experimental_strokes[stroke_id]
            for axis in (0, 1):
                channel = stroke.channels[axis]
                start = struct.unpack("<f", fields[0x1400349E + axis].raw)[0]
                extent = struct.unpack("<f", fields[0x140034A0 + axis].raw)[0]
                assert start == pytest.approx((min(channel) - 1) / 1270, abs=2e-6)
                assert extent == pytest.approx(
                    (max(channel) - min(channel) + 2) / 1270, abs=2e-6
                )
