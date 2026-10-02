"""Project compact reading snapshots without serializing the parsed store."""

from typing import Any

from pyonenote.models import ContentBlock, Document
from pyonenote.readable import (
    ReadableAttachment,
    ReadableBlock,
    ReadableDiagnostic,
    ReadableDocument,
    ReadablePage,
    ReadableRun,
    ReadableSection,
    SemanticRole,
)


def _readable_block(block: ContentBlock, *, title: bool = False) -> ReadableBlock:
    """Retain semantic structure, collapsing only transparent container wrappers."""
    roles: list[SemanticRole] = ["title"] if title else []
    if block.properties.get("IsTitleDate"):
        roles.append("title_date")
    if block.properties.get("IsTitleTime"):
        roles.append("title_time")
    if (
        block.kind == "container"
        and len(block.children) == 1
        and not (block.runs or block.style or block.attachment_id or roles)
    ):
        return _readable_block(block.children[0])
    uniform = len(block.runs) == 1
    return ReadableBlock(
        kind=block.kind,
        text=block.text,
        inline_text=block.inline_text if uniform and block.children else None,
        roles=roles,
        style=block.style | block.runs[0].style if uniform else dict(block.style),
        runs=(
            []
            if uniform
            else [ReadableRun(text=run.text, style=run.style) for run in block.runs]
        ),
        children=[_readable_block(child) for child in block.children],
        attachment_id=block.attachment_id,
    )


def readable_document(document: Document, assets: dict[str, str]) -> ReadableDocument:
    """Project sections and attachment metadata directly into a reading snapshot.

    :param assets: Attachment IDs mapped to relative exported asset paths.
    :return: A schema-v3 snapshot with no raw properties or embedded bytes.
    """
    return ReadableDocument(
        sections=[
            ReadableSection(
                section_id=section.section_id,
                title=section.title,
                text=section.text,
                pages=[
                    ReadablePage(
                        page_id=page.page_id,
                        title=page.title,
                        text=page.text,
                        title_block=(
                            _readable_block(page.title_block, title=True)
                            if page.title_block is not None
                            else None
                        ),
                        blocks=[_readable_block(block) for block in page.blocks],
                    )
                    for page in section.pages
                ],
            )
            for section in document.sections
        ],
        attachments=[
            ReadableAttachment(
                attachment_id=identifier,
                file_name=attachment.file_name,
                content_type=attachment.content_type,
                path=assets[identifier],
            )
            for identifier, attachment in document.store.attachments.items()
            if identifier in assets
        ],
        diagnostics=[
            ReadableDiagnostic(code=item.code, message=item.message, offset=item.offset)
            for item in document.store.diagnostics
            if not item.code.upper().startswith("UNKNOWN_")
        ],
    )


def document_payload(
    document: Document, *, debug: bool = False, assets: dict[str, str] | None = None
) -> dict[str, Any]:
    """Select a reading snapshot or the complete schema-v2 diagnostic document.

    :param debug: Include the entire parsed store, unknown fields and bytes.
    :param assets: Relative attachment paths, required when attachments exist.
    :return: JSON-compatible data without mutating the source document.
    """
    if debug:
        return document.model_dump(mode="json")
    return readable_document(document, assets or {}).model_dump(mode="json")
