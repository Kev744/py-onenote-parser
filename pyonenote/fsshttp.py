"""Extract saved MS-FSSHTTP SOAP and MS-FSSHTTPB binary response bodies."""

import base64
import binascii
import gzip
from email import policy
from email.parser import BytesParser
from io import BytesIO
from urllib.parse import unquote
from xml.etree import ElementTree

from .binary import BinaryReader
from .errors import ParseError, UnsupportedFormatError
from .models import ParsedStore
from .package import (
    StreamObject,
    parse_data_element_packages,
    read_compact_uint,
    read_stream_object,
)

MAX_RESPONSE_BYTES = 512 * 1024 * 1024


def _binary_packages(data: bytes) -> list[StreamObject]:
    """Validate response status and return serialized data packages."""
    reader = BinaryReader(data)
    version, minimum, signature = reader.unpack("<HHQ")
    if version not in (12, 13, 14) or minimum != 11 or signature != 0x9B069439F329CF9D:
        raise ParseError("Unsupported FSSHTTPB response header")
    response = read_stream_object(reader)
    if response.type_id != 0x62 or reader.remaining:
        raise ParseError("Invalid FSSHTTPB response framing", reader.tell())
    if len(response.payload) != 1 or response.payload[0] & 1:
        raise ParseError("FSSHTTPB response failed", response.offset)
    packages = []
    pending = list(response.children)
    while pending:
        child = pending.pop(0)
        if child.type_id == 0x15:
            packages.append(child)
            continue
        if child.type_id == 0x41:
            prefix = BinaryReader(child.payload, offset=child.offset)
            read_compact_uint(prefix)
            read_compact_uint(prefix)
            if prefix.uint(1) & 1:
                raise ParseError("FSSHTTPB sub-response failed", child.offset)
        pending[0:0] = child.children
    return packages


def _unchunk(data: bytes, max_bytes: int) -> bytes:
    """Decode bounded HTTP chunks and reject incomplete captures."""
    stream = BytesIO(data)
    output = bytearray()
    while True:
        line = stream.readline()
        try:
            size = int(line.split(b";", maxsplit=1)[0].strip(), 16)
        except ValueError as error:
            raise ParseError("Invalid HTTP chunk size") from error
        if size < 0 or size > max_bytes - len(output):
            raise ParseError("HTTP chunk size exceeds limit")
        if size == 0:
            if not stream.read().endswith(b"\r\n"):
                raise ParseError("Truncated HTTP chunk ending")
            return bytes(output)
        chunk = stream.read(size)
        if len(chunk) != size or stream.read(2) != b"\r\n":
            raise ParseError("Truncated HTTP chunk")
        output.extend(chunk)


def _http_body(data: bytes, max_bytes: int) -> tuple[bytes, dict[str, bytes]]:
    """Decode saved HTTP and MIME using the standard-library MIME parser."""
    headers, separator, body = data.partition(b"\r\n\r\n")
    if not separator:
        raise ParseError("Incomplete saved HTTP response")
    if data.startswith(b"HTTP/"):
        status, _, headers = headers.partition(b"\r\n")
        fields = status.split()
        if len(fields) < 2 or not fields[1].startswith(b"2"):
            raise ParseError("Saved HTTP response failed")
    message = BytesParser(policy=policy.default).parsebytes(
        headers + b"\r\n\r\n", headersonly=True
    )
    transfer = str(message.get("Transfer-Encoding", "")).lower()
    if transfer == "chunked":
        body = _unchunk(body, max_bytes)
    elif transfer:
        raise UnsupportedFormatError(f"Unsupported HTTP transfer encoding: {transfer}")
    encoding = str(message.get("Content-Encoding", "")).lower()
    if encoding == "gzip":
        try:
            with gzip.GzipFile(fileobj=BytesIO(body)) as compressed:
                body = compressed.read(max_bytes + 1)
        except (OSError, EOFError) as error:
            raise ParseError("Invalid gzip response body") from error
    elif encoding and encoding != "identity":
        raise UnsupportedFormatError(f"Unsupported HTTP content encoding: {encoding}")
    if len(body) > max_bytes:
        raise ParseError("Decoded HTTP body exceeds size limit")
    if not message.get_content_type().startswith("multipart/"):
        return body, {}
    return _mime_body(headers, body, max_bytes)


