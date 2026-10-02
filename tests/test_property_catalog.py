"""Regress the complete MS-ONE 2.1.12 property catalog and contextual names."""

import pytest

from pyonenote.property_catalog import (
    PROPERTY_ALIASES,
    PROPERTY_NAMES,
    property_name,
    property_names,
)


def test_official_table_has_every_row_without_losing_aliases() -> None:
    """Keep all MS-ONE rows plus three confirmed MS-ONESTORE header fields."""
    assert len(PROPERTY_ALIASES) == 138
    assert sum(len(names) for names in PROPERTY_ALIASES.values()) == 147
    assert PROPERTY_NAMES[0x1C001C30] == "NotebookManagementEntityGuid"
    assert PROPERTY_NAMES[0x18001C65] == "TopologyCreationTimeStamp"
    assert PROPERTY_NAMES[0x18001D77] == "LastModifiedTimeStamp"
    assert PROPERTY_NAMES[0x1400346B] == "TaskTagDueDate"
    assert PROPERTY_NAMES[0x1C001DE9] == "IsDeletedGraphSpaceContent"


@pytest.mark.parametrize(
    ("identifier", "jcid", "expected"),
    [
        (0x24001C1F, 0x6000D, "ContentChildNodesOfOutlineElement"),
        (0x24001C1F, 0x60037, "ContentChildNodesOfPageManifest"),
        (0x24001C20, 0x60007, "ElementChildNodesOfSection"),
        (0x24001C20, 0x6000B, "ElementChildNodesOfPage"),
        (0x24001C20, 0x6002C, "ElementChildNodesOfTitle"),
        (0x24001C20, 0x6000C, "ElementChildNodesOfOutline"),
        (0x24001C20, 0x6000D, "ElementChildNodesOfOutlineElement"),
        (0x24001C20, 0x60022, "ElementChildNodesOfTable"),
        (0x24001C20, 0x60023, "ElementChildNodesOfTableRow"),
        (0x24001C20, 0x60024, "ElementChildNodesOfTableCell"),
        (0x24001C20, 0x6003C, "ElementChildNodesOfVersionHistory"),
    ],
)
def test_shared_ids_use_documented_owner_context(
    identifier: int, jcid: int, expected: str
) -> None:
    """Choose each shared identifier's documented owner-specific alias."""
    assert property_name(identifier, jcid) == expected
    assert property_name(identifier, jcid | 0x80000000) == expected
    assert property_name(identifier, jcid & ~0x40000) == expected
    assert property_name(identifier, jcid ^ 0x20000) == PROPERTY_NAMES[identifier]


def test_boolean_value_bit_and_unknown_owner_are_not_false_names() -> None:
    """Mask the Boolean value bit and retain honest generic or unknown names."""
    assert property_names(0x88001C04) == ("Bold",)
    assert property_name(0x24001C1F) == "ContentChildNodes"
    assert property_name(0x24001C20, 0x600FF) == "ElementChildNodes"
    assert property_names(0x240034D8) == ()
    assert property_name(0x240034D8) == "Unknown_0x240034D8"
    assert property_names(0x24001C1F) == (
        "ContentChildNodesOfOutlineElement",
        "ContentChildNodesOfPageManifest",
    )
    assert len(property_names(0x24001C20)) == 9
