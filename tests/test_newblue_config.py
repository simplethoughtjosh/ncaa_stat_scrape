"""Requirements tests for NewBlue YAML loading and variable remapping."""

from pathlib import Path

import pytest
import requests

from college_sports_scraper.newblue.client import CaptivateClient, CaptivateConnectionError
from college_sports_scraper.newblue.config import NewBlueConfig, load_newblue_config, remap_variables

CFG = Path(__file__).resolve().parents[1] / "config" / "newblue_titles.example.yaml"


def test_example_config_targets_stp_controller():
    cfg = load_newblue_config(CFG)
    assert cfg.transport == "dry-run"
    assert "STP_NCAA_Basketball: Main" in cfg.titles["player_l3"].input
    assert "player_l3" in cfg.titles
    assert "substitution" in cfg.titles
    assert "shot_maps" in cfg.titles


def test_remap_variables_uses_yaml_names():
    cfg = load_newblue_config(CFG)
    mapping = cfg.titles["home_on_court"]
    remapped = remap_variables(
        {
            "team_name": "Home U",
            "player_1_name": "Ada",
            "player_1_name_first": "Ada",
            "player_1_photo": "/tmp/ada.jpg",
        },
        mapping,
    )
    assert remapped["home_team"] == "Home U"
    assert remapped["home_p1_name"] == "Ada"
    assert remapped["home_p1_name_first"] == "Ada"
    assert remapped["home_p1_photo"] == "/tmp/ada.jpg"


def test_closed_captivate_raises_a_short_connection_error(caplog):
    caplog.set_level("WARNING")
    client = CaptivateClient(NewBlueConfig(transport="http", host="127.0.0.1", http_port=9))

    def refused(*args, **kwargs):
        raise requests.ConnectionError("connection refused")

    client._http.post = refused  # type: ignore[method-assign]
    with pytest.raises(CaptivateConnectionError):
        client.schedule_action("still", input_name="STP_NCAA_Basketball: Main", variables={"a": "1"})
    with pytest.raises(CaptivateConnectionError):
        client.schedule_action("still", input_name="STP_NCAA_Basketball: Main", variables={"a": "1"})
    warnings = [rec for rec in caplog.records if "not accepting updates" in rec.message]
    assert len(warnings) == 1
