#!/usr/bin/env python3
"""Generate textures for existing OBJ/GLB geometry through Tencent TokenHub."""
from __future__ import annotations

import argparse
import base64
import importlib.util
from pathlib import Path
import sys
import urllib.parse


def load_client():
    path = Path(__file__).resolve().parents[2] / "hunyuan-3d-generator" / "scripts" / "hunyuan_3d.py"
    if not path.is_file():
        raise RuntimeError("Install hunyuan-3d-generator beside hunyuan-3d-material")
    spec = importlib.util.spec_from_file_location("_hunyuan_material_shared_client", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if (getattr(module, "COMMON_CLIENT_API_VERSION", None) != 1
            or "texture_image_redaction" not in getattr(module, "COMMON_CLIENT_FEATURES", ())):
        raise RuntimeError("Update adjacent hunyuan-3d-generator; texture image redaction support is required")
    return module


h = load_client()
# Conservative decimal interpretation of the guide's unspecified M/mb units.
REFERENCE_ENCODED_LIMIT = 10_000_000  # Strictly less than this number.
MULTIVIEW_RAW_LIMIT = 6_000_000
MULTIVIEW_ENCODED_LIMIT = 8_000_000


def common_parser():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--provider", choices=("tokenhub",), default="tokenhub")
    parser.add_argument("--model", type=lambda s: s.lower().removeprefix("hy-3d-"),
                        choices=("texture",), default="texture")
    parser.add_argument("--base-url", help="Official TokenHub origin; default is Guangzhou")
    parser.add_argument("--http-timeout", type=float, default=60.0)
    parser.add_argument("--dry-run", action="store_true", help="Offline validation; no credentials or API call")
    return parser


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("submit", "texture"):
        sub = commands.add_parser(name, parents=[common_parser()])
        sub.add_argument("--file-url", required=True, help="Public HTTPS URL of one OBJ/GLB geometry model")
        source = sub.add_mutually_exclusive_group(required=True)
        source.add_argument("--prompt", help="Positive texture description, at most 200 characters")
        source.add_argument("--image", type=Path, help="Local JPEG/PNG reference image")
        source.add_argument("--image-url", help="Public HTTPS reference image URL")
        sub.add_argument("--view", action="append", default=[], metavar="VIEW=FILE_OR_HTTPS_URL",
                         help="Additional view; documented for 3.1 only; no version selector is documented")
        sub.add_argument("--enable-pbr", action="store_true")
        sub.add_argument("--enable-keep-uv", action="store_true")
        sub.add_argument("--texture-size", type=int, help="Square texture edge size, 720..4096; service default 4096")
        if name == "texture":
            h._add_wait_arguments(sub)
            sub.set_defaults(output_dir=Path("hunyuan-3d-material-output"))
            sub.add_argument("--no-download", action="store_true")
    query = commands.add_parser("query", parents=[common_parser()])
    query.add_argument("job_id")
    query.add_argument("--wait", action="store_true")
    query.add_argument("--download", action="store_true")
    h._add_wait_arguments(query)
    query.set_defaults(output_dir=Path("hunyuan-3d-material-output"))
    commands.add_parser("check-auth", parents=[common_parser()])
    return parser


def local_image(path: Path, *, view=False) -> bytes:
    path = path.expanduser().resolve()
    if not path.is_file() or path.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
        raise h.HunyuanError("Texture reference/view must be an existing JPEG or PNG file")
    raw_limit = MULTIVIEW_RAW_LIMIT if view else REFERENCE_ENCODED_LIMIT // 4 * 3
    if path.stat().st_size > raw_limit:
        raise h.HunyuanError("Local texture image exceeds the permitted data size")
    data = path.read_bytes()
    if len(data) > raw_limit:
        raise h.HunyuanError("Local texture image exceeds the permitted data size")
    dimensions = h._image_dimensions(data, path.suffix.lower())
    maximum = 4999 if view else 4095
    if dimensions is None or not all(129 <= side <= maximum for side in dimensions):
        raise h.HunyuanError(f"Texture {'view' if view else 'reference'} dimensions must be 129..{maximum} pixels per side")
    if not view and 4 * ((len(data) + 2) // 3) >= REFERENCE_ENCODED_LIMIT:
        raise h.HunyuanError("Reference image Base64 must be strictly below 10000000 bytes")
    return data


def image_url(value):
    url = h._https_url(value)
    suffix = Path(urllib.parse.unquote(url.path)).suffix.lower()
    if suffix and suffix not in {".jpg", ".jpeg", ".png"}:
        raise h.HunyuanError("Texture reference/view URLs must provide JPEG or PNG images")
    return value


def build_payload(args):
    h.validate_options(args)
    if args.provider != "tokenhub" or args.model != "texture":
        raise h.HunyuanError("Texture generation requires TokenHub and hy-3d-texture")
    url = h._https_url(args.file_url)
    suffix = Path(urllib.parse.unquote(url.path)).suffix.lower()
    if suffix and suffix not in {".obj", ".glb"}:
        raise h.HunyuanError("Texture geometry input must be OBJ or GLB, not FBX/ZIP or a local path")
    prompt = (args.prompt or "").strip()
    if sum((bool(prompt), args.image is not None, bool(args.image_url))) != 1:
        raise h.HunyuanError("Provide exactly one non-empty --prompt, --image or --image-url")
    if len(prompt) > 200:
        raise h.HunyuanError("Texture prompt exceeds 200 characters")
    if args.texture_size is not None and (isinstance(args.texture_size, bool)
            or not isinstance(args.texture_size, int) or not 720 <= args.texture_size <= 4096):
        raise h.HunyuanError("--texture-size must be an integer from 720 to 4096")
    payload = {"model": "hy-3d-texture", "file_3d": {"url": args.file_url}}
    total_raw = total_encoded = 0
    if prompt:
        payload["prompt"] = prompt
    elif args.image is not None:
        data = local_image(args.image)
        encoded = base64.b64encode(data).decode("ascii")
        total_raw, total_encoded = len(data), len(encoded)
        payload["image"] = {"base64": encoded}
    else:
        payload["image"] = {"url": image_url(args.image_url)}
    views, seen = [], set()
    for value in args.view:
        view, separator, source = value.partition("=")
        if not separator or view not in h.VIEW_TYPES or not source:
            raise h.HunyuanError("--view must be VIEW=FILE_OR_HTTPS_URL; supported views: " + ", ".join(sorted(h.VIEW_TYPES)))
        if view in seen:
            raise h.HunyuanError(f"Duplicate texture view: {view}")
        seen.add(view)
        if source.lower().startswith(("http:", "https:")):
            encoded = image_url(source)
        else:
            data = local_image(Path(source), view=True)
            encoded = base64.b64encode(data).decode("ascii")
            total_raw += len(data)
            total_encoded += len(encoded)
        views.append({"view": view, "image": encoded})
    if views:
        if total_raw > MULTIVIEW_RAW_LIMIT or total_encoded > MULTIVIEW_ENCODED_LIMIT:
            raise h.HunyuanError("Combined local reference/views exceed 6000000 raw or 8000000 Base64 bytes")
        payload["multi_view_images"] = views
    if args.enable_pbr:
        payload["enable_pbr"] = True
    if args.enable_keep_uv:
        payload["enable_keep_uv"] = True
    if args.texture_size is not None:
        payload["texture_size"] = args.texture_size
    return payload


def submit_task(args):
    return h.submit_payload(build_payload(args), args)


def run(args):
    return h.run(args, submitter=submit_task, generation_commands=("submit", "texture"))


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        return run(build_parser().parse_args())
    except (h.HunyuanError, OSError) as exc:
        print(f"error: {h.redact(str(exc))}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted; query the same texture Job ID and region to resume.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
