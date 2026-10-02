"""Keep file payloads separate from attachment icons and alternate pictures."""

import json
import hashlib
from zipfile import ZipFile
from pathlib import Path

import pytest

from pyonenote import OneNoteExtractor
from pyonenote.content import assemble_document
from pyonenote.exports import export_document
from pyonenote.models import Attachment, OneObject, ParsedStore
from tests.test_content import prop


def _store() -> ParsedStore:
    """Build an attachment with distinct file and icon references."""
    objects = [
        OneObject(
            object_id="page", jcid=0x6000B, properties=[prop(0x24001C20, ["file"])]
        ),
        OneObject(
            object_id="file",
            jcid=0x60035,
            properties=[
                prop(0x20001C3F, "icon-object"),
                prop(0x20001D9B, "file-object"),
                prop(0x1C001D9C, "recording.webm"),
            ],
        ),
    ]
    return ParsedStore(
        format="revision",
        objects={o.object_id: o for o in objects},
        attachments={
            "icon": Attachment(
                attachment_id="icon",
                object_id="icon-object",
                file_name="icon.png",
                data=b"icon-bytes",
                content_type="image/png",
            ),
            "file": Attachment(
                attachment_id="file",
                object_id="file-object",
                file_name="uuid.webm",
                data=b"file-bytes",
            ),
        },
    )


def test_file_reference_wins_over_icon() -> None:
    """Assign the original filename and media type only to the file payload."""
    store = _store()
    document = assemble_document(store)
    assert document.sections[0].pages[0].blocks[0].attachment_id == "file"
    assert store.attachments["file"].file_name == "recording.webm"
    assert store.attachments["file"].content_type == "video/webm"
    assert store.attachments["icon"].file_name == "icon.png"
    assert store.attachments["icon"].content_type == "image/png"


def test_missing_payload_does_not_link_to_icon() -> None:
    """Report an unavailable file instead of presenting its icon as the file."""
    store = _store()
    del store.attachments["file"]
    document = assemble_document(store)
    assert not document.sections[0].pages[0].blocks[0].attachment_id
    assert any(d.code == "MISSING_ATTACHMENT" for d in store.diagnostics)
    assert store.attachments["icon"].file_name == "icon.png"


@pytest.mark.parametrize("debug", [False, True])
def test_export_excludes_icons_but_preserves_store(tmp_path: Path, debug: bool) -> None:
    """Keep one payload in assets and preserve both original blobs in debug data."""
    document = assemble_document(_store())
    before = document.model_dump_json()
    result = export_document(document, tmp_path, ["json", "xml", "html"], debug=debug)
    assert set(result.assets) == {"file"}
    assert len(list((tmp_path / "assets").iterdir())) == 1
    assert Path(result.assets["file"]).read_bytes() == b"file-bytes"
    assert document.model_dump_json() == before
    payload = json.loads((tmp_path / "document.json").read_text(encoding="utf-8"))
    if debug:
        assert set(payload["store"]["attachments"]) == {"icon", "file"}
    else:
        assert [a["attachment_id"] for a in payload["attachments"]] == ["file"]
    html = next(tmp_path.glob("*.html")).read_text(encoding="utf-8")
    assert "video controls" in html and Path(result.assets["file"]).name in html


def test_icon_shared_with_real_image_is_exported(tmp_path: Path) -> None:
    """Retain an image independently used on the page, even when also an icon."""
    store = _store()
    store.objects["page"].properties = [prop(0x24001C20, ["file", "image"])]
    store.objects["image"] = OneObject(
        object_id="image",
        jcid=0x60011,
        properties=[prop(0x20001C3F, "icon-object"), prop(0x1C001DD7, "picture.png")],
    )
    result = export_document(assemble_document(store), tmp_path, ["json"])
    assert set(result.assets) == {"file", "icon"}


def test_alternate_image_keeps_actual_storage_extension() -> None:
    """Do not give an alternate PNG the unavailable original metafile's suffix."""
    store = _store()
    store.objects["file"] = OneObject(
        object_id="file",
        jcid=0x60011,
        properties=[
            prop(0x20001C3F, "missing-emf"),
            prop(0x200034C8, "icon-object"),
            prop(0x1C001DD7, "Image sans titre.emf"),
        ],
    )
    document = assemble_document(store)
    assert document.sections[0].pages[0].blocks[0].attachment_id == "icon"
    assert store.attachments["icon"].file_name == "Image sans titre.png"
    assert store.attachments["icon"].content_type == "image/png"


def test_unreferenced_history_stays_out_of_assets(tmp_path: Path) -> None:
    """Export only current-page references while keeping historical payload bytes."""
    store = _store()
    store.attachments["old"] = Attachment(
        attachment_id="old", file_name="old.webm", data=b"old-video"
    )
    document = assemble_document(store)
    result = export_document(document, tmp_path, ["json"])
    assert set(result.assets) == {"file"}
    assert document.store.attachments["old"].data == b"old-video"


def test_real_revision_asset_count_and_bytes(tmp_path: Path) -> None:
    """Verify the supplied notebook's five current payloads and exact asset bytes."""
    document = OneNoteExtractor().parse(
        Path(__file__).resolve().parents[1] / "Equipe Euro 2016.one"
    )
    expected = {
        "bf05906c-2f52-46a2-9a1e-d510781943a8": "Portugal_Logo.png",
        "83c4a03b-d2de-4a3b-a56b-9c28ea7abca2": "Kid Massive - Touch Me (Avicii Remix).webm",
        "e1b08d31-512d-49a6-a155-df7d975e1107": "Projet PeaceWatcher.mp4",
        "3a258502-ea05-4be0-99b9-afc1eadabc0d": "Dash_fr_modern.xlsx",
        "10612f73-5922-4ffc-8f86-17889bb5e8c8": "Image sans titre.png",
    }
    result = export_document(document, tmp_path, ["json", "xml", "html"])
    assert set(result.assets) == set(expected)
    assert len(list((tmp_path / "assets").iterdir())) == len(expected) == 5
    for identifier, name in expected.items():
        attachment = document.store.attachments[identifier]
        output = Path(result.assets[identifier])
        assert attachment.file_name == name and output.name.endswith("-" + name)
        assert (
            hashlib.sha256(output.read_bytes()).digest()
            == hashlib.sha256(attachment.data).digest()
        )
        if name.endswith(".png"):
            assert output.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    video = Path(result.assets["83c4a03b-d2de-4a3b-a56b-9c28ea7abca2"]).read_bytes()
    assert len(video) == 5453791 and video.startswith(bytes.fromhex("1A45DFA3"))
    with ZipFile(result.assets["3a258502-ea05-4be0-99b9-afc1eadabc0d"]) as workbook:
        assert workbook.testzip() is None
        assert "xl/workbook.xml" in workbook.namelist()
    manifest = json.loads((tmp_path / "document.json").read_text(encoding="utf-8"))[
        "attachments"
    ]
    assert {a["attachment_id"] for a in manifest} == set(expected)
    assert all((tmp_path / a["path"]).is_file() for a in manifest)
