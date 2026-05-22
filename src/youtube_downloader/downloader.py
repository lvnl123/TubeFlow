from __future__ import annotations

import ctypes
import json
import math
import os
from ctypes import wintypes
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
from typing import Callable
from urllib.parse import urlparse

from yt_dlp import YoutubeDL

from .models import AVPlan, FormatOption, VideoMetadata

ProgressCallback = Callable[[float, str], None]
StatusCallback = Callable[[str], None]

CONTROL_SLEEP_SECONDS = 0.2
FAST_CONCURRENT_FRAGMENTS = 4
FAST_HTTP_CHUNK_SIZE = 10 * 1024 * 1024
DOWNLOAD_RETRY_DELAY_SECONDS = 2.0
TRANSIENT_HTTP_ERRORS = ("HTTP Error 403", "Forbidden", "403: Forbidden")
ENGINE_FALLBACK_ERRORS = (
    "Requested format is not available",
    "requested format not available",
    "Some web client https formats have been skipped as they are missing a url",
    "YouTube is forcing SABR streaming for this client",
)
TEST_VIDEO_URL = "https://www.youtube.com/watch?v=BaW_jenozKc"
DOWNLOAD_ENGINES = ("auto", "cli", "python")
COOKIE_MODES = ("none", "file", "browser")
BROWSER_COOKIE_MAP = {"chrome": "chrome", "edge": "edge"}
DOWNLOAD_PROGRESS_RE = re.compile(
    r"\[download\]\s+(?P<percent>\d+(?:\.\d+)?)%.*?at\s+(?P<speed>.+?)\s+ETA\s+(?P<eta>.+)$"
)


if os.name == "nt":
    TH32CS_SNAPPROCESS = 0x00000002
    TH32CS_SNAPTHREAD = 0x00000004
    THREAD_SUSPEND_RESUME = 0x0002
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class PROCESSENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    class THREADENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ThreadID", wintypes.DWORD),
            ("th32OwnerProcessID", wintypes.DWORD),
            ("tpBasePri", ctypes.c_long),
            ("tpDeltaPri", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
        ]


class DownloadFailure(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


def _close_handle(handle) -> None:
    if os.name == "nt" and handle not in (None, 0, INVALID_HANDLE_VALUE):
        kernel32.CloseHandle(handle)


def _child_process_ids(root_pid: int) -> list[int]:
    if os.name != "nt":
        return []
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == INVALID_HANDLE_VALUE:
        return []
    children: dict[int, list[int]] = {}
    try:
        entry = PROCESSENTRY32()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32)
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            children.setdefault(int(entry.th32ParentProcessID), []).append(int(entry.th32ProcessID))
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        _close_handle(snapshot)

    result: list[int] = []
    queue = list(children.get(root_pid, []))
    while queue:
        pid = queue.pop(0)
        result.append(pid)
        queue.extend(children.get(pid, []))
    return result


def _thread_ids_for_process(pid: int) -> list[int]:
    if os.name != "nt":
        return []
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
    if snapshot == INVALID_HANDLE_VALUE:
        return []
    thread_ids: list[int] = []
    try:
        entry = THREADENTRY32()
        entry.dwSize = ctypes.sizeof(THREADENTRY32)
        ok = kernel32.Thread32First(snapshot, ctypes.byref(entry))
        while ok:
            if int(entry.th32OwnerProcessID) == pid:
                thread_ids.append(int(entry.th32ThreadID))
            ok = kernel32.Thread32Next(snapshot, ctypes.byref(entry))
    finally:
        _close_handle(snapshot)
    return thread_ids


def _suspend_process_tree(root_pid: int) -> None:
    if os.name != "nt":
        return
    for pid in [root_pid, *_child_process_ids(root_pid)]:
        for thread_id in _thread_ids_for_process(pid):
            thread_handle = kernel32.OpenThread(THREAD_SUSPEND_RESUME, False, thread_id)
            if not thread_handle:
                continue
            try:
                kernel32.SuspendThread(thread_handle)
            finally:
                _close_handle(thread_handle)


def _resume_process_tree(root_pid: int) -> None:
    if os.name != "nt":
        return
    for pid in [root_pid, *_child_process_ids(root_pid)]:
        for thread_id in _thread_ids_for_process(pid):
            thread_handle = kernel32.OpenThread(THREAD_SUSPEND_RESUME, False, thread_id)
            if not thread_handle:
                continue
            try:
                while kernel32.ResumeThread(thread_handle) > 0:
                    pass
            finally:
                _close_handle(thread_handle)


