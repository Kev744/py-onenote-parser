# Experimental drawing property sets

`EXPERIMENTAL_PROPERTY_SETS` records candidate ink fields and their possible
referenced objects. It is available from `pyonenote.property_catalog` and
`pyonenote.experimental_property_sets`. Enable candidate binary decoding explicitly
with `OneNoteExtractor(experimental=True)` or the CLI's `--experimental` option.
Default parsing and official property recognition remain unchanged.

```powershell
uv run pyonenote "Equipe Euro 2016.one" --output exports/experimental-ink-example --format json --format xml --experimental --debug
```

Choose a fresh output directory. `--debug` includes the decoded store in JSON/XML;
the default readable view contains reading content and diagnostics, not ink fields.
Ink rendering is not implemented, so content exporters still report drawings as
unrendered. `--experimental` changes binary interpretation, not visual rendering.

```python
from pyonenote import OneNoteExtractor

extractor = OneNoteExtractor(experimental=True)
document = extractor.parse("Equipe Euro 2016.one")
for object_id, stroke in document.store.experimental_strokes.items():
    print(object_id, stroke.dimension_ids, stroke.sample_count)
    print(stroke.channels)  # Absolute coordinates, dimension-major, original units.
```

Each recognized candidate property retains `raw` and its original `value`.
Its `decoded_value` carries the tentative interpretation, and `interpretation`
is `"experimental"`. The `experimental` metadata records its encoding, evidence,
notes, possible child JCIDs and any enum hypotheses. Officially decoded fields
keep their existing interpretation. `Property.known` and `OneObject.jcid_known`
continue to reflect the documented catalog. Candidate object names appear only
when decoding is enabled; unresolved fields retain their unknown/raw status.

Both current objects and retained revision snapshots receive field decoding after
owner JCIDs are resolved. `store.experimental_strokes` contains only the current
resolved graph: dimension GUIDs, sample count and cumulative channels. The raw
path's decoded value contains deltas, while each dimension descriptor contains
its GUID, signed minimum/maximum and eight unclassified bytes as `metadata_hex`.
No pressure channels are dropped and no unit or ancestor transform is inferred.

Malformed candidate payloads remain available with `interpretation="invalid"`
and `experimental_error`; current-object failures also produce diagnostics.
Missing or unexpected child/style targets and mismatched dimension counts produce
diagnostics without recursive graph expansion. Normal binary framing errors still
raise the parser's bounded, offset-bearing `ParseError`.

Low-level callers can also pass `experimental=True` to `decode_property_set`
(with an explicit `jcid`), `parse_revision_store`, `parse_package_store`,
`parse_data_element_packages`, and `parse_fsshttp_response`. Nested property sets
do not inherit their enclosing object's candidate owner.

```python
from pyonenote.property_catalog import EXPERIMENTAL_PROPERTY_SETS
from pyonenote.experimental_property_sets import experimental_property

container = EXPERIMENTAL_PROPERTY_SETS[0x00060014]
edge = container.properties[0x20003415]
print(edge.name, edge.possible_child_jcids)  # InkData, (0x0002003B,)
print(experimental_property(0x00120048, 0x88003411).name)  # InkIgnorePressure
```

Property keys include the wire-type bits. Boolean keys omit the value bit:
`0x08003411` is the identity, `0x88003411` encodes true, and there are no
additional value bytes. Lookups retain meaningful JCID flags and property type
bits; they do not match identifiers using only their low bits.

Evidence labels distinguish:

- `documented`: an existing MS-ONE scalar reused by an experimental owner.
- `corroborated`: a source mapping whose ownership, bytes or relationships were
  also checked against this fixture. Notes still qualify uncertain semantics.
- `source_only`: reported by another implementation, absent in this fixture.
- `inferred`: a hypothesis supported by local numerical patterns or framing.
- `unresolved`: observed bytes with no defensible semantic interpretation.

Possible targets are research metadata, not exhaustive schema validation rules.
The decoder checks these references against the current graph but never expands
them recursively or substitutes defaults for missing properties. A shared style target is marked `relationship="style"`; it is not
another drawing to render.

## Object graph and field identifiers

The supplied `Equipe Euro 2016.one` contains 13 containers, 13 data objects,
13 stroke objects and two shared style objects in the resolved store observed
during this investigation. The main reference chain is:

```text
InkContainer          JCID 0x00060014
  0x20003415 InkData -> InkDataNode       JCID 0x0002003B
    0x24003416 InkStrokes[] -> InkStrokeNode  JCID 0x00020047
      0x1C00340B InkPath (binary coordinates)
      0x20003409 InkStrokeProperties -> StrokePropertiesNode JCID 0x00120048
        0x1C00340A InkDimensions (binary dimension descriptors)
```

