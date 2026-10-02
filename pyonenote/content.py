"""Assemble MS-ONE section, page and JCID content graphs for reflow exports."""

import mimetypes
import math
from pathlib import Path
import struct
from typing import Any

from pyonenote.errors import ParseError
from pyonenote.jcid import normalize_jcid
from pyonenote.models import (
    ContentBlock,
    Diagnostic,
    Document,
    OneObject,
    Page,
    ParsedStore,
    Section,
    TextRun,
)
from pyonenote.properties import PROPERTY_NAMES
from pyonenote.property_catalog import property_name

_CHILD_IDS = (0x24001C1F, 0x24001C20, 0x24001D5F)
_BLOCK_KINDS = {
    0x2000C: "container",
    0x2000D: "container",
    0x2000E: "paragraph",
    0x20011: "image",
    0x20019: "container",
    0x20022: "table",
    0x20023: "row",
    0x20024: "cell",
    0x2002C: "heading",
    0x20035: "attachment",
}
_STYLE_NAMES = {
    "Bold": "bold",
    "Italic": "italic",
    "Underline": "underline",
    "Strikethrough": "strikethrough",
    "Superscript": "superscript",
    "Subscript": "subscript",
    "Font": "font",
    "WzHyperlinkUrl": "href",
}


def _properties(obj: OneObject) -> dict[str, Any]:
    """Index interpreted properties by their documented names.

    :return: Named values suitable for structural exports.
    """
    return {
        property_name(prop.property_id, obj.jcid): prop.value for prop in obj.properties
    }


def _references(obj: OneObject, identifiers: tuple[int, ...] = _CHILD_IDS) -> list[str]:
    """Read ordered references for selected relationships.

    :return: References in relationship priority, then original array order.
    """
    result = []
    for identifier in identifiers:
        for prop in obj.properties:
            if prop.property_id & 0x7FFFFFFF != identifier:
                continue
            if isinstance(prop.value, str):
                result.append(prop.value)
            elif isinstance(prop.value, list):
                result.extend(value for value in prop.value if isinstance(value, str))
    return result


def _text(values: dict[str, Any]) -> str:
    """Select Unicode text before legacy encoded text.

    :return: Interpreted text, retaining undecodable bytes as replacements.
    """
    value = values.get("RichEditTextUnicode", values.get("TextExtendedAscii", ""))
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("cp1252", errors="replace")
    return ""


def _style(values: dict[str, Any]) -> dict[str, Any]:
    """Translate basic text formatting into export properties.

    :return: Reflow formatting, with sizes expressed in points.
    """
    result = {
        target: values[source]
        for source, target in _STYLE_NAMES.items()
        if source in values
    }
    if "FontSize" in values:
        result["font_size"] = values["FontSize"] / 2
    for source, target in (("FontColor", "color"), ("Highlight", "highlight")):
        if source in values and isinstance(values[source], int):
            color = values[source]
            if color == 0xFF000000:
                result[target] = "auto"
                continue
            result[target] = (
                f"#{color & 255:02x}{(color >> 8) & 255:02x}{(color >> 16) & 255:02x}"
            )
    return result | _paragraph_style(values)


def _paragraph_style(values: dict[str, Any]) -> dict[str, Any]:
    """Decode documented paragraph layout without confusing float bits with sizes.

    :return: Alignment, reading direction and spacing expressed in points.
    """
    result: dict[str, Any] = {}
    alignment = values.get("ParagraphAlignment")
    if alignment in (0, 1, 2):
        result["alignment"] = ("left", "center", "right")[alignment]
    if "ReadingOrderRTL" in values:
        result["direction"] = "rtl" if values["ReadingOrderRTL"] else "ltr"
    for source, target in (
        ("ParagraphSpaceBefore", "space_before"),
        ("ParagraphSpaceAfter", "space_after"),
        ("ParagraphLineSpacingExact", "line_spacing"),
    ):
        value = values.get(source)
        if isinstance(value, int) and 0 <= value <= 0xFFFFFFFF:
            value = struct.unpack("<f", struct.pack("<I", value))[0]
        if (
            isinstance(value, float)
            and math.isfinite(value)
            and 0 <= value <= 27777.777
        ):
            result[target] = value * 36
    return result


