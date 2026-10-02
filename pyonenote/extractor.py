"""Public file, binary-stream and direct-URL extraction interface."""

import gzip
import io
from pathlib import Path
from typing import BinaryIO, Iterable, Protocol
from urllib.parse import unquote, urlsplit
from urllib.request import Request, urlopen
from uuid import UUID

from pyonenote.content import assemble_document
from pyonenote.errors import ParseError, UnsupportedFormatError
from pyonenote.exports import export_document
from pyonenote.fsshttp import parse_fsshttp_response
from pyonenote.models import Document, ExportResult
from pyonenote.package import parse_package_store
from pyonenote.revision import parse_revision_store

REVISION_FORMAT = UUID("109add3f-911b-49f5-a5d0-1791edc8aed8")
PACKAGE_FORMAT = UUID("638de92f-a6d4-4bc1-9a36-b3fc2511a5b7")


class ReadableBinary(Protocol):
    """Describe the read operation shared by files and decompression streams."""

    def read(self, size: int = -1) -> bytes:
        """Return up to ``size`` binary bytes, or empty bytes at EOF."""


class OneNoteExtractor:
    """Decode OneNote inputs and export their content without changing sources.

    :param max_bytes: Maximum input size, defaulting to 512 MiB.
    :param timeout: Timeout in seconds for direct HTTP downloads.
    :param experimental: Enable tentative ink decoding with preserved raw properties.
    """

    def __init__(
        self,
        *,
        max_bytes: int = 512 * 1024 * 1024,
        timeout: float = 30,
        experimental: bool = False,
    ) -> None:
        """Configure bounded acquisition for local and remote inputs."""
        if max_bytes < 1 or timeout <= 0:
            raise ValueError("max_bytes and timeout must be positive")
        self.max_bytes = max_bytes
        self.timeout = timeout
        self.experimental = experimental

    def _read(self, stream: ReadableBinary) -> bytes:
        """Read a bounded stream, including streams that return short chunks."""
        chunks: list[bytes] = []
        count = 0
        while True:
            chunk = stream.read(min(1024 * 1024, self.max_bytes + 1 - count))
            if not isinstance(chunk, bytes):
                raise TypeError("Input must be a binary stream")
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
            count += len(chunk)
            if count > self.max_bytes:
                raise ParseError(
                    f"Input exceeds configured {self.max_bytes}-byte limit"
                )

    def _decode(self, data: bytes, source_name: str) -> Document:
        """Dispatch from actual bytes, never from a URL or filename suffix."""
        prefix = data[:256].replace(b"\0", b"").lstrip(b"\xef\xbb\xbf\xff\xfe \t\r\n")
        if prefix.lower().startswith(
            (b"<", b"http/", b"content-type:", b"mime-version:")
        ) or data[:2] in (
            b"\x0c\x00",
            b"\x0d\x00",
            b"\x0e\x00",
        ):
            store = parse_fsshttp_response(
                data, max_bytes=self.max_bytes, experimental=self.experimental
            )
        elif len(data) < 64:
            raise ParseError("Input is shorter than a OneNote header")
        else:
            format_id = UUID(bytes_le=data[48:64])
            if format_id == REVISION_FORMAT:
                store = parse_revision_store(data, experimental=self.experimental)
            elif format_id == PACKAGE_FORMAT:
                store = parse_package_store(data, experimental=self.experimental)
            else:
                raise UnsupportedFormatError(
                    f"Unsupported OneNote format GUID {format_id}", 48
                )
        return assemble_document(store, source_name=source_name)

    def parse(self, source: str | Path | BinaryIO) -> Document:
        """Parse a local path or binary stream without closing caller streams."""
        if isinstance(source, (str, Path)):
            path = Path(source)
            with path.open("rb") as stream:
                data = self._read(stream)
            return self._decode(data, path.stem)
        return self._decode(self._read(source), "OneNote")

    def parse_url(self, url: str) -> Document:
        """Download a direct HTTP(S) resource and detect its storage format.

        Interactive Microsoft account authentication is outside this interface.
        """
        parsed = urlsplit(url)
        if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
            raise ValueError("Only direct HTTP or HTTPS URLs are supported")
        request = Request(
            url,
            headers={
                "User-Agent": "pyonenote-extractor/0.1",
                "Accept-Encoding": "identity",
            },
        )
        with urlopen(
            request, timeout=self.timeout
        ) as response:  # nosec: explicit user URL
            if urlsplit(response.url).scheme.lower() not in ("http", "https"):
                raise ValueError("HTTP redirect selected an unsupported URL scheme")
            size = response.headers.get("Content-Length")
            if size and size.isdecimal() and int(size) > self.max_bytes:
                raise ParseError(
                    f"Download exceeds configured {self.max_bytes}-byte limit"
                )
            data = self._read(response)
            encoding = (
                response.headers.get("Content-Encoding", "identity").strip().lower()
            )
            if encoding == "gzip":
                with gzip.GzipFile(fileobj=io.BytesIO(data)) as decompressed:
                    data = self._read(decompressed)
            elif encoding != "identity":
                raise UnsupportedFormatError(
                    f"Unsupported HTTP content encoding: {encoding}"
                )
            content_type = response.headers.get("Content-Type", "")
            if content_type.lower().startswith("multipart/"):
                data = f"Content-Type: {content_type}\r\n\r\n".encode("ascii") + data
        return self._decode(data, Path(unquote(parsed.path)).stem or "OneNote")

    @staticmethod
    def export(
        document: Document,
        output_dir: str | Path,
        formats: Iterable[str],
        *,
        overwrite: bool = False,
        debug: bool = False,
    ) -> ExportResult:
        """Export selected formats and byte-identical attachment files.

        :param debug: Export complete schema-v2 JSON/XML instead of reading snapshots.
        """
        return export_document(
            document, output_dir, formats, overwrite=overwrite, debug=debug
        )
