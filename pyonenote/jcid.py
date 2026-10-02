"""Documented MS-ONE object names, independent of visual renderer coverage.

Sources: MS-ONE sections 2.1.13 (property sets), 2.1.5 (file data), and
MS-ONESTORE section 2.6.14 (JCID bit fields). Shared IDs retain all aliases.
"""

JCID_NAMES: dict[int, tuple[str, ...]] = {
    0x00120001: ("jcidReadOnlyPersistablePropertyContainerForAuthor",),
    0x00020001: (
        "jcidPersistablePropertyContainerForTOC",
        "jcidPersistablePropertyContainerForTOCSection",
    ),
    0x00060007: ("jcidSectionNode",),
    0x00060008: ("jcidPageSeriesNode",),
    0x0006000B: ("jcidPageNode",),
    0x0006000C: ("jcidOutlineNode",),
    0x0006000D: ("jcidOutlineElementNode",),
    0x0006000E: ("jcidRichTextOENode",),
    0x00060011: ("jcidImageNode",),
    0x00060012: ("jcidNumberListNode",),
    0x00060019: ("jcidOutlineGroup",),
    0x00060022: ("jcidTableNode",),
    0x00060023: ("jcidTableRowNode",),
    0x00060024: ("jcidTableCellNode",),
    0x0006002C: ("jcidTitleNode",),
    0x00020030: ("jcidPageMetaData",),
    0x00020031: ("jcidSectionMetaData",),
    0x00060035: ("jcidEmbeddedFileNode",),
    0x00060037: ("jcidPageManifestNode",),
    0x00020038: ("jcidConflictPageMetaData",),
    0x0006003C: ("jcidVersionHistoryContent",),
    0x0006003D: ("jcidVersionProxy",),
    0x00120043: ("jcidNoteTagSharedDefinitionContainer",),
    0x00020044: ("jcidRevisionMetaData",),
    0x00020046: ("jcidVersionHistoryMetaData",),
    0x0012004D: ("jcidParagraphStyleObject", "jcidParagraphStyleObjectForText"),
    0x00080036: ("jcidEmbeddedFileContainer",),
    0x00080039: ("jcidPictureContainer14",),
}


def normalize_jcid(jcid: int) -> int:
    """Ignore only the undefined graph bit and reserved bits.

    :return: Index plus binary/property-set/file-data/read-only flags.
    """
    return jcid & 0x001BFFFF


_NORMALIZED_NAMES = {normalize_jcid(key): names for key, names in JCID_NAMES.items()}


def jcid_names(jcid: int) -> tuple[str, ...]:
    """Look up all documented names without conflating meaningful flags.

    :return: Every matching name, or an empty tuple for an unknown object.
    """
    return _NORMALIZED_NAMES.get(normalize_jcid(jcid), ())


def jcid_name(jcid: int) -> str:
    """Describe an object without guessing between shared-ID aliases.

    :return: Joined documented names or the original unknown numeric ID.
    """
    return " | ".join(jcid_names(jcid)) or f"Unknown_0x{jcid:08X}"
