"""Render HTML with WeasyPrint and finalize PDF documents with pypdf."""

import io
import mimetypes
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from urllib.request import url2pathname

from pypdf import PdfReader, PdfWriter

from pyonenote.errors import ExportError
from pyonenote.models import Document


def _configure_windows_dlls() -> None:
    """Find existing native libraries without changing explicit user settings."""
    if os.name == "nt" and "WEASYPRINT_DLL_DIRECTORIES" not in os.environ:
        library_dirs = [
            Path(value)
            for value in os.environ.get("PATH", "").split(os.pathsep)
            if value
        ]
        library_dir = next(
            (
                path
                for path in library_dirs
                if (path / "libgobject-2.0-0.dll").is_file()
            ),
            None,
        )
        if library_dir:
            os.environ["WEASYPRINT_DLL_DIRECTORIES"] = str(library_dir)


def write_pdf(
    html: str, document: Document, assets: dict[str, Path], destination: Path
) -> None:
    """Render reflowed content and embed original attachment payloads.

    :raises ExportError: If the platform rendering dependencies are absent.
    """
    _configure_windows_dlls()
    try:
        from weasyprint import HTML  # pylint: disable=import-outside-toplevel

        # pylint: disable=import-outside-toplevel
        from weasyprint.urls import (
            URLFetcher,
            URLFetcherResponse,
        )
    except (ImportError, OSError) as error:
        raise ExportError(
            "PDF rendering requires WeasyPrint's Pango libraries. On Windows set "
            "WEASYPRINT_DLL_DIRECTORIES to the directory containing the Pango DLLs; "
            "see https://doc.courtbouillon.org/weasyprint/stable/first_steps.html"
        ) from error
    approved = {path.resolve() for path in assets.values()}

    def fetch_asset(url: str, headers: Any = None) -> Any:
        """Allow rendering to read only explicitly exported attachment files."""
        del headers
        parsed = urlsplit(url)
        path = Path(url2pathname(parsed.path)).resolve()
        if parsed.scheme != "file" or path not in approved:
            raise ValueError("Only exported assets may be loaded by the PDF renderer")
        return URLFetcherResponse(
            url,
            body=path.read_bytes(),
            headers={
                "Content-Type": mimetypes.guess_type(path.name)[0]
                or "application/octet-stream"
            },
        )

    fetcher = URLFetcher(allowed_protocols=("file",), fail_on_errors=True)
    fetcher.fetch = fetch_asset

    writer = PdfWriter()
    writer.append(
        PdfReader(
            io.BytesIO(
                HTML(
                    string=html,
                    base_url=destination.parent.as_uri() + "/",
                    url_fetcher=fetcher,
                ).write_pdf(presentational_hints=True)
            )
        )
    )
    writer.add_metadata(
        {"/Producer": "pyonenote-extractor", "/Title": destination.stem}
    )
    for attachment_id, path in assets.items():
        writer.add_attachment(path.name, document.store.attachments[attachment_id].data)
    with destination.open("wb") as stream:
        writer.write(stream)
