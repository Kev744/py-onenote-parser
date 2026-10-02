"""End-to-end input acquisition tests with local HTTP responses."""

import io
import gzip
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator

import pytest

from pyonenote import OneNoteExtractor
from pyonenote.errors import ParseError, UnsupportedFormatError

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(name="http_url")
def http_url_fixture() -> Iterator[str]:
    """Serve only project fixtures through a temporary local HTTP server."""
    handler = partial(SimpleHTTPRequestHandler, directory=str(ROOT))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/Section%20sans%20titre.one"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_file_stream_and_url_agree(http_url: str) -> None:
    """Require HTTP content sniffing to select the same package decoder."""
    extractor = OneNoteExtractor()
    path = ROOT / "Section sans titre.one"
    local = extractor.parse(path)
    streamed = extractor.parse(io.BytesIO(path.read_bytes()))
    downloaded = extractor.parse_url(http_url)
    assert local.store.objects == streamed.store.objects == downloaded.store.objects
    assert local.sections and downloaded.sections


def test_input_limits_and_unknown_formats(http_url: str) -> None:
    """Bound input before parsing and reject unsupported header GUIDs."""
    extractor = OneNoteExtractor(max_bytes=100)
    with pytest.raises(ParseError, match="limit"):
        extractor.parse(io.BytesIO(b"x" * 101))
    with pytest.raises(ParseError, match="limit"):
        extractor.parse_url(http_url)
    with pytest.raises(UnsupportedFormatError):
        OneNoteExtractor().parse(io.BytesIO(b"\x00" * 64))
    with pytest.raises(ValueError, match="HTTP"):
        extractor.parse_url("file:///private/file.one")


def test_short_input_and_cli_errors() -> None:
    """Return explicit failures for malformed input, without a traceback."""
    from pyonenote.cli import main  # pylint: disable=import-outside-toplevel

    with pytest.raises(ParseError):
        OneNoteExtractor().parse(io.BytesIO(b"short"))
    assert main(["missing-file.one", "--output", "unused"]) == 1


def test_gzip_file_download_and_decompressed_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Decode a compressed download while applying the limit to expanded bytes."""
    from email.message import Message  # pylint: disable=import-outside-toplevel

    class Response(io.BytesIO):
        """Represent a compressed HTTP response independently of transport."""

        url = "https://example.test/note.one"
        headers = Message()

    payload = gzip.compress((ROOT / "Section sans titre.one").read_bytes())
    Response.headers["Content-Encoding"] = "gzip"
    monkeypatch.setattr(
        "pyonenote.extractor.urlopen", lambda *_args, **_kwargs: Response(payload)
    )
    assert OneNoteExtractor().parse_url(Response.url).store.format == "package"
    with pytest.raises(ParseError, match="limit"):
        OneNoteExtractor(max_bytes=len(payload) + 1).parse_url(Response.url)
