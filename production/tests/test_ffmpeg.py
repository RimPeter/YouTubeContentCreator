import subprocess
from decimal import Decimal
from types import SimpleNamespace

from django.test import SimpleTestCase

from production.services.ffmpeg import FFmpegClient, FFmpegError


class Runner:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def __call__(self, arguments, **kwargs):
        self.calls.append((arguments, kwargs))
        if self.error:
            raise self.error
        return self.result


class FFmpegClientTests(SimpleTestCase):
    def test_trim_uses_fixed_argument_array_and_frame_accurate_encoding(self):
        runner = Runner(SimpleNamespace(returncode=0, stdout="", stderr=""))
        client = FFmpegClient(executable="fixed-ffmpeg", timeout_seconds=9, runner=runner)

        client.trim(
            "input;not-a-command.mp4",
            "output.mp4",
            Decimal("1.250"),
            Decimal("3.750"),
        )

        arguments, kwargs = runner.calls[0]
        self.assertEqual(arguments[0], "fixed-ffmpeg")
        self.assertIn("input;not-a-command.mp4", arguments)
        self.assertIn("libx264", arguments)
        self.assertIn("0:a:0?", arguments)
        self.assertIs(kwargs["shell"], False)
        self.assertEqual(kwargs["timeout"], 9)

    def test_trim_and_health_failures_are_sanitized(self):
        failed = Runner(SimpleNamespace(returncode=1, stdout="", stderr="secret path"))
        with self.assertRaises(FFmpegError) as caught:
            FFmpegClient(runner=failed).trim(
                "input.mp4", "output.mp4", Decimal("0"), Decimal("1")
            )
        self.assertEqual(caught.exception.code, "clip_trim_failed")
        self.assertNotIn("secret", str(caught.exception))

        timeout = Runner(error=subprocess.TimeoutExpired("secret command", 1))
        with self.assertRaises(FFmpegError) as caught:
            FFmpegClient(runner=timeout).healthcheck()
        self.assertEqual(caught.exception.code, "ffmpeg_timeout")

    def test_healthcheck_reports_version(self):
        runner = Runner(
            SimpleNamespace(returncode=0, stdout="ffmpeg version test\nmore", stderr="")
        )
        self.assertEqual(FFmpegClient(runner=runner).healthcheck(), "ffmpeg version test")