def _merge_lists(blocks: list[ContentBlock]) -> list[ContentBlock]:
    """Join adjacent compatible lists while preserving explicit restarts.

    :return: Ordered blocks with consecutive list items grouped together.
    """
    result: list[ContentBlock] = []
    for block in blocks:
        if (
            result
            and block.kind == result[-1].kind == "list"
            and "start" not in block.style
            and block.style
            == {key: value for key, value in result[-1].style.items() if key != "start"}
        ):
            result[-1].children += block.children
        else:
            result.append(block)
    return result


class _Assembler:
    """Resolve ordered content graphs with explicit cycle detection."""

    def __init__(self, store: ParsedStore) -> None:
        """Index a parsed store by object space.

        :param store: Latest resolved objects and retained structural records.
        """
        self.store = store
        self.spaces: dict[str, list[str]] = {}
        for obj in store.objects.values():
            if obj.object_space_id:
                self.spaces.setdefault(obj.object_space_id, []).append(obj.object_id)
        self.roots: dict[str, list[str]] = {}
        current = store.header.get("current_revisions", {})
        for revision in store.revisions:
            if revision.committed and (
                not current
                or current.get(revision.object_space_id) == revision.revision_id
            ):
                self.roots[revision.object_space_id] = list(
                    revision.root_objects.values()
                )

    def diagnostic(self, code: str, message: str) -> None:
        """Append an idempotent diagnostic.

        :param code: Stable machine-readable issue category.
        """
        if not any(
            item.code == code and item.message == message
            for item in self.store.diagnostics
        ):
            self.store.diagnostics.append(Diagnostic(code=code, message=message))

    def object(self, identifier: str) -> OneObject | None:
        """Resolve one object and report missing references.

        :return: The referenced object, if available.
        """
        obj = self.store.objects.get(identifier)
        if obj is None:
            self.diagnostic("MISSING_OBJECT", f"Object {identifier} is unavailable")
        return obj

    def unknown_relationships(self, obj: OneObject) -> None:
        """Report undocumented references whose rendering cannot be inferred.

        :param obj: A visible content or page graph object.
        """
        for prop in obj.properties:
            identifier = prop.property_id & 0x7FFFFFFF
            if (
                identifier not in PROPERTY_NAMES
                and 8 <= (identifier >> 26) <= 13
                and prop.interpretation != "experimental"
            ):
                self.diagnostic(
                    "UNKNOWN_CONTENT_PROPERTY",
                    f"Property 0x{identifier:08X} on {obj.object_id} is retained "
                    "but its relationships are not interpreted",
                )

    def pages(
        self, identifier: str, ancestry: frozenset[str] = frozenset()
    ) -> list[OneObject]:
        """Follow section/page-series/manifest relationships.

        :raises ParseError: If the hierarchy contains a cycle.
        """
        if identifier in ancestry:
            raise ParseError(f"Cycle in section/page hierarchy at {identifier}")
        if len(ancestry) > 128:
            raise ParseError("Section/page graph exceeds 128 levels")
        ancestry = ancestry | {identifier}
        if identifier not in self.store.objects and identifier in self.spaces:
            roots = self.roots.get(identifier) or [
                item
                for item in self.spaces[identifier]
                if normalize_jcid(self.store.objects[item].jcid) in (0x2000B, 0x20037)
            ]
            return [page for root in roots for page in self.pages(root, ancestry)]
        obj = self.object(identifier)
        if obj is None:
            return []
        if normalize_jcid(obj.jcid) == 0x2000B:
            return [obj]
        return [
            page
            for child in _references(obj, _CHILD_IDS + (0x2C001D63,))
            for page in self.pages(child, ancestry)
        ]

    def runs(self, obj: OneObject, text: str) -> list[TextRun]:
        """Split rich text at UTF-16 character positions.

        :return: Formatted runs, or no runs when formatting is absent.
        """
        values = _properties(obj)
        formats = _references(obj, (0x24001E13,))
        if not formats:
            return []
        raw_indices = values.get("TextRunIndex", b"")
        if not isinstance(raw_indices, bytes) or len(raw_indices) % 4:
            self.diagnostic(
                "INVALID_TEXT_RUNS", f"Invalid text run indexes on {obj.object_id}"
            )
            return []
        indices = [item[0] for item in struct.iter_unpack("<I", raw_indices)]
        raw_text = text.encode("utf-16-le")
        ends = indices + [len(raw_text) // 2]
        if (
            len(formats) != len(ends)
            or any(end < start for start, end in zip([0] + ends, ends))
            or any(end <= start for start, end in zip(indices, indices[1:]))
            or any(end > len(raw_text) // 2 for end in ends)
        ):
            self.diagnostic(
                "INVALID_TEXT_RUNS",
                f"Text run count or bounds invalid on {obj.object_id}",
            )
            return []
        result = []
        start = 0
        for identifier, end in zip(formats, ends):
            style_obj = self.object(identifier)
            try:
                run_text = raw_text[start * 2 : end * 2].decode("utf-16-le")
            except UnicodeDecodeError:
                self.diagnostic(
                    "INVALID_TEXT_RUNS",
                    f"Text run splits a surrogate on {obj.object_id}",
                )
                return []
            result.append(
                TextRun(
                    text=run_text,
                    style=_style(_properties(style_obj)) if style_obj else {},
                )
            )
            start = end
        return result

    def attachment(self, obj: OneObject, values: dict[str, Any]) -> str:
        """Associate a content node with its original attachment bytes.

        :return: The attachment mapping key, or an empty string.
        """
        is_file = normalize_jcid(obj.jcid) == 0x20035
        identifiers = _references(
            obj, (0x20001D9B,) if is_file else (0x20001C3F, 0x200034C8)
        )
        for identifier in identifiers:
            for key, attachment in self.store.attachments.items():
                if identifier in (key, attachment.object_id, attachment.attachment_id):
                    name = values.get(
                        "EmbeddedFileName" if is_file else "ImageFilename", ""
                    )
                    if isinstance(name, str) and name:
                        extension = Path(attachment.file_name).suffix
                        # Alternate images can use a different format from the original.
                        if not is_file and extension and extension.lower() != ".bin":
                            name = str(Path(name).with_suffix(extension))
                        attachment.file_name = name
                    if attachment.file_name:
                        attachment.content_type = (
                            mimetypes.guess_type(attachment.file_name)[0]
                            or attachment.content_type
                        )
                    return key
        if identifiers or is_file:
            self.diagnostic(
                "MISSING_ATTACHMENT", f"Attachment for {obj.object_id} is unavailable"
            )
        return ""

    def block(
        self, identifier: str, ancestry: frozenset[str] = frozenset()
    ) -> ContentBlock | None:
        """Interpret one visible content object and its ordered children.

        :raises ParseError: If visible objects form a cycle.
        """
        if identifier in ancestry:
            raise ParseError(f"Cycle in content graph at {identifier}")
        if len(ancestry) > 128:
            raise ParseError("Content graph exceeds 128 levels")
        obj = self.object(identifier)
        if obj is None:
            return None
        self.unknown_relationships(obj)
        values = _properties(obj)
        kind = _BLOCK_KINDS.get(normalize_jcid(obj.jcid), "unsupported")
        if kind == "unsupported":
            self.diagnostic(
                (
                    "UNSUPPORTED_CONTENT"
                    if obj.jcid_known or obj.experimental
                    else "UNKNOWN_JCID"
                ),
                f"{obj.jcid_name} (JCID 0x{obj.jcid:08X}) on {identifier} "
                "retained without visual interpretation",
            )
        children = []
        for child in _references(obj):
            block = self.block(child, ancestry | {identifier})
            if block is not None:
                children.append(block)
        text = _text(values)
        style = _style(values)
        style_refs = _references(obj, (0x2000342C,))
        if style_refs:
            style_obj = self.object(style_refs[0])
            if style_obj is not None:
                style = _style(_properties(style_obj)) | style
        runs = self.runs(obj, text) if kind == "paragraph" else []
        block = ContentBlock(
            kind=kind,
            runs=runs or ([TextRun(text=text)] if text else []),
            children=_merge_lists(children),
            source_object_id=identifier,
            properties={
                key: value
                for key, value in values.items()
                if key not in {"RichEditTextUnicode", "TextExtendedAscii"}
            },
            style=style,
            attachment_id=(
                self.attachment(obj, values) if kind in ("image", "attachment") else ""
            ),
        )
        if kind == "heading":
            block.style["level"] = 2
        return self.list_block(obj, block)

    def list_block(self, obj: OneObject, block: ContentBlock) -> ContentBlock:
        """Wrap outline elements carrying list metadata in semantic list nodes.

        :return: A list containing its item, or the original content block.
        """
        lists = _references(obj, (0x24001C26,))
        if not lists:
            return block
        list_obj = self.object(lists[-1])
        if list_obj is None:
            return block
        values = _properties(list_obj)
        number_format = values.get("NumberListFormat", b"")
        if isinstance(number_format, bytes):
            if len(number_format) < 2 or len(number_format) % 2:
                self.diagnostic(
                    "INVALID_LIST", f"Invalid list format on {list_obj.object_id}"
                )
                return block
            number_format = number_format[2:].decode("utf-16-le", errors="replace")
        block.kind = "list_item"
        style = {"ordered": "\ufffd" in str(number_format), "level": len(lists) - 1}
        if "ListRestart" in values:
            style["start"] = values["ListRestart"]
        return ContentBlock(kind="list", children=[block], style=style)

    def page(self, obj: OneObject) -> Page:
        """Assemble one page and derive its visible title.

        :return: A page retaining the original properties.
        """
        values = _properties(obj)
        self.unknown_relationships(obj)
        blocks = [
            block
            for child in _references(obj)
            if (block := self.block(child)) is not None
        ]
        title_block = next((block for block in blocks if block.kind == "heading"), None)
        title = values.get(
            "CachedTitleString", values.get("CachedTitleStringFromPage", "")
        )
        if not title and title_block:
            title = _block_text(title_block)
        return Page(
            page_id=obj.object_id,
            title=str(title or "Untitled page"),
            title_block=title_block,
            blocks=_merge_lists(
                [block for block in blocks if block is not title_block]
            ),
            properties=values,
        )


def _block_text(block: ContentBlock) -> str:
    """Collect title text while excluding date and time nodes.

    :return: Concatenated visible text.
    """
    if block.properties.get("IsTitleDate") or block.properties.get("IsTitleTime"):
        return ""
    return block.inline_text or " ".join(
        text for child in block.children if (text := _block_text(child))
    )


def assemble_document(store: ParsedStore, source_name: str = "") -> Document:
    """Resolve a parsed store into ordered sections and visible pages.

    :param store: Storage-independent parsed objects.
    :param source_name: Optional filename used as a section title fallback.
    :return: The source store and its interpreted document tree.
    """
    assembler = _Assembler(store)
    section_objects = [
        obj for obj in store.objects.values() if normalize_jcid(obj.jcid) == 0x20007
    ]
    sections = []
    fallback_title = Path(source_name).stem or "Untitled section"
    for section_obj in section_objects:
        seen = set()
        pages = []
        for page_obj in assembler.pages(section_obj.object_id):
            if page_obj.object_id not in seen:
                seen.add(page_obj.object_id)
                pages.append(assembler.page(page_obj))
        title = _properties(section_obj).get("SectionDisplayName", fallback_title)
        sections.append(
            Section(section_id=section_obj.object_id, title=str(title), pages=pages)
        )
    if not section_objects:
        pages = [
            assembler.page(obj)
            for obj in store.objects.values()
            if normalize_jcid(obj.jcid) == 0x2000B
        ]
        sections.append(
            Section(section_id="section", title=fallback_title, pages=pages)
        )
    return Document(store=store, sections=sections)
