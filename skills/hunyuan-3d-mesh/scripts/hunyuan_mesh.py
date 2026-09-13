#!/usr/bin/env python3
"""Split components, reduce meshes or convert formats using Tencent TokenHub 3D models."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys
import urllib.parse


def load_client():
    # Resolve only the declared adjacent dependency, never a same-named module on sys.path.
    path = Path(__file__).resolve().parents[2] / "hunyuan-3d-generator" / "scripts" / "hunyuan_3d.py"
    if not path.is_file():
        raise RuntimeError("Install hunyuan-3d-generator beside hunyuan-3d-mesh; shared client is missing")
    spec = importlib.util.spec_from_file_location("_hunyuan_mesh_shared_client", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if getattr(module, "COMMON_CLIENT_API_VERSION", None) != 1:
        raise RuntimeError("Update the adjacent hunyuan-3d-generator skill: shared client API version 1 is required")
    return module


h = load_client()
OUTPUT_FORMATS = ("STL", "USDZ", "FBX", "MP4", "GIF", "OBJ", "GLB")
# The guide says <=60m without a byte definition; use a conservative decimal limit for known sizes.
MAX_CONVERSION_INPUT_BYTES = 60_000_000


def _add_component_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--part-segmentation-info", type=Path, metavar="JSON_FILE",
                        help="Component: edited UTF-8 segmentation JSON; sent as a string")
    parser.add_argument("--enable-staged-generation", action="store_true",
                        help="Component: request one stage; never automatically submit the next")
    parser.add_argument("--enable-post-process", action="store_true",
                        help="Component: one output model link; documented surcharge: 20 credits")


def _add_retopology_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--file-type", type=str.lower, choices=("obj", "glb", "fbx"),
                        help="Retopology: optional input type, useful for extensionless URLs")
    parser.add_argument("--polygon-type", type=str.lower, choices=("triangle", "quadrilateral"),
                        help="Retopology: surface polygon type; service default is triangle")
    parser.add_argument("--face-level", type=str.lower, choices=("high", "medium", "low"),
                        help="Retopology: output face-count tier; no exact count is documented")


def _common_parser(model: str = "component") -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--provider", choices=("tokenhub",), default="tokenhub")
    common.add_argument("--model", type=lambda value: value.lower().removeprefix("hy-3d-"),
                        choices=("component", "retopology", "format"), default=model)
    common.add_argument("--base-url", help="Official TokenHub origin; default is Guangzhou")
    common.add_argument("--http-timeout", type=float, default=60.0)
    common.add_argument("--dry-run", action="store_true", help="Validate offline; no key or API call")
    return common


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("submit", "split", "reduce", "convert"):
        common = _common_parser({"reduce": "retopology", "convert": "format"}.get(command, "component"))
        sub = commands.add_parser(command, parents=[common], aliases=["retopology"] if command == "reduce" else [],
                                  help="Submit once" if command == "submit" else "Submit once, wait, and download")
        sub.add_argument("--file-url", required=True,
                         help="Public HTTPS model URL; component: FBX; retopology/format: OBJ/GLB/FBX")
        if command in {"submit", "split"}:
            _add_component_arguments(sub)
        if command in {"submit", "reduce"}:
            _add_retopology_arguments(sub)
        if command in {"submit", "convert"}:
            sub.add_argument("--format", type=str.upper, choices=OUTPUT_FORMATS, required=command == "convert",
                             help="Format conversion: required output format, including MP4/GIF media")
            sub.add_argument("--input-size-bytes", type=int,
                             help="Format conversion: optional verified input byte size, for offline limit checking")
        if command != "submit":
            h._add_wait_arguments(sub)
            sub.set_defaults(output_dir=Path("hunyuan-3d-mesh-output"))
            sub.add_argument("--no-download", action="store_true")
    query = commands.add_parser("query", parents=[_common_parser()],
                                help="Resume a job using its model: component (default), retopology or format")
    query.add_argument("job_id")
    query.add_argument("--wait", action="store_true")
    query.add_argument("--download", action="store_true")
    h._add_wait_arguments(query)
    query.set_defaults(output_dir=Path("hunyuan-3d-mesh-output"))
    commands.add_parser("check-auth", parents=[_common_parser()], help="Read TokenHub model catalog without submitting a job")
    return parser


def _reject_constant(value):
    raise ValueError("Non-finite JSON value")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def build_payload(args: argparse.Namespace) -> dict:
    h.validate_options(args)
    if args.provider != "tokenhub" or args.model not in {"component", "retopology", "format"}:
        raise h.HunyuanError("Mesh processing requires TokenHub and model component, retopology or format")
    if args.command == "split" and args.model != "component":
        raise h.HunyuanError("split uses component; use reduce or submit --model retopology for reduction")
    if args.command in {"reduce", "retopology"} and args.model != "retopology":
        raise h.HunyuanError("reduce uses retopology; use split for component generation")
    if args.command == "convert" and args.model != "format":
        raise h.HunyuanError("convert requires model format")
    if args.model == "format":
        return build_format_payload(args)
    if any(getattr(args, field, None) is not None for field in ("format", "input_size_bytes")):
        raise h.HunyuanError("--format and --input-size-bytes require --model format")
    if args.model == "retopology":
        return build_retopology_payload(args)
    if any(getattr(args, field, None) is not None for field in ("file_type", "polygon_type", "face_level")):
        raise h.HunyuanError("--file-type, --polygon-type and --face-level require --model retopology")
    url = h._https_url(args.file_url)
    suffix = Path(urllib.parse.unquote(url.path)).suffix.lower()
    # Reject known incompatible formats, but allow extensionless signed download endpoints.
    if suffix and suffix != ".fbx":
        raise h.HunyuanError("Component input must be FBX; convert the model before providing its URL")
    payload = {"model": "hy-3d-component", "file": {"url": args.file_url}}
    if args.part_segmentation_info is not None:
        path = args.part_segmentation_info.expanduser().resolve()
        try:
            raw = path.read_text(encoding="utf-8-sig")
            data = json.loads(raw, parse_constant=_reject_constant, object_pairs_hook=_unique_object)
            json.dumps(data, allow_nan=False)
        except (OSError, UnicodeError, ValueError, RecursionError) as exc:
            raise h.HunyuanError("Segmentation file must be readable UTF-8 JSON with unique keys and finite values") from exc
        if not isinstance(data, dict) or not data:
            raise h.HunyuanError("Segmentation data must be a non-empty JSON object")
        # The API declares STRING, not OBJECT. Preserve provider fields and face IDs unchanged.
        payload["part_segmentation_info"] = raw
    if args.enable_staged_generation:
        payload["enable_staged_generation"] = True
    if args.enable_post_process:
        payload["enable_post_process"] = True
    return payload


def build_retopology_payload(args: argparse.Namespace) -> dict:
    if any(getattr(args, field, None) for field in
           ("part_segmentation_info", "enable_staged_generation", "enable_post_process")):
        raise h.HunyuanError("Retopology does not accept component segmentation, staged generation or post-processing")
    url = h._https_url(args.file_url)
    suffix = Path(urllib.parse.unquote(url.path)).suffix.lower()
    if suffix and suffix not in {".obj", ".glb", ".fbx"}:
        raise h.HunyuanError("Retopology input must be OBJ, GLB or FBX, not ZIP/GLTF or a local path")
    file_type = args.file_type
    if file_type is not None and file_type not in {"obj", "glb", "fbx"}:
        raise h.HunyuanError("--file-type must be obj, glb or fbx")
    if file_type and suffix and file_type != suffix[1:]:
        raise h.HunyuanError("--file-type conflicts with the model URL extension")
    file_3d = {"url": args.file_url}
    if file_type:
        file_3d["type"] = file_type
    payload = {"model": "hy-3d-retopology", "file_3d": file_3d}
    for field, allowed in (("polygon_type", {"triangle", "quadrilateral"}),
                           ("face_level", {"high", "medium", "low"})):
        value = getattr(args, field)
        if value is not None:
            if value not in allowed:
                raise h.HunyuanError(f"Invalid --{field.replace('_', '-')}")
            payload[field] = value
    return payload


def build_format_payload(args: argparse.Namespace) -> dict:
    if any(getattr(args, field, None) for field in
           ("part_segmentation_info", "enable_staged_generation", "enable_post_process")):
        raise h.HunyuanError("Format conversion does not accept component segmentation or post-processing options")
    if any(getattr(args, field, None) is not None for field in ("file_type", "polygon_type", "face_level")):
        raise h.HunyuanError("Format conversion does not accept retopology file-type, polygon-type or face-level options")
    if args.format not in OUTPUT_FORMATS:
        raise h.HunyuanError("Format conversion requires --format " + "/".join(OUTPUT_FORMATS))
    url = h._https_url(args.file_url)
    suffix = Path(urllib.parse.unquote(url.path)).suffix.lower()
    if suffix and suffix not in {".obj", ".glb", ".fbx"}:
        raise h.HunyuanError("Format conversion input must be OBJ, GLB or FBX; output formats are not all valid inputs")
    size = args.input_size_bytes
    if size is not None and (isinstance(size, bool) or not isinstance(size, int)
                             or not 0 < size <= MAX_CONVERSION_INPUT_BYTES):
        raise h.HunyuanError("--input-size-bytes must be 1..60000000 (conservative interpretation of the documented 60m limit)")
    # Size is caller-verified metadata for local validation, not a documented wire field.
    return {"model": "hy-3d-format", "file": {"url": args.file_url}, "format": args.format}


def submit_task(args: argparse.Namespace):
    payload = build_payload(args)
    return h.submit_payload(payload, args)


def run(args: argparse.Namespace) -> int:
    return h.run(args, submitter=submit_task, generation_commands=("submit", "split", "reduce", "retopology", "convert"))


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        return run(build_parser().parse_args())
    except (h.HunyuanError, OSError) as exc:
        print(f"error: {h.redact(str(exc))}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted; use query with the same Job ID, --model and TokenHub region to resume.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
