"""Render normalized OneNote content as escaped, reflowable HTML and text."""

from html import escape
from typing import Any, Iterator
from urllib.parse import quote, urlsplit

from pyonenote.models import ContentBlock, Document, Page, Section, TextRun

STYLE = """
@page { size: A4; margin: 18mm; }
body { font: 11pt sans-serif; line-height: 1.45; color: #20242a; }
h1, h2, h3 { line-height: 1.2; }
article + article { break-before: page; }
img { max-width: 100%; height: auto; }
table { border-collapse: collapse; width: 100%; }
td, th { border: 1px solid #bbb; padding: 0.4em; vertical-align: top; }
p { white-space: pre-wrap; }
.unsupported { border-left: 3px solid #999; padding-left: 0.75em; }
"""


def walk_blocks(blocks: list[ContentBlock]) -> Iterator[ContentBlock]:
    """Yield a content tree in its original order."""
    for block in blocks:
        yield block
        yield from walk_blocks(block.children)


def safe_link(value: Any) -> str:
    """Allow human-readable web/mail links while rejecting active schemes."""
    text = str(value)
    return text if urlsplit(text).scheme.lower() in ("http", "https", "mailto") else ""


def styled_text(text: str, style: dict[str, Any]) -> str:
    """Escape a text run and apply supported inline formatting."""
    result = escape(text)
    tags = {
        "bold": "strong",
        "italic": "em",
        "underline": "u",
        "strikethrough": "s",
        "superscript": "sup",
        "subscript": "sub",
    }
    for key, tag in tags.items():
        if style.get(key):
            result = f"<{tag}>{result}</{tag}>"
    declarations = _inline_css(style)
    if declarations:
        result = f'<span style="{escape(";".join(declarations), quote=True)}">{result}</span>'
    link = safe_link(style.get("href", ""))
    if link:
        result = f'<a href="{escape(link, quote=True)}">{result}</a>'
    return result


def _inline_css(style: dict[str, Any]) -> list[str]:
    """Return safe typography declarations, including explicit color resets."""
    declarations = []
    for key, css_key in (("color", "color"), ("highlight", "background-color")):
        value = str(style.get(key, ""))
        if value == "auto":
            declarations.append(
                f'{css_key}:{"initial" if key == "color" else "transparent"}'
            )
        if (
            len(value) == 7
            and value.startswith("#")
            and all(char in "0123456789abcdefABCDEF" for char in value[1:])
        ):
            declarations.append(f"{css_key}:{value}")
    size = style.get("font_size")
    if isinstance(size, (float, int)) and 1 <= size <= 200:
        declarations.append(f"font-size:{size}pt")
    if style.get("font"):
        font = "".join(
            char if char.isalnum() or char in " -_" else f"\\{ord(char):x} "
            for char in str(style["font"])
        )
        declarations.append(f'font-family:"{font}"')
    return declarations


def _paragraph_attributes(style: dict[str, Any]) -> str:
    """Render paragraph-only layout with validated CSS values."""
    declarations = []
    if style.get("alignment") in ("left", "center", "right"):
        declarations.append(f'text-align:{style["alignment"]}')
    for key, css_key in (
        ("space_before", "margin-top"),
        ("space_after", "margin-bottom"),
        ("line_spacing", "line-height"),
    ):
        value = style.get(key)
        if isinstance(value, (int, float)) and 0 <= value <= 1_000_000:
            if key != "line_spacing" or value > 0:
                declarations.append(f"{css_key}:{value}pt")
    attributes = f' style="{";".join(declarations)}"' if declarations else ""
    if style.get("direction") in ("rtl", "ltr"):
        attributes += f' dir="{style["direction"]}"'
    return attributes


def block_html(block: ContentBlock, assets: dict[str, str], document: Document) -> str:
    """Render a block without executing or fetching embedded content."""
    children = "".join(block_html(child, assets, document) for child in block.children)
    text = "".join(styled_text(run.text, block.style | run.style) for run in block.runs)
    content = text + children
    if block.attachment_id:
        target = assets.get(block.attachment_id)
        if not target:
            return '<p class="unsupported">Attachment unavailable</p>'
        attachment = document.store.attachments[block.attachment_id]
        href = escape(quote(target, safe="/"), quote=True)
        label = escape(block.inline_text or attachment.file_name or "Attachment")
        mime = attachment.content_type
        if block.kind == "image":
            return f'<figure><img src="{href}" alt="{label}"></figure>'
        if mime.startswith(("audio/", "video/")):
            tag = mime.split("/", 1)[0]
            return f'<{tag} controls src="{href}"></{tag}><p><a href="{href}">{label}</a></p>'
        return f'<p><a href="{href}">{label}</a></p>'
    tags = {
        "paragraph": "p",
        "container": "div",
        "table": "table",
        "row": "tr",
        "cell": "td",
        "list_item": "li",
        "heading": "h3",
    }
    tag = tags.get(block.kind, "div")
    attributes = ""
    if block.kind == "list":
        tag = (
            "ol"
            if block.style.get("list_type") == "ordered" or block.style.get("ordered")
            else "ul"
        )
        if tag == "ol" and isinstance(block.style.get("start"), int):
            attributes = f' start="{block.style["start"]}"'
    if block.kind == "unsupported":
        description = content or "Unsupported object retained in structural export"
        return f'<div class="unsupported">{description}</div>'
    if block.kind in ("paragraph", "heading"):
        if block.kind == "heading":
            tag = f'h{max(1, min(6, int(block.style.get("level", 3))))}'
        attributes += _paragraph_attributes(block.style)
        content = text
    else:
        children = ""
    return f"<{tag}{attributes}>{content}</{tag}>" + children


def page_heading(page: Page) -> ContentBlock:
    """Build one formatted page heading without its separate date/time nodes."""

    def title_parts(block: ContentBlock) -> list[ContentBlock]:
        """Collect title paragraphs while excluding metadata subtrees."""
        if block.properties.get("IsTitleDate") or block.properties.get("IsTitleTime"):
            return []
        return ([block] if block.runs else []) + [
            part for child in block.children for part in title_parts(child)
        ]

    parts = title_parts(page.title_block) if page.title_block else []
    runs: list[TextRun] = []
    for part in parts:
        if runs:
            runs.append(TextRun(text=" "))
        runs.extend(
            TextRun(text=run.text, style=part.style | run.style) for run in part.runs
        )
    if "".join(run.text for run in runs) != page.title:
        runs = [TextRun(text=page.title)]
    return ContentBlock(
        kind="heading",
        runs=runs,
        style=(parts[0].style if parts else {}) | {"level": 2},
    )


def section_html(section: Section, document: Document, assets: dict[str, str]) -> str:
    """Render a complete standalone HTML document for one section."""
    pages = []
    for page in section.pages:
        blocks = "".join(
            block_html(block, assets, document) for block in page.content_blocks
        )
        pages.append(
            f'<article id="{escape(page.page_id, quote=True)}">'
            f"{block_html(page_heading(page), assets, document)}{blocks}</article>"
        )
    title = escape(section.title)
    return (
        f'<!doctype html><html><head><meta charset="utf-8"><title>{title}</title>'
        f'<style>{STYLE}</style></head><body><h1>{title}</h1>{"".join(pages)}</body></html>'
    )


def block_text(block: ContentBlock) -> str:
    """Flatten one block without duplicating rich text runs."""
    return block.text


def section_text(section: Section) -> str:
    """Preserve section/page boundaries in a UTF-8 text export."""
    return section.text + "\n"
