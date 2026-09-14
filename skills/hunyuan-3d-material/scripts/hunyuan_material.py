#!/usr/bin/env python3
"""Generate textures or unwrap UVs for existing models through Tencent TokenHub."""
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
UV_INPUT_LIMITS = {"input_size_bytes": 60_000_000, "face_count": 30_000, "component_count": 100}


def common_parser(model="texture"):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--provider", choices=("tokenhub",), default="tokenhub")
    parser.add_argument("--model", type=lambda s: s.lower().removeprefix("hy-3d-"),
                        choices=("texture", "uv"), default=model)
    parser.add_argument("--base-url", help="Official TokenHub origin; default is Guangzhou")
    parser.add_argument("--http-timeout", type=float, default=60.0)
    parser.add_argument("--dry-run", action="store_true", help="Offline validation; no credentials or API call")
    return parser


def add_texture_arguments(parser, *, required):
    source = parser.add_mutually_exclusive_group(required=required)
    source.add_argument("--prompt", help="Texture: positive description, at most 200 characters")
    source.add_argument("--image", type=Path, help="Texture: local JPEG/PNG reference image")
    source.add_argument("--image-url", help="Texture: public HTTPS reference image URL")
    parser.add_argument("--view", action="append", default=[], metavar="VIEW=FILE_OR_HTTPS_URL",
                        help="Texture: additional view; documented for 3.1 only, without a version selector")
    parser.add_argument("--enable-pbr", action="store_true")
    parser.add_argument("--enable-keep-uv", action="store_true")
    parser.add_argument("--texture-size", type=int, help="Texture: square edge size, 720..4096; default 4096")


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("submit", "texture", "uv"):
        sub = commands.add_parser(name, parents=[common_parser("uv" if name == "uv" else "texture")],
                                  aliases=["unwrap"] if name == "uv" else [])
        sub.add_argument("--file-url", required=True, help="Public HTTPS model URL: texture OBJ/GLB; UV FBX/OBJ/GLB")
        if name in {"submit", "texture"}:
            add_texture_arguments(sub, required=name == "texture")
        if name in {"submit", "uv"}:
            sub.add_argument("--file-type", type=str.lower, choices=("fbx", "obj", "glb"),
                             help="UV: optional input type, useful for extensionless URLs")
            sub.add_argument("--input-size-bytes", type=int, help="UV: verified byte size, local check only; <=60000000")
            sub.add_argument("--face-count", type=int, help="UV: verified input face count, local check only; <=30000")
            sub.add_argument("--component-count", type=int,
                             help="UV: verified component/connected-region count, local check only; <=100")
        if name != "submit":
            h._add_wait_arguments(sub)
            sub.set_defaults(output_dir=Path("hunyuan-3d-material-output"))
            sub.add_argument("--no-download", action="store_true")
    query = commands.add_parser("query", parents=[common_parser()],
                                help="Resume with the original model: texture (default) or uv")
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
    if args.provider != "tokenhub" or args.model not in {"texture", "uv"}:
        raise h.HunyuanError("Material processing requires TokenHub and model texture or uv")
    if args.command == "texture" and args.model != "texture":
        raise h.HunyuanError("texture requires --model texture; use uv for UV unwrapping")
    if args.command in {"uv", "unwrap"} and args.model != "uv":
        raise h.HunyuanError("uv/unwrap requires --model uv; use texture for texture generation")
    if args.model == "uv":
        return build_uv_payload(args)
    if any(getattr(args, field, None) is not None for field in ("file_type", *UV_INPUT_LIMITS)):
        raise h.HunyuanError("--file-type, --input-size-bytes, --face-count and --component-count require --model uv")
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


def build_uv_payload(args):
    if (any(getattr(args, field, None) is not None for field in ("prompt", "image", "image_url", "texture_size"))
            or any(getattr(args, field, None) for field in ("view", "enable_pbr", "enable_keep_uv"))):
        raise h.HunyuanError("UV unwrapping does not accept texture prompts, images, views, PBR, keep-UV or texture size")
    url = h._https_url(args.file_url)
    suffix = Path(urllib.parse.unquote(url.path)).suffix.lower()
    if suffix and suffix not in {".fbx", ".obj", ".glb"}:
        raise h.HunyuanError("UV input must be FBX, OBJ or GLB, not ZIP/GLTF or a local path")
    file_type = args.file_type
    if file_type is not None and file_type not in {"fbx", "obj", "glb"}:
        raise h.HunyuanError("--file-type must be fbx, obj or glb")
    if file_type and suffix and file_type != suffix[1:]:
        raise h.HunyuanError("--file-type conflicts with the model URL extension")
    for field, maximum in UV_INPUT_LIMITS.items():
        value = getattr(args, field)
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= maximum):
            raise h.HunyuanError(f"--{field.replace('_', '-')} must be an integer from 1 to {maximum}; preprocess input if needed")
    file = {"url": args.file_url}
    if file_type:
        file["type"] = file_type
    # Known input measurements are local checks, not API fields or automatic preprocessing requests.
    return {"model": "hy-3d-uv", "file": file}


def submit_task(args):
    return h.submit_payload(build_payload(args), args)


def run(args):
    return h.run(args, submitter=submit_task, generation_commands=("submit", "texture", "uv", "unwrap"))


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
        print("Interrupted; query the same Job ID, --model and TokenHub region to resume.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