def _terminate_process_tree(root_pid: int) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(root_pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
        return
    try:
        os.kill(root_pid, 9)
    except OSError:
        pass


def _decode_process_bytes(data: bytes | None) -> str:
    if not data:
        return ""
    for encoding in ("utf-8", "gbk", sys.getfilesystemencoding() or "utf-8"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _safe_int(value: object) -> int | None:
    if value in (None, "none", ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_float(value: object) -> float | None:
    if value in (None, "none", ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _filesize_text(size: int | None) -> str:
    if not size:
        return "?"
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(size)
    idx = 0
    while value >= 1024 and idx < len(units) - 1:
        value /= 1024
        idx += 1
    return f"{value:.1f}{units[idx]}"


def _duration_text(seconds: object) -> str:
    total = _safe_int(seconds)
    if total is None:
        return "未知"
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _resolution_text(width: int | None, height: int | None) -> str:
    if width and height:
        return f"{width}x{height}"
    if height:
        return f"{height}p"
    return "未知"


def _video_label(fmt: dict) -> str:
    height = _safe_int(fmt.get("height"))
    width = _safe_int(fmt.get("width"))
    fps = _safe_float(fmt.get("fps"))
    tbr = _safe_float(fmt.get("tbr"))
    ext = fmt.get("ext") or "?"
    note = fmt.get("format_note") or ""
    size = _filesize_text(_safe_int(fmt.get("filesize")) or _safe_int(fmt.get("filesize_approx")))

    parts = [_resolution_text(width, height), ext]
    if fps:
        parts.append(f"{int(round(fps))}fps")
    if tbr:
        parts.append(f"{int(round(tbr))}kbps")
    if note:
        parts.append(note)
    parts.append(size)
    return " | ".join(parts)


def _audio_label(fmt: dict) -> str:
    abr = _safe_float(fmt.get("abr")) or _safe_float(fmt.get("tbr"))
    asr = _safe_int(fmt.get("asr"))
    ext = fmt.get("ext") or "?"
    size = _filesize_text(_safe_int(fmt.get("filesize")) or _safe_int(fmt.get("filesize_approx")))

    parts = [ext]
    if abr:
        parts.append(f"{int(round(abr))}kbps")
    if asr:
        parts.append(f"{asr}Hz")
    parts.append(size)
    return " | ".join(parts)


def _is_real_media_format(fmt: dict) -> bool:
    if fmt.get("has_drm"):
        return False
    vcodec = fmt.get("vcodec")
    acodec = fmt.get("acodec")
    if vcodec == "none" and acodec == "none":
        return False
    return bool(fmt.get("url") or fmt.get("manifest_url"))


def _format_to_option(fmt: dict, media_kind: str) -> FormatOption:
    return FormatOption(
        format_id=str(fmt.get("format_id")),
        media_kind=media_kind,
        ext=fmt.get("ext") or "?",
        label=_video_label(fmt) if media_kind == "video" else _audio_label(fmt),
        width=_safe_int(fmt.get("width")),
        height=_safe_int(fmt.get("height")),
        fps=_safe_float(fmt.get("fps")),
        tbr=_safe_float(fmt.get("tbr")),
        abr=_safe_float(fmt.get("abr")) or _safe_float(fmt.get("tbr")),
        filesize=_safe_int(fmt.get("filesize")) or _safe_int(fmt.get("filesize_approx")),
        vcodec=fmt.get("vcodec"),
        acodec=fmt.get("acodec"),
        format_note=fmt.get("format_note") or "",
        protocol=fmt.get("protocol") or "",
    )


def _build_common_video_options(video_options: list[FormatOption]) -> list[FormatOption]:
    selected: list[FormatOption] = []
    seen_heights: set[int] = set()

    def score(option: FormatOption) -> tuple[int, int, int]:
        return (
            2 if option.ext == "mp4" else 1,
            1 if option.fps and option.fps >= 50 else 0,
            int(option.tbr or 0),
        )

    groups: dict[int, list[FormatOption]] = {}
    for option in video_options:
        height = option.height or 0
        groups.setdefault(height, []).append(option)

    for height in sorted(groups.keys(), reverse=True):
        if height in seen_heights:
            continue
        best = sorted(groups[height], key=score, reverse=True)[0]
        selected.append(best)
        seen_heights.add(height)
        if len(selected) >= 8:
            break

    return selected


def _build_common_audio_options(audio_options: list[FormatOption]) -> list[FormatOption]:
    selected: list[FormatOption] = []
    seen_exts: set[str] = set()

    for option in audio_options:
        if option.ext not in seen_exts:
            selected.append(option)
            seen_exts.add(option.ext)
        if len(selected) >= 3:
            break

    if not selected and audio_options:
        selected.append(audio_options[0])
    return selected


def _build_av_display_options(video_options: list[FormatOption]) -> list[AVPlan]:
    plans: list[AVPlan] = []
    for option in video_options:
        output_ext = "mp4" if option.ext == "mp4" else "webm" if option.ext == "webm" else "mkv"
        plans.append(
            AVPlan(
                video_format_id=option.format_id,
                label=f"{option.label} | 下载时自动匹配最佳音频 -> {output_ext.upper()}",
                video_label=option.label,
                audio_label="下载时自动选择",
                output_ext=output_ext,
            )
        )
    return plans


def _build_basic_metadata(info: dict) -> VideoMetadata:
    return VideoMetadata(
        title=info.get("title") or "未命名视频",
        duration_text=_duration_text(info.get("duration")),
        uploader=info.get("uploader") or "未知作者",
        webpage_url=info.get("webpage_url") or "",
    )


def _build_metadata(info: dict, show_all_formats: bool = False) -> VideoMetadata:
    formats = info.get("formats") or []
    video_only: list[FormatOption] = []
    audio_only: list[FormatOption] = []

    for fmt in formats:
        if not _is_real_media_format(fmt):
            continue

        vcodec = fmt.get("vcodec")
        acodec = fmt.get("acodec")
        if vcodec and vcodec != "none" and acodec == "none":
            video_only.append(_format_to_option(fmt, "video"))
        elif acodec and acodec != "none" and vcodec == "none":
            audio_only.append(_format_to_option(fmt, "audio"))

    video_only.sort(key=lambda item: (item.height or 0, item.fps or 0, item.tbr or 0, item.filesize or 0), reverse=True)
    audio_only.sort(key=lambda item: (item.abr or 0, item.tbr or 0, item.filesize or 0), reverse=True)

    selected_video = video_only if show_all_formats else _build_common_video_options(video_only)
    selected_audio = audio_only if show_all_formats else _build_common_audio_options(audio_only)

    return VideoMetadata(
        title=info.get("title") or "未命名视频",
        duration_text=_duration_text(info.get("duration")),
        uploader=info.get("uploader") or "未知作者",
        webpage_url=info.get("webpage_url") or "",
        video_options=selected_video,
        audio_options=selected_audio,
        av_options=_build_av_display_options(selected_video),
    )


def _base_options(
    ffmpeg_location: str,
    proxy: str | None = None,
    cookie_file: str | None = None,
    cookie_browser: str | None = None,
) -> dict:
    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "cachedir": False,
        "socket_timeout": 15,
        "retries": 3,
        "ffmpeg_location": ffmpeg_location,
    }
    if proxy:
        options["proxy"] = proxy
    if cookie_file:
        options["cookiefile"] = cookie_file
    elif cookie_browser:
        options["cookiesfrombrowser"] = (cookie_browser,)
    return options


def _apply_download_acceleration(options: dict) -> dict:
    options["concurrent_fragment_downloads"] = FAST_CONCURRENT_FRAGMENTS
    options["http_chunk_size"] = FAST_HTTP_CHUNK_SIZE
    options["fragment_retries"] = 8
    return options


def _is_retryable_error(error_text: str) -> bool:
    return any(marker in error_text for marker in TRANSIENT_HTTP_ERRORS + ENGINE_FALLBACK_ERRORS)


def _humanize_cookie_mode(cookie_mode: str, cookie_file: str | None, cookie_browser: str | None) -> str:
    if cookie_mode == "file" and cookie_file:
        return f"cookies.txt: {cookie_file}"
    if cookie_mode == "browser" and cookie_browser:
        label = "Chrome" if cookie_browser == "chrome" else "Edge"
        return f"浏览器 Cookie: {label}"
    return "未启用"


class DownloaderService:
    def __init__(self) -> None:
        self.ffmpeg_location = self._detect_ffmpeg()
        self.helper_python = self._detect_helper_python()
        self.yt_dlp_path = self._detect_yt_dlp_cli()
        self.js_runtime = self._detect_js_runtime()
        self.cli_supports_js_runtimes = self._detect_cli_flag_support("--js-runtimes")
        self.prefer_helper = sys.version_info < (3, 10) and self.helper_python is not None
        self.download_engine_mode = "auto"
        self.proxy: str | None = None
        self.cookie_mode = "none"
        self.cookie_file: str | None = None
        self.cookie_browser: str | None = None
        self._control_lock = threading.RLock()
        self._active_download = False
        self._paused_download = False
        self._cancel_requested = False
        self._active_process: subprocess.Popen | None = None
        self._current_status_callback: StatusCallback | None = None
        self._process_suspended = False
        self._active_engine_name = "idle"

    def set_proxy(self, proxy: str | None) -> None:
        self.proxy = (proxy or "").strip() or None

    def set_download_engine_mode(self, mode: str | None) -> None:
        normalized = (mode or "auto").strip().lower()
        self.download_engine_mode = normalized if normalized in DOWNLOAD_ENGINES else "auto"

    def set_cookie_source(
        self,
        mode: str | None,
        cookie_file: str | None = None,
        cookie_browser: str | None = None,
    ) -> None:
        normalized_mode = (mode or "none").strip().lower()
        self.cookie_mode = normalized_mode if normalized_mode in COOKIE_MODES else "none"
        self.cookie_file = (cookie_file or "").strip() or None
        browser = (cookie_browser or "").strip().lower()
        self.cookie_browser = browser if browser in BROWSER_COOKIE_MAP else None
        if self.cookie_mode == "file" and not self.cookie_file:
            self.cookie_mode = "none"
        if self.cookie_mode == "browser" and not self.cookie_browser:
            self.cookie_mode = "none"

    def has_active_download(self) -> bool:
        with self._control_lock:
            return self._active_download

    def is_download_paused(self) -> bool:
        with self._control_lock:
            return self._paused_download

    def pause_download(self) -> bool:
        with self._control_lock:
            if not self._active_download or self._paused_download:
                return False
            self._paused_download = True
            process = self._active_process
            if process is not None and process.poll() is None and os.name == "nt":
                _suspend_process_tree(process.pid)
                self._process_suspended = True
        self._notify_status("下载已暂停。")
        return True

    def resume_download(self) -> bool:
        with self._control_lock:
            if not self._active_download or not self._paused_download:
                return False
            process = self._active_process
            if process is not None and process.poll() is None and self._process_suspended and os.name == "nt":
                _resume_process_tree(process.pid)
                self._process_suspended = False
            self._paused_download = False
        self._notify_status("继续下载中...")
        return True

    def cancel_download(self) -> bool:
        with self._control_lock:
            if not self._active_download:
                return False
            self._cancel_requested = True
            process = self._active_process
            suspended = self._process_suspended
        self._notify_status("正在取消下载...")
        if process is not None and process.poll() is None:
            if suspended and os.name == "nt":
                _resume_process_tree(process.pid)
                with self._control_lock:
                    self._process_suspended = False
            _terminate_process_tree(process.pid)
        return True

    def get_environment_summary(self) -> dict[str, str]:
        engine_label = {
            "auto": "自动",
            "cli": "CLI",
            "python": "Python/helper",
        }.get(self.download_engine_mode, "自动")
        if self.download_engine_mode == "auto":
            if self.yt_dlp_path:
                effective = "CLI"
            elif self.prefer_helper:
                effective = "Python/helper"
            else:
                effective = "Python"
        elif self.download_engine_mode == "cli":
            effective = "CLI"
        else:
            effective = "Python/helper" if self.prefer_helper else "Python"
        runtime_name = self.js_runtime[0] if self.js_runtime else "未检测到"
        return {
            "engine_mode": engine_label,
            "effective_engine": effective,
            "yt_dlp_path": self.yt_dlp_path or "未检测到",
            "ffmpeg_path": self.ffmpeg_location or "未检测到",
            "proxy": self.proxy or "未启用",
            "cookie": _humanize_cookie_mode(self.cookie_mode, self.cookie_file, self.cookie_browser),
            "js_runtime": runtime_name,
            "active_engine": self._active_engine_name,
        }

    def diagnose_proxy(self) -> dict[str, str | bool]:
        proxy = self.proxy or ""
        if not proxy:
            return {"ok": False, "message": "未填写代理地址。"}
        parsed = urlparse(proxy)
        if not parsed.scheme or not parsed.hostname:
            return {"ok": False, "message": "代理地址格式无效，请填写 http:// 或 socks5:// 开头的完整地址。"}
        if not self.yt_dlp_path:
            return {"ok": False, "message": "未检测到 yt-dlp.exe，暂时无法执行代理连通性测试。"}

        command = [
            self.yt_dlp_path,
            "--ignore-config",
            "--no-playlist",
            "--proxy",
            proxy,
            "--skip-download",
            "--dump-single-json",
            TEST_VIDEO_URL,
        ]
        try:
            result = subprocess.run(command, capture_output=True, timeout=20)
        except subprocess.TimeoutExpired:
            return {"ok": False, "message": f"代理测试超时：{proxy}"}

        stdout = _decode_process_bytes(result.stdout).strip()
        stderr = _decode_process_bytes(result.stderr).strip()
        if result.returncode == 0 and stdout:
            return {"ok": True, "message": f"代理可用：{proxy}"}
        detail = stderr or stdout or f"退出码 {result.returncode}"
        return {"ok": False, "message": f"代理不可用：{detail}"}

    def diagnose_cookie(self) -> dict[str, str | bool]:
        if self.cookie_mode == "none":
            return {"ok": False, "message": "当前未启用 Cookie。"}
        if self.cookie_mode == "file":
            cookie_path = Path(self.cookie_file or "")
            if not cookie_path.exists():
                return {"ok": False, "message": f"Cookie 文件不存在：{cookie_path}"}
            if cookie_path.stat().st_size == 0:
                return {"ok": False, "message": f"Cookie 文件为空：{cookie_path}"}
            return {"ok": True, "message": f"Cookie 文件已就绪：{cookie_path}"}
        browser_name = self.cookie_browser or ""
        browser_path = self._detect_browser_cookie_store(browser_name)
        if browser_path is None:
            label = "Chrome" if browser_name == "chrome" else "Edge"
            return {"ok": False, "message": f"未找到 {label} 浏览器配置目录。"}
        return {"ok": True, "message": f"浏览器 Cookie 已就绪：{browser_path}"}

    def explain_error(self, message: str) -> str:
        if "403" in message and "Forbidden" in message:
            return f"{message}\n\n中文提示：源站拒绝了当前请求，常见于刚切换节点、代理链路变化或 Cookie/会话失效。"
        lowered = message.lower()
        if "ffmpeg is not installed" in lowered or "ffmpeg" in lowered and "not installed" in lowered:
            return f"{message}\n\n中文提示：当前任务需要 FFmpeg 合并音视频，但系统没有找到可用的 FFmpeg。"
        if "proxy" in lowered and ("timed out" in lowered or "timeout" in lowered or "refused" in lowered):
            return f"{message}\n\n中文提示：代理不可达、超时，或代理地址填写有误。"
        if "cookies" in lowered and ("invalid" in lowered or "decrypt" in lowered or "permission" in lowered):
            return f"{message}\n\n中文提示：Cookie 无效、过期，或当前环境没有权限读取浏览器 Cookie。"
        return message

    def _notify_status(self, text: str) -> None:
        callback = self._current_status_callback
        if callback is not None:
            callback(text)

    def _begin_download_session(self, status_callback: StatusCallback) -> None:
        with self._control_lock:
            self._active_download = True
            self._paused_download = False
            self._cancel_requested = False
            self._active_process = None
            self._current_status_callback = status_callback
            self._process_suspended = False
            self._active_engine_name = "准备中"

    def _finish_download_session(self) -> None:
        with self._control_lock:
            self._active_download = False
            self._paused_download = False
            self._cancel_requested = False
            self._active_process = None
            self._current_status_callback = None
            self._process_suspended = False
            self._active_engine_name = "idle"

    def _wait_if_paused_or_cancelled(self) -> None:
        while True:
            with self._control_lock:
                paused = self._paused_download
                cancelled = self._cancel_requested
            if cancelled:
                raise RuntimeError("下载已取消。")
            if not paused:
                return
            time.sleep(CONTROL_SLEEP_SECONDS)

    def _detect_ffmpeg(self) -> str:
        known_paths = [
            os.environ.get("YTDL_FFMPEG"),
            shutil.which("ffmpeg"),
            r"E:\ffmpeg-7.0.2-full_build\bin\ffmpeg.exe",
        ]
        for candidate in known_paths:
            if candidate and Path(candidate).exists():
                return str(candidate)
        return "ffmpeg"

    def _detect_helper_python(self) -> str | None:
        candidates: list[str] = []
        env_python = os.environ.get("YTDL_HELPER_PYTHON")
        if env_python:
            candidates.append(env_python)
        candidates.extend(
            [
                r"C:\Users\Val\AppData\Local\Programs\Python\Python311\python.exe",
                r"C:\Users\Val\AppData\Local\Programs\Python\Python312\python.exe",
                r"C:\Users\Val\AppData\Local\Programs\Python\Python310\python.exe",
            ]
        )

        current = Path(sys.executable).resolve()
        for candidate in candidates:
            path = Path(candidate)
            if not path.exists():
                continue
            if path.resolve() == current:
                continue
            return str(path)
        return None

    def _detect_yt_dlp_cli(self) -> str | None:
        project_root = Path(__file__).resolve().parents[2]
        candidates = [
            project_root / ".venv" / "Scripts" / "yt-dlp.exe",
            Path(sys.executable).resolve().parent / "yt-dlp.exe",
        ]
        which_path = shutil.which("yt-dlp")
        if which_path:
            candidates.append(Path(which_path))
        which_exe_path = shutil.which("yt-dlp.exe")
        if which_exe_path:
            candidates.append(Path(which_exe_path))

        seen: set[str] = set()
        for candidate in candidates:
            text = str(candidate)
            if text in seen:
                continue
            seen.add(text)
            if candidate.exists():
                return text
        return None

    def _detect_js_runtime(self) -> tuple[str, str] | None:
        node_path = shutil.which("node")
        if node_path:
            return ("node", node_path)
        deno_path = shutil.which("deno")
        if deno_path:
            return ("deno", deno_path)
        return None

    def _detect_cli_flag_support(self, flag: str) -> bool:
        if not self.yt_dlp_path:
            return False
        try:
            result = subprocess.run(
                [self.yt_dlp_path, "--help"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        help_text = f"{result.stdout}\n{result.stderr}"
        return flag in help_text

    def _detect_browser_cookie_store(self, browser: str) -> str | None:
        local_app_data = Path(os.environ.get("LOCALAPPDATA", ""))
        if browser == "chrome":
            root = local_app_data / "Google" / "Chrome" / "User Data"
        elif browser == "edge":
            root = local_app_data / "Microsoft" / "Edge" / "User Data"
        else:
            return None
        if root.exists():
            return str(root)
        return None

    def _cookie_args(self) -> tuple[str | None, str | None]:
        if self.cookie_mode == "file" and self.cookie_file:
            return (self.cookie_file, None)
        if self.cookie_mode == "browser" and self.cookie_browser:
            return (None, self.cookie_browser)
        return (None, None)

    def _extract_info_local(self, url: str, basic_only: bool = False) -> dict:
        cookie_file, cookie_browser = self._cookie_args()
        options = _base_options(self.ffmpeg_location, self.proxy, cookie_file, cookie_browser)
        options.update({"skip_download": True, "extract_flat": basic_only})
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
        if "entries" in info:
            raise ValueError("请提供单个视频链接，不要传入播放列表。")
        return info

    def inspect_basic(self, url: str, status_callback: StatusCallback | None = None) -> VideoMetadata:
        if status_callback:
            status_callback("开始读取基础信息...")
            status_callback("环境检查：准备读取标题、作者、时长。")
        if self.prefer_helper:
            if status_callback:
                status_callback("启动 helper：当前环境将通过兼容解释器读取基础信息。")
            return self._inspect_via_helper(url, basic_only=True, status_callback=status_callback)
        info = self._extract_info_local(url, basic_only=True)
        if status_callback:
            status_callback("基础信息已返回。")
        return _build_basic_metadata(info)

    def inspect(
        self,
        url: str,
        show_all_formats: bool = False,
        status_callback: StatusCallback | None = None,
    ) -> VideoMetadata:
        if status_callback:
            mode_text = "全部格式" if show_all_formats else "常用格式"
            status_callback(f"开始加载{mode_text}列表...")
            status_callback("环境检查：准备整理音视频格式。")
        if self.prefer_helper:
            if status_callback:
                status_callback("启动 helper：当前环境将通过兼容解释器加载格式列表。")
            return self._inspect_via_helper(
                url,
                basic_only=False,
                show_all_formats=show_all_formats,
                status_callback=status_callback,
            )

        info = self._extract_info_local(url, basic_only=False)
        data = _build_metadata(info, show_all_formats=show_all_formats)
        if status_callback:
            status_callback(
                f"格式列表已整理完成：视频 {len(data.video_options)}，音频 {len(data.audio_options)}，音视频 {len(data.av_options)}。"
            )
        if data.video_options or data.audio_options:
            return data
        if self.helper_python:
            if status_callback:
                status_callback("本地格式列表为空，尝试兼容解释器回退。")
            return self._inspect_via_helper(
                url,
                basic_only=False,
                show_all_formats=show_all_formats,
                status_callback=status_callback,
            )
        return data

    def _inspect_via_helper(
        self,
        url: str,
        basic_only: bool,
        show_all_formats: bool = False,
        status_callback: StatusCallback | None = None,
    ) -> VideoMetadata:
        if not self.helper_python:
            raise RuntimeError("当前环境没有可用的兼容解释器。")
        helper_path = Path(__file__).resolve().parent / "helper_cli.py"
        cookie_file, cookie_browser = self._cookie_args()
        command = [
            self.helper_python,
            str(helper_path),
            "inspect_basic" if basic_only else "inspect",
            url,
            "1" if show_all_formats else "0",
            self.ffmpeg_location,
            self.proxy or "",
            cookie_file or "",
            cookie_browser or "",
        ]
        result = subprocess.run(command, capture_output=True)

        stdout = _decode_process_bytes(result.stdout)
        stderr = _decode_process_bytes(result.stderr)
        if result.returncode != 0:
            message = stderr.strip() or stdout.strip() or f"兼容解析器执行失败，退出码：{result.returncode}"
            raise RuntimeError(message)

        payload_text = stdout.strip()
        if not payload_text:
            raise RuntimeError("兼容解析器没有返回可用数据。")
        payload = json.loads(payload_text)
        data = VideoMetadata(
            title=payload["title"],
            duration_text=payload["duration_text"],
            uploader=payload["uploader"],
            webpage_url=payload["webpage_url"],
            video_options=[FormatOption(**item) for item in payload.get("video_options", [])],
            audio_options=[FormatOption(**item) for item in payload.get("audio_options", [])],
            av_options=[AVPlan(**item) for item in payload.get("av_options", [])],
        )
        if status_callback:
            if basic_only:
                status_callback("兼容解释器已返回基础信息。")
            else:
                status_callback(
                    f"兼容解释器已返回格式列表：视频 {len(data.video_options)}，音频 {len(data.audio_options)}，音视频 {len(data.av_options)}。"
                )
        return data

    def choose_av_download_plan(
        self,
        video_format_id: str,
        video_options: list[FormatOption],
        audio_options: list[FormatOption],
    ) -> tuple[FormatOption, FormatOption, str]:
        video_option = next((item for item in video_options if item.format_id == video_format_id), None)
        if video_option is None:
            raise RuntimeError(f"没有找到已选择的视频格式：{video_format_id}")
        if not audio_options:
            raise RuntimeError("没有可用的音频格式。")

        if video_option.ext == "mp4":
            preferred_audio_exts = ("m4a", "mp4")
            merge_output_format = "mp4"
        elif video_option.ext == "webm":
            preferred_audio_exts = ("webm",)
            merge_output_format = "webm"
        else:
            preferred_audio_exts = ("m4a", "webm", "mp4")
            merge_output_format = "mkv"

        audio_option = next(
            (item for ext in preferred_audio_exts for item in audio_options if item.ext == ext),
            audio_options[0],
        )
        return video_option, audio_option, merge_output_format

    def _progress_hook(self, progress_callback: ProgressCallback, status_callback: StatusCallback) -> Callable[[dict], None]:
        def hook(data: dict) -> None:
            self._wait_if_paused_or_cancelled()
            status = data.get("status")
            if status == "downloading":
                total = data.get("total_bytes") or data.get("total_bytes_estimate")
                downloaded = data.get("downloaded_bytes") or 0
                percent = max(0.0, min(100.0, downloaded / total * 100)) if total else 0.0
                speed = data.get("speed")
                eta = data.get("eta")
                speed_text = f"{speed / 1024 / 1024:.2f} MB/s" if speed else "?"
                eta_text = f"{math.ceil(eta)}s" if eta is not None else "?"
                progress_callback(percent, f"{percent:.1f}% | {speed_text} | ETA {eta_text}")
            elif status == "finished":
                progress_callback(100.0, "下载完成，正在整理文件...")
                status_callback("合并音视频：下载完成，正在执行后处理...")

        return hook

    def download_video_only(
        self,
        url: str,
        format_id: str,
        output_dir: str | Path,
        progress_callback: ProgressCallback,
        status_callback: StatusCallback,
    ) -> None:
        status_callback("开始下载纯视频...")
        self._download(url, format_id, output_dir, progress_callback, status_callback)

    def download_audio_only(
        self,
        url: str,
        format_id: str,
        output_dir: str | Path,
        progress_callback: ProgressCallback,
        status_callback: StatusCallback,
    ) -> None:
        status_callback("开始下载纯音频...")
        self._download(url, format_id, output_dir, progress_callback, status_callback)

    def download_av(
        self,
        url: str,
        video_format_id: str,
        audio_format_id: str,
        merge_output_format: str,
        output_dir: str | Path,
        progress_callback: ProgressCallback,
        status_callback: StatusCallback,
    ) -> None:
        status_callback(
            "开始下载并合并音视频："
            f"视频 {video_format_id} + 音频 {audio_format_id} -> {merge_output_format.upper()}"
        )
        self._download(
            url,
            f"{video_format_id}+{audio_format_id}",
            output_dir,
            progress_callback,
            status_callback,
            merge_output_format=merge_output_format,
        )

    def _download(
        self,
        url: str,
        format_selector: str,
        output_dir: str | Path,
        progress_callback: ProgressCallback,
        status_callback: StatusCallback,
        merge_output_format: str | None = None,
    ) -> None:
        self._begin_download_session(status_callback)
        try:
            self._wait_if_paused_or_cancelled()
            status_callback("环境检查：正在检查保存目录...")
            output_path = Path(output_dir)
            output_path.mkdir(parents=True, exist_ok=True)

            engines = self._candidate_download_engines()
            last_error: DownloadFailure | None = None

            for engine_index, engine in enumerate(engines):
                engine_label = self._engine_label(engine)
                if engine_index > 0:
                    status_callback(f"切换引擎：上一条链路失败，改用 {engine_label} 再试一次...")
                try:
                    for attempt in range(1, 3):
                        if attempt > 1:
                            status_callback(f"重试来源：{engine_label}，正在重新建立下载连接（第 {attempt} 次）...")
                            time.sleep(DOWNLOAD_RETRY_DELAY_SECONDS)
                        self._wait_if_paused_or_cancelled()
                        if engine == "cli":
                            self._download_via_cli(
                                url,
                                format_selector,
                                output_path,
                                progress_callback,
                                status_callback,
                                merge_output_format,
                            )
                        else:
                            self._download_via_python_engine(
                                url,
                                format_selector,
                                output_path,
                                progress_callback,
                                status_callback,
                                merge_output_format,
                            )
                        return
                except DownloadFailure as exc:
                    last_error = exc
                    if exc.retryable:
                        status_callback(f"重试来源：{engine_label}，检测到 403 / Forbidden，准备重连...")
                        continue
                    break

            if last_error is not None:
                raise RuntimeError(str(last_error))
            raise RuntimeError("下载失败：没有可用的下载引擎。")
        finally:
            self._finish_download_session()

    def _candidate_download_engines(self) -> list[str]:
        if self.download_engine_mode == "cli":
            if not self.yt_dlp_path:
                raise RuntimeError("当前未检测到 yt-dlp.exe，无法使用 CLI 模式下载。")
            return ["cli", "python"]
        if self.download_engine_mode == "python":
            return ["python"]
        engines: list[str] = []
        if self.yt_dlp_path:
            engines.append("cli")
        engines.append("python")
        return engines

    def _engine_label(self, engine: str) -> str:
        return "CLI" if engine == "cli" else "Python/helper"

    def _cli_base_command(self) -> list[str]:
        if not self.yt_dlp_path:
            raise DownloadFailure("当前未检测到 yt-dlp.exe，无法使用 CLI 下载。")
        command = [
            self.yt_dlp_path,
            "--ignore-config",
            "--no-playlist",
            "--newline",
            "--windows-filenames",
            "--ffmpeg-location",
            self.ffmpeg_location,
        ]
        if self.proxy:
            command.extend(["--proxy", self.proxy])
        cookie_file, cookie_browser = self._cookie_args()
        if cookie_file:
            command.extend(["--cookies", cookie_file])
        elif cookie_browser:
            command.extend(["--cookies-from-browser", cookie_browser])
        if self.js_runtime and self.cli_supports_js_runtimes:
            command.extend(["--js-runtimes", self.js_runtime[0]])
        return command

    def _download_via_cli(
        self,
        url: str,
        format_selector: str,
        output_dir: Path,
        progress_callback: ProgressCallback,
        status_callback: StatusCallback,
        merge_output_format: str | None,
    ) -> None:
        status_callback("启动 CLI：正在启动 yt-dlp.exe ...")
        command = self._cli_base_command()
        command.extend(
            [
                "-o",
                str(output_dir / "%(title)s [%(id)s].%(ext)s"),
                "-f",
                format_selector,
            ]
        )
        if merge_output_format:
            command.extend(["--merge-output-format", merge_output_format])
        command.append(url)

        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        with self._control_lock:
            self._active_process = process
            self._active_engine_name = "CLI"

        status_callback("请求视频源：CLI 已创建下载任务。")
        self._consume_cli_output(process, progress_callback, status_callback)

    def _consume_cli_output(
        self,
        process: subprocess.Popen,
        progress_callback: ProgressCallback,
        status_callback: StatusCallback,
    ) -> None:
        assert process.stdout is not None
        plain_output: list[str] = []
        started_downloading = False

        for raw_line in iter(process.stdout.readline, b""):
            self._wait_if_paused_or_cancelled()
            line = _decode_process_bytes(raw_line).strip()
            if not line:
                continue
            plain_output.append(line)
            lowered = line.lower()
            if "merging formats into" in lowered:
                status_callback("合并音视频：正在调用 FFmpeg 合并文件...")
                continue
            if "has already been downloaded" in lowered:
                progress_callback(100.0, "100.0% | 已存在同名文件 | ETA 0s")
                status_callback("开始传输：文件已存在，yt-dlp 直接跳过。")
                continue
            if line.startswith("[download]") and "destination" in lowered:
                status_callback("开始传输：目标文件已创建，正在接收数据...")
                continue
            if line.startswith("[download]"):
                match = DOWNLOAD_PROGRESS_RE.search(line)
                if match:
                    started_downloading = True
                    progress_callback(
                        float(match.group("percent")),
                        f"{match.group('percent')}% | {match.group('speed').strip()} | ETA {match.group('eta').strip()}",
                    )
                    continue
            if not started_downloading and "request" in lowered:
                status_callback("请求视频源：正在向视频源发起请求...")

        return_code = process.wait()
        detail = "\n".join(plain_output[-10:]).strip()
        if self._cancel_requested:
            raise RuntimeError("下载已取消。")
        if return_code != 0:
            message = detail or f"yt-dlp CLI 退出码：{return_code}"
            raise DownloadFailure(message, retryable=_is_retryable_error(message))

    def _download_via_python_engine(
        self,
        url: str,
        format_selector: str,
        output_dir: Path,
        progress_callback: ProgressCallback,
        status_callback: StatusCallback,
        merge_output_format: str | None,
    ) -> None:
        if self.prefer_helper:
            status_callback("启动 helper：当前环境将通过兼容下载器执行下载。")
            self._download_via_helper(
                url,
                format_selector,
                output_dir,
                progress_callback,
                status_callback,
                merge_output_format,
            )
            return

        cookie_file, cookie_browser = self._cookie_args()
        options = _apply_download_acceleration(
            _base_options(self.ffmpeg_location, self.proxy, cookie_file, cookie_browser)
        )
        options.update(
            {
                "outtmpl": str(output_dir / "%(title)s [%(id)s].%(ext)s"),
                "format": format_selector,
                "progress_hooks": [self._progress_hook(progress_callback, status_callback)],
                "windowsfilenames": True,
                "overwrites": False,
            }
        )
        if merge_output_format:
            options["merge_output_format"] = merge_output_format

        with self._control_lock:
            self._active_process = None
            self._active_engine_name = "Python"

        status_callback(f"启动 Python：已启用加速下载，并发分片 {FAST_CONCURRENT_FRAGMENTS}。")
        status_callback("请求视频源：正在向视频源发起请求...")
        try:
            with YoutubeDL(options) as ydl:
                ydl.download([url])
        except Exception as exc:  # noqa: BLE001
            message = str(exc)
            raise DownloadFailure(message, retryable=_is_retryable_error(message)) from exc

    def _download_via_helper(
        self,
        url: str,
        format_selector: str,
        output_dir: Path,
        progress_callback: ProgressCallback,
        status_callback: StatusCallback,
        merge_output_format: str | None,
    ) -> None:
        if not self.helper_python:
            raise DownloadFailure("当前环境没有可用的兼容解释器。")

        helper_path = Path(__file__).resolve().parent / "helper_cli.py"
        cookie_file, cookie_browser = self._cookie_args()
        command = [
            self.helper_python,
            str(helper_path),
            "download",
            url,
            str(output_dir),
            format_selector,
            merge_output_format or "",
            self.ffmpeg_location,
            self.proxy or "",
            cookie_file or "",
            cookie_browser or "",
        ]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        with self._control_lock:
            self._active_process = process
            self._active_engine_name = "helper"

        status_callback("启动 helper：兼容下载器已启动，正在建立下载任务...")
        assert process.stdout is not None
        plain_output: list[str] = []
        error_text = ""
        for raw_line in iter(process.stdout.readline, b""):
            self._wait_if_paused_or_cancelled()
            line = _decode_process_bytes(raw_line).strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                plain_output.append(line)
                continue
            event = payload.get("event")
            if event == "status":
                status_callback(payload.get("text", ""))
            elif event == "progress":
                progress_callback(float(payload.get("percent", 0.0)), payload.get("text", ""))
            elif event == "error":
                error_text = payload.get("text", "下载失败")

        return_code = process.wait()
        detail = error_text or "\n".join(plain_output[-8:]).strip()
        if self._cancel_requested:
            raise RuntimeError("下载已取消。")
        if return_code != 0:
            detail = detail or f"兼容解析器退出码：{return_code}"
            raise DownloadFailure(detail, retryable=_is_retryable_error(detail))
