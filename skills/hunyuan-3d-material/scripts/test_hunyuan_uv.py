"""Offline UV tests: exercise native payloads, input limits and recoverable job lifecycles."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import hunyuan_material as m
h = m.h


class Response(io.BytesIO):
    def __init__(self, data):
        super().__init__(json.dumps(data).encode() if isinstance(data, dict) else data)
        self.headers = {}


class UVTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.opener = Mock()
        for ctx in (patch.dict(os.environ, {}, clear=True),
                    patch.object(h.urllib.request, "build_opener", return_value=self.opener),
                    patch("socket.socket.connect", side_effect=AssertionError("Offline test attempted network"))):
            ctx.start()
            self.addCleanup(ctx.stop)
        self.out, self.err = io.StringIO(), io.StringIO()

    def args(self, *args):
        return m.build_parser().parse_args(args)

    def uv(self, *args):
        return self.args("uv", "--file-url", "https://example.com/test.fbx", *args)

    def run_quiet(self, args):
        with contextlib.redirect_stdout(self.out), contextlib.redirect_stderr(self.err):
            return m.run(args)

    def credentials(self):
        os.environ["TOKENHUB_API_KEY"] = "sk-offline-uv-fixture"

    def body(self, index=0):
        return json.loads(self.opener.open.call_args_list[index].args[0].data)

    def test_minimal_request_matches_official_example_and_alias(self):
        expected = {"model": "hy-3d-uv", "file": {"url": "https://example.com/test.fbx"}}
        self.assertEqual(m.build_payload(self.uv()), expected)
        self.assertEqual(m.build_payload(self.args("unwrap", "--file-url", "https://example.com/test.fbx")), expected)
        self.assertEqual(m.build_payload(self.args("submit", "--model", "hy-3d-uv", "--file-url",
                                                  "https://example.com/test.fbx")), expected)

    def test_all_input_formats_and_optional_type_use_file_object(self):
        for kind in ("fbx", "obj", "glb"):
            url = f"https://example.com/model.{kind.upper()}?signature=example"
            payload = m.build_payload(self.args("uv", "--file-url", url, "--file-type", kind.upper()))
            self.assertEqual(payload, {"model": "hy-3d-uv", "file": {"url": url, "type": kind}})

    def test_extensionless_url_does_not_invent_file_type(self):
        url = "https://example.com/download?id=model"
        self.assertEqual(m.build_payload(self.args("uv", "--file-url", url))["file"], {"url": url})
        self.assertEqual(m.build_payload(self.args("uv", "--file-url", url, "--file-type", "glb"))["file"],
                         {"url": url, "type": "glb"})

    def test_invalid_formats_and_unsafe_urls_fail_before_submission(self):
        for url in ("https://example.com/a.zip", "https://example.com/a.gltf", "https://example.com/a.stl",
                    "https://example.com/a%2Ezip", "http://example.com/a.obj", "D:/a.obj",
                    "https://127.0.0.1/a.obj", "https://user:password@example.com/a.obj"):
            with self.subTest(url=url), self.assertRaises(h.HunyuanError):
                m.submit_task(self.args("uv", "--file-url", url))
        self.opener.open.assert_not_called()

    def test_type_mismatch_is_rejected(self):
        with self.assertRaises(h.HunyuanError):
            m.submit_task(self.uv("--file-type", "obj"))
        with contextlib.redirect_stderr(self.err), self.assertRaises(SystemExit):
            self.uv("--file-type", "stl")
        self.opener.open.assert_not_called()

    def test_known_measurement_boundaries_are_local_only(self):
        expected = {"model": "hy-3d-uv", "file": {"url": "https://example.com/test.fbx"}}
        for size, faces, components in ((1, 1, 1), (60_000_000, 30_000, 100)):
            payload = m.build_payload(self.uv("--input-size-bytes", str(size), "--face-count", str(faces),
                                               "--component-count", str(components)))
            self.assertEqual(payload, expected)

    def test_invalid_measurements_fail_before_billable_request(self):
        for field, maximum in (("input_size_bytes", 60_000_000), ("face_count", 30_000), ("component_count", 100)):
            for value in (0, -1, maximum + 1, True, 1.5):
                args = self.uv()
                setattr(args, field, value)
                with self.subTest(field=field, value=value), self.assertRaises(h.HunyuanError):
                    m.submit_task(args)
        self.opener.open.assert_not_called()

    def test_operation_and_model_must_agree(self):
        for args in (self.uv("--model", "texture"),
                     self.args("unwrap", "--file-url", "https://example.com/a.obj", "--model", "texture"),
                     self.args("texture", "--file-url", "https://example.com/a.obj", "--prompt", "木纹", "--model", "uv")):
            with self.assertRaises(h.HunyuanError):
                m.submit_task(args)
        self.opener.open.assert_not_called()

    def test_submit_rejects_options_from_other_operation(self):
        for extra in (("--prompt", "木纹"), ("--prompt", ""), ("--image", "ref.png"),
                      ("--image-url", "https://example.com/a.png"), ("--view", "left=ref.png"),
                      ("--enable-pbr",), ("--enable-keep-uv",), ("--texture-size", "2048")):
            with self.subTest(extra=extra), self.assertRaises(h.HunyuanError):
                m.submit_task(self.args("submit", "--model", "uv", "--file-url", "https://example.com/a.obj", *extra))
        for extra in (("--file-type", "obj"), ("--face-count", "30000"),
                      ("--component-count", "100"), ("--input-size-bytes", "1000")):
            with self.subTest(extra=extra), self.assertRaises(h.HunyuanError):
                m.submit_task(self.args("submit", "--file-url", "https://example.com/a.obj", "--prompt", "木纹", *extra))
        self.opener.open.assert_not_called()

    def test_uv_command_rejects_texture_and_undocumented_parameters(self):
        for extra in (("--prompt", "木纹"), ("--enable-pbr",), ("--enable-keep-uv",),
                      ("--texture-size", "2048"), ("--result-format", "glb"), ("--seam-angle", "66"),
                      ("--provider", "legacy"), ("--model", "3.1")):
            with contextlib.redirect_stderr(self.err), self.assertRaises(SystemExit):
                self.uv(*extra)
        self.opener.open.assert_not_called()

    def test_submit_still_requires_texture_source_by_default(self):
        args = self.args("submit", "--file-url", "https://example.com/a.obj")
        self.assertEqual(args.model, "texture")
        with self.assertRaises(h.HunyuanError):
            m.submit_task(args)
        self.opener.open.assert_not_called()

    def test_dry_run_redacts_signed_url_without_credentials_network_or_files(self):
        destination = self.root / "not-created"
        self.assertEqual(self.run_quiet(self.args("uv", "--file-url", "https://example.com/a.fbx?signature=private",
                                                  "--dry-run", "--output-dir", str(destination))), 0)
        self.assertEqual(json.loads(self.out.getvalue())["payload"]["model"], "hy-3d-uv")
        self.assertNotIn("private", self.out.getvalue())
        self.assertFalse(destination.exists())
        self.opener.open.assert_not_called()

    def test_submit_only_sends_bearer_once_and_prints_correct_resume(self):
        self.credentials()
        self.opener.open.return_value = Response({"id": "789", "status": "queued"})
        self.run_quiet(self.args("submit", "--model", "uv", "--file-url", "https://example.com/a.obj"))
        self.opener.open.assert_called_once()
        request = self.opener.open.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer sk-offline-uv-fixture")
        self.assertEqual(request.full_url, "https://tokenhub.tencentmaas.com/v1/api/3d/submit")
        self.assertEqual(self.body(), {"model": "hy-3d-uv", "file": {"url": "https://example.com/a.obj"}})
        self.assertIn("query 789 --provider tokenhub --model uv", self.err.getvalue())

    def test_lifecycle_polls_same_model_and_downloads_all_official_resource_types(self):
        self.credentials()
        files = [{"type": kind, "url": f"https://example.com/{name}"} for kind, name in
                 (("obj", "model.obj"), ("fbx", "model.fbx"), ("image", "latent.png"))]
        self.opener.open.side_effect = [Response({"id": "789", "status": "queued"}),
            Response({"status": "completed", "data": files}), Response(b"obj"), Response(b"fbx"), Response(b"image")]
        with patch.object(h.time, "sleep") as sleep:
            self.assertEqual(self.run_quiet(self.uv("--output-dir", str(self.root))), 0)
        sleep.assert_called_once_with(5.0)
        self.assertEqual(self.body(1), {"model": "hy-3d-uv", "id": "789"})
        result = json.loads(self.out.getvalue())
        self.assertEqual([Path(p).read_bytes() for p in result["downloaded"]], [b"obj", b"fbx", b"image"])
        for call in self.opener.open.call_args_list[2:]:
            self.assertIsNone(call.args[0].get_header("Authorization"))

    def test_completed_alias_can_skip_download_and_query(self):
        self.credentials()
        self.opener.open.return_value = Response({"id": "789", "status": "completed"})
        self.assertEqual(self.run_quiet(self.args("unwrap", "--file-url", "https://example.com/a.glb", "--no-download")), 0)
        self.opener.open.assert_called_once()

    def test_query_uses_uv_model_id_and_original_region(self):
        self.credentials()
        self.opener.open.return_value = Response({"status": "completed"})
        self.run_quiet(self.args("query", "789", "--model", "hy-3d-uv", "--base-url", "https://tokenhub-intl.tencentmaas.com"))
        self.assertEqual(self.body(), {"model": "hy-3d-uv", "id": "789"})
        self.assertEqual(self.opener.open.call_args.args[0].full_url, "https://tokenhub-intl.tencentmaas.com/v1/api/3d/query")
        self.assertEqual(json.loads(self.out.getvalue())["job_id"], "789")
        self.assertEqual(self.args("query", "789").model, "texture")

    def test_failed_query_does_not_download_or_resubmit(self):
        self.credentials()
        self.opener.open.return_value = Response({"status": "failed", "request_id": "uv-failure", "error_message": "invalid model"})
        with self.assertRaisesRegex(h.HunyuanError, "uv-failure"):
            self.run_quiet(self.args("query", "789", "--model", "uv", "--wait", "--download"))
        self.opener.open.assert_called_once()

    def test_uncertain_submit_does_not_retry(self):
        self.credentials()
        self.opener.open.side_effect = TimeoutError()
        with self.assertRaisesRegex(h.HunyuanError, "Do not automatically resubmit"):
            self.run_quiet(self.uv())
        self.opener.open.assert_called_once()

    def test_missing_id_does_not_retry(self):
        self.credentials()
        self.opener.open.return_value = Response({"request_id": "req-only"})
        with self.assertRaisesRegex(h.HunyuanError, "no task ID"):
            self.run_quiet(self.uv())
        self.opener.open.assert_called_once()

    def test_invalid_timing_and_origin_fail_before_submit(self):
        for extra in (("--poll-interval", "1"), ("--wait-timeout", "nan"), ("--http-timeout", "-1"),
                      ("--base-url", "https://example.com")):
            with self.assertRaises(h.HunyuanError):
                self.run_quiet(self.uv(*extra))
        self.opener.open.assert_not_called()


if __name__ == "__main__":
    unittest.main()
