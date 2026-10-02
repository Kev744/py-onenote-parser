"""Opt-in research metadata for ink, used only when experimental decoding is enabled.

Identifiers are full PropertyIDs, not their low-26-bit identity components.
Evidence describes a hypothesis, not official Microsoft format support.
See ``docs/experimental_ink.md`` for fixture observations and unresolved issues.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from pyonenote.jcid import normalize_jcid

Evidence = Literal[
    "documented", "corroborated", "source_only", "inferred", "unresolved"
]

_SOURCE_BASE = (
    "https://github.com/msiemens/onenote.rs/blob/"
    "fa4d7a044324af3bfe68727704a9789a08b36a3c/crates/parser/src/one/"
)


class ExperimentalProperty(BaseModel):
    """Describe a candidate field and the possible targets of its references."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    wire_type: int = Field(ge=1, le=31)
    encoding: str
    evidence: Evidence
    notes: str
    possible_child_jcids: tuple[int, ...] = ()
    relationship: Literal["value", "child", "style"] = "value"
    value_hypotheses: dict[int, str] = Field(default_factory=dict)


class ExperimentalPropertySet(BaseModel):
    """Group tentative properties by owning JCID with traceable research sources."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    properties: dict[int, ExperimentalProperty]
    sources: tuple[str, ...]


EXPERIMENTAL_PROPERTY_SETS: dict[int, ExperimentalPropertySet] = {
    0x00060014: ExperimentalPropertySet(
        name="InkContainer",
        sources=(
            _SOURCE_BASE + "property_set/ink_container.rs",
            _SOURCE_BASE + "property/mod.rs",
        ),
        properties={
            0x14001D7A: ExperimentalProperty(
                name="LastModifiedTime",
                wire_type=5,
                encoding="uint32_le",
                evidence="documented",
                notes="Existing MS-ONE timestamp; observed on ink containers.",
            ),
            0x14001C14: ExperimentalProperty(
                name="OffsetFromParentHoriz",
                wire_type=5,
                encoding="float32_le",
                evidence="documented",
                notes="Parent-relative X in half-inch units; not absolute page X.",
            ),
            0x14001C15: ExperimentalProperty(
                name="OffsetFromParentVert",
                wire_type=5,
                encoding="float32_le",
                evidence="documented",
                notes="Parent-relative Y in half-inch units; not absolute page Y.",
            ),
            0x20003415: ExperimentalProperty(
                name="InkData",
                wire_type=8,
                encoding="object_id",
                evidence="corroborated",
                notes="Observed container-to-data link in the revision fixture.",
                possible_child_jcids=(0x0002003B,),
                relationship="child",
            ),
            0x24001C1F: ExperimentalProperty(
                name="ContentChildNodes",
                wire_type=9,
                encoding="object_id_array",
                evidence="source_only",
                notes="Possible nested ink containers when InkData is absent; not observed here.",
                possible_child_jcids=(0x00060014,),
                relationship="child",
            ),
            0x14001C46: ExperimentalProperty(
                name="InkScalingX",
                wire_type=5,
                encoding="float32_le",
                evidence="source_only",
                notes="Reported X scale; absent from this fixture's ink containers.",
            ),
            0x14001C47: ExperimentalProperty(
                name="InkScalingY",
                wire_type=5,
                encoding="float32_le",
                evidence="source_only",
                notes="Reported Y scale, distinct from X; absent in this fixture.",
            ),
            0x1400349E: ExperimentalProperty(
                name="EmbeddedInkStartX",
                wire_type=5,
                encoding="float32_le",
                evidence="corroborated",
                notes=(
                    "Observed as (minimum stroke X - 1) / 1270; coordinate frame "
                    "remains tentative."
                ),
            ),
            0x1400349F: ExperimentalProperty(
                name="EmbeddedInkStartY",
                wire_type=5,
                encoding="float32_le",
                evidence="corroborated",
                notes="Observed as (minimum stroke Y - 1) / 1270; not a file offset.",
            ),
            0x140034A0: ExperimentalProperty(
                name="EmbeddedInkWidth",
                wire_type=5,
                encoding="float32_le",
                evidence="corroborated",
                notes="Observed as (maximum X - minimum X + 2) / 1270.",
            ),
            0x140034A1: ExperimentalProperty(
                name="EmbeddedInkHeight",
                wire_type=5,
                encoding="float32_le",
                evidence="corroborated",
                notes="Observed as (maximum Y - minimum Y + 2) / 1270.",
            ),
            0x140034A2: ExperimentalProperty(
                name="EmbeddedInkOffsetHoriz",
                wire_type=5,
                encoding="float32_le",
                evidence="corroborated",
                notes="Candidate padded left edge; smaller than EmbeddedInkStartX in the fixture.",
            ),
            0x140034A3: ExperimentalProperty(
                name="EmbeddedInkOffsetVert",
                wire_type=5,
                encoding="float32_le",
                evidence="corroborated",
                notes="Candidate padded top edge; smaller than EmbeddedInkStartY in the fixture.",
            ),
            0x140034A4: ExperimentalProperty(
                name="InkPaddedWidthCandidate",
                wire_type=5,
                encoding="float32_le",
                evidence="inferred",
                notes="Fixture: width + 2 * (start_x - offset_x); padding purpose is unconfirmed.",
            ),
            0x140034A5: ExperimentalProperty(
                name="InkPaddedHeightCandidate",
                wire_type=5,
                encoding="float32_le",
                evidence="inferred",
                notes="Fixture: height + 2 * (start_y - offset_y); padding purpose is unconfirmed.",
            ),
            0x14001D4E: ExperimentalProperty(
                name="Unknown_0x14001D4E",
                wire_type=5,
                encoding="uint32_le",
                evidence="unresolved",
                notes="Observed value 1; no verified drawing-tool meaning.",
            ),
            0x140035A4: ExperimentalProperty(
                name="Unknown_0x140035A4",
                wire_type=5,
                encoding="uint32_le",
                evidence="unresolved",
                notes="Observed value 1; do not label as pencil or brush type.",
            ),
        },
    ),
    0x0002003B: ExperimentalPropertySet(
        name="InkDataNode",
        sources=(_SOURCE_BASE + "property_set/ink_data_node.rs",),
        properties={
            0x24003416: ExperimentalProperty(
                name="InkStrokes",
                wire_type=9,
                encoding="object_id_array",
                evidence="corroborated",
                notes="Ordered stroke references; each observed data node has one stroke.",
                possible_child_jcids=(0x00020047,),
                relationship="child",
            ),
            0x1C003418: ExperimentalProperty(
                name="InkBoundingBox",
                wire_type=7,
                encoding="uint32_le[4]",
                evidence="source_only",
                notes="Reported x_min, y_min, x_max, y_max (16 bytes); absent here.",
            ),
        },
    ),
    0x00020047: ExperimentalPropertySet(
        name="InkStrokeNode",
        sources=(_SOURCE_BASE + "property_set/ink_stroke_node.rs",),
        properties={
            0x1C00340B: ExperimentalProperty(
                name="InkPath",
                wire_type=7,
                encoding="signed_varint_stream",
                evidence="corroborated",
                notes="Count-prefixed sign/magnitude varints; fixture uses dimension-major deltas.",
            ),
            0x20003409: ExperimentalProperty(
                name="InkStrokeProperties",
                wire_type=8,
                encoding="object_id",
                evidence="corroborated",
                notes="Shared style reference, not an additional visible stroke.",
                possible_child_jcids=(0x00120048,),
                relationship="style",
            ),
            0x0C00341C: ExperimentalProperty(
                name="InkBias",
                wire_type=3,
                encoding="uint8",
                evidence="corroborated",
                notes=(
                    "Source: 0 handwriting, 1 drawing, 2 both; observed 0 does not "
                    "exclude a heart."
                ),
                value_hypotheses={0: "handwriting", 1: "drawing", 2: "both"},
            ),
            0x14001C3B: ExperimentalProperty(
                name="LanguageID",
                wire_type=5,
                encoding="uint32_le",
                evidence="source_only",
                notes=(
                    "Source decoder accepts this documented locale field; absent on "
                    "these strokes."
                ),
            ),
            0x1000341B: ExperimentalProperty(
                name="InkLanguageIdCandidate",
                wire_type=4,
                encoding="uint16_le",
                evidence="inferred",
                notes="Observed 1036 (0x040C), consistent with French LCID; role unconfirmed.",
            ),
            0x14003419: ExperimentalProperty(
                name="InkStrokeIndexCandidate",
                wire_type=5,
                encoding="uint32_le",
                evidence="inferred",
                notes="Small per-stroke integers; ordering or identity role remains tentative.",
            ),
            0x1C00341A: ExperimentalProperty(
                name="InkStrokeGuidCandidate",
                wire_type=7,
                encoding="guid_bytes_le_candidate",
                evidence="inferred",
                notes="Observed 16-byte values varying per stroke; identity meaning unconfirmed.",
            ),
            0x1C00341D: ExperimentalProperty(
                name="InkStrokeTimeCandidate",
                wire_type=7,
                encoding="filetime_le_candidate",
                evidence="inferred",
                notes="Observed 8-byte values; possible FILETIME, not promoted to a timestamp.",
            ),
            0x0C003610: ExperimentalProperty(
                name="Unknown_0x0C003610",
                wire_type=3,
                encoding="uint8",
                evidence="unresolved",
                notes="Observed 0; no verified semantic name.",
            ),
        },
    ),
    0x00120048: ExperimentalPropertySet(
        name="StrokePropertiesNode",
        sources=(
            _SOURCE_BASE + "property_set/stroke_properties_node.rs",
            _SOURCE_BASE + "property/mod.rs",
            "https://github.com/msiemens/onenote.rs/wiki/Ink-Object-Format",
            "https://github.com/msiemens/one2html/blob/"
            "59930ad309004030f812790c6749efe4265bb6bd/src/page/ink.rs",
        ),
        properties={
            0x1C00340A: ExperimentalProperty(
                name="InkDimensions",
                wire_type=7,
                encoding="dimension_descriptor[32]",
                evidence="corroborated",
                notes="Observed 64/96 bytes: X, Y, optionally pressure; GUID then metric metadata.",
            ),
            0x1400340C: ExperimentalProperty(
                name="InkHeight",
                wire_type=5,
                encoding="float32_le",
                evidence="corroborated",
                notes=(
                    "Pen-tip height candidate, about 100 here; HIMETRIC units "
                    "reported externally."
                ),
            ),
            0x1400340D: ExperimentalProperty(
                name="InkWidth",
                wire_type=5,
                encoding="float32_le",
                evidence="corroborated",
                notes=(
                    "Pen-tip width candidate, about 100 here; equal dimensions cannot "
                    "verify axis assignment."
                ),
            ),
            0x1400340F: ExperimentalProperty(
                name="InkColor",
                wire_type=5,
                encoding="colorref_uint32_le_candidate",
                evidence="corroborated",
                notes=(
                    "E7 12 25 00 = 0x002512E7; renderer uses low-byte red (#E71225); "
                    "wiki ARGB conflicts."
                ),
            ),
            0x08003411: ExperimentalProperty(
                name="InkIgnorePressure",
                wire_type=2,
                encoding="boolean_in_property_id",
                evidence="corroborated",
                notes="True stored as 0x88003411 with no payload; observed on the X/Y-only style.",
            ),
            0x0C003412: ExperimentalProperty(
                name="InkPenTip",
                wire_type=3,
                encoding="uint8",
                evidence="source_only",
                notes=(
                    "Reported 0 round, 1 rectangle; absent here; not a pencil/brush "
                    "preset identifier."
                ),
                value_hypotheses={0: "round", 1: "rectangle"},
            ),
            0x0C003413: ExperimentalProperty(
                name="InkRasterOperation",
                wire_type=3,
                encoding="uint8",
                evidence="source_only",
                notes="Reported stroke compositing mode; absent here; no brush enum inferred.",
            ),
            0x0C003414: ExperimentalProperty(
                name="InkTransparency",
                wire_type=3,
                encoding="uint8",
                evidence="source_only",
                notes=(
                    "Absent here; renderer uses 0 opaque / 255 transparent; wiki "
                    "states the inverse."
                ),
            ),
            0x0800340E: ExperimentalProperty(
                name="InkAntialiasedCandidate",
                wire_type=2,
                encoding="boolean_in_property_id",
                evidence="inferred",
                notes=(
                    "Unobserved; upstream 0x8000340E has invalid Boolean type bits; "
                    "0x0800340E is a hypothesis."
                ),
            ),
            0x08003410: ExperimentalProperty(
                name="InkFitToCurveCandidate",
                wire_type=2,
                encoding="boolean_in_property_id",
                evidence="inferred",
                notes=(
                    "Unobserved; upstream 0x80003410 has invalid Boolean type bits; "
                    "0x08003410 is a hypothesis."
                ),
            ),
        },
    ),
}

_NORMALIZED_PROPERTY_SETS = {
    normalize_jcid(jcid): record for jcid, record in EXPERIMENTAL_PROPERTY_SETS.items()
}


def experimental_property_set(jcid: int) -> ExperimentalPropertySet | None:
    """Look up an experimental owner without changing documented JCID recognition."""
    return _NORMALIZED_PROPERTY_SETS.get(normalize_jcid(jcid))


def experimental_property(jcid: int, property_id: int) -> ExperimentalProperty | None:
    """Find an owner-specific candidate while masking only the Boolean payload bit."""
    record = experimental_property_set(jcid)
    return record.properties.get(property_id & 0x7FFFFFFF) if record else None
