"""Exercise the old module entrypoints through the new extraction pipeline."""

import json
from pathlib import Path

from Main import process_onenote_file
from OneDocument import OneDocment
from SOAPFileReader import Packaging

ROOT = Path(__file__).resolve().parents[1]


def test_legacy_document_and_cli_adapter(tmp_path: Path) -> None:
    """Keep legacy dictionary keys and extraction methods available."""
    with (ROOT / "Equipe Euro 2016.one").open("rb") as stream:
        document = OneDocment(stream)
        assert document.get_properties()
        assert document.get_files()
        assert set(document.get_json()) >= {"headers", "properties", "files"}
        stream.seek(0)
        data = json.loads(process_onenote_file(stream, str(tmp_path), "", True))
    assert data["files"]


def test_legacy_package_adapter() -> None:
    """Expose the modern decoded package through the original constructor."""
    with (ROOT / "Section sans titre.one").open("rb") as stream:
        package = Packaging(stream)
    assert package.store.format == "package"
    assert package.store.objects
