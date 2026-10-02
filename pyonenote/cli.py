"""Command-line interface for local and direct-URL OneNote extraction."""

import argparse
import json
import sys
from pathlib import Path
from urllib.error import URLError

from pyonenote.errors import ExportError, ParseError
from pyonenote.exports import FORMATS
from pyonenote.extractor import OneNoteExtractor


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, export requested files and return a process exit code."""
    parser = argparse.ArgumentParser(
        description="Decode OneNote sections, pages and attachments."
    )
    parser.add_argument("source", help="Local file or direct HTTP(S) download URL")
    parser.add_argument("--output", "-o", type=Path, required=True)
    parser.add_argument(
        "--format",
        "-f",
        action="append",
        choices=(*FORMATS, "all", "md"),
        dest="formats",
        help="Output format (default: readable schema-v3 JSON and HTML)",
    )
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Export complete schema-v2 JSON/XML with raw properties, revisions and bytes",
    )
    parser.add_argument(
        "--experimental",
        action="store_true",
        help=(
            "Decode tentative ink fields; use --debug to export decoded properties "
            "and stroke channels"
        ),
    )
    parser.add_argument("--max-bytes", type=int, default=512 * 1024 * 1024)
    parser.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args(argv)
    formats = args.formats or ["json", "html"]
    if "all" in formats:
        formats = list(FORMATS)
    try:
        extractor = OneNoteExtractor(
            max_bytes=args.max_bytes,
            timeout=args.timeout,
            experimental=args.experimental,
        )
        document = (
            extractor.parse_url(args.source)
            if args.source.lower().startswith(("http://", "https://"))
            else extractor.parse(args.source)
        )
        result = extractor.export(
            document, args.output, formats, overwrite=args.overwrite, debug=args.debug
        )
    except (ParseError, ExportError, OSError, URLError, ValueError) as error:
        print(f"pyonenote: {error}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "files": result.files,
                "assets": result.assets,
                "diagnostics": [item.model_dump() for item in result.diagnostics],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0
