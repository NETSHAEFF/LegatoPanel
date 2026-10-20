import os
import sys
import time
import uuid
import random
import shutil
import asyncio
import subprocess
import re
import math
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, Optional, Callable, Tuple, List
import ctypes
from ctypes import wintypes

from .config import BASE_DIR, UPLOADS_DIR
from .storage import get_processing_settings, add_log

# Fonts directories
FONTS_DIR = BASE_DIR / "backend" / "fonts"
DEFAULT_BUNDLED_FONT = FONTS_DIR / "default.ttf"
WINDOWS_FONTS_DIR = Path("C:/Windows/Fonts")

LINUX_FONT_DIRS = [
    Path("/usr/share/fonts"),
    Path("/usr/local/share/fonts"),
    Path("~/.local/share/fonts").expanduser(),
    Path("~/.fonts").expanduser(),
]

# Linux font fallback mapping when Windows fonts like Arial or Impact are requested
LINUX_FONT_FALLBACKS = {
    "arial.ttf": ["LiberationSans-Regular.ttf", "DejaVuSans.ttf", "FreeSans.ttf"],
    "arialbd.ttf": ["LiberationSans-Bold.ttf", "DejaVuSans-Bold.ttf", "FreeSansBold.ttf"],
    "impact.ttf": ["Impact.ttf", "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf"],
    "segoeui.ttf": ["LiberationSans-Regular.ttf", "DejaVuSans.ttf"],
    "segoeuib.ttf": ["LiberationSans-Bold.ttf", "DejaVuSans-Bold.ttf"],
    "tahoma.ttf": ["DejaVuSans.ttf", "LiberationSans-Regular.ttf"],
    "trebuc.ttf": ["LiberationSans-Regular.ttf", "DejaVuSans.ttf"],
    "calibri.ttf": ["LiberationSans-Regular.ttf", "DejaVuSans.ttf"],
    "calibrib.ttf": ["LiberationSans-Bold.ttf", "DejaVuSans-Bold.ttf"],
    "times.ttf": ["LiberationSerif-Regular.ttf", "DejaVuSerif.ttf", "FreeSerif.ttf"],
    "georgia.ttf": ["LiberationSerif-Regular.ttf", "DejaVuSerif.ttf"],
    "comic.ttf": ["ComicSansMS.ttf", "DejaVuSans.ttf"],
    "bahnschrift.ttf": ["LiberationSans-Regular.ttf", "DejaVuSans.ttf"],
}

# Curated list of high-quality fonts on Windows
POPULAR_FONTS: List[Dict[str, str]] = [
    {"id": "arial.ttf", "name": "Arial (Стандартный без засечек)"},
    {"id": "arialbd.ttf", "name": "Arial Bold (Жирный акцентный)"},
    {"id": "impact.ttf", "name": "Impact (Крупный мемный / заметный)"},
    {"id": "segoeui.ttf", "name": "Segoe UI (Современный минимализм)"},
    {"id": "segoeuib.ttf", "name": "Segoe UI Bold (Современный жирный)"},
    {"id": "tahoma.ttf", "name": "Tahoma (Четкий и читаемый)"},
    {"id": "trebuc.ttf", "name": "Trebuchet MS (Стильный Sans)"},
    {"id": "calibri.ttf", "name": "Calibri (Мягкий универсальный)"},
    {"id": "calibrib.ttf", "name": "Calibri Bold (Мягкий жирный)"},
    {"id": "times.ttf", "name": "Times New Roman (Классический с засечками)"},
    {"id": "georgia.ttf", "name": "Georgia (Элегантный Serif)"},
    {"id": "comic.ttf", "name": "Comic Sans MS (Неформальный игривый)"},
    {"id": "bahnschrift.ttf", "name": "Bahnschrift (Геометрический гротеск)"},
]

# Smartphone / Camera profiles for metadata spoofing
DEVICE_PROFILES: List[Dict[str, str]] = [
    {"id": "iphone15", "make": "Apple", "model": "iPhone 15 Pro", "software": "17.4.1", "handler": "Core Media Video"},
    {"id": "iphone14", "make": "Apple", "model": "iPhone 14 Pro Max", "software": "16.6.1", "handler": "Core Media Video"},
    {"id": "s24", "make": "Samsung", "model": "SM-S928B (Galaxy S24 Ultra)", "software": "OneUI 6.1", "handler": "VideoHandle"},
    {"id": "s23", "make": "Samsung", "model": "SM-S911B (Galaxy S23)", "software": "OneUI 6.0", "handler": "VideoHandle"},
    {"id": "pixel8", "make": "Google", "model": "Pixel 8 Pro", "software": "Android 14", "handler": "VideoHandler"},
    {"id": "xiaomi14", "make": "Xiaomi", "model": "23127PN0CG (Xiaomi 14)", "software": "HyperOS 1.0", "handler": "VideoHandler"},
    {"id": "sony_a7", "make": "Sony", "model": "ILCE-7M4 (A7 IV)", "software": "Ver.3.00", "handler": "Sony Video"},
]

