"""Offline mesh integration tests; network is mocked and socket access blocked."""

import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.error

import hunyuan_mesh as m

h = m.h


class Response(io.BytesIO):
    def __init__(self, data, headers=None):
        super().__init__(json.dumps(data).encode() if isinstance(data, (dict, list)) else data)
        self.headers = headers or {}


class MeshTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.opener = Mock()
        for ctx in (
            patch.dict(os.environ, {}, clear=True),
            patch.object(h.urllib.request, "build_opener", return_value=self.opener),
            patch("socket.socket.connect", side_effect=AssertionError("Offline test attempted network access")),
        ):
            ctx.start()
            self.addCleanup(ctx.stop)
        self.out = io.StringIO()
        self.err = io.StringIO()

    def args(self, *values):
        return m.build_parser().parse_args(values)

    def split_args(self, *extra):
        return self.args("split", "--file-url", "https://example.com/character.fbx", *extra)

    def credentials(self):
        os.environ["TOKENHUB_API_KEY"] = "sk-offline-mesh-fixture"

    def run_quiet(self, args):
        with contextlib.redirect_stdout(self.out), contextlib.redirect_stderr(self.err):
            return m.run(args)

    def request_body(self, index=0):
        return json.loads(self.opener.open.call_args_list[index].args[0].data)

    def segmentation(self, raw):
        path = self.root / "edited segmentation.json"
        path.write_text(raw, encoding="utf-8-sig")
        return str(path)

    def test_default_payload_uses_documented_minimal_schema(self):
        self.assertEqual(m.build_payload(self.split_args()), {
            "model": "hy-3d-component", "file": {"url": "https://example.com/character.fbx"}})

    def test_signed_fbx_url_preserved_but_log_redacted(self):
        url = "https://example.com/Character.FBX?signature=private-signature"
        payload = m.build_payload(self.args("submit", "--file-url", url))
        self.assertEqual(payload["file"]["url"], url)
        self.run_quiet(self.args("submit", "--file-url", url, "--dry-run"))
        self.assertNotIn("private-signature", self.out.getvalue())

    def test_extensionless_endpoint_allowed_without_claiming_content_validation(self):
        m.build_payload(self.args("submit", "--file-url", "https://example.com/download?id=123"))

    def test_invalid_sources_never_submit(self):
        for url in ("character.fbx", "D:/models/a.fbx", "file:///a.fbx", "http://example.com/a.fbx",
                    "https://localhost/a.fbx", "https://127.0.0.1/a.fbx", "https://example.com/a.glb",
                    "https://example.com/a.obj", "https://example.com/a.zip", "https://example.com/a.blend",
                    "https://example.com/a%2Eglb", "https://user:password@example.com/a.fbx",
                    "https://example.com/a.fbx#x"):
            with self.subTest(url=url), self.assertRaises(h.HunyuanError):
                m.submit_task(self.args("submit", "--file-url", url))
        self.opener.open.assert_not_called()

    def test_dry_run_needs_no_credentials_or_network_and_creates_no_output(self):
        destination = self.root / "should-not-exist"
        self.assertEqual(self.run_quiet(self.split_args("--dry-run", "--output-dir", str(destination))), 0)
        self.opener.open.assert_not_called()
        self.assertFalse(destination.exists())
        self.assertEqual(json.loads(self.out.getvalue())["payload"]["model"], "hy-3d-component")

    def test_bad_wait_or_origin_options_fail_before_submission(self):
        for options in (("--poll-interval", "1"), ("--wait-timeout", "nan"),
                        ("--http-timeout", "-1"), ("--base-url", "https://example.com")):
            with self.subTest(options=options), self.assertRaises(h.HunyuanError):
                self.run_quiet(self.split_args(*options))
        self.opener.open.assert_not_called()

    def test_unrelated_generation_options_are_not_accepted(self):
        for options in (("--provider", "legacy"), ("--model", "3.1"), ("--result-format", "obj"),
                        ("--face-count", "10000"), ("--image", "x.png"), ("--local-file", "x.fbx")):
            with self.subTest(options=options), contextlib.redirect_stderr(self.err), self.assertRaises(SystemExit):
                self.split_args(*options)
        self.opener.open.assert_not_called()

    def test_staged_and_postprocess_only_sent_when_explicit(self):
        payload = m.build_payload(self.split_args("--enable-staged-generation"))
        self.assertTrue(payload["enable_staged_generation"])
        self.assertNotIn("enable_post_process", payload)
        self.assertNotIn("part_segmentation_info", payload)
        payload = m.build_payload(self.split_args("--enable-post-process"))
        self.assertTrue(payload["enable_post_process"])
        self.assertNotIn("enable_staged_generation", payload)

    def test_segmentation_stays_string_and_preserves_indices_without_inferred_switches(self):
        raw = '{"part_0": {"face_ids": [7, 3, 12]}, "part_2": {"face_ids": [99]}}'
        payload = m.build_payload(self.split_args("--part-segmentation-info", self.segmentation(raw)))
        self.assertIsInstance(payload["part_segmentation_info"], str)
        self.assertEqual(payload["part_segmentation_info"], raw)
        self.assertNotIn("enable_staged_generation", payload)
        self.assertNotIn("enable_post_process", payload)

    def test_invalid_segmentation_json_does_not_submit(self):
        for raw in ("bad-json", "[]", "null", '"text"', "{}", '{"part_0":NaN}', '{"x":Infinity}',
                    '{"part_0":{},"part_0":{}}', '{"part_0":{"x":1,"x":2}}', '{"x":1e999}'):
            with self.subTest(raw=raw), self.assertRaises(h.HunyuanError):
                m.submit_task(self.split_args("--part-segmentation-info", self.segmentation(raw)))
        self.opener.open.assert_not_called()

    def test_missing_segmentation_file_does_not_submit(self):
        with self.assertRaises(h.HunyuanError):
            m.submit_task(self.split_args("--part-segmentation-info", str(self.root / "missing.json")))
        self.opener.open.assert_not_called()

    def test_segmentation_dry_run_omits_large_face_data(self):
        path = self.segmentation('{"part_0":{"face_ids":[123456789]}}')
        self.run_quiet(self.split_args("--part-segmentation-info", path, "--dry-run"))
        self.assertNotIn("123456789", self.out.getvalue())
        self.assertIn("segmentation JSON omitted", self.out.getvalue())

    def test_submit_only_returns_id_without_polling(self):
        self.credentials()
        self.opener.open.return_value = Response({"id": "123", "status": "queued", "request_id": "req-1"})
        self.run_quiet(self.args("submit", "--file-url", "https://example.com/a.fbx"))
        self.opener.open.assert_called_once()
        request = self.opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://tokenhub.tencentmaas.com/v1/api/3d/submit")
        self.assertEqual(request.get_header("Authorization"), "Bearer sk-offline-mesh-fixture")
        self.assertEqual(json.loads(self.out.getvalue())["job_id"], "123")
        self.assertIn("query 123 --provider tokenhub --model component", self.err.getvalue())

    def test_staged_submit_does_not_automatically_create_second_job(self):
        self.credentials()
        self.opener.open.return_value = Response({"id": "123", "status": "completed"})
        self.run_quiet(self.split_args("--enable-staged-generation", "--no-download"))
        self.opener.open.assert_called_once()
        self.assertTrue(self.request_body()["enable_staged_generation"])

    def test_query_uses_component_model_and_original_region(self):
        self.credentials()
        self.opener.open.return_value = Response({"status": "queued"})
        self.run_quiet(self.args("query", "123", "--base-url", "https://tokenhub-intl.tencentmaas.com"))
        self.assertEqual(self.request_body(), {"model": "hy-3d-component", "id": "123"})
        self.assertEqual(self.opener.open.call_args.args[0].full_url,
                         "https://tokenhub-intl.tencentmaas.com/v1/api/3d/query")

    def test_full_lifecycle_downloads_all_components_and_segmentation_without_auth(self):
        self.credentials()
        files = [{"type": "glb", "url": "https://example.com/one/part.glb?signature=secret"},
                 {"type": "glb", "url": "https://example.com/two/part.glb"},
                 {"type": "json", "url": "https://example.com/parts.json"}]
        self.opener.open.side_effect = [Response({"id": "123", "status": "queued"}),
                                       Response({"status": "in_progress"}),
                                       Response({"status": "completed", "data": files}),
                                       Response(b"part-one"), Response(b"part-two"), Response(b'{"part_0":{}}')]
        with patch.object(h.time, "sleep") as sleep:
            self.assertEqual(self.run_quiet(self.split_args("--output-dir", str(self.root))), 0)
        self.assertEqual(sleep.call_count, 2)
        result = json.loads(self.out.getvalue())
        self.assertEqual(len(result["downloaded"]), 3)
        self.assertEqual([Path(p).read_bytes() for p in result["downloaded"]],
                         [b"part-one", b"part-two", b'{"part_0":{}}'])
        self.assertEqual(self.request_body(1), {"model": "hy-3d-component", "id": "123"})
        self.assertEqual(self.request_body(2), {"model": "hy-3d-component", "id": "123"})
        for call in self.opener.open.call_args_list[3:]:
            self.assertIsNone(call.args[0].get_header("Authorization"))
        self.assertNotIn("signature=secret", self.out.getvalue())
        self.assertNotIn("sk-offline-mesh-fixture", self.out.getvalue() + self.err.getvalue())

    def test_resume_completed_response_without_id_downloads_under_requested_id(self):
        self.credentials()
        self.opener.open.side_effect = [Response({"status": "completed", "data": [
            {"type": "glb", "url": "https://example.com/part.glb"}]}), Response(b"glb")]
        self.run_quiet(self.args("query", "123", "--wait", "--download", "--output-dir", str(self.root)))
        self.assertEqual(json.loads(self.out.getvalue())["job_id"], "123")
        self.assertTrue((self.root / "123" / "part.glb").is_file())
        self.assertEqual(self.opener.open.call_count, 2)

    def test_partial_download_keeps_other_components_and_reports_resume(self):
        self.credentials()
        self.opener.open.side_effect = [Response({"status": "completed", "data": [
            {"type": "glb", "url": "https://example.com/part-0.glb"},
            {"type": "glb", "url": "https://example.com/part-1.glb"}]}),
            Response(b"first"), urllib.error.URLError("download failed")]
        with self.assertRaisesRegex(h.HunyuanError, "query the same job"):
            self.run_quiet(self.args("query", "123", "--download", "--output-dir", str(self.root)))
        self.assertEqual((self.root / "123" / "part-0.glb").read_bytes(), b"first")
        self.assertFalse(list(self.root.rglob("*.part")))

    def test_submit_timeout_never_retries(self):
        self.credentials()
        self.opener.open.side_effect = TimeoutError()
        with self.assertRaisesRegex(h.HunyuanError, "Do not automatically resubmit"):
            self.run_quiet(self.split_args())
        self.opener.open.assert_called_once()

    def test_missing_job_id_never_retries(self):
        self.credentials()
        self.opener.open.return_value = Response({"request_id": "request-only"})
        with self.assertRaisesRegex(h.HunyuanError, "no task ID"):
            self.run_quiet(self.split_args())
        self.opener.open.assert_called_once()

    def test_failed_job_reports_request_id_and_does_not_download(self):
        self.credentials()
        self.opener.open.return_value = Response({"status": "failed", "request_id": "req-failure",
                                                "error_message": "invalid input"})
        with self.assertRaisesRegex(h.HunyuanError, "req-failure"):
            self.run_quiet(self.args("query", "123", "--wait", "--download"))
        self.opener.open.assert_called_once()

    def test_poll_timeout_preserves_job_id_without_resubmission(self):
        self.credentials()
        self.opener.open.return_value = Response({"id": "123", "status": "queued"})
        with self.assertRaisesRegex(h.HunyuanError, "123; resume with query"):
            self.run_quiet(self.split_args("--wait-timeout", "1"))
        self.opener.open.assert_called_once()

    def test_check_auth_remains_read_only(self):
        self.credentials()
        self.opener.open.return_value = Response({"data": [{"id": "hy-3d-component"}]})
        self.run_quiet(self.args("check-auth"))
        request = self.opener.open.call_args.args[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.full_url, "https://tokenhub.tencentmaas.com/v1/models")
        self.assertIsNone(request.data)
        self.opener.open.assert_called_once()


    def reduce_args(self, *extra):
        return self.args("reduce", "--file-url", "https://example.com/high-poly.glb", *extra)

    def test_retopology_minimal_payload_matches_documented_example(self):
        self.assertEqual(m.build_payload(self.reduce_args()), {
            "model": "hy-3d-retopology", "file_3d": {"url": "https://example.com/high-poly.glb"}})

    def test_command_defaults_do_not_change_existing_component_commands(self):
        parser = m.build_parser()
        for command in ("reduce", "submit", "split", "retopology", "split"):
            args = parser.parse_args([command, "--file-url", "https://example.com/a.fbx"])
            expected = "retopology" if command in {"reduce", "retopology"} else "component"
            self.assertEqual(args.model, expected)
            self.assertEqual(m.build_payload(args)["model"], "hy-3d-" + expected)
        self.assertEqual(parser.parse_args(["query", "123"]).model, "component")
        self.assertEqual(parser.parse_args(["query", "123", "--model", "hy-3d-retopology"]).model, "retopology")

    def test_retopology_supports_all_input_formats_polygon_types_and_face_levels(self):
        for fmt in ("obj", "glb", "fbx"):
            for polygon in ("triangle", "quadrilateral"):
                for level in ("high", "medium", "low"):
                    with self.subTest(fmt=fmt, polygon=polygon, level=level):
                        url = f"https://example.com/model.{fmt}"
                        payload = m.build_payload(self.args("reduce", "--file-url", url,
                            "--file-type", fmt, "--polygon-type", polygon, "--face-level", level))
                        self.assertEqual(payload, {"model": "hy-3d-retopology", "file_3d": {"url": url, "type": fmt},
                                                   "polygon_type": polygon, "face_level": level})

    def test_retopology_extensionless_signed_url_with_explicit_type(self):
        url = "https://example.com/download?signature=private-signature"
        payload = m.build_payload(self.args("reduce", "--file-url", url, "--file-type", "GLB"))
        self.assertEqual(payload["file_3d"], {"url": url, "type": "glb"})
        self.assertNotIn("private-signature", json.dumps(h.redact(payload)))

    def test_retopology_rejects_file_type_mismatch_and_unsupported_sources_before_network(self):
        for url, extra in (("https://example.com/a.fbx", ("--file-type", "glb")),
                           ("https://example.com/a.zip", ()), ("https://example.com/a.gltf", ()),
                           ("https://example.com/a.blend", ()), ("https://example.com/a.stl", ()),
                           ("https://example.com/a%2Ezip", ()), ("D:/models/a.glb", ()),
                           ("http://example.com/a.glb", ()), ("https://127.0.0.1/a.glb", ())):
            with self.subTest(url=url), self.assertRaises(h.HunyuanError):
                m.submit_task(self.args("reduce", "--file-url", url, *extra))
        self.opener.open.assert_not_called()

    def test_retopology_never_accepts_exact_counts_ratios_or_output_format_options(self):
        for options in (("--face-count", "10000"), ("--ratio", "0.5"), ("--result-format", "glb"),
                        ("--file-type", "zip"), ("--polygon-type", "quad"), ("--face-level", "extreme"),
                        ("--enable-staged-generation",), ("--enable-post-process",)):
            with self.subTest(options=options), contextlib.redirect_stderr(self.err), self.assertRaises(SystemExit):
                self.reduce_args(*options)
        self.opener.open.assert_not_called()

    def test_submit_rejects_cross_model_options_before_network(self):
        cases = [("retopology", "--enable-post-process"), ("retopology", "--enable-staged-generation"),
                 ("retopology", "--part-segmentation-info", "unused.json"),
                 ("component", "--face-level", "low"), ("component", "--polygon-type", "triangle"),
                 ("component", "--file-type", "fbx")]
        for model, *options in cases:
            with self.subTest(model=model, options=options), self.assertRaises(h.HunyuanError):
                m.submit_task(self.args("submit", "--model", model, "--file-url", "https://example.com/a.fbx", *options))
        self.opener.open.assert_not_called()

    def test_action_model_conflicts_do_not_silently_change_operation(self):
        for command, model in (("split", "retopology"), ("reduce", "component"), ("retopology", "component")):
            with self.subTest(command=command), self.assertRaises(h.HunyuanError):
                self.run_quiet(self.args(command, "--model", model, "--file-url", "https://example.com/a.fbx"))
        self.opener.open.assert_not_called()

    def test_retopology_dry_run_is_offline_and_does_not_invent_face_count(self):
        destination = self.root / "offline"
        self.run_quiet(self.reduce_args("--face-level", "low", "--dry-run", "--output-dir", str(destination)))
        payload = json.loads(self.out.getvalue())["payload"]
        self.assertEqual(payload["face_level"], "low")
        self.assertNotIn("face_count", payload)
        self.assertNotIn("file", payload)
        self.opener.open.assert_not_called()
        self.assertFalse(destination.exists())

    def test_retopology_validates_wait_settings_before_submission(self):
        for options in (("--wait-timeout", "-1"), ("--poll-interval", "nan"), ("--http-timeout", "inf")):
            with self.subTest(options=options), self.assertRaises(h.HunyuanError):
                self.run_quiet(self.reduce_args(*options))
        self.opener.open.assert_not_called()

    def test_retopology_submit_only_prints_correct_resume_model(self):
        self.credentials()
        self.opener.open.return_value = Response({"id": "987", "status": "queued"})
        self.run_quiet(self.args("submit", "--model", "hy-3d-retopology", "--file-url", "https://example.com/a.obj",
                                 "--face-level", "medium", "--polygon-type", "quadrilateral"))
        self.opener.open.assert_called_once()
        self.assertEqual(self.request_body(), {"model": "hy-3d-retopology",
            "file_3d": {"url": "https://example.com/a.obj"}, "face_level": "medium", "polygon_type": "quadrilateral"})
        self.assertEqual(json.loads(self.out.getvalue())["model"], "retopology")
        self.assertIn("query 987 --provider tokenhub --model retopology", self.err.getvalue())

    def test_retopology_lifecycle_downloads_models_and_image_with_same_model_queries(self):
        self.credentials()
        files = [{"type": fmt, "url": f"https://example.com/{name}"} for fmt, name in
                 (("obj", "reduced.obj"), ("glb", "reduced.glb"), ("image", "latent.png"))]
        self.opener.open.side_effect = [Response({"id": "987", "status": "queued"}),
            Response({"status": "completed", "data": files}),
            Response(b"obj"), Response(b"glb"), Response(b"image")]
        with patch.object(h.time, "sleep") as sleep:
            self.assertEqual(self.run_quiet(self.reduce_args("--face-level", "low", "--output-dir", str(self.root))), 0)
        sleep.assert_called_once_with(5.0)
        self.assertEqual(self.request_body(1), {"model": "hy-3d-retopology", "id": "987"})
        result = json.loads(self.out.getvalue())
        self.assertEqual(result["model"], "retopology")
        self.assertEqual([Path(p).read_bytes() for p in result["downloaded"]], [b"obj", b"glb", b"image"])
        for call in self.opener.open.call_args_list[2:]:
            self.assertIsNone(call.args[0].get_header("Authorization"))

    def test_retopology_alias_completed_submit_does_not_query_or_download_when_disabled(self):
        self.credentials()
        self.opener.open.return_value = Response({"id": "987", "status": "completed"})
        self.run_quiet(self.args("retopology", "--file-url", "https://example.com/a.fbx", "--no-download"))
        self.opener.open.assert_called_once()
        self.assertEqual(self.request_body()["model"], "hy-3d-retopology")

    def test_retopology_query_resume_retains_model_region_and_id_without_submit(self):
        self.credentials()
        self.opener.open.side_effect = [Response({"status": "in_progress"}), Response({"status": "completed", "data": [
            {"type": "glb", "url": "https://example.com/reduced.glb"}]}), Response(b"glb")]
        with patch.object(h.time, "sleep"):
            self.run_quiet(self.args("query", "987", "--model", "retopology", "--wait", "--download",
                "--base-url", "https://tokenhub-intl.tencentmaas.com", "--output-dir", str(self.root)))
        for index in (0, 1):
            request = self.opener.open.call_args_list[index].args[0]
            self.assertEqual(request.full_url, "https://tokenhub-intl.tencentmaas.com/v1/api/3d/query")
            self.assertEqual(self.request_body(index), {"model": "hy-3d-retopology", "id": "987"})
        self.assertEqual(json.loads(self.out.getvalue())["job_id"], "987")

    def test_retopology_uncertain_submission_never_retries(self):
        self.credentials()
        self.opener.open.side_effect = TimeoutError()
        with self.assertRaisesRegex(h.HunyuanError, "Do not automatically resubmit"):
            self.run_quiet(self.reduce_args())
        self.opener.open.assert_called_once()

    def test_retopology_failed_query_never_switches_model_or_resubmits(self):
        self.credentials()
        self.opener.open.return_value = Response({"status": "failed", "request_id": "retopo-failure",
                                                "error_message": "bad mesh"})
        with self.assertRaisesRegex(h.HunyuanError, "retopo-failure"):
            self.run_quiet(self.args("query", "987", "--model", "retopology", "--wait", "--download"))
        self.opener.open.assert_called_once()
        self.assertEqual(self.request_body(), {"model": "hy-3d-retopology", "id": "987"})


    def convert_args(self, *extra):
        return self.args("convert", "--file-url", "https://example.com/model.glb", "--format", "stl", *extra)

    def test_format_request_matches_documented_uppercase_schema(self):
        self.assertEqual(m.build_payload(self.convert_args()), {
            "model": "hy-3d-format", "file": {"url": "https://example.com/model.glb"}, "format": "STL"})

    def test_format_accepts_all_documented_inputs_and_outputs(self):
        for source in ("obj", "glb", "fbx"):
            for output in ("stl", "usdz", "fbx", "mp4", "gif", "obj", "glb"):
                with self.subTest(source=source, output=output):
                    url = f"https://example.com/model.{source.upper()}"
                    payload = m.build_payload(self.args("convert", "--file-url", url, "--format", output))
                    self.assertEqual(payload, {"model": "hy-3d-format", "file": {"url": url}, "format": output.upper()})

    def test_convert_default_does_not_change_component_or_retopology_defaults(self):
        parser = m.build_parser()
        for command, model, extra in (("convert", "format", ["--format", "fbx"]),
                                       ("split", "component", []), ("reduce", "retopology", []),
                                       ("submit", "component", [])):
            args = parser.parse_args([command, "--file-url", "https://example.com/a.fbx", *extra])
            self.assertEqual(m.build_payload(args)["model"], "hy-3d-" + model)
        self.assertEqual(parser.parse_args(["query", "123"]).model, "component")
        self.assertEqual(parser.parse_args(["query", "123", "--model", "hy-3d-format"]).model, "format")

    def test_format_missing_or_unsupported_target_never_submits(self):
        for options in ([], ["--format", "blend"], ["--format", "gltf"]):
            with self.subTest(options=options), contextlib.redirect_stderr(self.err), self.assertRaises(SystemExit):
                self.args("convert", "--file-url", "https://example.com/a.glb", *options)
        with self.assertRaisesRegex(h.HunyuanError, "requires --format"):
            m.submit_task(self.args("submit", "--model", "format", "--file-url", "https://example.com/a.glb"))
        self.opener.open.assert_not_called()

    def test_format_output_only_formats_are_not_accepted_as_input(self):
        for suffix in ("stl", "usdz", "gif", "mp4", "zip", "gltf", "blend"):
            with self.subTest(suffix=suffix), self.assertRaises(h.HunyuanError):
                m.submit_task(self.args("convert", "--file-url", f"https://example.com/a.{suffix}", "--format", "fbx"))
        self.opener.open.assert_not_called()

    def test_format_rejects_unsafe_urls_before_submission(self):
        for url in ("D:/a.glb", "file:///a.glb", "http://example.com/a.glb", "https://localhost/a.glb",
                    "https://10.0.0.1/a.glb", "https://user:password@example.com/a.glb", "https://example.com/a.glb#x"):
            with self.subTest(url=url), self.assertRaises(h.HunyuanError):
                m.submit_task(self.args("convert", "--file-url", url, "--format", "fbx"))
        self.opener.open.assert_not_called()

    def test_format_known_size_checks_boundary_without_sending_extra_wire_field(self):
        for size in (1, 60_000_000):
            payload = m.build_payload(self.convert_args("--input-size-bytes", str(size)))
            self.assertEqual(set(payload), {"model", "file", "format"})
            self.assertEqual(set(payload["file"]), {"url"})
        for size in (0, -1, 60_000_001, 60 * 1024 * 1024):
            with self.subTest(size=size), self.assertRaises(h.HunyuanError):
                m.submit_task(self.convert_args("--input-size-bytes", str(size)))
        self.opener.open.assert_not_called()

    def test_format_source_size_validation_is_not_applied_to_other_models(self):
        for model in ("component", "retopology"):
            with self.subTest(model=model), self.assertRaises(h.HunyuanError):
                m.submit_task(self.args("submit", "--model", model, "--file-url", "https://example.com/a.fbx",
                                        "--input-size-bytes", "100"))
        self.opener.open.assert_not_called()

    def test_format_rejects_cross_model_parameters_before_network(self):
        cases = [("format", ["--face-level", "low"]), ("format", ["--polygon-type", "triangle"]),
                 ("format", ["--file-type", "glb"]), ("format", ["--enable-post-process"]),
                 ("format", ["--enable-staged-generation"]), ("format", ["--part-segmentation-info", "unused.json"]),
                 ("component", []), ("retopology", [])]
        for model, extra in cases:
            with self.subTest(model=model, extra=extra), self.assertRaises(h.HunyuanError):
                m.submit_task(self.args("submit", "--model", model, "--file-url", "https://example.com/a.fbx",
                                        "--format", "glb", *extra))
        self.opener.open.assert_not_called()

    def test_format_model_conflicts_do_not_switch_operations(self):
        for command, model, extra in (("convert", "component", ["--format", "stl"]),
                                       ("convert", "retopology", ["--format", "stl"]),
                                       ("split", "format", []), ("reduce", "format", [])):
            with self.subTest(command=command, model=model), self.assertRaises(h.HunyuanError):
                self.run_quiet(self.args(command, "--model", model, "--file-url", "https://example.com/a.fbx", *extra))
        self.opener.open.assert_not_called()

    def test_format_dry_run_preserves_signed_url_in_payload_but_redacts_output(self):
        url = "https://example.com/download?signature=private-signature"
        args = self.args("convert", "--file-url", url, "--format", "gif", "--dry-run",
                         "--output-dir", str(self.root / "offline"))
        self.assertEqual(m.build_payload(args)["file"]["url"], url)
        self.run_quiet(args)
        self.assertNotIn("private-signature", self.out.getvalue())
        self.assertEqual(json.loads(self.out.getvalue())["payload"]["format"], "GIF")
        self.opener.open.assert_not_called()
        self.assertFalse((self.root / "offline").exists())

    def test_format_invalid_wait_configuration_prevents_paid_submit(self):
        for options in (("--poll-interval", "1"), ("--wait-timeout", "nan"), ("--http-timeout", "-1")):
            with self.subTest(options=options), self.assertRaises(h.HunyuanError):
                self.run_quiet(self.convert_args(*options))
        self.opener.open.assert_not_called()

    def test_format_submit_only_emits_correct_resume_command(self):
        self.credentials()
        self.opener.open.return_value = Response({"id": "456", "status": "queued"})
        self.run_quiet(self.args("submit", "--model", "hy-3d-format", "--file-url", "https://example.com/a.obj",
                                 "--format", "fbx"))
        self.opener.open.assert_called_once()
        self.assertEqual(self.request_body(), {"model": "hy-3d-format", "file": {"url": "https://example.com/a.obj"},
                                              "format": "FBX"})
        self.assertIn("query 456 --provider tokenhub --model format", self.err.getvalue())

    def test_format_lifecycle_handles_type_and_format_results_and_extensionless_urls(self):
        self.credentials()
        self.opener.open.side_effect = [Response({"id": "456", "status": "queued"}),
            Response({"status": "completed", "data": [
                {"type": "stl", "url": "https://example.com/output.stl"},
                {"format": "GLB", "url": "https://example.com/download?signature=private-signature"},
                {"format": "gif", "url": "https://example.com/?signature=private-signature"}]}),
            Response(b"stl"), Response(b"glb"), Response(b"gif")]
        with patch.object(h.time, "sleep") as sleep:
            self.assertEqual(self.run_quiet(self.convert_args("--output-dir", str(self.root))), 0)
        sleep.assert_called_once_with(5.0)
        self.assertEqual(self.request_body(1), {"model": "hy-3d-format", "id": "456"})
        result = json.loads(self.out.getvalue())
        self.assertEqual(result["model"], "format")
        self.assertEqual([Path(p).suffix for p in result["downloaded"]], [".stl", ".glb", ".gif"])
        self.assertEqual([Path(p).read_bytes() for p in result["downloaded"]], [b"stl", b"glb", b"gif"])
        for call in self.opener.open.call_args_list[2:]:
            self.assertIsNone(call.args[0].get_header("Authorization"))
        self.assertNotIn("private-signature", self.out.getvalue())

    def test_format_query_resume_uses_original_region_without_output_format(self):
        self.credentials()
        self.opener.open.side_effect = [Response({"status": "in_progress"}), Response({"status": "completed", "data": [
            {"format": "mp4", "url": "https://example.com/render.mp4"}]}), Response(b"video")]
        with patch.object(h.time, "sleep"):
            self.run_quiet(self.args("query", "456", "--model", "format", "--wait", "--download",
                "--base-url", "https://tokenhub-intl.tencentmaas.com", "--output-dir", str(self.root)))
        for index in (0, 1):
            request = self.opener.open.call_args_list[index].args[0]
            self.assertEqual(request.full_url, "https://tokenhub-intl.tencentmaas.com/v1/api/3d/query")
            self.assertEqual(self.request_body(index), {"model": "hy-3d-format", "id": "456"})
        self.assertEqual((self.root / "456" / "render.mp4").read_bytes(), b"video")

    def test_format_failed_or_uncertain_submit_never_retries(self):
        self.credentials()
        self.opener.open.side_effect = TimeoutError()
        with self.assertRaisesRegex(h.HunyuanError, "Do not automatically resubmit"):
            self.run_quiet(self.convert_args())
        self.opener.open.assert_called_once()
        self.opener.open.reset_mock(side_effect=True)
        self.opener.open.return_value = Response({"status": "failed", "request_id": "format-failure",
                                                "error_message": "unsupported input"})
        with self.assertRaisesRegex(h.HunyuanError, "format-failure"):
            self.run_quiet(self.args("query", "456", "--model", "format", "--wait", "--download"))
        self.opener.open.assert_called_once()

    def test_format_completed_submit_no_download_does_not_query(self):
        self.credentials()
        self.opener.open.return_value = Response({"id": "456", "status": "completed"})
        self.run_quiet(self.convert_args("--no-download"))
        self.opener.open.assert_called_once()


if __name__ == "__main__":
    unittest.main()
