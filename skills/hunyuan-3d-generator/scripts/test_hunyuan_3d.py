"""Offline behavior tests: no credentials, network access, or generation charges."""
import argparse
import base64
import contextlib
import io
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.error
import urllib.request
import zlib

import hunyuan_3d as h


def png_bytes(width=256, height=256):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress((b"\0" + b"\x70\x80\x90" * width) * height))
            + chunk(b"IEND", b""))


class Response(io.BytesIO):
    def __init__(self, data, headers=None):
        super().__init__(json.dumps(data).encode() if isinstance(data, (dict, list)) else data)
        self.headers = headers or {}


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.front = self.root / "front image.png"
        self.front.write_bytes(png_bytes())
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.opener = Mock()
        self.factory = patch.object(h.urllib.request, "build_opener", return_value=self.opener)
        self.factory.start()
        self.addCleanup(self.factory.stop)
        self.net = patch("socket.socket.connect", side_effect=AssertionError("Offline test attempted network access"))
        self.net.start()
        self.addCleanup(self.net.stop)

    def args(self, *values):
        return h.build_parser().parse_args(values)

    def credentials(self):
        os.environ["TOKENHUB_API_KEY"] = "sk-offline-tokenhub-fixture"
        os.environ["HUNYUAN_3D_API_KEY"] = "sk-offline-legacy-fixture"

    def quiet(self, callback, *args, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return callback(*args, **kwargs)

    def test_default_tokenhub_payload(self):
        payload = h.build_payload(self.args("submit", "--prompt", "猫", "--enable-pbr", "--face-count", "12000"))
        self.assertEqual(payload, {"model": "hy-3d-3.1", "prompt": "猫",
                                   "enable_pbr": True, "face_count": 12000})

    def test_model_and_enum_aliases(self):
        payload = h.build_payload(self.args("submit", "--prompt", "猫", "--model", "hy-3d-3.0",
                                           "--generate-type", "low_poly", "--result-format", "fbx"))
        self.assertEqual(payload["generate_type"], "low_poly")
        self.assertEqual(payload["model"], "hy-3d-3.0")
        self.assertEqual(payload["result_format"], "fbx")

    def test_tokenhub_local_image_is_raw_base64(self):
        payload = h.build_payload(self.args("image-to-3d", "--image", str(self.front)))
        self.assertEqual(base64.b64decode(payload["image_base64"]), self.front.read_bytes())
        self.assertNotIn("ImageUrl", payload)
        self.assertNotIn("image_url", payload)

    def test_tokenhub_remote_image(self):
        payload = h.build_payload(self.args("submit", "--image-url", "https://example.com/reference.png"))
        self.assertEqual(payload["image_url"], "https://example.com/reference.png")

    def test_tokenhub_single_image_inclusive_boundaries(self):
        for side in (128, 5000):
            image = self.root / f"{side}.png"
            image.write_bytes(png_bytes(side, 128))
            h.build_payload(self.args("submit", "--image", str(image)))
        image.write_bytes(png_bytes(127, 128))
        with self.assertRaises(h.HunyuanError):
            h.build_payload(self.args("submit", "--image", str(image)))

    def test_multiview_wire_schema(self):
        payload = h.build_payload(self.args("submit", "--image", str(self.front),
                                           "--view", f"left={self.front}",
                                           "--view", "back=https://example.com/back.png?a=b"))
        views = payload["multi_view_images"]
        self.assertEqual([v["view_type"] for v in views], ["left", "back"])
        self.assertEqual(base64.b64decode(views[0]["view_image_base64"]), self.front.read_bytes())
        self.assertEqual(views[1]["view_image_base64"], "https://example.com/back.png?a=b")

    def test_multiview_rejects_duplicates_missing_front_and_invalid_views(self):
        for options in [
            ["--prompt", "猫", "--view", "left=x.png"],
            ["--image", str(self.front), "--view", f"left={self.front}", "--view", f"left={self.front}"],
            ["--image", str(self.front), "--view", f"front={self.front}"],
            ["--image", str(self.front), "--view", "left="],
            ["--provider", "legacy", "--image", str(self.front), "--view", f"left={self.front}"],
        ]:
            with self.subTest(options=options), self.assertRaises(h.HunyuanError):
                h.build_payload(self.args("submit", *options))

    def test_multiview_checks_combined_size(self):
        data = b"x" * (4 * 1024 * 1024)
        with patch.object(h, "_validated_image", return_value=(data, "image/png")):
            with self.assertRaisesRegex(h.HunyuanError, "Combined"):
                h.build_payload(self.args("submit", "--image", str(self.front), "--view", f"left={self.front}"))

    def test_multiview_strict_dimensions_and_format(self):
        image = self.root / "boundary.png"
        image.write_bytes(png_bytes(128, 256))
        with self.assertRaises(h.HunyuanError):
            h.build_payload(self.args("submit", "--image", str(self.front), "--view", f"left={image}"))
        webp = self.root / "ref.webp"
        webp.write_bytes(b"RIFF" + b"\0" * 4 + b"WEBPVP8L" + b"\5\0\0\0" + b"\x2f" + b"\0" * 4)
        with self.assertRaises(h.HunyuanError):
            h.build_payload(self.args("submit", "--image", str(self.front), "--view", f"left={webp}"))

    def test_invalid_modes_are_rejected_before_network(self):
        cases = [
            ["--generate-type", "LowPoly"], ["--generate-type", "sketch"],
            ["--generate-type", "geometry", "--enable-pbr"], ["--face-count", "2999"],
            ["--face-count", "1500001"], ["--polygon-type", "triangle"],
            ["--model", "3.0", "--generate-type", "LowPoly", "--face-count", "3000"],
            ["--image", str(self.front)],
        ]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(h.HunyuanError):
                h.submit_task(self.args("submit", "--prompt", "猫", *case))
        self.opener.open.assert_not_called()

    def test_sketch_three_zero_allows_prompt_and_image(self):
        payload = h.build_payload(self.args("submit", "--model", "3.0", "--generate-type", "sketch",
                                           "--prompt", "一只小猫", "--image", str(self.front)))
        self.assertIn("prompt", payload)
        self.assertIn("image_base64", payload)

    def test_prompt_limit_counts_characters_not_utf8_bytes(self):
        h.build_payload(self.args("submit", "--prompt", "猫" * 1024))
        with self.assertRaises(h.HunyuanError):
            h.build_payload(self.args("submit", "--prompt", "猫" * 1025))

    def test_invalid_timing_rejected_before_submission(self):
        for name, value in [("--poll-interval", "1"), ("--http-timeout", "0"),
                            ("--wait-timeout", "-1"), ("--poll-interval", "nan"),
                            ("--wait-timeout", "inf")]:
            with self.subTest(name=name, value=value), self.assertRaises(h.HunyuanError):
                h.run(self.args("generate", "--prompt", "猫", name, value))
        self.opener.open.assert_not_called()

    def test_dry_run_does_not_read_key_or_open_network(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch.object(h, "_api_key", side_effect=AssertionError):
            h.run(self.args("generate", "--image", str(self.front), "--view", f"left={self.front}", "--dry-run"))
        result = json.loads(output.getvalue())
        self.assertEqual(result["payload"]["image_base64"], "<redacted>")
        self.assertEqual(result["payload"]["multi_view_images"][0]["view_image_base64"], "<redacted>")
        self.opener.open.assert_not_called()

    def test_tokenhub_auth_and_submit_endpoint(self):
        self.credentials()
        self.opener.open.return_value = Response({"id": "123456", "status": "queued"})
        job_id, response = h.submit_task(self.args("submit", "--prompt", "猫"))
        self.assertEqual(job_id, "123456")
        req = self.opener.open.call_args.args[0]
        self.assertEqual(req.full_url, "https://tokenhub.tencentmaas.com/v1/api/3d/submit")
        self.assertEqual(req.get_header("Authorization"), "Bearer sk-offline-tokenhub-fixture")
        self.assertEqual(json.loads(req.data)["model"], "hy-3d-3.1")
        self.assertEqual(self.opener.open.call_count, 1)

    def test_legacy_auth_payload_and_envelope(self):
        self.credentials()
        self.opener.open.return_value = Response({"Response": {"JobId": "123456", "Status": "WAIT"}})
        job_id, _ = h.submit_task(self.args("submit", "--provider", "legacy", "--prompt", "猫"))
        self.assertEqual(job_id, "123456")
        req = self.opener.open.call_args.args[0]
        self.assertEqual(req.full_url, "https://api.ai3d.cloud.tencent.com/v1/ai3d/submit")
        self.assertEqual(req.get_header("Authorization"), "sk-offline-legacy-fixture")
        self.assertEqual(json.loads(req.data)["Model"], "3.1")

    def test_legacy_does_not_use_tokenhub_only_key(self):
        os.environ["TOKENHUB_API_KEY"] = "sk-offline-tokenhub-fixture"
        with self.assertRaises(h.HunyuanError):
            h._api_key("legacy")

    def test_legacy_image_encoding_and_strict_boundary(self):
        payload = h.build_payload(self.args("submit", "--provider", "legacy", "--image", str(self.front)))
        self.assertTrue(payload["ImageUrl"]["Url"].startswith("data:image/png;base64,"))
        raw = h.build_payload(self.args("submit", "--provider", "legacy", "--image", str(self.front),
                                       "--image-transport", "raw-base64"))
        self.assertEqual(base64.b64decode(raw["ImageBase64"]), self.front.read_bytes())
        self.front.write_bytes(png_bytes(128, 256))
        with self.assertRaises(h.HunyuanError):
            h.build_payload(self.args("submit", "--provider", "legacy", "--image", str(self.front)))

    def test_query_uses_exact_model_and_id(self):
        self.credentials()
        self.opener.open.return_value = Response({"status": "in_progress"})
        h.query_task("123456", self.args("query", "123456", "--model", "3.0"))
        req = self.opener.open.call_args.args[0]
        self.assertEqual(json.loads(req.data), {"model": "hy-3d-3.0", "id": "123456"})
        self.assertTrue(req.full_url.endswith("/v1/api/3d/query"))

    def test_status_normalization(self):
        for raw, normal in [("queued", "WAIT"), ("in_progress", "RUN"), ("completed", "DONE"),
                            ("failed", "FAIL"), ("WAIT", "WAIT"), ("DONE", "DONE")]:
            self.assertEqual(h.status_of({"status": raw}), normal)
        self.assertEqual(h.status_of({"Response": {"Status": "RUN"}}), "RUN")

    def test_polling_uses_query_only_and_minimum_interval(self):
        args = self.args("query", "123", "--wait")
        responses = [{"status": "in_progress"}, {"status": "completed", "data": []}]
        with patch.object(h, "query_task", side_effect=responses) as query, patch.object(h.time, "sleep") as sleep:
            result = self.quiet(h.wait_for_job, "123", args, initial={"status": "queued"})
        self.assertEqual(h.status_of(result), "DONE")
        self.assertEqual(query.call_count, 2)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [5.0, 5.0])

    def test_polling_timeout_does_not_resubmit(self):
        args = self.args("query", "123", "--wait", "--wait-timeout", "10")
        with patch.object(h.time, "monotonic", side_effect=[0, 10]), patch.object(h, "submit_task") as submit:
            with self.assertRaisesRegex(h.HunyuanError, "resume with query"):
                self.quiet(h.wait_for_job, "123", args, initial={"status": "in_progress"})
        submit.assert_not_called()

    def test_failed_single_query_exits_nonzero_and_keeps_request_id(self):
        self.credentials()
        self.opener.open.return_value = Response({"status": "failed", "error_message": "Rejected", "request_id": "req-1"})
        with self.assertRaisesRegex(h.HunyuanError, "req-1"):
            h.run(self.args("query", "123"))
        self.assertEqual(self.opener.open.call_count, 1)

    def test_unknown_status_does_not_poll_forever(self):
        with self.assertRaisesRegex(h.HunyuanError, "Unexpected"):
            self.quiet(h.wait_for_job, "123", self.args("query", "123"), initial={"status": "mystery"})
        self.opener.open.assert_not_called()

    def test_timeout_and_missing_id_never_retry_submit(self):
        self.credentials()
        self.opener.open.side_effect = TimeoutError()
        with self.assertRaisesRegex(h.HunyuanError, "Do not automatically resubmit"):
            h.submit_task(self.args("submit", "--prompt", "猫"))
        self.assertEqual(self.opener.open.call_count, 1)
        self.opener.open.reset_mock(side_effect=True)
        self.opener.open.return_value = Response({"request_id": "req-1", "data": [{"id": "not-a-job"}]})
        with self.assertRaisesRegex(h.HunyuanError, "no task ID"):
            h.submit_task(self.args("submit", "--prompt", "猫"))
        self.assertEqual(self.opener.open.call_count, 1)

    def test_http_and_success_body_errors_are_redacted(self):
        self.credentials()
        error = {"error": {"code": "AuthError", "message": os.environ["TOKENHUB_API_KEY"]}, "request_id": "req-2"}
        self.opener.open.side_effect = urllib.error.HTTPError(
            "https://tokenhub.tencentmaas.com", 401, "No", {}, Response(error))
        with self.assertRaises(h.HunyuanError) as captured:
            h.run(self.args("check-auth"))
        self.assertIn("401", str(captured.exception))
        self.assertIn("req-2", str(captured.exception))
        self.assertNotIn(os.environ["TOKENHUB_API_KEY"], str(captured.exception))
        self.opener.open.side_effect = None
        self.opener.open.return_value = Response(error)
        with self.assertRaisesRegex(h.HunyuanError, "AuthError"):
            h.run(self.args("check-auth"))

    def test_invalid_json_body_is_not_dumped(self):
        self.credentials()
        self.opener.open.return_value = Response(b"private request body not JSON")
        with self.assertRaises(h.HunyuanError) as captured:
            h.run(self.args("check-auth"))
        self.assertNotIn("private request body", str(captured.exception))

    def test_check_auth_is_get_without_submit(self):
        self.credentials()
        self.opener.open.return_value = Response({"data": [{"id": "hy-3d-3.1"}, {"id": "other"}]})
        self.assertEqual(self.quiet(h.run, self.args("check-auth")), 0)
        req = self.opener.open.call_args.args[0]
        self.assertEqual(req.method, "GET")
        self.assertIsNone(req.data)
        self.assertTrue(req.full_url.endswith("/v1/models"))
        self.assertEqual(self.opener.open.call_count, 1)

    def test_api_origin_does_not_leak_auth_to_arbitrary_hosts(self):
        for origin in ["https://example.com", "https://tokenhub.tencentmaas.com/v1",
                       "https://tokenhub.tencentmaas.com?key=abc", "http://tokenhub.tencentmaas.com",
                       "https://user:pass@tokenhub.tencentmaas.com", "https://api.ai3d.cloud.tencent.com"]:
            with self.subTest(origin=origin), self.assertRaises(h.HunyuanError):
                h.run(self.args("check-auth", "--base-url", origin, "--dry-run"))
        self.opener.open.assert_not_called()

    def test_provider_base_environment_is_separate(self):
        os.environ["HUNYUAN_3D_BASE_URL"] = h.LEGACY_BASE_URL
        self.assertEqual(h._base_url(self.args("check-auth")), h.DEFAULT_BASE_URL)
        args = self.args("check-auth", "--base-url", "https://tokenhub-intl.tencentmaas.com")
        self.assertEqual(h._base_url(args), "https://tokenhub-intl.tencentmaas.com")

    def test_credential_redirect_is_refused(self):
        req = urllib.request.Request("https://tokenhub.tencentmaas.com", headers={"Authorization": "secret"})
        with self.assertRaises(h.HunyuanError):
            h._NoRedirect().redirect_request(req, None, 302, "Found", {}, "https://example.com")

    def test_dangerous_input_urls_and_job_ids(self):
        for value in ["http://example.com/a.png", "https://", "https://localhost/x",
                      "https://127.0.0.1/a.png", "https://user:secret@example.com/a.png"]:
            with self.subTest(value=value), self.assertRaises(h.HunyuanError):
                h.build_payload(self.args("submit", "--image-url", value))
        for value in ["../escape", "a/b", "a\\b", ".", "id;cmd"]:
            with self.subTest(value=value), self.assertRaises(h.HunyuanError):
                h.query_task(value, self.args("query", "123", "--dry-run"))

    def test_download_uses_data_and_deduplicates_previews(self):
        response = {"status": "completed", "data": [
            {"type": "obj", "url": "https://example.com/a.zip", "preview_image_url": "https://example.com/a.png"},
            {"type": "glb", "url": "https://example.com/a.glb", "preview_image_url": "https://example.com/a.png"}]}
        self.opener.open.side_effect = [Response(b"zip"), Response(b"png"), Response(b"glb")]
        paths = self.quiet(h.download_results, response, self.root / "out", 60)
        self.assertEqual([p.suffix for p in paths], [".zip", ".png", ".glb"])
        self.assertEqual([p.read_bytes() for p in paths], [b"zip", b"png", b"glb"])
        for call in self.opener.open.call_args_list:
            self.assertIsNone(call.args[0].get_header("Authorization"))

    def test_legacy_download_schema(self):
        response = {"Response": {"Status": "DONE", "ResultFile3Ds": [{"Type": "GLB", "Url": "https://example.com/a.glb"}]}}
        self.opener.open.return_value = Response(b"legacy model")
        paths = self.quiet(h.download_results, response, self.root / "out", 60)
        self.assertEqual(paths[0].read_bytes(), b"legacy model")

    def test_partial_download_removed_and_existing_file_preserved(self):
        target = self.root / "model.glb"
        target.write_bytes(b"existing")
        self.opener.open.return_value = Response(b"truncated", {"Content-Length": "1000"})
        with self.assertRaises(h.HunyuanError):
            h._download("https://example.com/model.glb", target, 60)
        self.assertEqual(target.read_bytes(), b"existing")
        self.assertEqual(list(self.root.glob("*.part")), [])
        self.assertFalse((self.root / "model_1.glb").exists())

    def test_preview_failure_does_not_block_later_models(self):
        response = {"status": "completed", "data": [
            {"url": "https://example.com/a.glb", "preview_image_url": "https://example.com/a.png"},
            {"url": "https://example.com/b.glb"}]}
        self.opener.open.side_effect = [Response(b"a"), urllib.error.URLError("preview unavailable"), Response(b"b")]
        paths = self.quiet(h.download_results, response, self.root / "out", 60)
        self.assertEqual([p.read_bytes() for p in paths], [b"a", b"b"])

    def test_model_failure_preserves_other_downloads_and_reports_error(self):
        response = {"status": "completed", "data": [
            {"url": "https://example.com/a.glb"}, {"url": "https://example.com/b.glb"}]}
        self.opener.open.side_effect = [urllib.error.URLError("failed"), Response(b"b")]
        with self.assertRaisesRegex(h.HunyuanError, "query the same job"):
            self.quiet(h.download_results, response, self.root / "out", 60)
        self.assertEqual((self.root / "out" / "b.glb").read_bytes(), b"b")

    def test_signed_urls_and_images_are_redacted(self):
        self.credentials()
        response = {"url": "https://example.com/model.glb?signature=private",
                    "image_base64": "private-image", "authorization": "Bearer secret",
                    "message": os.environ["TOKENHUB_API_KEY"]}
        redacted = json.dumps(h.redact(response))
        for forbidden in ["signature=private", "private-image", "Bearer secret", os.environ["TOKENHUB_API_KEY"]]:
            self.assertNotIn(forbidden, redacted)

    def test_interrupted_http_transfer_is_an_actionable_download_failure(self):
        response = {"status": "completed", "data": [{"url": "https://example.com/a.glb"}]}
        self.opener.open.side_effect = h.http.client.IncompleteRead(b"partial")
        with self.assertRaisesRegex(h.HunyuanError, "query the same job"):
            self.quiet(h.download_results, response, self.root / "out", 60)
        self.assertEqual(list((self.root / "out").glob("*.part")), [])

    def test_invalid_resource_url_does_not_block_other_models(self):
        response = {"status": "completed", "data": [
            {"url": "https://[malformed/a.glb"}, {"url": "https://example.com/b.glb"}]}
        self.opener.open.return_value = Response(b"b")
        with self.assertRaisesRegex(h.HunyuanError, "query the same job"):
            self.quiet(h.download_results, response, self.root / "out", 60)
        self.assertEqual((self.root / "out" / "b.glb").read_bytes(), b"b")

    def test_query_network_failure_advises_resuming_same_task(self):
        self.credentials()
        self.opener.open.side_effect = TimeoutError()
        with self.assertRaisesRegex(h.HunyuanError, "same Job ID"):
            h.query_task("123", self.args("query", "123"))
        self.assertEqual(self.opener.open.call_count, 1)

    def test_express_aliases_and_minimal_default_payload(self):
        for model in ("express", "hy-3d-express", "HY-3D-Express"):
            payload = h.build_payload(self.args("submit", "--model", model, "--prompt", "猫"))
            self.assertEqual(payload, {"model": "hy-3d-express", "prompt": "猫"})

    def test_express_local_image_pbr_glb(self):
        payload = h.build_payload(self.args("image-to-3d", "--model", "express", "--image", str(self.front),
                                           "--enable-pbr", "--result-format", "glb"))
        self.assertEqual(base64.b64decode(payload.pop("image_base64")), self.front.read_bytes())
        self.assertEqual(payload, {"model": "hy-3d-express", "enable_pbr": True, "result_format": "glb"})

    def test_express_remote_image_wire_payload(self):
        payload = h.build_payload(self.args("submit", "--model", "express", "--image-url",
                                           "https://example.com/front.png"))
        self.assertEqual(payload, {"model": "hy-3d-express", "image_url": "https://example.com/front.png"})

    def test_express_all_documented_output_formats(self):
        for extension in ("obj", "glb", "stl", "usdz", "fbx", "mp4"):
            with self.subTest(extension=extension):
                payload = h.build_payload(self.args("submit", "--model", "express", "--prompt", "猫",
                                                   "--result-format", extension))
                self.assertEqual(payload["result_format"], extension)

    def test_express_geometry_switch_and_alias_map_to_boolean(self):
        for switch in (["--enable-geometry"], ["--generate-type", "geometry"]):
            payload = h.build_payload(self.args("submit", "--model", "express", "--prompt", "猫", *switch))
            self.assertEqual(payload, {"model": "hy-3d-express", "prompt": "猫", "enable_geometry": True})

    def test_express_geometry_rejects_obj_and_pbr(self):
        for option in (["--result-format", "obj"], ["--enable-pbr"]):
            for switch in (["--enable-geometry"], ["--generate-type", "Geometry"]):
                with self.subTest(option=option, switch=switch), self.assertRaises(h.HunyuanError):
                    h.submit_task(self.args("submit", "--model", "express", "--prompt", "猫", *switch, *option))
        self.opener.open.assert_not_called()

    def test_express_rejects_professional_options_before_network(self):
        for option in (["--face-count", "200000"], ["--polygon-type", "triangle"],
                       ["--generate-type", "LowPoly"], ["--generate-type", "Sketch"],
                       ["--view", f"left={self.front}"]):
            with self.subTest(option=option), self.assertRaises(h.HunyuanError):
                h.submit_task(self.args("submit", "--model", "express", "--image", str(self.front), *option))
        self.opener.open.assert_not_called()

    def test_express_source_validation_before_network(self):
        for options in ([], ["--prompt", "猫", "--image", str(self.front)],
                        ["--prompt", "猫" * 1025], ["--image-url", "http://example.com/a.png"],
                        ["--image", str(self.front), "--image-transport", "data-url"],
                        ["--prompt", "猫", "--image-transport", "raw-base64"]):
            with self.subTest(options=options), self.assertRaises(h.HunyuanError):
                h.submit_task(self.args("submit", "--model", "express", *options))
        with self.assertRaises(h.HunyuanError):
            h.submit_task(self.args("image-to-3d", "--model", "express", "--prompt", "猫"))
        self.opener.open.assert_not_called()

    def test_express_prompt_and_image_boundary_limits(self):
        h.build_payload(self.args("submit", "--model", "express", "--prompt", "猫" * 1024))
        self.front.write_bytes(png_bytes(128, 5000))
        h.build_payload(self.args("submit", "--model", "express", "--image", str(self.front)))
        self.front.write_bytes(png_bytes(127, 256))
        with self.assertRaises(h.HunyuanError):
            h.build_payload(self.args("submit", "--model", "express", "--image", str(self.front)))

    def test_express_legacy_provider_rejected_for_submit_and_query(self):
        for options in (["submit", "--prompt", "猫"], ["query", "123"]):
            with self.subTest(options=options), self.assertRaisesRegex(h.HunyuanError, "TokenHub|tokenhub"):
                h.run(self.args(*options, "--model", "express", "--provider", "legacy"))
        self.opener.open.assert_not_called()

    def test_professional_rejects_express_only_options(self):
        for provider in ("tokenhub", "legacy"):
            for option in (["--enable-geometry"], ["--result-format", "obj"],
                           ["--result-format", "glb"], ["--result-format", "mp4"]):
                with self.subTest(provider=provider, option=option), self.assertRaises(h.HunyuanError):
                    h.submit_task(self.args("submit", "--provider", provider, "--prompt", "猫", *option))
        self.opener.open.assert_not_called()

    def test_express_dry_run_redacts_images_and_never_reads_credentials(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch.object(h, "_api_key", side_effect=AssertionError):
            h.run(self.args("image-to-3d", "--model", "express", "--image", str(self.front), "--dry-run"))
        result = json.loads(output.getvalue())
        self.assertEqual(result["payload"], {"model": "hy-3d-express", "image_base64": "<redacted>"})
        self.assertEqual(result["url"], "https://tokenhub.tencentmaas.com/v1/api/3d/submit")
        self.opener.open.assert_not_called()

    def test_express_submit_query_download_complete_lifecycle(self):
        self.credentials()
        self.opener.open.side_effect = [
            Response({"id": "express-job-1", "status": "queued"}),
            Response({"status": "completed", "data": [{"type": "glb", "url": "https://example.com/asset.glb"}]}),
            Response(b"model-binary")]
        args = self.args("generate", "--model", "express", "--prompt", "猫", "--result-format", "glb",
                         "--output-dir", str(self.root / "output"))
        with patch.object(h.time, "sleep"):
            self.assertEqual(self.quiet(h.run, args), 0)
        requests = [call.args[0] for call in self.opener.open.call_args_list]
        self.assertEqual(len(requests), 3)
        self.assertTrue(requests[0].full_url.endswith("/v1/api/3d/submit"))
        self.assertEqual(json.loads(requests[0].data), {"model": "hy-3d-express", "prompt": "猫", "result_format": "glb"})
        self.assertEqual(json.loads(requests[1].data), {"model": "hy-3d-express", "id": "express-job-1"})
        self.assertIsNone(requests[2].get_header("Authorization"))
        self.assertEqual((self.root / "output" / "express-job-1" / "asset.glb").read_bytes(), b"model-binary")

    def test_express_query_recovery_does_not_submit_again(self):
        self.credentials()
        self.opener.open.side_effect = [
            Response({"status": "completed", "data": [{"type": "mp4", "url": "https://example.com/asset.mp4"}]}),
            Response(b"video-binary")]
        args = self.args("query", "express-job-2", "--model", "hy-3d-express", "--download",
                         "--output-dir", str(self.root))
        with patch.object(h, "submit_task", side_effect=AssertionError("Recovery resubmitted")):
            self.assertEqual(self.quiet(h.run, args), 0)
        request = self.opener.open.call_args_list[0].args[0]
        self.assertEqual(json.loads(request.data), {"model": "hy-3d-express", "id": "express-job-2"})
        self.assertEqual((self.root / "express-job-2" / "asset.mp4").read_bytes(), b"video-binary")

    def test_express_failed_query_and_uncertain_submit_do_not_retry(self):
        self.credentials()
        self.opener.open.return_value = Response({"status": "failed", "request_id": "req-express"})
        with self.assertRaisesRegex(h.HunyuanError, "req-express"):
            h.run(self.args("query", "123", "--model", "express"))
        self.assertEqual(self.opener.open.call_count, 1)
        self.opener.open.reset_mock()
        self.opener.open.side_effect = TimeoutError()
        with self.assertRaisesRegex(h.HunyuanError, "Do not automatically resubmit"):
            h.run(self.args("submit", "--model", "express", "--prompt", "猫"))
        self.assertEqual(self.opener.open.call_count, 1)

    def test_completed_submit_skips_query(self):
        self.credentials()
        self.opener.open.return_value = Response({"id": "123", "status": "completed", "data": []})
        with patch.object(h, "query_task", side_effect=AssertionError("Unnecessary query")):
            result = self.quiet(h.run, self.args("generate", "--prompt", "猫", "--no-download"))
        self.assertEqual(result, 0)
        self.assertEqual(self.opener.open.call_count, 1)


if __name__ == "__main__":
    unittest.main()
