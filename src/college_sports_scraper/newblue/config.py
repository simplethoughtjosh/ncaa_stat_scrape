"""Load NewBlue title/variable mapping YAML."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_CANDIDATES = (
    Path("config/newblue_titles.yaml"),
    Path("config/newblue_titles.example.yaml"),
)


@dataclass
class TitleMapping:
    key: str
    title: str = ""
    input: str = ""
    variables: dict[str, str] = field(default_factory=dict)


@dataclass
class NewBlueConfig:
    host: str = "127.0.0.1"
    http_port: int = 9022
    tcp_port: int = 9025
    transport: str = "http"  # http | tcp | dry-run
    default_action: str = "update"
    titles: dict[str, TitleMapping] = field(default_factory=dict)

    @property
    def http_url(self) -> str:
        return f"http://{self.host}:{self.http_port}/api/"


def load_newblue_config(path: str | Path | None = None) -> NewBlueConfig:
    cfg_path: Path | None
    if path is not None:
        cfg_path = Path(path)
    else:
        cfg_path = next((p for p in DEFAULT_CONFIG_CANDIDATES if p.exists()), None)
    if cfg_path is None or not cfg_path.exists():
        raise FileNotFoundError(
            "No NewBlue config found. Copy config/newblue_titles.example.yaml "
            "to config/newblue_titles.yaml and edit title names."
        )
    raw: dict[str, Any] = yaml.safe_load(cfg_path.read_text()) or {}
    titles: dict[str, TitleMapping] = {}
    for key, entry in (raw.get("titles") or {}).items():
        titles[key] = TitleMapping(
            key=key,
            title=str(entry.get("title") or ""),
            input=str(entry.get("input") or ""),
            variables={str(k): str(v) for k, v in (entry.get("variables") or {}).items()},
        )
    return NewBlueConfig(
        host=str(raw.get("host") or "127.0.0.1"),
        http_port=int(raw.get("http_port") or 9022),
        tcp_port=int(raw.get("tcp_port") or 9025),
        transport=str(raw.get("transport") or "http"),
        default_action=str(raw.get("default_action") or "update"),
        titles=titles,
    )


def remap_variables(logical: dict[str, str], mapping: TitleMapping) -> dict[str, str]:
    """Map logical payload keys → Captivate variable names from YAML."""
    if not mapping.variables:
        return dict(logical)
    out: dict[str, str] = {}
    for logical_key, value in logical.items():
        captivate_name = mapping.variables.get(logical_key, logical_key)
        out[captivate_name] = value
    return out
