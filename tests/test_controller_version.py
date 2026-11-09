"""Requirements: controller page shows a synced version (Major.Minor.Patch)."""

import json
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "captivate_controller" / "STP_NCAA_Basketball"
VERSION_RE = re.compile(r"^\d+\.\d+\.\d{3}$")


def test_version_file_format_and_current_baseline():
    text = (ROOT / "VERSION").read_text().strip()
    assert VERSION_RE.match(text), f"VERSION must be Major.Minor.Patch with 3-digit patch, got {text!r}"
    # Baseline for this release line — bump intentionally when shipping
    assert text == "1.1.029"


def test_html_xml_and_version_file_agree():
    version = (ROOT / "VERSION").read_text().strip()
    html = (ROOT / "STP_NCAA_BasketballMain.html").read_text()
    xml = (ROOT / "STP_NCAA_BasketballMain.xml").read_text()

    assert f"v{version}" in html, "Controller page badge must show v{version}"
    assert f"CONTROLLER_VERSION = '{version}'" in html or f'CONTROLLER_VERSION = "{version}"' in html
    assert f'version="{version}"' in xml
    assert 'UIGroups="API"' in xml


def test_nbpak_matches_cookbook_layout():
    """content.manifest at archive root; payload copies to ${DATACONTROLLERS}."""
    version = (ROOT / "VERSION").read_text().strip()
    manifest = json.loads((ROOT / "content.manifest").read_text())
    assert manifest["manifestSpec"] == "1.0"
    assert manifest["version"] == version
    assert manifest["identifier"] == "com.newblue.controller.stp_ncaa_basketball"
    deploy = manifest["deployment"][0]
    assert deploy["operation"] == "copy"
    assert deploy["source"] == "payload/STP_NCAA_Basketball"
    assert deploy["target"] == "${DATACONTROLLERS}/STP_NCAA_Basketball"

    archive = ROOT / "STP_NCAA_Basketball-Controller.nbpak"
    assert archive.exists(), "NBPAK must be built next to the controller"
    with zipfile.ZipFile(archive) as zf:
        names = set(zf.namelist())
    assert "content.manifest" in names
    assert "payload/STP_NCAA_Basketball/STP_NCAA_BasketballMain.xml" in names
    assert "payload/STP_NCAA_Basketball/STP_NCAA_BasketballMain.html" in names
    assert "payload/STP_NCAA_Basketball/js/servicehandler.js" in names
    assert "payload/STP_NCAA_Basketball/js/connection-manager.js" in names
    # Manifest stays at the pack root, not inside the deployed folder.
    assert "payload/STP_NCAA_Basketball/content.manifest" not in names
