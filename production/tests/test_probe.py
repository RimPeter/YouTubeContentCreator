import subprocess
from types import SimpleNamespace

from django.test import SimpleTestCase

from production.services.probe import FFprobeClient, MediaProbeError


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


class FFprobeClientTests(SimpleTestCase):
    def test_probe_uses_argument_array_and_parses_metadata(self):
        runner = Runner(
            SimpleNamespace(
                returncode=0,
                stdout='{"format":{"format_name":"mov,mp4","duration":"2.5"},'
                '"streams":[{"codec_type":"video","codec_name":"h264",'
                '"width":1280,"height":720,"r_frame_rate":"30000/1001"},'
                '{"codec_type":"audio","codec_name":"aac"}]}',
                stderr="",
            )
        )
        client = FFprobeClient(executable="fixed-ffprobe", timeout_seconds=7, runner=runner)

        metadata = client.probe("safe-local-file.mp4", ".mp4")

        arguments, kwargs = runner.calls[0]
        self.assertEqual(arguments[0], "fixed-ffprobe")
        self.assertEqual(arguments[-1], "safe-local-file.mp4")
        self.assertIs(kwargs["shell"], False)
        self.assertEqual(kwargs["timeout"], 7)
        self.assertTrue(metadata.has_video)
        self.assertTrue(metadata.has_audio)
        self.assertEqual(metadata.width, 1280)
        self.assertEqual(metadata.detected_mime_type, "video/mp4")

    def test_probe_rejects_extension_container_mismatch(self):
        runner = Runner(
            SimpleNamespace(
                returncode=0,
                stdout='{"format":{"format_name":"mp3","duration":"2"},'
                '"streams":[{"codec_type":"audio","codec_name":"mp3"}]}',
                stderr="",
            )
        )
        with self.assertRaises(MediaProbeError) as caught:
            FFprobeClient(runner=runner).probe("file.mp4", ".mp4")
        self.assertEqual(caught.exception.code, "media_type_mismatch")

    def test_probe_rejects_invalid_json_and_missing_streams(self):
        invalid_json = Runner(SimpleNamespace(returncode=0, stdout="not-json", stderr=""))
        with self.assertRaises(MediaProbeError) as caught:
            FFprobeClient(runner=invalid_json).probe("file.mp4", ".mp4")
        self.assertEqual(caught.exception.code, "invalid_probe_output")

        no_streams = Runner(
            SimpleNamespace(
                returncode=0,
                stdout='{"format":{"format_name":"mov,mp4"},"streams":[]}',
                stderr="",
            )
        )
        with self.assertRaises(MediaProbeError) as caught:
            FFprobeClient(runner=no_streams).probe("file.mp4", ".mp4")
        self.assertEqual(caught.exception.code, "missing_media_stream")

    def test_unavailable_and_timeout_are_sanitized(self):
        unavailable = Runner(error=FileNotFoundError("sensitive path"))
        with self.assertRaises(MediaProbeError) as caught:
            FFprobeClient(runner=unavailable).healthcheck()
        self.assertEqual(caught.exception.code, "ffprobe_unavailable")
        self.assertNotIn("sensitive", str(caught.exception))

        timeout = Runner(error=subprocess.TimeoutExpired("secret command", 1))
        with self.assertRaises(MediaProbeError) as caught:
            FFprobeClient(runner=timeout).healthcheck()
        self.assertEqual(caught.exception.code, "ffprobe_timeout")

    def test_healthcheck_reports_version(self):
        runner = Runner(
            SimpleNamespace(returncode=0, stdout="ffprobe version test\nmore", stderr="")
        )
        self.assertEqual(
            FFprobeClient(runner=runner).healthcheck(),
            "ffprobe version test",
        )