def _mime_body(
    headers: bytes, body: bytes, max_bytes: int
) -> tuple[bytes, dict[str, bytes]]:
    """Resolve the MIME root part and index binary attachments by content ID."""
    multipart = BytesParser(policy=policy.default).parsebytes(
        headers + b"\r\n\r\n" + body
    )
    parts: dict[str, bytes] = {}
    root_data = b""
    root_id = str(multipart.get_param("start", "")).strip("<>")
    decoded_size = 0
    for part in multipart.walk():
        if part.is_multipart():
            continue
        payload = part.get_payload(decode=True)
        if not isinstance(payload, bytes):
            raise ParseError("Invalid MIME body")
        decoded_size += len(payload)
        if decoded_size > max_bytes:
            raise ParseError("Decoded MIME parts exceed size limit")
        content_id = str(part.get("Content-ID", "")).strip("<>")
        if content_id in parts:
            raise ParseError("Duplicate MIME content ID")
        parts[content_id] = payload
        if content_id == root_id or not root_id and not root_data:
            root_data = payload
    if not root_data:
        raise ParseError("MIME response has no root SOAP body")
    return root_data, parts


def _soap_binary(element: ElementTree.Element, attachments: dict[str, bytes]) -> bytes:
    """Validate a SOAP element and resolve any binary content it contains."""
    name = element.tag.rsplit("}", maxsplit=1)[-1]
    if name == "Include":
        href = element.get("href", "")
        content_id = unquote(href[4:]) if href.startswith("cid:") else ""
        if content_id not in attachments:
            raise ParseError("Unresolved XOP content ID")
        return attachments[content_id]
    if name in ("Fault", "SubResponseStreamInvalid"):
        raise ParseError("Saved SOAP response failed or contains invalid binary data")
    if name == "SubResponse" and element.get("ErrorCode", "Success") != "Success":
        raise ParseError(f"Saved SOAP sub-response failed: {element.get('ErrorCode')}")
    if name == "SubResponseData" and element.text and element.text.strip():
        try:
            return base64.b64decode("".join(element.text.split()), validate=True)
        except (binascii.Error, ValueError) as error:
            raise ParseError("Invalid base64 response data") from error
    return b""


def _soap_packages(
    data: bytes, attachments: dict[str, bytes], max_bytes: int
) -> list[StreamObject]:
    """Extract inline base64 or cid-based XOP binary response messages."""
    declaration_probe = data.replace(b"\0", b"").upper()
    if b"<!DOCTYPE" in declaration_probe or b"<!ENTITY" in declaration_probe:
        raise ParseError("DTD/entity declarations are not permitted in saved SOAP")
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError as error:
        raise ParseError("Invalid saved SOAP XML") from error
    packages = []
    decoded_size = 0
    for element in root.iter():
        binary = _soap_binary(element, attachments)
        if binary:
            decoded_size += len(binary)
            if decoded_size > max_bytes:
                raise ParseError("Decoded SOAP responses exceed size limit")
            packages.extend(_binary_packages(binary))
    if not packages:
        raise ParseError("Saved SOAP contains no data element package")
    return packages


def parse_fsshttp_response(
    data: bytes, *, max_bytes: int = MAX_RESPONSE_BYTES, experimental: bool = False
) -> ParsedStore:
    """Parse saved binary, SOAP/base64, or HTTP MIME/XOP responses.

    :raises ParseError: If a response failed, is incomplete, or exceeds limits.
    """
    if max_bytes < 1 or len(data) > max_bytes:
        raise ParseError("Saved response exceeds size limit")
    attachments: dict[str, bytes] = {}
    if data.startswith(b"HTTP/") or data.lower().startswith(
        (b"content-type:", b"mime-version:")
    ):
        data, attachments = _http_body(data, max_bytes)
    xml_probe = data[:256].replace(b"\0", b"").lstrip(b"\xef\xbb\xbf\xff\xfe \t\r\n")
    if xml_probe.startswith(b"<"):
        return parse_data_element_packages(
            _soap_packages(data, attachments, max_bytes), experimental=experimental
        )
    return parse_data_element_packages(
        _binary_packages(data), experimental=experimental
    )
