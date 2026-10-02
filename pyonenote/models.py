"""Shared loss-preserving models for both OneNote storage representations."""

from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_serializer,
    field_validator,
    model_validator,
)

from pyonenote.experimental_property_sets import (
    ExperimentalProperty,
    experimental_property_set,
)
from pyonenote.jcid import jcid_name, jcid_names


class OneNoteModel(BaseModel):
    """Serialize arbitrary binary values as reversible base64 strings."""

    model_config = ConfigDict(ser_json_bytes="base64", val_json_bytes="base64")


class Property(OneNoteModel):
    """Retain a property's wire identity, interpreted value and raw bytes."""

    property_id: int
    name: str = ""
    type_id: int = 0
    value: Any = None
    raw: bytes = b""
    name_aliases: list[str] = Field(default_factory=list)
    known: bool = False
    decoded_value: Any = None
    interpretation: Literal["decoded", "raw", "unknown", "invalid", "experimental"] = (
        "raw"
    )
    experimental: ExperimentalProperty | None = None
    experimental_error: str = ""

    @field_serializer("raw", when_used="json")
    def serialize_raw(self, value: bytes) -> str:
        """Serialize property bytes as explicit uppercase hexadecimal.

        :return: A prefixed hex string in original byte order.
        """
        return "0x" + value.hex().upper()

    @field_validator("raw", mode="before")
    @classmethod
    def deserialize_raw(cls, value: Any) -> Any:
        """Accept explicit hex while retaining the legacy Base64 fallback.

        :return: Original bytes, or input for the existing byte validator.
        """
        if isinstance(value, str) and value.startswith("0x"):
            return bytes.fromhex(value[2:])
        return value


class OneObject(OneNoteModel):
    """Represent one JCID-bearing object within an object space/revision."""

    object_id: str
    jcid: int = 0
    properties: list[Property] = Field(default_factory=list)
    revision_id: str = ""
    object_space_id: str = ""
    references: list[str] = Field(default_factory=list)
    raw_data: bytes = b""
    experimental: bool = False

    @computed_field  # type: ignore[prop-decorator]
    @property
    def jcid_name(self) -> str:
        """Return documented names, opted-in candidate names, or an unknown label."""
        definition = experimental_property_set(self.jcid) if self.experimental else None
        return definition.name if definition else jcid_name(self.jcid)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def jcid_aliases(self) -> list[str]:
        """Return all documented names for this object's current JCID."""
        return list(jcid_names(self.jcid))

    @computed_field  # type: ignore[prop-decorator]
    @property
    def jcid_known(self) -> bool:
        """Identify catalog recognition without implying rendering support."""
        return bool(jcid_names(self.jcid))


class Revision(OneNoteModel):
    """Record revision ancestry, roots and declared object identities."""

    revision_id: str
    object_space_id: str = ""
    dependent_revision_id: str = ""
    root_objects: dict[str, str] = Field(default_factory=dict)
    objects: list[str] = Field(default_factory=list)
    committed: bool = True
    role: int = 1
    context_id: str = ""


class Attachment(OneNoteModel):
    """Store original attachment bytes and their associated object identity."""

    attachment_id: str
    file_name: str = ""
    data: bytes = b""
    content_type: str = "application/octet-stream"
    object_id: str = ""


class Diagnostic(OneNoteModel):
    """Describe unsupported or incomplete content without discarding it."""

    code: str
    message: str
    offset: int | None = None


class ExperimentalStroke(OneNoteModel):
    """Retain candidate absolute channels in descriptor order and original units."""

    dimension_ids: list[str]
    sample_count: int = Field(ge=1)
    channels: list[list[int]]


class ParsedStore(OneNoteModel):
    """Contain decoded storage objects before OneNote semantic assembly."""

    format: Literal["revision", "package"]
    objects: dict[str, OneObject] = Field(default_factory=dict)
    revisions: list[Revision] = Field(default_factory=list)
    attachments: dict[str, Attachment] = Field(default_factory=dict)
    diagnostics: list[Diagnostic] = Field(default_factory=list)
    header: dict[str, Any] = Field(default_factory=dict)
    object_groups: dict[str, list[str]] = Field(default_factory=dict)
    revision_objects: dict[str, dict[str, OneObject]] = Field(default_factory=dict)
    experimental_strokes: dict[str, ExperimentalStroke] = Field(default_factory=dict)