The current external decoder also supports `0x24001C1F` on an ink container
pointing to nested ink containers when `InkData` is absent. This grouping was
not observed in the supplied fixture. These relationships come from the
[container parser](https://github.com/msiemens/onenote.rs/blob/fa4d7a044324af3bfe68727704a9789a08b36a3c/crates/parser/src/one/property_set/ink_container.rs),
[data parser](https://github.com/msiemens/onenote.rs/blob/fa4d7a044324af3bfe68727704a9789a08b36a3c/crates/parser/src/one/property_set/ink_data_node.rs)
and [stroke parser](https://github.com/msiemens/onenote.rs/blob/fa4d7a044324af3bfe68727704a9789a08b36a3c/crates/parser/src/one/property_set/ink_stroke_node.rs).

## Drawing position and bounds

| PropertyID | Candidate meaning | Payload |
| --- | --- | --- |
| `0x14001C14` / `0x14001C15` | Parent-relative horizontal / vertical offset | little-endian float32 |
| `0x14001C46` / `0x14001C47` | X / Y scaling (not observed here) | little-endian float32 |
| `0x1400349E` / `0x1400349F` | Stroke rectangle start X / Y | little-endian float32 |
| `0x140034A0` / `0x140034A1` | Stroke rectangle width / height | little-endian float32 |
| `0x140034A2` / `0x140034A3` | Padded rectangle start X / Y candidate | little-endian float32 |
| `0x140034A4` / `0x140034A5` | Padded width / height candidate | little-endian float32 |
| `0x1C003418` | Data-node bounding box (not observed here) | four little-endian uint32 values |

Names through `0x140034A3` are reported in the external
[property enumeration](https://github.com/msiemens/onenote.rs/blob/fa4d7a044324af3bfe68727704a9789a08b36a3c/crates/parser/src/one/property/mod.rs).
The last two padded-extent names are local hypotheses, not official names.

Local numerical checks independently decode the count-prefixed `InkPath` values
as sign/magnitude varints, split them by dimension, and accumulate each
dimension's deltas. Across all 13 observed strokes:

```text
start_x = (minimum_x - 1) / 1270
start_y = (minimum_y - 1) / 1270
width   = (maximum_x - minimum_x + 2) / 1270
height  = (maximum_y - minimum_y + 2) / 1270
padded_width  ~= width  + 2 * (start_x - padded_start_x)
padded_height ~= height + 2 * (start_y - padded_start_y)
```

1,270 HIMETRIC units equal half an inch. The fixture matches that conversion,
but this does not establish universal transforms for every OneNote ink variant.
Offsets remain parent-relative; establishing absolute page position also
requires ancestor transforms. These are canvas values, not byte offsets within
the `.one` file. Padding purpose and the exact pen-envelope computation remain
unconfirmed. The regression tests check these relationships with float tolerance.

## Color, pen dimensions and tool type

| PropertyID | Candidate meaning | Observed in this fixture |
| --- | --- | --- |
| `0x1400340F` | InkColor | `E7 12 25 00`, integer `0x002512E7` |
| `0x1400340C` / `0x1400340D` | InkHeight / InkWidth | each `01 00 C8 42`, float32 about 100 |
| `0x0C003412` | InkPenTip | absent; reported candidates 0 round, 1 rectangle |
| `0x0C003413` | InkRasterOperation | absent; compositing, not a named brush preset |
| `0x0C003414` | InkTransparency | absent |
| `0x08003411` | InkIgnorePressure | true (`0x88003411`) on one shared style |

The [style parser](https://github.com/msiemens/onenote.rs/blob/fa4d7a044324af3bfe68727704a9789a08b36a3c/crates/parser/src/one/property_set/stroke_properties_node.rs)
identifies these fields. The [renderer](https://github.com/msiemens/one2html/blob/59930ad309004030f812790c6749efe4265bb6bd/src/page/ink.rs)
uses the least significant byte as red: the observed color becomes `#E71225`.
It also treats increasing transparency as decreasing opacity. The
[unofficial wiki](https://github.com/msiemens/onenote.rs/wiki/Ink-Object-Format)
instead describes ARGB color and the opposite transparency polarity. The
registry records this disagreement; opt-in decoding exposes a labelled
`COLORREF_candidate` RGB value while retaining the full packed integer. It makes
no alpha or transparency-polarity inference.

The reported pen dimensions use HIMETRIC (100 units would mean 1 mm), but equal
height and width in this fixture cannot confirm which axis each field controls.
The inspected style-parser assignments swap the names. Neither the absent
tip field nor the observed integer flags can establish the user's selected
"pencil", "pen", "highlighter" or "brush" preset. Tip shape and tool preset are
different concepts. Missing fields remain missing, with no guessed tool name.

The style dimension blobs are 64 or 96 bytes: 32-byte records beginning with
GUIDs. Two dimensions correspond to X/Y; the third is a pressure candidate with
an observed range of 0..32767. Its GUID is
`7307502d-f9f4-4e18-b3f2-2ce1b1a3610c`; preserve it as observed rather than
substituting the different pressure GUID listed in the wiki.

## Additional uncertainties

- `0x14001D4E` and `0x140035A4`: both observed as 1; purposes unresolved.
- `0x14003419`: possible stroke index; `0x1C00341A`: possible stroke GUID;
  `0x1C00341D`: possible timestamp. Payload shape alone does not prove meaning.
- `0x1000341B`: observed 1036, compatible with French LCID; meaning tentative.
- `0x0C003610`: observed zero; purpose unresolved.
- `0x0C00341C`: reported handwriting/drawing bias; zero does not prove the
  stroke is handwriting rather than a hand-drawn heart.
- Upstream lists anti-aliasing and curve-fit IDs as `0x8000340E` and
  `0x80003410`, which have wire type zero under MS-ONESTORE framing. Proposed
  canonical Boolean IDs `0x0800340E` and `0x08003410` are recorded only as
  unobserved hypotheses, not silently accepted as verified identifiers.

The pinned sources identify the exact implementations inspected on 2026-10-01.
The fixture observations and original property bytes remain the stronger
evidence for this specific file. Full stroke rendering and additional
controlled drawing samples are needed to establish broader compatibility.
