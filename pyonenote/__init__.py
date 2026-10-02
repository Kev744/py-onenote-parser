"""Decode OneNote stores and export their content using Microsoft specifications."""

from pyonenote.extractor import OneNoteExtractor
from pyonenote.models import ContentBlock, Document, Page, Section, TextRun
from pyonenote.readable import ReadableDocument

__version__ = "0.1.0"


__all__ = [
    "OneNoteExtractor",
    "ContentBlock",
    "Document",
    "ReadableDocument",
    "Page",
    "Section",
    "TextRun",
    "__version__",
]