class TextRun(OneNoteModel):
    """Store inline text and explicit overrides of its paragraph's style."""

    text: str = ""
    style: dict[str, Any] = Field(default_factory=dict)


class ContentBlock(OneNoteModel):
    """Separate inline runs from structural children, with live aggregate text."""

    kind: str = "paragraph"
    runs: list[TextRun] = Field(default_factory=list)
    children: list["ContentBlock"] = Field(default_factory=list)
    source_object_id: str = ""
    properties: dict[str, Any] = Field(default_factory=dict)
    attachment_id: str = ""
    style: dict[str, Any] = Field(default_factory=dict)

    def __init__(self, *, text: str | None = None, **data: Any) -> None:
        """Accept convenient plain text as well as explicit typed runs.

        :param text: Legacy or plain inline text; explicit runs take precedence.
        :param data: Content fields validated by Pydantic.
        """
        if text is not None:
            data["text"] = text
        super().__init__(**data)

    @model_validator(mode="before")
    @classmethod
    def migrate_text(cls, value: Any) -> Any:
        """Accept legacy text/run children without reimporting aggregate text.

        :return: A copied input using explicit runs and structural children.
        """
        if not isinstance(value, dict):
            return value
        result = dict(value)
        children = []
        legacy_runs = []
        for child in result.get("children", []):
            data = (
                {"kind": child.kind, "text": child.inline_text, "style": child.style}
                if isinstance(child, ContentBlock)
                else child
            )
            if isinstance(data, dict) and data.get("kind") == "run":
                legacy_runs.append(
                    {"text": data.get("text", ""), "style": data.get("style", {})}
                )
            else:
                children.append(child)
        if "runs" not in result:
            result["runs"] = legacy_runs or (
                [{"text": result["text"]}] if result.get("text") else []
            )
        elif legacy_runs:
            raise ValueError("Do not mix explicit runs with legacy run children")
        result["children"] = children
        result.pop("text", None)
        return result

    @property
    def inline_text(self) -> str:
        """Return this block's own text, excluding structural descendants."""
        return "".join(run.text for run in self.runs)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def text(self) -> str:
        """Return full subtree text, using tabs for cells and newlines for blocks."""
        parts = [self.inline_text] if self.runs else []
        parts.extend(child.text for child in self.children)
        return ("\t" if self.kind == "row" else "\n").join(parts)


class Page(OneNoteModel):
    """Contain a titled, ordered sequence of page content blocks."""

    page_id: str
    title: str = ""
    title_block: ContentBlock | None = None
    blocks: list[ContentBlock] = Field(default_factory=list)
    properties: dict[str, Any] = Field(default_factory=dict)

    @property
    def content_blocks(self) -> list[ContentBlock]:
        """Return title date/time metadata followed by body blocks for rendering."""

        def metadata(block: ContentBlock) -> list[ContentBlock]:
            """Select title metadata without repeating the title text."""
            if block.properties.get("IsTitleDate") or block.properties.get(
                "IsTitleTime"
            ):
                return [block]
            return [item for child in block.children for item in metadata(child)]

        return (metadata(self.title_block) if self.title_block else []) + self.blocks

    @computed_field  # type: ignore[prop-decorator]
    @property
    def text(self) -> str:
        """Return the title, date/time and complete page body in reading order."""
        return "\n".join([self.title, *(block.text for block in self.content_blocks)])


class Section(OneNoteModel):
    """Group ordered pages belonging to a OneNote section."""

    section_id: str
    title: str = ""
    pages: list[Page] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def text(self) -> str:
        """Return a readable section title and all pages without manual traversal."""
        return "\n\n".join([self.title, *(page.text for page in self.pages)])


class Document(OneNoteModel):
    """Combine a decoded store with its assembled sections and pages."""

    schema_version: Literal[2] = 2
    store: ParsedStore
    sections: list[Section] = Field(default_factory=list)


class ExportResult(OneNoteModel):
    """List generated document files, assets and source diagnostics."""

    files: list[str] = Field(default_factory=list)
    assets: dict[str, str] = Field(default_factory=dict)
    diagnostics: list[Diagnostic] = Field(default_factory=list)
