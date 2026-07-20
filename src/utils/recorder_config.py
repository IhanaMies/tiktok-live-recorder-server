from dataclasses import dataclass
from multiprocessing.sharedctypes import Synchronized
from typing import Optional

from utils.enums import Mode


@dataclass
class RecorderConfig:
    mode: Mode
    url: str | None = None
    user: str | None = None
    room_id: str | None = None
    automatic_interval: int = 60
    cookies: dict | None = None
    proxy: str | None = None
    output: str | None = None
    duration: int | None = None
    use_telegram: bool = False
    bitrate: str | None = None
    ffmpeg_path: str | None = None
    status_dict: dict | None = None
    username: str | None = None
    interval_value: Optional[Synchronized] = None