def find_ffmpeg() -> str:
    """Finds FFmpeg executable cross-platform (Ubuntu / Linux / Windows)."""
    # 1. System PATH (standard on Ubuntu: /usr/bin/ffmpeg)
    which_ffmpeg = shutil.which("ffmpeg")
    if which_ffmpeg:
        return which_ffmpeg

    # 2. Local project binaries and standard OS paths
    candidates = [
        BASE_DIR / "ffmpeg",
        BASE_DIR / "ffmpeg.exe",
        BASE_DIR / "backend" / "bin" / "ffmpeg",
        BASE_DIR / "backend" / "bin" / "ffmpeg.exe",
        Path("/usr/bin/ffmpeg"),
        Path("/usr/local/bin/ffmpeg"),
        Path("C:/Coding/LegatoPanel/ffmpeg.exe"),
        Path("C:/Coding/MetaChandger/ffmpeg.exe"),
        Path("C:/Coding/Poster/dist/ffmpeg.exe"),
    ]
    for c in candidates:
        if c.is_file():
            if sys.platform != "win32":
                try:
                    os.chmod(c, 0o755)
                except Exception:
                    pass
            return str(c.resolve())

    # 3. imageio_ffmpeg fallback
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.exists(exe):
            return exe
    except ImportError:
        pass

    raise FileNotFoundError(
        "FFmpeg не найден! На Ubuntu установите его командой: sudo apt-get install -y ffmpeg"
    )

def probe_video(ffmpeg_path: str, video_path: Path) -> Dict[str, Any]:
    """Extracts duration, width, height, sample rate, audio presence via ffmpeg -i."""
    cmd = [ffmpeg_path, "-hide_banner", "-i", str(video_path)]
    result = subprocess.run(cmd, stderr=subprocess.PIPE, stdout=subprocess.PIPE, text=True, errors="ignore")
    output = result.stderr

    duration = 0.0
    width = 0
    height = 0
    has_audio = False
    sample_rate = 44100
    fps = 30.0

    dur_match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", output)
    if dur_match:
        hours = float(dur_match.group(1))
        minutes = float(dur_match.group(2))
        seconds = float(dur_match.group(3))
        duration = hours * 3600 + minutes * 60 + seconds

    stream_match = re.search(r"Video:.*,\s*(\d{2,5})x(\d{2,5})", output)
    if stream_match:
        width = int(stream_match.group(1))
        height = int(stream_match.group(2))

    fps_match = re.search(r"(\d+(?:\.\d+)?)\s*fps", output)
    if fps_match:
        try:
            fps = float(fps_match.group(1))
        except ValueError:
            pass

    if "Audio:" in output:
        has_audio = True
        sr_match = re.search(r"Audio:.*,\s*(\d{4,6})\s*Hz", output)
        if sr_match:
            sample_rate = int(sr_match.group(1))

    return {
        "duration": duration,
        "width": width,
        "height": height,
        "fps": fps,
        "has_audio": has_audio,
        "sample_rate": sample_rate,
    }

def get_font_file_path(font_name: str) -> str:
    """
    Resolves font file path cross-platform (Windows & Linux/Ubuntu):
    1. Bundled project fonts in backend/fonts/
    2. Windows C:/Windows/Fonts if running on Windows
    3. Linux /usr/share/fonts (direct or fallback equivalents like DejaVu, Liberation)
    4. Guaranteed fallback to backend/fonts/default.ttf
    """
    target = font_name if font_name.endswith(".ttf") or font_name.endswith(".otf") else f"{font_name}.ttf"

    # 1. Check local bundled font in backend/fonts/
    bundled_target = FONTS_DIR / target
    if bundled_target.is_file():
        return str(bundled_target.resolve())

    # 2. On Windows: check Windows Fonts directory
    if sys.platform == "win32" and WINDOWS_FONTS_DIR.is_dir():
        win_font = WINDOWS_FONTS_DIR / target
        if win_font.is_file():
            return str(win_font)
        arial = WINDOWS_FONTS_DIR / "arial.ttf"
        if arial.is_file():
            return str(arial)

    # 3. On Linux/Ubuntu: check system font directories
    for fdir in LINUX_FONT_DIRS:
        if not fdir.is_dir():
            continue
        candidate = fdir / target
        if candidate.is_file():
            return str(candidate)

        try:
            for p in fdir.rglob("*.ttf"):
                if p.name.lower() == target.lower():
                    return str(p)
        except Exception:
            pass

        # Check known Linux fallbacks for requested font
        fallbacks = LINUX_FONT_FALLBACKS.get(target.lower(), [])
        for fb in fallbacks:
            try:
                for p in fdir.rglob(fb):
                    if p.is_file():
                        return str(p)
            except Exception:
                pass

    # 4. Fallback to guaranteed embedded default font
    if DEFAULT_BUNDLED_FONT.is_file():
        return str(DEFAULT_BUNDLED_FONT.resolve())

    return target

