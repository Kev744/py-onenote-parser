"""Check rich text inheritance, safe links and list numbering in HTML."""

from pyonenote.models import ContentBlock, Document, ParsedStore
from pyonenote.rendering import block_html


def test_runs_inherit_default_style_and_can_override_it() -> None:
    """Retain paragraph defaults while respecting explicit run overrides."""
    block = ContentBlock(
        text="ab",
        style={"bold": True, "font_size": 14},
        children=[
            ContentBlock(kind="run", text="a"),
            ContentBlock(kind="run", text="b", style={"bold": False}),
        ],
    )
    html = block_html(block, {}, Document(store=ParsedStore(format="revision")))
    assert "<strong>a</strong>" in html
    assert "<strong>b</strong>" not in html
    assert html.count("font-size:14pt") == 2


def test_ordered_list_restart_is_explicit() -> None:
    """Keep a list beginning at a number other than one."""
    block = ContentBlock(
        kind="list",
        style={"ordered": True, "start": 5},
        children=[ContentBlock(kind="list_item", text="Fifth")],
    )
    html = block_html(block, {}, Document(store=ParsedStore(format="revision")))
    assert '<ol start="5">' in html


def test_script_links_and_markup_are_not_executable() -> None:
    """Escape note text and reject active hyperlink schemes."""
    block = ContentBlock(
        text="<script>bad()</script>", style={"href": "javascript:bad()"}
    )
    html = block_html(block, {}, Document(store=ParsedStore(format="revision")))
    assert "<script>" not in html
    assert "javascript:" not in html
    assert "&lt;script&gt;" in html
