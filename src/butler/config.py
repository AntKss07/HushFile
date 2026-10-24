"""Configuration management for Downloads Butler."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

DEFAULT_CATEGORIES: list[str] = [
    "Finance",
    "Education",
    "Identity",
    "Work",
    "Travel",
    "Health",
    "Receipts",
    "Screenshots",
    "Photos",
    "Voice Notes",
    "Recordings",
    "Code",
    "Archives",
    "Installers",
    "Media",
    "Misc",
]

DEFAULT_IGNORE_EXTENSIONS: list[str] = [
    ".crdownload",
    ".part",
    ".tmp",
    ".download",
]


class RuleConfig(BaseModel):
    """Rule for fast keyword/extension pre-classification."""
    match: str
    category: str


class ModelsConfig(BaseModel):
    text: str = "qwen2.5:7b-instruct"
    vision: str = "qwen2.5vl:3b"
    embed: str = "nomic-embed-text"
    whisper: str = "small"
    device: str = "cuda"  # cuda or cpu
    keep_alive: str = "5m"


class ExtractConfig(BaseModel):
    max_chars: int = 12000
    audio_seconds: int = 300
    ocr_languages: str = "eng"


class ButlerConfig(BaseModel):
    watch_dir: Path = Field(default_factory=lambda: Path.home() / "Downloads")
    output_dir: Path = Field(default_factory=lambda: Path.home() / "Organized")
    db_path: Path | None = None
    mode: str = "auto"  # auto or dry_run
    confidence_threshold: float = 0.6
    stability_seconds: int = 3
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    extract: ExtractConfig = Field(default_factory=ExtractConfig)
    categories: list[str] = Field(default_factory=lambda: list(DEFAULT_CATEGORIES))
    ignore_extensions: list[str] = Field(default_factory=lambda: list(DEFAULT_IGNORE_EXTENSIONS))
    rules: list[RuleConfig] = Field(
        default_factory=lambda: [
            RuleConfig(match="invoice", category="Finance"),
            RuleConfig(match="receipt", category="Receipts"),
        ]
    )

    @field_validator("watch_dir", "output_dir", mode="before")
    @classmethod
    def expand_path(cls, v: Any) -> Path:
        if isinstance(v, (str, Path)):
            return Path(os.path.expanduser(str(v))).resolve()
        return v

    @field_validator("db_path", mode="before")
    @classmethod
    def expand_db_path(cls, v: Any) -> Path | None:
        if v is not None and isinstance(v, (str, Path)):
            return Path(os.path.expanduser(str(v))).resolve()
        return v


def get_default_config_dir() -> Path:
    """Return the platform-appropriate configuration directory."""
    if os.name == "nt":
        app_data = os.environ.get("APPDATA")
        base = Path(app_data) if app_data else Path.home() / "AppData" / "Roaming"
    else:
        xdg_config = os.environ.get("XDG_CONFIG_HOME")
        base = Path(xdg_config) if xdg_config else Path.home() / ".config"
    config_dir = base / "butler"
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


def get_default_config_path() -> Path:
    return get_default_config_dir() / "butler.yaml"


def load_config(config_path: Path | str | None = None) -> ButlerConfig:
    """Load configuration from file or create with defaults if missing."""
    path = Path(config_path) if config_path else get_default_config_path()
    if not path.exists():
        cfg = ButlerConfig()
        if cfg.db_path is None:
            cfg.db_path = get_default_config_dir() / "butler.db"
        save_config(cfg, path)
        return cfg

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    cfg = ButlerConfig.model_validate(data)
    if cfg.db_path is None:
        cfg.db_path = get_default_config_dir() / "butler.db"
    return cfg


def save_config(cfg: ButlerConfig, path: Path | str | None = None) -> None:
    """Save configuration to YAML file."""
    p = Path(path) if path else get_default_config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    dump_data = cfg.model_dump(mode="python")
    # Convert Path objects to strings for clean yaml
    dump_data["watch_dir"] = str(cfg.watch_dir)
    dump_data["output_dir"] = str(cfg.output_dir)
    if cfg.db_path:
        dump_data["db_path"] = str(cfg.db_path)

    with open(p, "w", encoding="utf-8") as f:
        yaml.dump(dump_data, f, sort_keys=False, default_flow_style=False)
