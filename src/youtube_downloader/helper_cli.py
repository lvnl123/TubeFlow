from __future__ import annotations

import json
from pathlib import Path
import sys

PACKAGE_ROOT = Path(__file__).resolve().parent
SRC_ROOT = PACKAGE_ROOT.parent
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from yt_dlp import YoutubeDL

from youtube_downloader.downloader import (
    _apply_download_acceleration,
    _base_options,
    _build_basic_metadata,
    _build_metadata,
)


def _emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def _extract_info(
    url: str,
    basic_only: bool,
    ffmpeg_location: str = "ffmpeg",
    proxy: str | None = None,
    cookie_file: str | None = None,
    cookie_browser: str | None = None,
) -> dict:
    options = _base_options(ffmpeg_location, proxy, cookie_file, cookie_browser)
    options.update({"skip_download": True, "extract_flat": basic_only})
    with YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=False)
    if "entries" in info:
        raise ValueError("Please use a single video URL, not a playlist URL.")
    return info


def _dump_metadata(data) -> None:
    payload = {
        "title": data.title,
        "duration_text": data.duration_text,
        "uploader": data.uploader,
        "webpage_url": data.webpage_url,
        "video_options": [item.__dict__ for item in data.video_options],
        "audio_options": [item.__dict__ for item in data.audio_options],
        "av_options": [item.__dict__ for item in data.av_options],
    }
    print(json.dumps(payload, ensure_ascii=False))


def inspect_basic(
    url: str,
    ffmpeg_location: str = "ffmpeg",
    proxy: str | None = None,
    cookie_file: str | None = None,
    cookie_browser: str | None = None,
) -> None:
    _dump_metadata(
        _build_basic_metadata(
            _extract_info(
                url,
                basic_only=True,
                ffmpeg_location=ffmpeg_location,
                proxy=proxy,
                cookie_file=cookie_file,
                cookie_browser=cookie_browser,
            )
        )
    )


def inspect_url(
    url: str,
    show_all_formats: bool,
    ffmpeg_location: str = "ffmpeg",
    proxy: str | None = None,
    cookie_file: str | None = None,
    cookie_browser: str | None = None,
) -> None:
    _dump_metadata(
        _build_metadata(
            _extract_info(
                url,
                basic_only=False,
                ffmpeg_location=ffmpeg_location,
                proxy=proxy,
                cookie_file=cookie_file,
                cookie_browser=cookie_browser,
            ),
            show_all_formats=show_all_formats,
        )
    )


def download_url(
    url: str,
    output_dir: str,
    format_selector: str,
    merge_output_format: str,
    ffmpeg_location: str = "ffmpeg",
    proxy: str | None = None,
    cookie_file: str | None = None,
    cookie_browser: str | None = None,
) -> None:
    started_downloading = False

    def progress_hook(data: dict) -> None:
        nonlocal started_downloading
        status = data.get("status")
        if status == "downloading":
            if not started_downloading:
                started_downloading = True
                _emit({"event": "status", "text": "开始传输：源站已响应，正在传输数据..."})
            total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
            downloaded = data.get("downloaded_bytes") or 0
            percent = downloaded / total * 100 if total else 0.0
            speed = data.get("speed")
            eta = data.get("eta")
            speed_text = f"{speed / 1024 / 1024:.2f} MB/s" if speed else "?"
            eta_text = f"{eta}s" if eta is not None else "?"
            _emit({"event": "progress", "percent": percent, "text": f"{percent:.1f}% | {speed_text} | ETA {eta_text}"})
        elif status == "finished":
            _emit({"event": "progress", "percent": 100.0, "text": "下载完成，正在整理文件..."})
            _emit({"event": "status", "text": "合并音视频：下载完成，正在执行后处理..."})

    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)

    options = _apply_download_acceleration(_base_options(ffmpeg_location, proxy, cookie_file, cookie_browser))
    options.update(
        {
            "outtmpl": str(target / "%(title)s [%(id)s].%(ext)s"),
            "format": format_selector,
            "progress_hooks": [progress_hook],
            "windowsfilenames": True,
            "overwrites": False,
        }
    )
    if merge_output_format:
        options["merge_output_format"] = merge_output_format

    _emit({"event": "status", "text": "启动 helper：已启用兼容下载器。"})
    _emit({"event": "status", "text": "请求视频源：正在向视频源发起请求..."})
    with YoutubeDL(options) as ydl:
        ydl.download([url])


def main() -> None:
    if len(sys.argv) < 3:
        raise SystemExit(
            "Usage: helper_cli.py inspect_basic|inspect <url> [show_all] | "
            "download <url> <output_dir> <format> <merge_ext>"
        )

    mode = sys.argv[1]
    if mode == "inspect_basic":
        inspect_basic(
            sys.argv[2],
            sys.argv[4] if len(sys.argv) > 4 else "ffmpeg",
            sys.argv[5] if len(sys.argv) > 5 else None,
            sys.argv[6] if len(sys.argv) > 6 else None,
            sys.argv[7] if len(sys.argv) > 7 else None,
        )
        return
    if mode == "inspect":
        inspect_url(
            sys.argv[2],
            len(sys.argv) > 3 and sys.argv[3] == "1",
            sys.argv[4] if len(sys.argv) > 4 else "ffmpeg",
            sys.argv[5] if len(sys.argv) > 5 else None,
            sys.argv[6] if len(sys.argv) > 6 else None,
            sys.argv[7] if len(sys.argv) > 7 else None,
        )
        return
    if mode == "download":
        if len(sys.argv) < 6:
            raise SystemExit("download mode needs: <url> <output_dir> <format> <merge_ext>")
        download_url(
            sys.argv[2],
            sys.argv[3],
            sys.argv[4],
            sys.argv[5],
            sys.argv[6] if len(sys.argv) > 6 else "ffmpeg",
            sys.argv[7] if len(sys.argv) > 7 else None,
            sys.argv[8] if len(sys.argv) > 8 else None,
            sys.argv[9] if len(sys.argv) > 9 else None,
        )
        return
    raise SystemExit(f"Unknown mode: {mode}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        _emit({"event": "error", "text": str(exc)})
        raise SystemExit(1)
