"""FFmpeg normalize, ken burns, concat, ducking mix and hardware encode."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from core.control import run_process

from config.ffmpeg_presets import (
    BGM_VOLUME,
    DUCK_ATTACK,
    DUCK_RATIO,
    DUCK_RELEASE,
    DUCK_THRESHOLD,
    FPS,
    final_video_args,
    intermediate_video_args,
)
from config.settings import Settings, get_settings

_ENCODER: str | None = None


class MediaError(RuntimeError):
    pass


def run_cmd(cmd: list[str], timeout: float | None = None) -> str:
    try:
        completed = run_process(cmd, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise MediaError(f"命令超时: {cmd[0]}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        tail = detail[-1800:]
        raise MediaError(tail or f"命令失败: {cmd[0]}")
    return completed.stderr or ""


def probe_duration(path: Path, settings: Settings | None = None) -> float:
    cfg = settings or get_settings()
    completed = subprocess.run(
        [
            cfg.ffprobe_bin,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        raise MediaError(completed.stderr[-500:])
    payload = json.loads(completed.stdout or "{}")
    return float((payload.get("format") or {}).get("duration") or 0)


def detect_encoder(settings: Settings | None = None) -> str:
    global _ENCODER
    if _ENCODER:
        return _ENCODER
    cfg = settings or get_settings()
    listed = subprocess.run(
        [cfg.ffmpeg_bin, "-hide_banner", "-encoders"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    blob = listed.stdout or ""
    candidates = ["libx264"]
    if "h264_nvenc" in blob:
        candidates.insert(0, "h264_nvenc")
    if "h264_qsv" in blob:
        candidates.insert(0, "h264_qsv")
    if "h264_amf" in blob:
        candidates.insert(0, "h264_amf")
    if "h264_videotoolbox" in blob:
        candidates.insert(0, "h264_videotoolbox")
    for encoder in candidates:
        try:
            run_cmd(
                [
                    cfg.ffmpeg_bin,
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=black:s=320x180:d=0.2:r=30",
                    *final_video_args(encoder),
                    "-f",
                    "null",
                    "-",
                ],
                timeout=40,
            )
            _ENCODER = encoder
            return encoder
        except MediaError:
            continue
    _ENCODER = "libx264"
    return _ENCODER


def _fit_filter(width: int, height: int, slow: float | None = None) -> str:
    steps = []
    if slow and abs(slow - 1) > 0.03:
        steps.append(f"setpts={slow:.4f}*PTS")
    steps.append(f"scale={width}:{height}:force_original_aspect_ratio=increase")
    steps.append(f"crop={width}:{height}")
    steps.append(f"fps={FPS}")
    steps.append("setsar=1")
    steps.append("format=yuv420p")
    return ",".join(steps)


def normalize_video(
    source: Path,
    dest: Path,
    *,
    width: int,
    height: int,
    duration: float,
    settings: Settings | None = None,
) -> Path:
    cfg = settings or get_settings()
    dest.parent.mkdir(parents=True, exist_ok=True)
    source_duration = probe_duration(source, cfg)
    cmd = [cfg.ffmpeg_bin, "-y"]
    if source_duration + 0.05 >= duration:
        start = min(max(0.0, source_duration - duration) * 0.15, max(0.0, source_duration - duration))
        cmd += ["-ss", f"{start:.3f}", "-i", str(source), "-t", f"{duration:.3f}", "-vf", _fit_filter(width, height)]
    elif source_duration * 1.45 >= duration and source_duration > 0.2:
        slow = duration / source_duration
        cmd += ["-i", str(source), "-t", f"{duration:.3f}", "-vf", _fit_filter(width, height, slow)]
    else:
        cmd += ["-stream_loop", "-1", "-i", str(source), "-t", f"{duration:.3f}", "-vf", _fit_filter(width, height)]
    cmd += [*intermediate_video_args(), "-an", str(dest)]
    run_cmd(cmd, timeout=max(60, duration * 8))
    return dest


def ken_burns(
    image: Path,
    dest: Path,
    *,
    width: int,
    height: int,
    duration: float,
    settings: Settings | None = None,
) -> Path:
    cfg = settings or get_settings()
    dest.parent.mkdir(parents=True, exist_ok=True)
    frames = max(1, int(round(duration * FPS)))
    zoom = (
        f"scale={width * 2}:{height * 2}:force_original_aspect_ratio=increase,crop={width * 2}:{height * 2},"
        f"zoompan=z='1.04+0.14*on/{max(frames - 1, 1)}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"d={frames}:s={width}x{height}:fps={FPS},format=yuv420p"
    )
    try:
        run_cmd(
            [
                cfg.ffmpeg_bin,
                "-y",
                "-loop",
                "1",
                "-i",
                str(image),
                "-vf",
                zoom,
                "-frames:v",
                str(frames),
                "-t",
                f"{duration:.3f}",
                *intermediate_video_args(),
                "-an",
                str(dest),
            ],
            timeout=max(60, duration * 10),
        )
        return dest
    except MediaError:
        pan = (
            f"scale={int(width * 1.25)}:{int(height * 1.25)}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height}:x='(in_w-{width})*t/{max(duration, 0.1)}':y='(in_h-{height})/2',"
            f"fps={FPS},format=yuv420p"
        )
        run_cmd(
            [
                cfg.ffmpeg_bin,
                "-y",
                "-loop",
                "1",
                "-i",
                str(image),
                "-t",
                f"{duration:.3f}",
                "-vf",
                pan,
                *intermediate_video_args(),
                "-an",
                str(dest),
            ],
            timeout=max(60, duration * 8),
        )
        return dest


def concat_videos(paths: list[Path], dest: Path, settings: Settings | None = None) -> Path:
    cfg = settings or get_settings()
    if len(paths) == 1:
        if paths[0].resolve() != dest.resolve():
            shutil.copyfile(paths[0], dest)
        return dest
    listing = dest.with_suffix(".txt")
    lines = [f"file '{path.resolve().as_posix()}'" for path in paths]
    listing.write_text("\n".join(lines), encoding="utf-8")
    run_cmd(
        [cfg.ffmpeg_bin, "-y", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(dest)],
        timeout=120,
    )
    return dest


def concat_audio(paths: list[Path], dest: Path, settings: Settings | None = None) -> Path:
    cfg = settings or get_settings()
    dest.parent.mkdir(parents=True, exist_ok=True)
    if len(paths) == 1:
        run_cmd(
            [cfg.ffmpeg_bin, "-y", "-i", str(paths[0]), "-ar", "48000", "-ac", "2", str(dest)],
            timeout=60,
        )
        return dest
    cmd = [cfg.ffmpeg_bin, "-y"]
    for path in paths:
        cmd += ["-i", str(path)]
    labels = "".join(f"[{index}:a]" for index in range(len(paths)))
    cmd += [
        "-filter_complex",
        f"{labels}concat=n={len(paths)}:v=0:a=1[a]",
        "-map",
        "[a]",
        "-ar",
        "48000",
        "-ac",
        "2",
        str(dest),
    ]
    run_cmd(cmd, timeout=120)
    return dest


def silence(dest: Path, duration: float, settings: Settings | None = None) -> Path:
    cfg = settings or get_settings()
    run_cmd(
        [
            cfg.ffmpeg_bin,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=stereo",
            "-t",
            f"{max(duration, 0.2):.3f}",
            str(dest),
        ],
        timeout=30,
    )
    return dest


def pad_audio(source: Path, dest: Path, duration: float, settings: Settings | None = None) -> Path:
    cfg = settings or get_settings()
    run_cmd(
        [
            cfg.ffmpeg_bin,
            "-y",
            "-i",
            str(source),
            "-af",
            f"apad,atrim=0:{duration:.3f}",
            "-ar",
            "48000",
            "-ac",
            "2",
            str(dest),
        ],
        timeout=60,
    )
    return dest


def extract_audio(
    source: Path,
    dest: Path,
    settings: Settings | None = None,
    *,
    sample_rate: int = 16000,
    channels: int = 1,
) -> Path:
    cfg = settings or get_settings()
    run_cmd(
        [
            cfg.ffmpeg_bin,
            "-y",
            "-i",
            str(source),
            "-vn",
            "-ac",
            str(channels),
            "-ar",
            str(sample_rate),
            str(dest),
        ],
        timeout=180,
    )
    return dest


def fit_audio(source: Path, dest: Path, duration: float, settings: Settings | None = None) -> Path:
    cfg = settings or get_settings()
    length = probe_duration(source, cfg)
    cmd = [cfg.ffmpeg_bin, "-y"]
    if length + 0.05 < duration:
        cmd += ["-stream_loop", "-1"]
    cmd += [
        "-i",
        str(source),
        "-t",
        f"{duration:.3f}",
        "-ar",
        "48000",
        "-ac",
        "2",
        str(dest),
    ]
    run_cmd(cmd, timeout=90)
    return dest


def cut_clip(
    source: Path,
    dest: Path,
    *,
    start: float,
    end: float,
    width: int,
    height: int,
    settings: Settings | None = None,
) -> Path:
    cfg = settings or get_settings()
    duration = max(0.3, end - start)
    run_cmd(
        [
            cfg.ffmpeg_bin,
            "-y",
            "-ss",
            f"{start:.3f}",
            "-t",
            f"{duration:.3f}",
            "-i",
            str(source),
            "-vf",
            _fit_filter(width, height),
            *intermediate_video_args(),
            "-c:a",
            "aac",
            "-ar",
            "48000",
            "-ac",
            "2",
            str(dest),
        ],
        timeout=max(60, duration * 8),
    )
    return dest


def make_bed(dest: Path, duration: float, settings: Settings | None = None) -> Path:
    cfg = settings or get_settings()
    length = max(duration, 1.0)
    fade_out = max(0.0, length - 1.0)
    graph = (
        "[0:a]volume=0.07[a];"
        "[1:a]volume=0.04[b];"
        "[2:a]volume=0.03[c];"
        "[3:a]highpass=f=80,lowpass=f=480,volume=0.2[n];"
        "[a][b][c][n]amix=inputs=4:normalize=0,"
        f"afade=t=in:st=0:d=0.6,afade=t=out:st={fade_out:.3f}:d=0.8[out]"
    )
    run_cmd(
        [
            cfg.ffmpeg_bin,
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=196:duration={length:.3f}:sample_rate=48000",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=247:duration={length:.3f}:sample_rate=48000",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=294:duration={length:.3f}:sample_rate=48000",
            "-f",
            "lavfi",
            "-i",
            f"anoisesrc=color=pink:amplitude=0.12:duration={length:.3f}:sample_rate=48000",
            "-filter_complex",
            graph,
            "-map",
            "[out]",
            "-ac",
            "2",
            str(dest),
        ],
        timeout=60,
    )
    return dest


def speech_regions(source: Path, settings: Settings | None = None) -> list[tuple[float, float]]:
    cfg = settings or get_settings()
    completed = subprocess.run(
        [
            cfg.ffmpeg_bin,
            "-i",
            str(source),
            "-af",
            "silencedetect=noise=-35dB:d=0.45",
            "-f",
            "null",
            "-",
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    text = (completed.stderr or "") + (completed.stdout or "")
    silences: list[tuple[float, float | None]] = []
    current_start: float | None = None
    for line in text.splitlines():
        if "silence_start:" in line:
            current_start = float(line.split("silence_start:")[1].split()[0])
        elif "silence_end:" in line and current_start is not None:
            end = float(line.split("silence_end:")[1].split()[0])
            silences.append((current_start, end))
            current_start = None
    total = probe_duration(source, cfg)
    regions: list[tuple[float, float]] = []
    cursor = 0.0
    for start, end in silences:
        if start - cursor >= 2.4:
            regions.append((cursor, start))
        cursor = end or cursor
    if total - cursor >= 2.4:
        regions.append((cursor, total))
    chunks: list[tuple[float, float]] = []
    for start, end in regions:
        piece = start
        while end - piece > 7.5:
            chunks.append((piece, piece + 6.0))
            piece += 6.0
        if end - piece >= 2.4:
            chunks.append((piece, end))
    return chunks


def composite(
    background: Path,
    narration: Path,
    bgm: Path,
    dest: Path,
    *,
    duration: float,
    width: int,
    height: int,
    overlay: Path | None = None,
    ass_path: Path | None = None,
    settings: Settings | None = None,
) -> Path:
    cfg = settings or get_settings()
    encoder = detect_encoder(cfg)
    fade = max(0.0, duration - 0.7)
    cmd = [cfg.ffmpeg_bin, "-y", "-i", str(background)]
    if overlay and overlay.exists():
        cmd += ["-i", str(overlay)]
        video = (
            f"[1:v]format=yuva420p,scale={width}:{height}[ov];"
            f"[0:v][ov]overlay=0:0:format=auto:eof_action=pass[v];"
        )
        voice_index = 2
    elif ass_path and ass_path.exists():
        escaped = ass_path.resolve().as_posix().replace(":", r"\:").replace("'", r"\'")
        video = f"[0:v]ass='{escaped}'[v];"
        voice_index = 1
    else:
        video = "[0:v]null[v];"
        voice_index = 1
    cmd += ["-i", str(narration), "-i", str(bgm)]
    bed_index = voice_index + 1
    audio = (
        f"[{voice_index}:a]apad,atrim=0:{duration:.3f},asplit=2[voice][sc];"
        f"[{bed_index}:a]volume={BGM_VOLUME},atrim=0:{duration:.3f},"
        f"afade=t=in:st=0:d=0.35,afade=t=out:st={fade:.3f}:d=0.6[bgm];"
        f"[bgm][sc]sidechaincompress=threshold={DUCK_THRESHOLD}:ratio={DUCK_RATIO}:"
        f"attack={DUCK_ATTACK}:release={DUCK_RELEASE}:makeup=1[ducked];"
        f"[voice][ducked]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a]"
    )
    graph = video + audio
    cmd += [
        "-filter_complex",
        graph,
        "-map",
        "[v]",
        "-map",
        "[a]",
        *final_video_args(encoder),
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-t",
        f"{duration:.3f}",
        "-movflags",
        "+faststart",
        str(dest),
    ]
    run_cmd(cmd, timeout=max(180, duration * 20))
    return dest
