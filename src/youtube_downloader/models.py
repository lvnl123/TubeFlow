from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class FormatOption:
    format_id: str
    media_kind: str
    ext: str
    label: str
    width: Optional[int] = None
    height: Optional[int] = None
    fps: Optional[float] = None
    tbr: Optional[float] = None
    abr: Optional[float] = None
    filesize: Optional[int] = None
    vcodec: Optional[str] = None
    acodec: Optional[str] = None
    format_note: str = ""
    protocol: str = ""


@dataclass
class AVPlan:
    video_format_id: str
    audio_format_id: str = ""
    label: str = ""
    video_label: str = ""
    audio_label: str = ""
    output_ext: str = "mp4"


@dataclass
class VideoMetadata:
    title: str
    duration_text: str
    uploader: str
    webpage_url: str
    video_options: list[FormatOption] = field(default_factory=list)
    audio_options: list[FormatOption] = field(default_factory=list)
    av_options: list[AVPlan] = field(default_factory=list)
