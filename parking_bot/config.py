from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class TableAirConfig:
    base_url: str
    username: str
    password: str


@dataclass
class TelegramConfig:
    bot_token: str
    chat_id: str


@dataclass
class Config:
    tableair: TableAirConfig
    telegram: TelegramConfig
    poll_interval_seconds: int = 60
    jitter_seconds: int = 5
    timezone: str = "Europe/Vilnius"
    work_start: str = "07:00"
    work_end: str = "18:00"

    @classmethod
    def load(cls, path: str | Path = "config.yaml") -> "Config":
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return cls(
            tableair=TableAirConfig(**raw["tableair"]),
            telegram=TelegramConfig(**raw["telegram"]),
            poll_interval_seconds=raw.get("poll_interval_seconds", 60),
            jitter_seconds=raw.get("jitter_seconds", 5),
            timezone=raw.get("timezone", "Europe/Vilnius"),
            work_start=raw.get("work_start", "07:00"),
            work_end=raw.get("work_end", "18:00"),
        )