def escape_ffmpeg_path(path_str: str) -> str:
    """Escapes Windows and Linux paths for FFmpeg filter arguments."""
    p = str(Path(path_str).resolve()).replace("\\", "/")
    p = p.replace(":", r"\:")
    p = p.replace("'", r"\'")
    return p

def hex_to_ffmpeg_color(hex_str: str, alpha: float = 1.0) -> str:
    """
    Converts '#ffffff' or 'white' + alpha 0.85 -> '0xFFFFFF@0.85'.
    """
    s = hex_str.strip().lstrip("#").upper()
    if len(s) == 3:
        s = "".join([c * 2 for c in s])
    if re.fullmatch(r"[0-9A-F]{6}", s):
        clamped_alpha = max(0.0, min(1.0, float(alpha)))
        return f"0x{s}@{clamped_alpha:.2f}"
    
    # Fallback standard colors
    named_map = {
        "WHITE": "0xFFFFFF",
        "BLACK": "0x000000",
        "RED": "0xFF0000",
        "GREEN": "0x00FF00",
        "BLUE": "0x0000FF",
        "YELLOW": "0xFFFF00",
        "CYAN": "0x00FFFF",
    }
    hex_code = named_map.get(s, "0xFFFFFF")
    clamped_alpha = max(0.0, min(1.0, float(alpha)))
    return f"{hex_code}@{clamped_alpha:.2f}"

def set_file_timestamps(filepath: Path, target_time: datetime):
    """Sets file creation, access, modification timestamps cross-platform (Linux & Windows)."""
    try:
        # Cross-platform: sets mtime and atime
        ts = target_time.timestamp()
        os.utime(filepath, (ts, ts))

        # Windows-specific: also sets Win32 CreationTime
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes
            FILE_WRITE_ATTRIBUTES = 0x0100
            OPEN_EXISTING = 3
            FILE_FLAG_BACKUP_SEMANTICS = 0x02000000

            CreateFileW = ctypes.windll.kernel32.CreateFileW
            SetFileTime = ctypes.windll.kernel32.SetFileTime
            CloseHandle = ctypes.windll.kernel32.CloseHandle

            EPOCH_DIFF = 116444736000000000
            filetime_val = int(ts * 10000000) + EPOCH_DIFF
            low = filetime_val & 0xFFFFFFFF
            high = (filetime_val >> 32) & 0xFFFFFFFF
            filetime = wintypes.FILETIME(low, high)

            handle = CreateFileW(
                str(filepath),
                FILE_WRITE_ATTRIBUTES,
                0,
                None,
                OPEN_EXISTING,
                FILE_FLAG_BACKUP_SEMANTICS,
                None,
            )
            if handle and handle != wintypes.HANDLE(-1).value:
                SetFileTime(handle, ctypes.byref(filetime), ctypes.byref(filetime), ctypes.byref(filetime))
                CloseHandle(handle)
    except Exception:
        pass

