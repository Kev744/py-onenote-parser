"""Documented property names, independent of semantic decoding support.

Source: https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-one/
e9bf7da8-7aab-4668-be5e-e0c421175e3c (144 rows, 135 distinct identifiers).
Additional observed header fields: MS-ONESTORE 2.7.2, Header Cell:
https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-onestore/
07a5dc4d-0d97-4a4c-ab69-aa7957d7115c.
"""

from pyonenote.experimental_property_sets import EXPERIMENTAL_PROPERTY_SETS
from pyonenote.jcid import normalize_jcid

__all__ = [
    "EXPERIMENTAL_PROPERTY_SETS",
    "PROPERTY_ALIASES",
    "PROPERTY_NAMES",
    "property_name",
    "property_names",
]

PROPERTY_ALIASES: dict[int, tuple[str, ...]] = {
    0x08001C00: ("LayoutTightLayout",),
    0x14001C01: ("PageWidth",),
    0x14001C02: ("PageHeight",),
    0x0C001C03: ("OutlineElementChildLevel",),
    0x08001C04: ("Bold",),
    0x08001C05: ("Italic",),
    0x08001C06: ("Underline",),
    0x08001C07: ("Strikethrough",),
    0x08001C08: ("Superscript",),
    0x08001C09: ("Subscript",),
    0x1C001C0A: ("Font",),
    0x10001C0B: ("FontSize",),
    0x14001C0C: ("FontColor",),
    0x14001C0D: ("Highlight",),
    0x1C001C12: ("RgOutlineIndentDistance",),
    0x0C001C13: ("BodyTextAlignment",),
    0x14001C14: ("OffsetFromParentHoriz",),
    0x14001C15: ("OffsetFromParentVert",),
    0x1C001C1A: ("NumberListFormat",),
    0x14001C1B: ("LayoutMaxWidth",),
    0x14001C1C: ("LayoutMaxHeight",),
    0x24001C1F: (
        "ContentChildNodesOfOutlineElement",
        "ContentChildNodesOfPageManifest",
    ),
    0x24001C20: (
        "ElementChildNodesOfSection",
        "ElementChildNodesOfPage",
        "ElementChildNodesOfTitle",
        "ElementChildNodesOfOutline",
        "ElementChildNodesOfOutlineElement",
        "ElementChildNodesOfTable",
        "ElementChildNodesOfTableRow",
        "ElementChildNodesOfTableCell",
        "ElementChildNodesOfVersionHistory",
    ),
    0x08001E1E: ("EnableHistory",),
    0x1C001C22: ("RichEditTextUnicode",),
    0x24001C26: ("ListNodes",),
    0x1C001C30: ("NotebookManagementEntityGuid",),
    0x08001C34: ("OutlineElementRTL",),
    0x14001C3B: ("LanguageID",),
    0x14001C3E: ("LayoutAlignmentInParent",),
    0x20001C3F: ("PictureContainer",),
    0x14001C4C: ("PageMarginTop",),
    0x14001C4D: ("PageMarginBottom",),
    0x14001C4E: ("PageMarginLeft",),
    0x14001C4F: ("PageMarginRight",),
    0x1C001C52: ("ListFont",),
    0x18001C65: ("TopologyCreationTimeStamp",),
    0x14001C84: ("LayoutAlignmentSelf",),
    0x08001C87: ("IsTitleTime",),
    0x08001C88: ("IsBoilerText",),
    0x14001C8B: ("PageSize",),
    0x08001C8E: ("PortraitPage",),
    0x08001C91: ("EnforceOutlineStructure",),
    0x08001C92: ("EditRootRTL",),
    0x08001CB2: ("CannotBeSelected",),
    0x08001CB4: ("IsTitleText",),
    0x08001CB5: ("IsTitleDate",),
    0x14001CB7: ("ListRestart",),
    0x08001CBD: ("IsLayoutSizeSetByUser",),
    0x14001CCB: ("ListSpacingMu",),
    0x14001CDB: ("LayoutOutlineReservedWidth",),
    0x08001CDC: ("LayoutResolveChildCollisions",),
    0x08001CDE: ("IsReadOnly",),
    0x14001CEC: ("LayoutMinimumOutlineWidth",),
    0x14001CF1: ("LayoutCollisionPriority",),
    0x1C001CF3: ("CachedTitleString",),
    0x08001CF9: ("DescendantsCannotBeMoved",),
    0x10001CFE: ("RichEditTextLangID",),
    0x08001CFF: ("LayoutTightAlignment",),
    0x0C001D01: ("Charset",),
    0x14001D09: ("CreationTimeStamp",),
    0x08001D0C: ("Deletable",),
    0x10001D0E: ("ListMSAAIndex",),
    0x08001D13: ("IsBackground",),
    0x14001D24: ("IRecordMedia",),
    0x1C001D3C: ("CachedTitleStringFromPage",),
    0x14001D57: ("RowCount",),
    0x14001D58: ("ColumnCount",),
    0x08001D5E: ("TableBordersVisible",),
    0x24001D5F: ("StructureElementChildNodes",),
    0x2C001D63: ("ChildGraphSpaceElementNodes",),
    0x1C001D66: ("TableColumnWidths",),
    0x1C001D75: ("Author",),
    0x18001D77: ("LastModifiedTimeStamp",),
    0x20001D78: ("AuthorOriginal",),
    0x20001D79: ("AuthorMostRecent",),
    0x14001D7A: ("LastModifiedTime",),
    0x08001D7C: ("IsConflictPage",),
    0x1C001D7D: ("TableColumnsLocked",),
    0x14001D82: ("SchemaRevisionInOrderToRead",),
    0x08001D96: ("IsConflictObjectForRender",),
    0x20001D9B: ("EmbeddedFileContainer",),
    0x1C001D9C: ("EmbeddedFileName",),
    0x1C001D9D: ("SourceFilepath",),
    0x1C001D9E: ("ConflictingUserName",),
    0x1C001DD7: ("ImageFilename",),
    0x08001DDB: ("IsConflictObjectForSelection",),
    0x14001DFF: ("PageLevel",),
    0x1C001E12: ("TextRunIndex",),
    0x24001E13: ("TextRunFormatting",),
    0x08001E14: ("Hyperlink",),
    0x0C001E15: ("UnderlineType",),
    0x08001E16: ("Hidden",),
    0x08001E19: ("HyperlinkProtected",),
    0x08001E22: ("TextRunIsEmbeddedObject",),
    0x14001E26: ("CellShadingColor",),
    0x1C001E58: ("ImageAltText",),
    0x08003401: ("MathFormatting",),
    0x2000342C: ("ParagraphStyle",),
    0x1400342E: ("ParagraphSpaceBefore",),
    0x1400342F: ("ParagraphSpaceAfter",),
    0x14003430: ("ParagraphLineSpacingExact",),
    0x24003442: ("MetaDataObjectsAboveGraphSpace",),
    0x24003458: ("TextRunDataObject",),
    0x40003499: ("TextRunData",),
    0x1C00345A: ("ParagraphStyleId",),
    0x08003462: ("HasVersionPages",),
    0x10003463: ("ActionItemType",),
    0x10003464: ("NoteTagShape",),
    0x14003465: ("NoteTagHighlightColor",),
    0x14003466: ("NoteTagTextColor",),
    0x14003467: ("NoteTagPropertyStatus",),
    0x1C003468: ("NoteTagLabel",),
    0x1400346E: ("NoteTagCreated",),
    0x1400346F: ("NoteTagCompleted",),
    0x20003488: ("NoteTagDefinitionOid",),
    0x04003489: ("NoteTagStates",),
    0x10003470: ("ActionItemStatus",),
    0x0C003473: ("ActionItemSchemaVersion",),
    0x08003476: ("ReadingOrderRTL",),
    0x0C003477: ("ParagraphAlignment",),
    0x3400347B: ("VersionHistoryGraphSpaceContextNodes",),
    0x14003480: ("DisplayedPageNumber",),
    0x1C00349B: ("SectionDisplayName",),
    0x1C00348A: ("NextStyle",),
    0x200034C8: ("WebPictureContainer14",),
    0x140034CB: ("ImageUploadState",),
    0x1C003498: ("TextExtendedAscii",),
    0x140034CD: ("PictureWidth",),
    0x140034CE: ("PictureHeight",),
    0x14001D0F: ("PageMarginOriginX",),
    0x14001D10: ("PageMarginOriginY",),
    0x1C001E20: ("WzHyperlinkUrl",),
    0x1400346B: ("TaskTagDueDate",),
    0x1C001DE9: ("IsDeletedGraphSpaceContent",),
    # MS-ONESTORE header-cell properties are outside the MS-ONE table.
    0x14001D93: ("FileNameCRC",),
    0x1C001D94: ("FileIdentityGuid",),
    0x1C001D95: ("FileAncestorIdentityGuid",),
}
PROPERTY_NAMES = {
    identifier: names[0] for identifier, names in PROPERTY_ALIASES.items()
}
PROPERTY_NAMES.update(
    {0x24001C1F: "ContentChildNodes", 0x24001C20: "ElementChildNodes"}
)
_CONTEXTUAL_NAMES = {
    0x24001C1F: {
        0x6000D: "ContentChildNodesOfOutlineElement",
        0x60037: "ContentChildNodesOfPageManifest",
    },
    0x24001C20: {
        0x60007: "ElementChildNodesOfSection",
        0x6000B: "ElementChildNodesOfPage",
        0x6002C: "ElementChildNodesOfTitle",
        0x6000C: "ElementChildNodesOfOutline",
        0x6000D: "ElementChildNodesOfOutlineElement",
        0x60022: "ElementChildNodesOfTable",
        0x60023: "ElementChildNodesOfTableRow",
        0x60024: "ElementChildNodesOfTableCell",
        0x6003C: "ElementChildNodesOfVersionHistory",
    },
}


def property_names(property_id: int) -> tuple[str, ...]:
    """Return all official aliases, ignoring the Boolean value bit.

    :return: Published aliases, or an empty tuple for an undocumented identifier.
    """
    return PROPERTY_ALIASES.get(property_id & 0x7FFFFFFF, ())


def property_name(property_id: int, jcid: int | None = None) -> str:
    """Choose a documented context alias without guessing an absent owner.

    :return: An owner-specific alias, generic name or explicit unknown marker.
    """
    identifier = property_id & 0x7FFFFFFF
    contexts = _CONTEXTUAL_NAMES.get(identifier, {})
    if jcid is not None:
        owner = normalize_jcid(jcid)
        for candidate, name in contexts.items():
            if normalize_jcid(candidate) == owner:
                return name
    return PROPERTY_NAMES.get(identifier, f"Unknown_0x{identifier:08X}")
