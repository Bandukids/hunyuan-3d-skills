"""Offline motion tests: validate native options, JSON passthrough and job recovery."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import hunyuan_animation as a
h = a.h


class Response(io.BytesIO):
    def __init__(self, data):
        super().__init__(json.dumps(data).encode() if isinstance(data, dict) else data)
        self.headers = {}


class MotionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.retarget = self.root / "retarget.json"
        self.opener = Mock()
        for ctx in (patch.dict(os.environ, {}, clear=True),
                    patch.object(h.urllib.request, "build_opener", return_value=self.opener),
                    patch("socket.socket.connect", side_effect=AssertionError("Offline test attempted network"))):
            ctx.start()
            self.addCleanup(ctx.stop)
        self.out, self.err = io.StringIO(), io.StringIO()

    def args(self, *args):
        return a.build_parser().parse_args(args)

    def motion(self, *args):
        return self.args("motion", "--prompt", "A person walks forward", *args)

    def run_quiet(self, args):
        with contextlib.redirect_stdout(self.out), contextlib.redirect_stderr(self.err):
            return a.run(args)

    def credentials(self):
        os.environ["TOKENHUB_API_KEY"] = "sk-offline-motion-fixture"

    def body(self, index=0):
        return json.loads(self.opener.open.call_args_list[index].args[0].data)

    def test_default_matches_official_example_and_omits_optional_fields(self):
        expected = {"model": "hy-3d-motion", "prompt": "A person walks forward"}
        self.assertEqual(a.build_payload(self.motion()), expected)
        self.assertEqual(a.build_payload(self.args("text-to-motion", "--prompt", expected["prompt"])), expected)
        self.assertEqual(a.build_payload(self.args("submit", "--model", "HY-3D-MOTION", "--prompt", expected["prompt"])), expected)

    def test_prompt_limit_counts_characters_not_utf8_bytes(self):
        self.assertEqual(a.build_payload(self.args("motion", "--prompt", " 人 " * 42))["prompt"], (" 人 " * 42).strip())
        a.build_payload(self.args("motion", "--prompt", "走" * 128))
        for prompt in ("", "  \n", "走" * 129):
            with self.subTest(prompt_length=len(prompt)), self.assertRaises(h.HunyuanError):
                a.submit_task(self.args("motion", "--prompt", prompt))
        self.opener.open.assert_not_called()

    def test_duration_accepts_one_to_twelve_and_rejects_non_integer_values(self):
        for value in (1, 5, 12):
            self.assertEqual(a.build_payload(self.motion("--duration", str(value)))["duration"], value)
        for value in (0, -1, 13, True, 1.5):
            args = self.motion()
            args.duration = value
            with self.subTest(value=value), self.assertRaises(h.HunyuanError):
                a.submit_task(args)
        self.opener.open.assert_not_called()

    def test_boolean_options_preserve_true_false_and_unspecified(self):
        for field, positive, negative in (("enable_mesh", "--enable-mesh", "--no-mesh"),
                                         ("enable_rewrite", "--enable-rewrite", "--no-rewrite"),
                                         ("enable_duration_est", "--enable-duration-est", "--no-duration-est")):
            self.assertNotIn(field, a.build_payload(self.motion()))
            self.assertIs(a.build_payload(self.motion(positive))[field], True)
            self.assertIs(a.build_payload(self.motion(negative))[field], False)
            args = self.motion()
            setattr(args, field, 1)
            with self.assertRaises(h.HunyuanError):
                a.submit_task(args)
            with contextlib.redirect_stderr(self.err), self.assertRaises(SystemExit):
                self.motion(positive, negative)
        self.opener.open.assert_not_called()

    def test_duration_and_estimation_are_forwarded_without_inventing_precedence(self):
        payload = a.build_payload(self.motion("--duration", "6", "--enable-duration-est", "--enable-rewrite", "--no-mesh"))
        self.assertEqual(payload, {"model": "hy-3d-motion", "prompt": "A person walks forward", "duration": 6,
                                  "enable_duration_est": True, "enable_rewrite": True, "enable_mesh": False})

    def test_retarget_json_passes_object_unchanged_without_claiming_inner_schema(self):
        # Synthetic provider object for passthrough behavior, not an example of a verified API schema.
        data = {"fixture_metadata": {"name": "人物", "keep": False}, "fixture_url": "https://example.com/character.fbx"}
        self.retarget.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8-sig")
        payload = a.build_payload(self.motion("--retarget-file-json", str(self.retarget)))
        self.assertEqual(payload["retarget_file"], data)
        self.assertIsInstance(payload["retarget_file"], dict)
        self.assertNotIn("file_3d", payload)

    def test_invalid_retarget_json_is_rejected_before_submit(self):
        for value in ("{}", "[]", '"string"', "null", "{bad", '{"x":1,"x":2}',
                      '{"x":{"y":1,"y":2}}', '{"x":NaN}', '{"x":Infinity}', '{"x":1e999}'):
            self.retarget.write_text(value, encoding="utf-8")
            with self.subTest(value=value), self.assertRaises(h.HunyuanError):
                a.submit_task(self.motion("--retarget-file-json", str(self.retarget)))
        self.retarget.write_bytes(b"\xff")
        with self.assertRaises(h.HunyuanError):
            a.submit_task(self.motion("--retarget-file-json", str(self.retarget)))
        with self.assertRaises(h.HunyuanError):
            a.submit_task(self.motion("--retarget-file-json", str(self.root / "missing.json")))
        self.opener.open.assert_not_called()

    def test_command_and_model_must_agree(self):
        for args in (self.motion("--model", "rigging"),
                     self.args("text-to-motion", "--prompt", "walk", "--model", "rigging"),
                     self.args("rig", "--file-url", "https://example.com/a.glb", "--model", "motion"),
                     self.args("rigging", "--file-url", "https://example.com/a.glb", "--model", "motion")):
            with self.assertRaises(h.HunyuanError):
                a.submit_task(args)
        self.opener.open.assert_not_called()

    def test_submit_rejects_mixed_operation_parameters_including_explicit_false(self):
        for extra in (("--file-url", "https://example.com/a.glb"), ("--file-type", "glb"),
                      ("--input-size-bytes", "1000"), ("--character-type", "humanoid"), ("--motion-type", "23")):
            with self.subTest(extra=extra), self.assertRaises(h.HunyuanError):
                a.submit_task(self.args("submit", "--model", "motion", "--prompt", "walk", *extra))
        for extra in (("--prompt", "walk"), ("--duration", "5"), ("--no-mesh",), ("--no-rewrite",),
                      ("--no-duration-est",), ("--retarget-file-json", str(self.retarget))):
            with self.subTest(extra=extra), self.assertRaises(h.HunyuanError):
                a.submit_task(self.args("submit", "--file-url", "https://example.com/a.glb", *extra))
        self.opener.open.assert_not_called()

    def test_submit_defaults_to_rigging_and_requires_the_selected_source(self):
        args = self.args("submit")
        self.assertEqual(args.model, "rigging")
        for args in (args, self.args("submit", "--model", "motion")):
            with self.assertRaises(h.HunyuanError):
                a.submit_task(args)
        self.opener.open.assert_not_called()

    def test_motion_rejects_undocumented_or_other_model_cli_fields(self):
        for extra in (("--file-url", "https://example.com/a.glb"), ("--motion-type", "23"), ("--fps", "30"),
                      ("--result-format", "glb"), ("--retarget-url", "https://example.com/a.fbx"),
                      ("--duration", "1.5"), ("--provider", "legacy"), ("--model", "3.1")):
            with contextlib.redirect_stderr(self.err), self.assertRaises(SystemExit):
                self.motion(*extra)
        self.opener.open.assert_not_called()

    def test_dry_run_redacts_retarget_urls_without_key_network_or_output(self):
        self.retarget.write_text(json.dumps({"fixture_url": "https://example.com/a.fbx?signature=private"}), encoding="utf-8")
        output = self.root / "not-created"
        with patch.object(h, "_api_key", side_effect=AssertionError("Unexpected credential read")):
            self.assertEqual(self.run_quiet(self.motion("--retarget-file-json", str(self.retarget),
                                                       "--output-dir", str(output), "--dry-run")), 0)
        self.assertNotIn("private", self.out.getvalue())
        self.assertFalse(output.exists())
        self.opener.open.assert_not_called()

    def test_submit_once_sends_native_payload_auth_and_resume(self):
        self.credentials()
        self.opener.open.return_value = Response({"id": "789", "status": "queued"})
        self.run_quiet(self.args("submit", "--model", "motion", "--prompt", "walk", "--no-mesh"))
        self.opener.open.assert_called_once()
        request = self.opener.open.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer sk-offline-motion-fixture")
        self.assertEqual(request.full_url, "https://tokenhub.tencentmaas.com/v1/api/3d/submit")
        self.assertEqual(self.body(), {"model": "hy-3d-motion", "prompt": "walk", "enable_mesh": False})
        self.assertIn("query 789 --provider tokenhub --model motion", self.err.getvalue())

    def test_lifecycle_queries_motion_and_downloads_animation_fbx_without_auth(self):
        self.credentials()
        self.opener.open.side_effect = [Response({"id": "789", "status": "queued"}),
            Response({"status": "completed", "data": [{"type": "fbx", "url": "https://example.com/motion.fbx"}]}),
            Response(b"animation-data")]
        with patch.object(h.time, "sleep") as sleep:
            self.assertEqual(self.run_quiet(self.motion("--duration", "5", "--output-dir", str(self.root))), 0)
        sleep.assert_called_once_with(5.0)
        self.assertEqual(self.body(1), {"model": "hy-3d-motion", "id": "789"})
        result = json.loads(self.out.getvalue())
        self.assertEqual(Path(result["downloaded"][0]).read_bytes(), b"animation-data")
        self.assertIsNone(self.opener.open.call_args_list[2].args[0].get_header("Authorization"))

    def test_completed_alias_can_skip_download_and_query(self):
        self.credentials()
        self.opener.open.return_value = Response({"id": "789", "status": "completed"})
        self.assertEqual(self.run_quiet(self.args("text-to-motion", "--prompt", "walk", "--no-download")), 0)
        self.opener.open.assert_called_once()

    def test_query_keeps_id_model_and_original_region(self):
        self.credentials()
        self.opener.open.return_value = Response({"status": "completed"})
        self.run_quiet(self.args("query", "789", "--model", "hy-3d-motion", "--base-url", "https://tokenhub-intl.tencentmaas.com"))
        self.assertEqual(self.body(), {"model": "hy-3d-motion", "id": "789"})
        self.assertEqual(self.opener.open.call_args.args[0].full_url, "https://tokenhub-intl.tencentmaas.com/v1/api/3d/query")
        self.assertEqual(json.loads(self.out.getvalue())["job_id"], "789")
        self.assertEqual(self.args("query", "789").model, "rigging")

    def test_failed_query_does_not_download_or_resubmit(self):
        self.credentials()
        self.opener.open.return_value = Response({"status": "failed", "request_id": "motion-failure", "error_message": "invalid motion"})
        with self.assertRaisesRegex(h.HunyuanError, "motion-failure"):
            self.run_quiet(self.args("query", "789", "--model", "motion", "--wait", "--download"))
        self.opener.open.assert_called_once()

    def test_uncertain_or_missing_id_submission_does_not_retry(self):
        self.credentials()
        for result, message in ((TimeoutError(), "Do not automatically resubmit"), (Response({"request_id": "req-only"}), "no task ID")):
            self.opener.open.reset_mock()
            self.opener.open.side_effect = [result]
            with self.assertRaisesRegex(h.HunyuanError, message):
                self.run_quiet(self.motion())
            self.opener.open.assert_called_once()

    def test_invalid_timing_and_origin_fail_before_submission(self):
        for extra in (("--poll-interval", "1"), ("--wait-timeout", "nan"), ("--http-timeout", "-1"),
                      ("--base-url", "https://example.com")):
            with self.assertRaises(h.HunyuanError):
                self.run_quiet(self.motion(*extra))
        self.opener.open.assert_not_called()


if __name__ == "__main__":
    unittest.main()