def compute_video_phash(ffmpeg_path: str, video_path: Path, sample_time: float = 1.0) -> str:
    """Calculates 64-bit DCT pHash for verification."""
    try:
        cmd = [
            ffmpeg_path, "-hide_banner", "-loglevel", "error",
            "-ss", f"{sample_time:.2f}",
            "-i", str(video_path),
            "-vframes", "1",
            "-f", "rawvideo",
            "-pix_fmt", "gray",
            "-s", "32x32",
            "pipe:1"
        ]
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
        if len(p.stdout) < 1024:
            return ""
        pixels = [p.stdout[i * 32:(i + 1) * 32] for i in range(32)]
        dct = [[0.0] * 8 for _ in range(8)]
        for u in range(8):
            cu = 1.0 / math.sqrt(2) if u == 0 else 1.0
            for v in range(8):
                cv = 1.0 / math.sqrt(2) if v == 0 else 1.0
                s = 0.0
                for x in range(32):
                    for y in range(32):
                        s += pixels[x][y] * math.cos((2 * x + 1) * u * math.pi / 64.0) * math.cos((2 * y + 1) * v * math.pi / 64.0)
                dct[u][v] = 0.25 * cu * cv * s
        vals = [dct[u][v] for u in range(8) for v in range(8) if not (u == 0 and v == 0)]
        med = sorted(vals)[len(vals) // 2]
        bits = 0
        for val in vals:
            bits = (bits << 1) | (1 if val > med else 0)
        return hex(bits)[2:].zfill(16)
    except Exception:
        return ""

def compute_hamming_distance(hash1: str, hash2: str) -> Tuple[int, float]:
    """Returns Hamming distance and percentage difference."""
    if not hash1 or not hash2 or len(hash1) != len(hash2):
        return 0, 0.0
    try:
        val1 = int(hash1, 16)
        val2 = int(hash2, 16)
        dist = bin(val1 ^ val2).count("1")
        total_bits = len(hash1) * 4
        pct = (dist / total_bits) * 100.0
        return dist, pct
    except Exception:
        return 0, 0.0

def build_watermark_filter(
    settings: Dict[str, Any],
    title: str = "",
    username: str = "",
    temp_dir: Optional[Path] = None
) -> Tuple[Optional[str], Optional[Path]]:
    """
    Builds the FFmpeg drawtext filter string for the watermark.
    Returns (filter_string, temp_text_file_path).
    """
    if not settings.get("watermark_enabled", True):
        return None, None

    raw_text = settings.get("watermark_text", "@wvids").strip()
    if not raw_text:
        return None, None

    # Replace dynamic tags
    display_user = f"@{username.lstrip('@')}" if username else "@wvids"
    now_date = datetime.now().strftime("%Y-%m-%d")
    final_text = (
        raw_text
        .replace("{username}", display_user)
        .replace("{channel}", display_user)
        .replace("{title}", title or "")
        .replace("{date}", now_date)
    )

    # Write text to a temporary UTF-8 file for safe FFmpeg consumption
    target_dir = temp_dir or UPLOADS_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    temp_txt = target_dir / f"wm_text_{uuid.uuid4().hex[:8]}.txt"
    with open(temp_txt, "w", encoding="utf-8") as f:
        f.write(final_text)

    font_path = get_font_file_path(settings.get("watermark_font", "arial.ttf"))
    escaped_font = escape_ffmpeg_path(font_path)
    escaped_txt = escape_ffmpeg_path(str(temp_txt))

    fontsize = int(settings.get("watermark_size", 36))
    fontcolor = hex_to_ffmpeg_color(
        settings.get("watermark_color", "#FFFFFF"),
        float(settings.get("watermark_opacity", 0.9))
    )

    # Position calculations
    pos = settings.get("watermark_position", "bottom_right").lower()
    mx = int(settings.get("watermark_margin_x", 30))
    my = int(settings.get("watermark_margin_y", 30))

    if pos == "bottom_right":
        x_expr = f"w-text_w-{mx}"
        y_expr = f"h-text_h-{my}"
    elif pos == "bottom_left":
        x_expr = f"{mx}"
        y_expr = f"h-text_h-{my}"
    elif pos == "top_right":
        x_expr = f"w-text_w-{mx}"
        y_expr = f"{my}"
    elif pos == "top_left":
        x_expr = f"{mx}"
        y_expr = f"{my}"
    elif pos == "center":
        x_expr = "(w-text_w)/2"
        y_expr = "(h-text_h)/2"
    elif pos == "bottom_center":
        x_expr = "(w-text_w)/2"
        y_expr = f"h-text_h-{my}"
    elif pos == "top_center":
        x_expr = "(w-text_w)/2"
        y_expr = f"{my}"
    else:
        x_expr = f"w-text_w-{mx}"
        y_expr = f"h-text_h-{my}"

    parts = [
        f"fontfile='{escaped_font}'",
        f"textfile='{escaped_txt}'",
        f"fontsize={fontsize}",
        f"fontcolor={fontcolor}",
        f"x={x_expr}",
        f"y={y_expr}",
    ]

    # Background box settings
    if settings.get("watermark_box_enabled", True):
        box_color = hex_to_ffmpeg_color(
            settings.get("watermark_box_color", "#000000"),
            float(settings.get("watermark_box_opacity", 0.55))
        )
        box_border = max(1, int(settings.get("watermark_box_border", 10)))
        parts.append("box=1")
        parts.append(f"boxcolor={box_color}")
        parts.append(f"boxborderw={box_border}")
    else:
        parts.append("box=0")

    # Timing: when watermark appears
    start_t = float(settings.get("watermark_start_time", 0.0))
    dur_t = float(settings.get("watermark_duration", 0.0))
    if start_t > 0 and dur_t > 0:
        parts.append(f"enable='between(t,{start_t:.2f},{start_t + dur_t:.2f})'")
    elif start_t > 0:
        parts.append(f"enable='gte(t,{start_t:.2f})'")
    elif dur_t > 0:
        parts.append(f"enable='between(t,0,{dur_t:.2f})'")

    filter_str = f"drawtext={':'.join(parts)}"
    return filter_str, temp_txt

def build_media_watermark_filter(
    settings: Dict[str, Any],
    video_w: int,
    video_h: int
) -> Tuple[Optional[str], Optional[Dict[str, Any]], bool]:
    """
    Builds overlay filter configuration for photo/video watermark.
    Returns (media_path, config_dict, is_video).
    """
    media_path_str = settings.get("watermark_media_path", "").strip()
    if not media_path_str:
        return None, None, False

    media_p = Path(media_path_str)
    if not media_p.is_file():
        return None, None, False

    ext = media_p.suffix.lower()
    is_video = ext in [".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v"]

    scale_pct = float(settings.get("watermark_media_scale", 18))
    target_w = max(32, int(video_w * (scale_pct / 100.0)))
    target_w -= (target_w % 2)

    opacity = max(0.05, min(1.0, float(settings.get("watermark_media_opacity", 0.85))))

    pos = settings.get("watermark_media_position", "top_right").lower()
    mx = int(settings.get("watermark_media_margin_x", 30))
    my = int(settings.get("watermark_media_margin_y", 30))

    if pos == "bottom_right":
        x_expr = f"main_w-overlay_w-{mx}"
        y_expr = f"main_h-overlay_h-{my}"
    elif pos == "bottom_left":
        x_expr = f"{mx}"
        y_expr = f"main_h-overlay_h-{my}"
    elif pos == "top_right":
        x_expr = f"main_w-overlay_w-{mx}"
        y_expr = f"{my}"
    elif pos == "top_left":
        x_expr = f"{mx}"
        y_expr = f"{my}"
    elif pos == "center":
        x_expr = "(main_w-overlay_w)/2"
        y_expr = "(main_h-overlay_h)/2"
    elif pos == "bottom_center":
        x_expr = "(main_w-overlay_w)/2"
        y_expr = f"main_h-overlay_h-{my}"
    elif pos == "top_center":
        x_expr = "(main_w-overlay_w)/2"
        y_expr = f"{my}"
    else:
        x_expr = f"main_w-overlay_w-{mx}"
        y_expr = f"{my}"

    start_t = float(settings.get("watermark_media_start_time", 2.0))
    dur_t = float(settings.get("watermark_media_duration", 0.0))

    enable_expr = ""
    if start_t > 0 and dur_t > 0:
        enable_expr = f":enable='between(t,{start_t:.2f},{start_t + dur_t:.2f})'"
    elif start_t > 0:
        enable_expr = f":enable='gte(t,{start_t:.2f})'"
    elif dur_t > 0:
        enable_expr = f":enable='between(t,0,{dur_t:.2f})'"

    return str(media_p.resolve()), {
        "target_w": target_w,
        "opacity": opacity,
        "x_expr": x_expr,
        "y_expr": y_expr,
        "enable_expr": enable_expr,
        "is_video": is_video
    }, is_video

async def process_video_if_enabled(
    input_file: str,
    title: str = "",
    username: str = "",
    progress_callback: Optional[Callable[[str, int], None]] = None
) -> Dict[str, Any]:
    """
    Main automated pipeline:
    1. Reads watermark & uniqueizer settings.
    2. Burns watermark onto video frames FIRST (before uniqueization).
    3. Executes uniqueization:
       - Duration modification (add seconds / trim seconds / micro-speed)
       - Perceptual hash shift (micro zoom, rotation, vignette, gamma)
       - Dynamic noise
       - Audio pitch shift & 3-band EQ
       - Metadata stripping & device profile spoofing
       - Windows file time timestamps
    4. Outputs new uniqueized file for upload.
    """
    def report(msg: str, pct: int):
        if progress_callback:
            progress_callback(msg, pct)

    settings = get_processing_settings()
    wm_enabled = bool(settings.get("watermark_enabled", True))
    wm_mode = settings.get("watermark_mode", "text")
    uniq_enabled = bool(settings.get("unique_enabled", True))

    input_p = Path(input_file).resolve()
    if not input_p.is_file():
        return {"processed": False, "error": f"Файл не найден: {input_file}"}

    # Evaluate active watermark components
    if not wm_enabled or wm_mode == "none":
        has_text_wm = False
        has_media_wm = False
    else:
        has_text_wm = wm_mode in ("both", "text")
        has_media_wm = wm_mode in ("both", "media")

    # If both features disabled, skip processing
    if not has_text_wm and not has_media_wm and not uniq_enabled:
        return {"processed": False, "reason": "Both watermark and uniqueizer disabled"}

    ffmpeg_bin = find_ffmpeg()
    info = probe_video(ffmpeg_bin, input_p)
    w, h, duration = info["width"], info["height"], info["duration"]

    if w == 0 or h == 0:
        add_log("WARNING", f"Не удалось определить разрешение {input_p.name}, пропускаем уникализацию")
        return {"processed": False, "reason": "Unknown video dimensions"}

    report("Подготовка фильтров обработки видео (Водяной знак + Уникализация)...", 5)

    # 1. WATERMARKS (Burned in BEFORE uniqueization)
    media_path = None
    med_cfg = None
    is_video_wm = False
    if has_media_wm:
        media_path, med_cfg, is_video_wm = build_media_watermark_filter(settings, w, h)
        if not media_path:
            has_media_wm = False

    wm_filter = None
    temp_txt_file = None
    if has_text_wm:
        wm_filter, temp_txt_file = build_watermark_filter(
            settings=settings,
            title=title,
            username=username,
            temp_dir=UPLOADS_DIR
        )
        if not wm_filter:
            has_text_wm = False

    # 2. UNIQUEIZATION FILTERS
    unique_video_filters: List[str] = []
    audio_filters: List[str] = []
    duration_args: List[str] = []
    dur_mode = settings.get("unique_duration_mode", "random")
    delta_sec = float(settings.get("unique_duration_delta", 1.0))
    duration_changed_desc = "без изменений"

    if uniq_enabled:
        # Determine duration action: "add", "trim", "speed"
        active_dur_mode = dur_mode
        if active_dur_mode == "random":
            # If video is very short (< 3.5s), favor adding seconds or micro-speed
            if duration < 3.5:
                active_dur_mode = random.choice(["add", "speed"])
            else:
                active_dur_mode = random.choice(["add", "trim", "speed"])

        # a) Add extra seconds (freeze-frame clone at the end + audio pad)
        if active_dur_mode == "add":
            pad_s = round(random.uniform(max(0.4, delta_sec * 0.7), delta_sec * 1.3), 2)
            unique_video_filters.append(f"tpad=stop_mode=clone:stop_duration={pad_s}")
            if info["has_audio"]:
                audio_filters.append(f"apad=pad_dur={pad_s}")
            duration_changed_desc = f"+{pad_s:.2f} сек (добавление хвоста)"

        # b) Reduce / Trim seconds (subtle cut at start/end)
        elif active_dur_mode == "trim":
            if duration > 3.0:
                cut_start = round(random.uniform(0.2, min(1.0, delta_sec * 0.6)), 2)
                cut_end = round(random.uniform(0.2, min(1.0, delta_sec * 0.6)), 2)
                new_dur = max(1.5, duration - cut_start - cut_end)
                duration_args = ["-ss", f"{cut_start:.2f}", "-t", f"{new_dur:.2f}"]
                duration_changed_desc = f"-{cut_start + cut_end:.2f} сек (подрезка {cut_start}s..{cut_end}s)"
            else:
                # If too short to trim safely, micro speedup
                speed = 1.012
                unique_video_filters.append(f"setpts={1.0 / speed:.5f}*PTS")
                if info["has_audio"]:
                    audio_filters.append(f"atempo={speed:.5f}")
                duration_changed_desc = f"уменьшение хронометража (скорость {speed}x)"

        # c) Micro-speed variation
        elif active_dur_mode == "speed":
            speed = round(random.choice([random.uniform(0.985, 0.993), random.uniform(1.008, 1.018)]), 4)
            unique_video_filters.append(f"setpts={1.0 / speed:.5f}*PTS")
            if info["has_audio"]:
                audio_filters.append(f"atempo={speed:.5f}")
            duration_changed_desc = f"сдвиг скорости {speed}x"

        # Perceptual Hash Jitter (Micro-zoom, slight asymmetric stretch, micro-rotation, crop, vignette, gamma)
        if settings.get("unique_visual_hash", True):
            crop_delta_w = random.choice([2, 4, 6])
            crop_delta_h = random.choice([2, 4, 6])
            target_w = (w - crop_delta_w) - ((w - crop_delta_w) % 2)
            target_h = (h - crop_delta_h) - ((h - crop_delta_h) % 2)

            # Micro zoom
            zoom_w = round(random.uniform(1.015, 1.025), 4)
            zoom_h = round(random.uniform(1.012, 1.022), 4)
            scaled_w = int(w * zoom_w) - (int(w * zoom_w) % 2)
            scaled_h = int(h * zoom_h) - (int(w * zoom_h) % 2)
            unique_video_filters.append(f"scale={scaled_w}:{scaled_h}")

            # Micro rotation (0.35°..0.65°)
            angle_deg = round(random.choice([-1, 1]) * random.uniform(0.35, 0.65), 3)
            rad = f"({angle_deg}*PI/180)"
            unique_video_filters.append(f"rotate={rad}:ow=rotw({rad}):oh=roth({rad})")

            # Center crop
            unique_video_filters.append(f"crop={target_w}:{target_h}")

            # Subtle vignette
            v_angle = round(random.uniform(0.18, 0.26), 3)
            unique_video_filters.append(f"vignette=angle={v_angle}")

            # Non-linear gamma & color jitter
            c_adj = round(random.uniform(1.008, 1.022), 3)
            b_adj = round(random.uniform(-0.003, 0.005), 3)
            s_adj = round(random.uniform(0.995, 1.020), 3)
            g_adj = round(random.choice([random.uniform(0.970, 0.985), random.uniform(1.015, 1.030)]), 3)
            unique_video_filters.append(f"eq=contrast={c_adj}:brightness={b_adj}:saturation={s_adj}:gamma={g_adj}")

        # Dynamic sensor noise
        if settings.get("unique_noise", True):
            unique_video_filters.append("noise=alls=2:allf=t")

        # Audio Content ID Disruption
        if info["has_audio"] and settings.get("unique_audio_hash", True):
            sr = info.get("sample_rate", 44100)
            pitch_shift = round(random.choice([random.uniform(0.981, 0.989), random.uniform(1.011, 1.019)]), 4)
            audio_filters.append(f"asetrate={sr}*{pitch_shift},aresample={sr},atempo={1.0 / pitch_shift:.5f}")

            # Parametric 3-band EQ
            f1 = random.randint(280, 420)
            g1 = round(random.choice([-1, 1]) * random.uniform(1.5, 2.3), 1)
            f2 = random.randint(1200, 1600)
            g2 = round(random.choice([-1, 1]) * random.uniform(1.5, 2.3), 1)
            audio_filters.append(f"equalizer=f={f1}:t=q:w=1.2:g={g1}")
            audio_filters.append(f"equalizer=f={f2}:t=q:w=1.5:g={g2}")

            # Phase micro-echo
            delay_ms = random.randint(14, 20)
            decay = round(random.uniform(0.04, 0.06), 2)
            audio_filters.append(f"aecho=0.8:0.88:{delay_ms}:{decay}")

    # Metadata & Smartphone Spoofing
    metadata_args: List[str] = []
    device_id = settings.get("unique_device_profile", "random")
    if device_id == "random":
        chosen_device = random.choice(DEVICE_PROFILES)
    else:
        chosen_device = next((d for d in DEVICE_PROFILES if d["id"] == device_id), DEVICE_PROFILES[0])

    random_days = random.randint(3, 75)
    random_secs = random.randint(0, 86400)
    shoot_date = datetime.now() - timedelta(days=random_days, seconds=random_secs)

    if settings.get("unique_metadata", True):
        metadata_args = [
            "-map_metadata", "-1",  # Strips all previous metadata/EXIF/chapters
            "-metadata", f"make={chosen_device['make']}",
            "-metadata", f"model={chosen_device['model']}",
            "-metadata", f"software={chosen_device['software']}",
            "-metadata:s:v:0", f"handler_name={chosen_device['handler']}",
            "-metadata", f"creation_time={shoot_date.strftime('%Y-%m-%dT%H:%M:%S.000000Z')}",
        ]

    # Output file destination
    unique_suffix = f"proc_{int(time.time())}_{uuid.uuid4().hex[:6]}.mp4"
    output_path = UPLOADS_DIR / unique_suffix

    # Build full FFmpeg command
    cmd: List[str] = [
        ffmpeg_bin,
        "-y",
        "-hide_banner",
        "-loglevel", "error",
    ]

    # Pre-input duration cut (trim) if active
    if duration_args:
        cmd.extend(duration_args)

    cmd.extend(["-i", str(input_p)])

    if has_media_wm and media_path and med_cfg:
        # Multi-input: add second input (media watermark)
        is_loop_media = is_video_wm or Path(media_path).suffix.lower() in [".gif"]
        if is_loop_media:
            cmd.extend(["-stream_loop", "-1", "-i", media_path])
        else:
            cmd.extend(["-loop", "1", "-i", media_path])

        fc_parts = []
        fc_parts.append(
            f"[1:v]scale={med_cfg['target_w']}:-2,format=rgba,colorchannelmixer=aa={med_cfg['opacity']:.2f}[wm_med]"
        )
        fc_parts.append(
            f"[0:v][wm_med]overlay=x={med_cfg['x_expr']}:y={med_cfg['y_expr']}{med_cfg['enable_expr']}:shortest=1[v_overlay]"
        )
        curr_v = "[v_overlay]"

        if has_text_wm and wm_filter:
            fc_parts.append(f"{curr_v}{wm_filter}[v_txt]")
            curr_v = "[v_txt]"

        if unique_video_filters:
            fc_parts.append(f"{curr_v}{','.join(unique_video_filters)}[outv]")
        else:
            fc_parts.append(f"{curr_v}null[outv]")

        cmd.extend(["-filter_complex", ";".join(fc_parts), "-map", "[outv]"])
        if info["has_audio"]:
            if audio_filters:
                cmd.extend(["-af", ",".join(audio_filters)])
            cmd.extend(["-map", "0:a"])
        else:
            cmd.append("-an")

    else:
        # Single input: chain text watermark (if any) and uniqueization filters
        video_filters: List[str] = []
        if has_text_wm and wm_filter:
            video_filters.append(wm_filter)
        if unique_video_filters:
            video_filters.extend(unique_video_filters)

        if video_filters:
            cmd.extend(["-vf", ",".join(video_filters)])

        if info["has_audio"]:
            if audio_filters:
                cmd.extend(["-af", ",".join(audio_filters)])
        else:
            cmd.append("-an")

    # Metadata
    if metadata_args:
        cmd.extend(metadata_args)

    # Encoding configuration
    preset = settings.get("unique_preset", "fast")
    crf = str(settings.get("unique_crf", 20))
    cmd.extend([
        "-c:v", "libx264",
        "-preset", preset,
        "-crf", crf,
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
    ])

    if info["has_audio"]:
        cmd.extend(["-c:a", "aac", "-b:a", "192k"])

    cmd.append(str(output_path))

    report("Рендеринг водяного знака и уникализация видео через FFmpeg...", 12)
    start_t = time.time()

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        _, stderr_data = await proc.communicate()

        if proc.returncode != 0:
            err_msg = stderr_data.decode("utf-8", errors="ignore")
            add_log("ERROR", f"Ошибка FFmpeg при обработке видео: {err_msg[:300]}")
            return {"processed": False, "error": f"FFmpeg failed: {err_msg[:200]}"}

        # Apply cross-platform timestamps (atime/mtime + Windows CreationTime)
        set_file_timestamps(output_path, shoot_date)

        wm_desc_parts = []
        if has_text_wm and wm_filter:
            wm_desc_parts.append("Текст")
        if has_media_wm and media_path:
            wm_desc_parts.append("Медиа (" + ("Видео" if is_video_wm else "Фото") + ")")
        wm_status = " + ".join(wm_desc_parts) if wm_desc_parts else "Выкл"

        elapsed = round(time.time() - start_t, 2)
        details = (
            f"Водяной знак: {wm_status}; "
            f"Хронометраж: {duration_changed_desc}; "
            f"Устройство: {chosen_device['model']}; "
            f"Время обработки: {elapsed}с"
        )
        report(f"Видео готово к выгрузке ({elapsed}с, {duration_changed_desc})", 18)

        return {
            "processed": True,
            "output_path": str(output_path),
            "original_path": str(input_p),
            "duration_change": duration_changed_desc,
            "device": chosen_device["model"],
            "elapsed_seconds": elapsed,
            "details": details
        }

    except Exception as e:
        add_log("ERROR", f"Исключение при уникализации видео: {str(e)}")
        return {"processed": False, "error": str(e)}

    finally:
        # Cleanup temporary watermark text file
        if temp_txt_file and temp_txt_file.exists():
            try:
                temp_txt_file.unlink()
            except Exception:
                pass

async def run_test_processing(video_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Executes a benchmark test run of watermark & uniqueizer with current settings.
    If no video is given, generates a 2.5-second test video.
    Returns before/after metrics (duration, pHash distance, size, processing time).
    """
    ffmpeg_bin = find_ffmpeg()
    temp_test_input = None

    if video_path and Path(video_path).is_file():
        test_file = Path(video_path)
    else:
        # Generate synthetic 2.5s clip with video + sine audio
        temp_test_input = UPLOADS_DIR / f"test_sample_{uuid.uuid4().hex[:6]}.mp4"
        gen_cmd = [
            ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=size=640x360:rate=25",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=2.5",
            "-t", "2.5",
            "-c:v", "libx264", "-preset", "ultrafast",
            "-c:a", "aac",
            str(temp_test_input)
        ]
        proc = await asyncio.create_subprocess_exec(*gen_cmd)
        await proc.communicate()
        test_file = temp_test_input

    orig_info = probe_video(ffmpeg_bin, test_file)
    orig_phash = compute_video_phash(ffmpeg_bin, test_file, sample_time=0.5)

    start_t = time.time()
    res = await process_video_if_enabled(
        input_file=str(test_file),
        title="TestVideo",
        username="preview_user"
    )
    elapsed = round(time.time() - start_t, 2)

    if not res.get("processed"):
        if temp_test_input and temp_test_input.exists():
            temp_test_input.unlink()
        return {"success": False, "error": res.get("error", "Обработка не вернула результат")}

    out_file = Path(res["output_path"])
    new_info = probe_video(ffmpeg_bin, out_file)
    new_phash = compute_video_phash(ffmpeg_bin, out_file, sample_time=0.5)
    dist, pct = compute_hamming_distance(orig_phash, new_phash)

    result_data = {
        "success": True,
        "elapsed_seconds": elapsed,
        "orig_duration": orig_info["duration"],
        "new_duration": new_info["duration"],
        "duration_delta": round(new_info["duration"] - orig_info["duration"], 2),
        "orig_size_bytes": test_file.stat().st_size,
        "new_size_bytes": out_file.stat().st_size,
        "orig_phash": orig_phash,
        "new_phash": new_phash,
        "phash_hamming_distance": dist,
        "phash_difference_pct": round(pct, 1),
        "device_profile": res.get("device", "Unknown"),
        "duration_desc": res.get("duration_change", ""),
        "details": res.get("details", "")
    }

    # Cleanup test files
    if temp_test_input and temp_test_input.exists():
        temp_test_input.unlink()
    if out_file.exists():
        out_file.unlink()

    return result_data
