import json
import subprocess
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from django.conf import settings


class MediaProbeError(Exception):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class MediaMetadata:
    duration_seconds: Decimal | None
    width: int | None
    height: int | None
    frame_rate: Decimal | None
    has_audio: bool
    has_video: bool
    detected_mime_type: str
    container: str
    video_codec: str
    audio_codec: str


FORMAT_MIME_TYPES = {
    "aac": "audio/aac",
    "image2": "image/*",
    "matroska": "video/x-matroska",
    "mov": "video/quicktime",
    "mp3": "audio/mpeg",
    "mp4": "video/mp4",
    "wav": "audio/wav",
    "webm": "video/webm",
}

EXTENSION_FORMATS = {
    ".aac": {"aac"},
    ".jpeg": {"image2"},
    ".jpg": {"image2"},
    ".m4a": {"mov", "mp4", "m4a", "3gp", "3g2", "mj2"},
    ".mkv": {"matroska", "webm"},
    ".mov": {"mov", "mp4", "m4a", "3gp", "3g2", "mj2"},
    ".mp3": {"mp3"},
    ".mp4": {"mov", "mp4", "m4a", "3gp", "3g2", "mj2"},
    ".png": {"image2"},
    ".wav": {"wav"},
    ".webm": {"matroska", "webm"},
    ".webp": {"image2"},
}


def _decimal(value):
    if value in (None, "", "N/A"):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise MediaProbeError("invalid_probe_output", "Media metadata is invalid.") from exc
    if not result.is_finite() or result < 0:
        raise MediaProbeError("invalid_probe_output", "Media metadata is invalid.")
    return result


def _frame_rate(value):
    if not value or value in {"0/0", "N/A"}:
        return None
    try:
        numerator, denominator = value.split("/", 1)
        denominator = Decimal(denominator)
        if denominator == 0:
            return None
        result = Decimal(numerator) / denominator
    except (InvalidOperation, ValueError, ZeroDivisionError) as exc:
        raise MediaProbeError("invalid_probe_output", "Media frame rate is invalid.") from exc
    if not result.is_finite() or result < 0:
        raise MediaProbeError("invalid_probe_output", "Media frame rate is invalid.")
    return result


class FFprobeClient:
    def __init__(self, executable=None, timeout_seconds=None, runner=None):
        self.executable = executable or settings.FFPROBE_EXECUTABLE
        self.timeout_seconds = timeout_seconds or settings.FFPROBE_TIMEOUT_SECONDS
        self.runner = runner or subprocess.run

    def _run(self, arguments):
        try:
            return self.runner(
                [self.executable, *arguments],
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
                shell=False,
            )
        except FileNotFoundError as exc:
            raise MediaProbeError("ffprobe_unavailable", "FFprobe is not available.") from exc
        except subprocess.TimeoutExpired as exc:
            raise MediaProbeError("ffprobe_timeout", "Media inspection timed out.") from exc
        except OSError as exc:
            raise MediaProbeError("ffprobe_unavailable", "FFprobe could not be started.") from exc

    def healthcheck(self):
        result = self._run(["-version"])
        if result.returncode != 0:
            raise MediaProbeError("ffprobe_unhealthy", "FFprobe health check failed.")
        first_line = (result.stdout or "").splitlines()
        return first_line[0][:255] if first_line else "ffprobe available"

    def probe(self, local_path, expected_extension):
        result = self._run(
            [
                "-v",
                "error",
                "-show_entries",
                "format=format_name,duration:stream=codec_type,codec_name,width,height,r_frame_rate",
                "-of",
                "json",
                str(local_path),
            ]
        )
        if result.returncode != 0:
            raise MediaProbeError("invalid_media", "The uploaded file is not valid media.")
        try:
            payload = json.loads(result.stdout)
            streams = payload.get("streams", [])
            format_data = payload.get("format", {})
        except (AttributeError, json.JSONDecodeError, TypeError) as exc:
            raise MediaProbeError("invalid_probe_output", "FFprobe returned invalid metadata.") from exc
        if not isinstance(streams, list) or not streams:
            raise MediaProbeError("missing_media_stream", "The file has no audio or video stream.")

        format_names = {
            item.strip().lower()
            for item in str(format_data.get("format_name", "")).split(",")
            if item.strip()
        }
        allowed_formats = EXTENSION_FORMATS.get(expected_extension)
        if not format_names or not allowed_formats or not format_names.intersection(allowed_formats):
            raise MediaProbeError(
                "media_type_mismatch",
                "The detected media container does not match the file extension.",
            )

        videos = [stream for stream in streams if stream.get("codec_type") == "video"]
        audios = [stream for stream in streams if stream.get("codec_type") == "audio"]
        if not videos and not audios:
            raise MediaProbeError("missing_media_stream", "The file has no audio or video stream.")
        video = videos[0] if videos else {}
        audio = audios[0] if audios else {}
        primary_format = sorted(format_names.intersection(allowed_formats))[0]
        mime_type = FORMAT_MIME_TYPES.get(primary_format, "application/octet-stream")
        if expected_extension == ".mp4":
            mime_type = "video/mp4"
        elif expected_extension == ".mov":
            mime_type = "video/quicktime"
        elif expected_extension == ".m4a":
            mime_type = "audio/mp4"
        elif expected_extension == ".png":
            mime_type = "image/png"
        elif expected_extension in {".jpg", ".jpeg"}:
            mime_type = "image/jpeg"
        elif expected_extension == ".webp":
            mime_type = "image/webp"
        elif not videos and mime_type.startswith("video/"):
            mime_type = "audio/mp4" if primary_format in {"mov", "mp4", "m4a"} else mime_type

        width = video.get("width")
        height = video.get("height")
        if width is not None and (not isinstance(width, int) or width <= 0):
            raise MediaProbeError("invalid_probe_output", "Media dimensions are invalid.")
        if height is not None and (not isinstance(height, int) or height <= 0):
            raise MediaProbeError("invalid_probe_output", "Media dimensions are invalid.")
        duration = _decimal(format_data.get("duration"))
        frame_rate = _frame_rate(video.get("r_frame_rate"))
        return MediaMetadata(
            duration_seconds=(
                duration.quantize(Decimal("0.001")) if duration is not None else None
            ),
            width=width,
            height=height,
            frame_rate=(
                frame_rate.quantize(Decimal("0.0001"))
                if frame_rate is not None
                else None
            ),
            has_audio=bool(audios),
            has_video=bool(videos),
            detected_mime_type=mime_type,
            container=",".join(sorted(format_names))[:100],
            video_codec=str(video.get("codec_name", ""))[:100],
            audio_codec=str(audio.get("codec_name", ""))[:100],
        )
