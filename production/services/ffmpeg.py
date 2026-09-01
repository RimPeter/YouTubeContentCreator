import subprocess

from django.conf import settings


class FFmpegError(Exception):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


class FFmpegClient:
    def __init__(self, executable=None, timeout_seconds=None, runner=None):
        self.executable = executable or settings.FFMPEG_EXECUTABLE
        self.timeout_seconds = timeout_seconds or settings.FFMPEG_TIMEOUT_SECONDS
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
            raise FFmpegError("ffmpeg_unavailable", "FFmpeg is not available.") from exc
        except subprocess.TimeoutExpired as exc:
            raise FFmpegError("ffmpeg_timeout", "Clip processing timed out.") from exc
        except OSError as exc:
            raise FFmpegError("ffmpeg_unavailable", "FFmpeg could not be started.") from exc

    def healthcheck(self):
        result = self._run(["-version"])
        if result.returncode != 0:
            raise FFmpegError("ffmpeg_unhealthy", "FFmpeg health check failed.")
        lines = (result.stdout or "").splitlines()
        return lines[0][:255] if lines else "ffmpeg available"

    def trim(self, input_path, output_path, start_seconds, end_seconds):
        duration = end_seconds - start_seconds
        if start_seconds < 0 or duration <= 0:
            raise FFmpegError("invalid_trim_range", "Clip boundaries are invalid.")
        result = self._run(
            [
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-y",
                "-ss",
                f"{start_seconds:.3f}",
                "-i",
                str(input_path),
                "-t",
                f"{duration:.3f}",
                "-map",
                "0:v:0",
                "-map",
                "0:a:0?",
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "20",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-movflags",
                "+faststart",
                str(output_path),
            ]
        )
        if result.returncode != 0:
            raise FFmpegError("clip_trim_failed", "FFmpeg could not create the source clip.")
