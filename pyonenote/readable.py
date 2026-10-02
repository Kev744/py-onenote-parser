"""Typed schema-v3 content snapshots, independent of parsed-store models."""

from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    field_validator,
    model_serializer,
)

SemanticRole = Literal["title", "title_date", "title_time"]


class ReadableModel(BaseModel):
    """Omit empty optional fields while retaining required text and style resets."""

    model_config = ConfigDict(extra="forbid")

    @model_serializer(mode="wrap")
    def compact_fields(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        """Serialize fields without trimming text or filtering inline style values."""
        values: dict[str, Any] = handler(self)
        required = {
            name
            for name, field in type(self).model_fields.items()
            if field.is_required()
        }
        return {
            key: value
            for key, value in values.items()
            if key in required
            or (key == "inline_text" and value is not None)
            or (value is not None and value != "" and value != [] and value != {})
        }


class ReadableRun(ReadableModel):
    """Keep mixed-format inline text with explicit paragraph-style overrides."""

    text: str
    style: dict[str, Any] = Field(default_factory=dict)


class ReadableBlock(ReadableModel):
    """Store full subtree text and compact inline formatting in reading order."""

    kind: str
    text: str
    inline_text: str | None = None
    roles: list[SemanticRole] = Field(default_factory=list)
    style: dict[str, Any] = Field(default_factory=dict)
    runs: list[ReadableRun] = Field(default_factory=list)
    children: list["ReadableBlock"] = Field(default_factory=list)
    attachment_id: str = ""


class ReadablePage(ReadableModel):
    """Preserve the formatted title subtree, ordered body and full page text."""

    page_id: str
    text: str
    title: str = ""
    title_block: ReadableBlock | None = None
    blocks: list[ReadableBlock] = Field(default_factory=list)


class ReadableSection(ReadableModel):
    """Group ordered pages and their full section text."""

    section_id: str
    text: str
    title: str = ""
    pages: list[ReadablePage] = Field(default_factory=list)


class ReadableAttachment(ReadableModel):
    """Describe original bytes stored once beside the reading document."""

    attachment_id: str
    file_name: str
    content_type: str
    path: str

    @field_validator("path")
    @classmethod
    def relative_asset_path(cls, value: str) -> str:
        """Require a portable relative asset path without traversal components."""
        path = PurePosixPath(value)
        if (
            len(path.parts) != 2
            or path.parts[0] != "assets"
            or ".." in path.parts
            or "\\" in value
            or ":" in value
        ):
            raise ValueError("Attachment path must be a relative assets/ filename")
        return value


class ReadableDiagnostic(ReadableModel):
    """Explain applicable parsing or interpretation limits to snapshot readers."""

    code: str
    message: str
    offset: int | None = None


class ReadableDocument(ReadableModel):
    """Validate a content snapshot; this is not a parsed-store round trip."""

    schema_version: Literal[3] = 3
    view: Literal["readable"] = "readable"
    sections: list[ReadableSection]
    attachments: list[ReadableAttachment]
    diagnostics: list[ReadableDiagnostic] = Field(default_factory=list)
