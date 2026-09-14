"""Offline rigging tests; HTTP is simulated and external socket connections blocked."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.error

import hunyuan_animation as a
h = a.h


class Response(io.BytesIO):
    def __init__(self, data):
        super().__init__(json.dumps(data).encode() if isinstance(data, dict) else data)
        self.headers = {}


class RiggingTests(unittest.TestCase):
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
        return a.build_parser().parse_args(args)

    def rig(self, *args):
        return self.args("rig", "--file-url", "https://example.com/character.glb", *args)

    def run_quiet(self, args):
        with contextlib.redirect_stdout(self.out), contextlib.redirect_stderr(self.err):
            return a.run(args)

    def credentials(self):
        os.environ["TOKENHUB_API_KEY"] = "sk-offline-rigging-fixture"

    def body(self, index=0):
        return json.loads(self.opener.open.call_args_list[index].args[0].data)

    def test_default_has_no_implicit_motion_or_character_type(self):
        self.assertEqual(a.build_payload(self.rig()),
                         {"model": "hy-3d-rigging", "file_3d": {"url": "https://example.com/character.glb"}})

    def test_motion_request_matches_official_example(self):
        self.assertEqual(a.build_payload(self.args("submit", "--file-url", "https://example.com/test.fbx", "--motion-type", "1")),
                         {"model": "hy-3d-rigging", "file_3d": {"url": "https://example.com/test.fbx"}, "motion_type": 1})

    def test_fbx_glb_and_optional_type_normalization(self):
        for kind in ("fbx", "glb"):
            url = f"https://example.com/character.{kind.upper()}?signature=example"
            payload = a.build_payload(self.args("rigging", "--model", "HY-3D-RIGGING", "--file-url", url, "--file-type", kind.upper()))
            self.assertEqual(payload["file_3d"], {"url": url, "type": kind})
        url = "https://example.com/download?id=character"
        self.assertEqual(a.build_payload(self.args("rig", "--file-url", url))["file_3d"], {"url": url})

    def test_wrong_formats_mismatched_type_and_unsafe_urls_fail_before_submit(self):
        for url in ("https://example.com/a.obj", "https://example.com/a.zip", "https://example.com/a.gltf",
                    "https://example.com/a%2Eobj", "D:/a.glb", "http://example.com/a.fbx",
                    "https://localhost/a.fbx", "https://127.0.0.1/a.glb", "https://user:pass@example.com/a.glb"):
            with self.subTest(url=url), self.assertRaises(h.HunyuanError):
                a.submit_task(self.args("rig", "--file-url", url))
        with self.assertRaises(h.HunyuanError):
            a.submit_task(self.rig("--file-type", "fbx"))
        self.opener.open.assert_not_called()

    def test_verified_size_boundaries_are_local_only(self):
        expected = a.build_payload(self.rig())
        for size in (1, 60_000_000):
            self.assertEqual(a.build_payload(self.rig("--input-size-bytes", str(size))), expected)
        for size in (0, -1, 60_000_001, True, 1.5):
            args = self.rig()
            args.input_size_bytes = size
            with self.subTest(size=size), self.assertRaises(h.HunyuanError):
                a.submit_task(args)
        self.opener.open.assert_not_called()

    def test_motion_ids_are_integer_one_through_48(self):
        for motion in range(1, 49):
            payload = a.build_payload(self.rig("--motion-type", str(motion), "--character-type", "humanoid"))
            self.assertEqual(payload["motion_type"], motion)
            self.assertNotIn("character_type", payload)
        for motion in (0, 49, -1, True, 1.5):
            args = self.rig()
            args.motion_type = motion
            with self.subTest(motion=motion), self.assertRaises(h.HunyuanError):
                a.submit_task(args)
        self.opener.open.assert_not_called()

    def test_non_humanoid_rigging_allowed_but_motion_templates_rejected(self):
        self.assertEqual(a.build_payload(self.rig("--character-type", "non-humanoid")), a.build_payload(self.rig()))
        with self.assertRaisesRegex(h.HunyuanError, "Non-humanoid"):
            a.submit_task(self.rig("--character-type", "non-humanoid", "--motion-type", "23"))
        self.opener.open.assert_not_called()

    def test_list_motions_is_offline_and_has_documented_mapping(self):
        with patch.object(h, "api_request", side_effect=AssertionError("Unexpected API call")):
            self.assertEqual(self.run_quiet(self.args("list-motions")), 0)
        motions = json.loads(self.out.getvalue())
        self.assertEqual(set(motions), {str(i) for i in range(1, 49)})
        self.assertEqual([motions[str(i)] for i in (1, 23, 26, 48)], ["回旋踢", "走路-1", "待机-1", "发送冲击波"])
        self.opener.open.assert_not_called()

    def test_unsupported_api_features_are_rejected(self):
        for extra in (("--prompt", "walk"), ("--duration", "5"), ("--skeleton-type", "mixamo"),
                      ("--enable-pbr",), ("--face-count", "30000"), ("--result-format", "obj"),
                      ("--file-type", "obj"), ("--provider", "legacy"), ("--model", "3.1")):
            with contextlib.redirect_stderr(self.err), self.assertRaises(SystemExit):
                self.rig(*extra)
        self.opener.open.assert_not_called()

    def test_dry_run_redacts_signed_url_and_does_not_read_key_or_create_outputs(self):
        destination = self.root / "not-created"
        with patch.object(h, "_api_key", side_effect=AssertionError("Unexpected key read")):
            self.assertEqual(self.run_quiet(self.args("rig", "--file-url", "https://example.com/a.glb?signature=private",
                                                    "--dry-run", "--output-dir", str(destination))), 0)
        self.assertNotIn("private", self.out.getvalue())
        self.assertFalse(destination.exists())
        self.opener.open.assert_not_called()

    def test_submit_once_sends_auth_and_correct_resume_model(self):
        self.credentials()
        self.opener.open.return_value = Response({"id": "789", "status": "queued"})
        self.run_quiet(self.args("submit", "--file-url", "https://example.com/a.fbx"))
        self.opener.open.assert_called_once()
        request = self.opener.open.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer sk-offline-rigging-fixture")
        self.assertEqual(request.full_url, "https://tokenhub.tencentmaas.com/v1/api/3d/submit")
        self.assertIn("query 789 --provider tokenhub --model rigging", self.err.getvalue())

    def test_lifecycle_downloads_fbx_and_all_other_returned_assets_without_auth(self):
        self.credentials()
        files = [{"type": kind, "url": f"https://example.com/{name}"} for kind, name in
                 (("fbx", "rigged.fbx"), ("image", "preview.png"))]
        self.opener.open.side_effect = [Response({"id": "789", "status": "queued"}),
            Response({"status": "completed", "data": files}), Response(b"rigged"), Response(b"preview")]
        with patch.object(h.time, "sleep") as sleep:
            self.assertEqual(self.run_quiet(self.rig("--motion-type", "23", "--output-dir", str(self.root))), 0)
        sleep.assert_called_once_with(5.0)
        self.assertEqual(self.body(1), {"model": "hy-3d-rigging", "id": "789"})
        result = json.loads(self.out.getvalue())
        self.assertEqual([Path(p).read_bytes() for p in result["downloaded"]], [b"rigged", b"preview"])
        for call in self.opener.open.call_args_list[2:]:
            self.assertIsNone(call.args[0].get_header("Authorization"))

    def test_completed_alias_can_skip_download_and_query(self):
        self.credentials()
        self.opener.open.return_value = Response({"id": "789", "status": "completed"})
        self.assertEqual(self.run_quiet(self.args("rigging", "--file-url", "https://example.com/a.glb", "--no-download")), 0)
        self.opener.open.assert_called_once()

    def test_query_keeps_region_model_and_id_when_server_omits_id(self):
        self.credentials()
        self.opener.open.return_value = Response({"status": "completed"})
        self.run_quiet(self.args("query", "789", "--model", "hy-3d-rigging", "--base-url", "https://tokenhub-intl.tencentmaas.com"))
        self.assertEqual(self.body(), {"model": "hy-3d-rigging", "id": "789"})
        self.assertEqual(self.opener.open.call_args.args[0].full_url, "https://tokenhub-intl.tencentmaas.com/v1/api/3d/query")
        self.assertEqual(json.loads(self.out.getvalue())["job_id"], "789")

    def test_partial_download_keeps_successful_files_for_recovery(self):
        self.credentials()
        self.opener.open.side_effect = [Response({"status": "completed", "data": [
            {"type": "fbx", "url": "https://example.com/rigged.fbx"}, {"type": "image", "url": "https://example.com/preview.png"}]}),
            Response(b"rigged"), urllib.error.URLError("download failure")]
        with self.assertRaisesRegex(h.HunyuanError, "query the same job"):
            self.run_quiet(self.args("query", "789", "--download", "--output-dir", str(self.root)))
        self.assertEqual((self.root / "789" / "rigged.fbx").read_bytes(), b"rigged")
        self.assertFalse(list(self.root.rglob("*.part")))

    def test_failed_query_does_not_download_or_resubmit(self):
        self.credentials()
        self.opener.open.return_value = Response({"status": "failed", "request_id": "rig-failure", "error_message": "invalid model"})
        with self.assertRaisesRegex(h.HunyuanError, "rig-failure"):
            self.run_quiet(self.args("query", "789", "--wait", "--download"))
        self.opener.open.assert_called_once()

    def test_uncertain_or_missing_id_submission_does_not_retry(self):
        self.credentials()
        for result, message in ((TimeoutError(), "Do not automatically resubmit"), (Response({"request_id": "req-only"}), "no task ID")):
            self.opener.open.reset_mock()
            self.opener.open.side_effect = [result]
            with self.assertRaisesRegex(h.HunyuanError, message):
                self.run_quiet(self.rig())
            self.opener.open.assert_called_once()

    def test_invalid_timing_and_origin_fail_before_submission(self):
        for extra in (("--poll-interval", "1"), ("--wait-timeout", "nan"), ("--http-timeout", "-1"),
                      ("--base-url", "https://example.com")):
            with self.assertRaises(h.HunyuanError):
                self.run_quiet(self.rig(*extra))
        self.opener.open.assert_not_called()

    def test_check_auth_only_reads_model_catalog(self):
        self.credentials()
        self.opener.open.return_value = Response({"data": [{"id": "hy-3d-rigging"}]})
        self.assertEqual(self.run_quiet(self.args("check-auth")), 0)
        request = self.opener.open.call_args.args[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertTrue(request.full_url.endswith("/v1/models"))
        self.opener.open.assert_called_once()


if __name__ == "__main__":
    unittest.main()
