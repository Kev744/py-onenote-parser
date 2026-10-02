"""Public exceptions raised by parsing, acquisition and export operations."""


class ParseError(ValueError):
    """Describe malformed input at an absolute byte offset.

    :param message: Explanation of the invalid record.
    :param offset: Byte offset relative to the original input.
    """

    def __init__(self, message: str, offset: int = 0) -> None:
        """Store the offending location and produce a readable error."""
        self.offset = offset
        super().__init__(f"{message} (offset {offset}, 0x{offset:x})")


class UnsupportedFormatError(ParseError):
    """Identify a format whose decoding is not implemented."""


class ExportError(ValueError):
    """Describe an export failure without claiming a complete result."""
