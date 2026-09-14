"""Offline texture tests; external connections are blocked and HTTP responses simulated."""
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
import zlib

import hunyuan_material as m
h = m.h


def png_bytes(width=256, height=256):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress((b"\0" + b"\x70\x80\x90" * width) * height)) + chunk(b"IEND", b""))


class Response(io.BytesIO):
    def __init__(self, data):
        super().__init__(json.dumps(data).encode() if isinstance(data, (dict, list)) else data)
        self.headers = {}


class TextureTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.ref = self.root / "reference image.png"
        self.ref.write_bytes(png_bytes())
        self.opener = Mock()
        for ctx in (patch.dict(os.environ, {}, clear=True),
                    patch.object(h.urllib.request, "build_opener", return_value=self.opener),
                    patch("socket.socket.connect", side_effect=AssertionError("Offline test attempted network"))):
            ctx.start()
            self.addCleanup(ctx.stop)
        self.out, self.err = io.StringIO(), io.StringIO()

    def args(self, *args):
        return m.build_parser().parse_args(args)

    def texture(self, *args):
        return self.args("texture", "--file-url", "https://example.com/geometry.glb", *args)

    def run_quiet(self, args):
        with contextlib.redirect_stdout(self.out), contextlib.redirect_stderr(self.err):
            return m.run(args)

    def credentials(self):
        os.environ["TOKENHUB_API_KEY"] = "sk-offline-texture-fixture"

    def body(self, index=0):
        return json.loads(self.opener.open.call_args_list[index].args[0].data)

    def test_remote_reference_matches_official_example(self):
        self.assertEqual(m.build_payload(self.args("submit", "--file-url", "https://example.com/test.obj",
            "--image-url", "https://example.com/test.png")), {"model": "hy-3d-texture",
            "file_3d": {"url": "https://example.com/test.obj"}, "image": {"url": "https://example.com/test.png"}})

    def test_prompt_default_omits_optional_options(self):
        self.assertEqual(m.build_payload(self.texture("--prompt", "陶瓷釉面")), {"model": "hy-3d-texture",
            "file_3d": {"url": "https://example.com/geometry.glb"}, "prompt": "陶瓷釉面"})

    def test_local_reference_uses_nested_raw_base64(self):
        payload = m.build_payload(self.texture("--image", str(self.ref)))
        self.assertEqual(base64.b64decode(payload["image"]["base64"]), self.ref.read_bytes())
        self.assertNotIn("image_base64", payload)
        self.assertNotIn("image_url", payload)

    def test_prompt_character_limit_is_not_utf8_byte_limit(self):
        m.build_payload(self.texture("--prompt", "猫" * 200))
        for prompt in ("猫" * 201, "   "):
            with self.assertRaises(h.HunyuanError):
                m.submit_task(self.texture("--prompt", prompt))
        self.opener.open.assert_not_called()

    def test_reference_and_prompt_are_mutually_exclusive_and_one_required(self):
        for extra in ([], ["--prompt", "猫", "--image", str(self.ref)],
                      ["--image", str(self.ref), "--image-url", "https://example.com/a.png"],
                      ["--view", "left=https://example.com/a.png"]):
            with contextlib.redirect_stderr(self.err), self.assertRaises(SystemExit):
                self.texture(*extra)
        self.opener.open.assert_not_called()

    def test_texture_size_supports_full_integer_range_including_non_powers_of_two(self):
        for size in (720, 721, 1024, 2048, 4096):
            payload = m.build_payload(self.texture("--prompt", "猫", "--texture-size", str(size),
                                                 "--enable-pbr", "--enable-keep-uv"))
            self.assertEqual(payload["texture_size"], size)
            self.assertTrue(payload["enable_pbr"])
            self.assertTrue(payload["enable_keep_uv"])
        for size in (719, 4097, 0):
            with self.assertRaises(h.HunyuanError):
                m.submit_task(self.texture("--prompt", "猫", "--texture-size", str(size)))
        self.opener.open.assert_not_called()

    def test_pbr_and_keep_uv_are_independent_explicit_options(self):
        pbr = m.build_payload(self.texture("--prompt", "猫", "--enable-pbr"))
        uv = m.build_payload(self.texture("--prompt", "猫", "--enable-keep-uv"))
        self.assertNotIn("enable_keep_uv", pbr)
        self.assertNotIn("enable_pbr", uv)

    def test_rejects_unsupported_model_sources_and_unsafe_urls_before_submit(self):
        for url in ("https://example.com/a.fbx", "https://example.com/a.zip", "https://example.com/a.gltf",
                    "https://example.com/a%2Efbx", "D:/a.obj", "http://example.com/a.obj",
                    "https://localhost/a.obj", "https://127.0.0.1/a.obj", "https://user:pass@example.com/a.obj"):
            with self.subTest(url=url), self.assertRaises(h.HunyuanError):
                m.submit_task(self.args("submit", "--file-url", url, "--prompt", "木纹"))
        self.opener.open.assert_not_called()

    def test_reference_dimensions_are_strict_128_4096(self):
        for side in (129, 4095):
            self.ref.write_bytes(png_bytes(side, 129))
            m.build_payload(self.texture("--image", str(self.ref)))
        for side in (128, 4096):
            self.ref.write_bytes(png_bytes(129, side))
            with self.assertRaises(h.HunyuanError):
                m.submit_task(self.texture("--image", str(self.ref)))
        self.opener.open.assert_not_called()

    def test_view_dimensions_are_strict_128_5000(self):
        for side in (129, 4999):
            self.ref.write_bytes(png_bytes(side, 129))
            m.build_payload(self.texture("--prompt", "木纹", "--view", f"back={self.ref}"))
        for side in (128, 5000):
            self.ref.write_bytes(png_bytes(side, 129))
            with self.assertRaises(h.HunyuanError):
                m.submit_task(self.texture("--prompt", "木纹", "--view", f"back={self.ref}"))
        self.opener.open.assert_not_called()

    def test_reference_base64_size_limit_is_strict(self):
        header = png_bytes()
        self.ref.write_bytes(header + b"x" * (7_499_997 - len(header)))
        self.assertEqual(len(m.local_image(self.ref)), 7_499_997)
        self.ref.write_bytes(header + b"x" * (7_500_000 - len(header)))
        with self.assertRaisesRegex(h.HunyuanError, "strictly below"):
            m.local_image(self.ref)

    def test_rejects_missing_corrupt_and_webp_local_images(self):
        self.ref.write_bytes(b"corrupt")
        webp = self.root / "a.webp"
        webp.write_bytes(png_bytes())
        for path in (self.ref, webp, self.root / "missing.png"):
            with self.subTest(path=path.name), self.assertRaises(h.HunyuanError):
                m.submit_task(self.texture("--image", str(path)))
        self.opener.open.assert_not_called()

    def test_remote_reference_urls_reject_known_unsupported_formats(self):
        for url in ("https://example.com/a.webp", "http://example.com/a.png", "https://localhost/a.png"):
            with self.assertRaises(h.HunyuanError):
                m.submit_task(self.texture("--image-url", url))
        self.opener.open.assert_not_called()

    def test_multiview_uses_texture_schema_and_preserves_signed_urls(self):
        remote = "https://example.com/back.png?signature=private-signature"
        payload = m.build_payload(self.texture("--image", str(self.ref), "--view", f"left={self.ref}",
                                               "--view", "back=" + remote))
        views = payload["multi_view_images"]
        self.assertEqual(set(views[0]), {"view", "image"})
        self.assertEqual(base64.b64decode(views[0]["image"]), self.ref.read_bytes())
        self.assertEqual(views[1], {"view": "back", "image": remote})
        self.assertNotIn("version", payload)

    def test_multiview_checks_duplicates_unknown_views_and_empty_sources(self):
        for views in (("front=x.png",), ("left=",), ("left=x.png", "left=x.png"),
                      ("left=https://example.com/a.png", "left=https://example.com/b.png")):
            with self.subTest(views=views), self.assertRaises(h.HunyuanError):
                m.submit_task(self.texture("--prompt", "木纹", *[arg for value in views for arg in ("--view", value)]))
        self.opener.open.assert_not_called()

    def test_multiview_counts_primary_reference_in_aggregate_limit(self):
        with patch.object(m, "local_image", return_value=b"x" * 3_000_000):
            m.build_payload(self.texture("--image", str(self.ref), "--view", f"left={self.ref}"))
        with patch.object(m, "local_image", return_value=b"x" * 3_000_001):
            with self.assertRaisesRegex(h.HunyuanError, "Combined"):
                m.submit_task(self.texture("--image", str(self.ref), "--view", f"left={self.ref}"))
        self.opener.open.assert_not_called()

    def test_all_seven_documented_views_supported_without_version_parameter(self):
        extra = [arg for view in sorted(h.VIEW_TYPES) for arg in ("--view", f"{view}=https://example.com/{view}.png")]
        payload = m.build_payload(self.texture("--prompt", "金属", *extra))
        self.assertEqual(len(payload["multi_view_images"]), 7)
        self.assertEqual(payload["model"], "hy-3d-texture")

    def test_dry_run_redacts_nested_and_multiview_base64_without_key_network_or_output(self):
        destination = self.root / "offline"
        encoded = base64.b64encode(self.ref.read_bytes()).decode()
        self.assertEqual(self.run_quiet(self.texture("--image", str(self.ref), "--view", f"left={self.ref}",
            "--view", "back=https://example.com/back.png?signature=private-signature", "--dry-run",
            "--output-dir", str(destination))), 0)
        self.assertNotIn(encoded, self.out.getvalue())
        self.assertNotIn("private-signature", self.out.getvalue())
        self.assertFalse(destination.exists())
        self.opener.open.assert_not_called()

    def test_invalid_timing_and_origin_fail_before_submission(self):
        for extra in (("--poll-interval", "1"), ("--wait-timeout", "nan"), ("--http-timeout", "-1"),
                      ("--base-url", "https://example.com")):
            with self.assertRaises(h.HunyuanError):
                self.run_quiet(self.texture("--prompt", "木纹", *extra))
        self.opener.open.assert_not_called()

    def test_generation_and_mesh_only_flags_are_not_accepted(self):
        for extra in (("--face-count", "10000"), ("--result-format", "fbx"), ("--file-type", "obj"),
                      ("--provider", "legacy"), ("--model", "3.1"), ("--version", "3.1")):
            with contextlib.redirect_stderr(self.err), self.assertRaises(SystemExit):
                self.texture("--prompt", "木纹", *extra)
        self.opener.open.assert_not_called()

    def test_submit_only_uses_bearer_and_emits_texture_resume(self):
        self.credentials()
        self.opener.open.return_value = Response({"id": "789", "status": "queued"})
        self.run_quiet(self.args("submit", "--file-url", "https://example.com/a.obj", "--prompt", "木纹"))
        self.opener.open.assert_called_once()
        request = self.opener.open.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer sk-offline-texture-fixture")
        self.assertEqual(request.full_url, "https://tokenhub.tencentmaas.com/v1/api/3d/submit")
        self.assertIn("query 789 --provider tokenhub --model texture", self.err.getvalue())

    def test_lifecycle_downloads_obj_glb_images_and_mtl_without_resource_auth(self):
        self.credentials()
        files = [{"type": kind, "url": f"https://example.com/{name}"} for kind, name in
                 (("obj", "model.obj"), ("glb", "model.glb"), ("image", "latent.png"),
                  ("texture_image", "texture.png"), ("mtl", "model.mtl"))]
        self.opener.open.side_effect = [Response({"id": "789", "status": "queued"}),
            Response({"status": "completed", "data": files}), *[Response(name.encode()) for name in ("obj", "glb", "latent", "texture", "mtl")]]
        with patch.object(h.time, "sleep") as sleep:
            self.assertEqual(self.run_quiet(self.texture("--prompt", "木纹", "--output-dir", str(self.root))), 0)
        sleep.assert_called_once_with(5.0)
        self.assertEqual(self.body(1), {"model": "hy-3d-texture", "id": "789"})
        result = json.loads(self.out.getvalue())
        self.assertEqual(len(result["downloaded"]), 5)
        self.assertEqual([Path(p).read_bytes() for p in result["downloaded"]], [b"obj", b"glb", b"latent", b"texture", b"mtl"])
        for call in self.opener.open.call_args_list[2:]:
            self.assertIsNone(call.args[0].get_header("Authorization"))

    def test_completed_submit_can_skip_download_and_query(self):
        self.credentials()
        self.opener.open.return_value = Response({"id": "789", "status": "completed"})
        self.run_quiet(self.texture("--prompt", "木纹", "--no-download"))
        self.opener.open.assert_called_once()

    def test_query_preserves_model_region_and_id_when_response_omits_id(self):
        self.credentials()
        self.opener.open.return_value = Response({"status": "completed"})
        self.run_quiet(self.args("query", "789", "--model", "hy-3d-texture", "--base-url", "https://tokenhub-intl.tencentmaas.com"))
        self.assertEqual(self.body(), {"model": "hy-3d-texture", "id": "789"})
        self.assertEqual(self.opener.open.call_args.args[0].full_url, "https://tokenhub-intl.tencentmaas.com/v1/api/3d/query")
        self.assertEqual(json.loads(self.out.getvalue())["job_id"], "789")

    def test_failed_query_does_not_download_or_resubmit(self):
        self.credentials()
        self.opener.open.return_value = Response({"status": "failed", "request_id": "texture-failure", "error_message": "invalid model"})
        with self.assertRaisesRegex(h.HunyuanError, "texture-failure"):
            self.run_quiet(self.args("query", "789", "--wait", "--download"))
        self.opener.open.assert_called_once()

    def test_uncertain_submit_does_not_retry(self):
        self.credentials()
        self.opener.open.side_effect = TimeoutError()
        with self.assertRaisesRegex(h.HunyuanError, "Do not automatically resubmit"):
            self.run_quiet(self.texture("--prompt", "木纹"))
        self.opener.open.assert_called_once()

    def test_missing_job_id_does_not_retry(self):
        self.credentials()
        self.opener.open.return_value = Response({"request_id": "req-only"})
        with self.assertRaisesRegex(h.HunyuanError, "no task ID"):
            self.run_quiet(self.texture("--prompt", "木纹"))
        self.opener.open.assert_called_once()

    def test_partial_texture_download_keeps_successful_assets(self):
        self.credentials()
        self.opener.open.side_effect = [Response({"status": "completed", "data": [
            {"type": "obj", "url": "https://example.com/model.obj"},
            {"type": "texture_image", "url": "https://example.com/texture.png"}]}),
            Response(b"obj"), urllib.error.URLError("download failed")]
        with self.assertRaisesRegex(h.HunyuanError, "query the same job"):
            self.run_quiet(self.args("query", "789", "--download", "--output-dir", str(self.root)))
        self.assertEqual((self.root / "789" / "model.obj").read_bytes(), b"obj")
        self.assertFalse(list(self.root.rglob("*.part")))

    def test_check_auth_only_reads_catalog(self):
        self.credentials()
        self.opener.open.return_value = Response({"data": [{"id": "hy-3d-texture"}]})
        self.run_quiet(self.args("check-auth"))
        request = self.opener.open.call_args.args[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertTrue(request.full_url.endswith("/v1/models"))
        self.opener.open.assert_called_once()


if __name__ == "__main__":
    unittest.main()
