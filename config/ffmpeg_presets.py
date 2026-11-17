"""Shared encode settings."""

FPS = 30
INTERMEDIATE_CRF = "20"
FINAL_X264_CRF = "18"
AUDIO_BITRATE = "192k"
BGM_VOLUME = 0.42
DUCK_THRESHOLD = 0.03
DUCK_RATIO = 8
DUCK_ATTACK = 20
DUCK_RELEASE = 350


def frame_size(aspect: str) -> tuple[int, int]:
    if aspect == "16:9":
        return 1920, 1080
    if aspect == "4:3":
        return 1440, 1080
    return 1080, 1920


def intermediate_video_args() -> list[str]:
    return [
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        INTERMEDIATE_CRF,
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(FPS),
    ]


def final_video_args(encoder: str) -> list[str]:
    if encoder == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "23", "-pix_fmt", "yuv420p"]
    if encoder == "h264_qsv":
        return ["-c:v", "h264_qsv", "-global_quality", "23", "-pix_fmt", "yuv420p"]
    if encoder == "h264_amf":
        return ["-c:v", "h264_amf", "-quality", "balanced", "-rc", "cqp", "-qp_i", "22", "-qp_p", "24", "-pix_fmt", "yuv420p"]
    if encoder == "h264_videotoolbox":
        return ["-c:v", "h264_videotoolbox", "-q:v", "60", "-pix_fmt", "yuv420p"]
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", FINAL_X264_CRF, "-pix_fmt", "yuv420p"]
