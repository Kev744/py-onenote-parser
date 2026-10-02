"""Export decoded documents with collision checks and original attachment bytes."""

import hashlib
import json
import pickle
import re
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree

from markdownify import markdownify

from pyonenote.errors import ExportError
from pyonenote.models import Document, ExportResult, Section
from pyonenote.office import write_docx, write_odt
from pyonenote.pdf import write_pdf
from pyonenote.rendering import section_html, section_text
from pyonenote.serialization import document_payload

FORMATS = ("json", "xml", "pickle", "html", "markdown", "txt", "pdf", "odt", "docx")
SUFFIXES = {"markdown": "md"}
_FORMAT_ALIASES = {"md": "markdown", "text": "txt", "pkl": "pickle"}


def safe_filename(value: str, default: str = "untitled") -> str:
    """Create a portable basename, including Windows reserved-name handling."""
    name = value.replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")[:120]
    if not name:
        name = default
    if name.split(".", 1)[0].upper() in {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }:
        name = "_" + name
    return name


def _xml_value(parent: ElementTree.Element, value: Any) -> None:
    """Map JSON-compatible values to explicit XML fields and ordered items."""
    if isinstance(value, dict):
        parent.set("type", "object")
        for key, item in value.items():
            child = ElementTree.SubElement(parent, "field", name=str(key))
            _xml_value(child, item)
    elif isinstance(value, list):
        parent.set("type", "array")
        for item in value:
            _xml_value(ElementTree.SubElement(parent, "item"), item)
    else:
        parent.set("type", "null" if value is None else type(value).__name__)
        parent.text = "" if value is None else str(value)


def _content_attachment_ids(document: Document) -> set[str]:
    """Select payloads used by current content, excluding file icons and history."""
    identifiers: set[str] = set()
    for section in document.sections:
        for page in section.pages:
            pending = list(page.blocks)
            if page.title_block is not None:
                pending.append(page.title_block)
            while pending:
                block = pending.pop()
                if block.attachment_id:
                    identifiers.add(block.attachment_id)
                pending.extend(block.children)
    return identifiers


def _asset_paths(document: Document, output_dir: Path) -> dict[str, Path]:
    """Assign stable, collision-resistant asset names independent of paths in notes."""
    result = {}
    selected = _content_attachment_ids(document)
    for attachment_id, attachment in document.store.attachments.items():
        if attachment_id not in selected:
            continue
        digest = hashlib.sha256(attachment_id.encode()).hexdigest()[:16]
        name = safe_filename(attachment.file_name, "attachment.bin")
        result[attachment_id] = output_dir / "assets" / f"{digest}-{name}"
    return result


def _write_content(
    format_name: str,
    section: Section,
    document: Document,
    assets: dict[str, Path],
    destination: Path,
) -> None:
    """Dispatch a section to its selected content renderer."""
    relative_assets = {key: "assets/" + path.name for key, path in assets.items()}
    if format_name == "docx":
        write_docx(section, assets, destination)
    elif format_name == "odt":
        write_odt(section, assets, destination)
    elif format_name == "txt":
        destination.write_text(section_text(section), encoding="utf-8")
    else:
        html = section_html(section, document, relative_assets)
        if format_name == "pdf":
            write_pdf(html, document, assets, destination)
        elif format_name == "markdown":
            # Only the body is content; style/title elements are not note text.
            body = html.split("<body>", 1)[1].rsplit("</body>", 1)[0]
            destination.write_text(
                markdownify(body, heading_style="ATX"), encoding="utf-8"
            )
        else:
            destination.write_text(html, encoding="utf-8")


def _export_jobs(
    document: Document, root: Path, formats: list[str]
) -> list[tuple[str, Section | None, Path]]:
    """Plan all destination files before writing any output."""
    jobs: list[tuple[str, Section | None, Path]] = []
    for format_name in formats:
        if format_name in ("json", "xml", "pickle"):
            jobs.append((format_name, None, root / f"document.{format_name}"))
        else:
            for index, section in enumerate(document.sections, 1):
                basename = f"{index:02d}-{safe_filename(section.title)}"
                jobs.append(
                    (
                        format_name,
                        section,
                        root / f"{basename}.{SUFFIXES.get(format_name, format_name)}",
                    )
                )
    return jobs


def _write_structure(
    document: Document,
    format_name: str,
    destination: Path,
    assets: dict[str, Path],
    *,
    debug: bool = False,
) -> None:
    """Serialize readable/debug JSON/XML or a complete trusted Python snapshot."""
    if format_name == "pickle":
        destination.write_bytes(pickle.dumps(document, protocol=5))
        return
    payload = document_payload(
        document,
        debug=debug,
        assets={key: "assets/" + path.name for key, path in assets.items()},
    )
    if format_name == "json":
        destination.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    elif format_name == "xml":
        root_element = ElementTree.Element(
            "onenote", schema_version=str(payload["schema_version"])
        )
        _xml_value(root_element, payload)
        ElementTree.indent(root_element)
        ElementTree.ElementTree(root_element).write(
            destination, encoding="utf-8", xml_declaration=True
        )


def export_document(
    document: Document,
    output_dir: str | Path,
    formats: Iterable[str],
    *,
    overwrite: bool = False,
    debug: bool = False,
) -> ExportResult:
    """Write requested formats and assets, refusing implicit overwrites.

    :param document: Fully decoded structural and semantic document.
    :param output_dir: Destination directory for documents and assets.
    :param formats: Names from :data:`FORMATS`.
    :param overwrite: Permit replacement of existing output files.
    :param debug: Export the complete schema-v2 store instead of reading snapshots.
    """
    requested = list(
        dict.fromkeys(
            _FORMAT_ALIASES.get(item.lower(), item.lower()) for item in formats
        )
    )
    if not requested or any(item not in FORMATS for item in requested):
        raise ExportError(f"Formats must be selected from: {', '.join(FORMATS)}")
    root = Path(output_dir).resolve()
    assets = _asset_paths(document, root)
    jobs = _export_jobs(document, root, requested)
    for target in [*assets.values(), *(job[2] for job in jobs)]:
        if not target.resolve().is_relative_to(root):
            raise ExportError(f"Output path escapes destination: {target.name}")
        if target.exists() and (not overwrite or not target.is_file()):
            raise ExportError(f"Output already exists: {target}")
    root.mkdir(parents=True, exist_ok=True)
    for attachment_id, target in assets.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(document.store.attachments[attachment_id].data)
    result = ExportResult(
        assets={key: str(path) for key, path in assets.items()},
        diagnostics=list(document.store.diagnostics),
    )
    for format_name, job_section, destination in jobs:
        if job_section is None:
            _write_structure(document, format_name, destination, assets, debug=debug)
        else:
            _write_content(format_name, job_section, document, assets, destination)
        result.files.append(str(destination))
    return result
