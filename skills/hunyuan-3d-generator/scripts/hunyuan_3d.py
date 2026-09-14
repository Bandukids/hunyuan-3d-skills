#!/usr/bin/env python3
"""Generate Hunyuan 3D Professional/Express assets via TokenHub, with legacy AI3D compatibility."""

from __future__ import annotations

import argparse
import base64
import ipaddress
import http.client
import json
import math
import mimetypes
import os
import re
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_BASE_URL = "https://tokenhub.tencentmaas.com"
LEGACY_BASE_URL = "https://api.ai3d.cloud.tencent.com"
KEY_ENV_NAMES = ("TOKENHUB_API_KEY", "HUNYUAN_3D_API_KEY", "TENCENT_HUNYUAN_API_KEY")
API_HOSTS = {
    "tokenhub": {"tokenhub.tencentmaas.com", "tokenhub.tencentmaas.cn",
                 "tokenhub-intl.tencentmaas.com", "tokenhub-intl.tencentmaas.cn"},
    "legacy": {"api.ai3d.cloud.tencent.com"},
}
VIEW_TYPES = {"left", "right", "back", "top", "bottom", "left_front", "right_front"}
TYPE_NAMES = {"normal": "Normal", "low_poly": "LowPoly", "geometry": "Geometry", "sketch": "Sketch"}
TERMINAL_STATUSES = {"DONE", "FAIL"}
RUNNING_STATUSES = {"WAIT", "RUN"}
SUPPORTED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
COMMON_CLIENT_API_VERSION = 1  # Shared by the adjacent hunyuan-3d-mesh skill.
COMMON_CLIENT_FEATURES = frozenset({"texture_image_redaction"})


class HunyuanError(RuntimeError):
    """An actionable API or validation error."""


def _common_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--provider", choices=("tokenhub", "legacy"), default="tokenhub")
    parser.add_argument("--base-url", help="Official API origin; default is TokenHub Guangzhou")
    parser.add_argument("--model", type=lambda s: s.lower().removeprefix("hy-3d-"),
                        choices=("3.0", "3.1", "express"), default="3.1")
    parser.add_argument("--http-timeout", type=float, default=60.0)
    parser.add_argument("--dry-run", action="store_true", help="Validate and print a redacted request offline")
    return parser


def _add_generation_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--prompt", help="Chinese positive prompt, up to 1024 characters")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--image", type=Path, help="Local JPEG, PNG, or WebP front/reference image")
    source.add_argument("--image-url", help="Public HTTPS front/reference image URL")
    parser.add_argument("--image-transport", choices=("data-url", "raw-base64"),
                        help="Legacy local-image encoding; TokenHub always uses raw Base64")
    parser.add_argument("--view", action="append", default=[], metavar="VIEW=FILE_OR_HTTPS_URL",
                        help="TokenHub additional view; repeat for distinct views")
    parser.add_argument("--generate-type", type=lambda s: TYPE_NAMES.get(s.lower(), s),
                        choices=("Normal", "LowPoly", "Geometry", "Sketch"), default="Normal")
    parser.add_argument("--enable-pbr", action="store_true")
    parser.add_argument("--enable-geometry", action="store_true",
                        help="Express only: create untextured geometry (also --generate-type Geometry)")
    parser.add_argument("--face-count", type=int)
    parser.add_argument("--polygon-type", choices=("triangle", "quadrilateral"))
    parser.add_argument("--result-format", type=str.upper,
                        choices=("OBJ", "GLB", "STL", "USDZ", "FBX", "MP4"),
                        help="Professional: STL/USDZ/FBX; Express additionally accepts OBJ/GLB/MP4")


def _add_wait_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--poll-interval", type=float, default=5.0)
    parser.add_argument("--wait-timeout", type=float, default=1800.0)
    parser.add_argument("--output-dir", type=Path, default=Path("hunyuan-3d-output"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    common = _common_parser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    submit = subparsers.add_parser("submit", parents=[common], help="Submit once and return its Job ID")
    _add_generation_arguments(submit)
    generate = subparsers.add_parser("generate", aliases=["image-to-3d"], parents=[common],
                                    help="Submit once, wait, and download (image-to-3d requires an image)")
    _add_generation_arguments(generate)
    _add_wait_arguments(generate)
    generate.add_argument("--no-download", action="store_true")
    query = subparsers.add_parser("query", parents=[common], help="Inspect or resume an existing job")
    query.add_argument("job_id")
    query.add_argument("--wait", action="store_true")
    query.add_argument("--download", action="store_true")
    _add_wait_arguments(query)
    subparsers.add_parser("check-auth", parents=[common],
                         help="TokenHub GET /v1/models; does not submit a generation job")
    return parser



def _jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    if not data.startswith(b"\xff\xd8"):
        return None
    offset = 2
    while offset + 4 <= len(data):
        if data[offset] != 0xFF:
            offset += 1
            continue
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset >= len(data):
            break
        marker = data[offset]
        offset += 1
        if marker in {0xD8, 0xD9}:
            continue
        if offset + 2 > len(data):
            break
        segment_length = struct.unpack(">H", data[offset : offset + 2])[0]
        if segment_length < 2 or offset + segment_length > len(data):
            break
        if marker in {
            0xC0,
            0xC1,
            0xC2,
            0xC3,
            0xC5,
            0xC6,
            0xC7,
            0xC9,
            0xCA,
            0xCB,
            0xCD,
            0xCE,
            0xCF,
        }:
            if segment_length < 7:
                break
            height, width = struct.unpack(">HH", data[offset + 3 : offset + 7])
            return width, height
        offset += segment_length
    return None


def _webp_dimensions(data: bytes) -> tuple[int, int] | None:
    if len(data) < 25 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return None
    chunk = data[12:16]
    if chunk == b"VP8X" and len(data) >= 30:
        width = 1 + int.from_bytes(data[24:27], "little")
        height = 1 + int.from_bytes(data[27:30], "little")
        return width, height
    if chunk == b"VP8 " and len(data) >= 30 and data[23:26] == b"\x9d\x01\x2a":
        width = int.from_bytes(data[26:28], "little") & 0x3FFF
        height = int.from_bytes(data[28:30], "little") & 0x3FFF
        return width, height
    if chunk == b"VP8L" and len(data) >= 25 and data[20] == 0x2F:
        bits = int.from_bytes(data[21:25], "little")
        width = (bits & 0x3FFF) + 1
        height = ((bits >> 14) & 0x3FFF) + 1
        return width, height
    return None


def _image_dimensions(data: bytes, suffix: str) -> tuple[int, int] | None:
    if suffix == ".png" and len(data) >= 24 and data.startswith(b"\x89PNG\r\n\x1a\n"):
        return struct.unpack(">II", data[16:24])
    if suffix in {".jpg", ".jpeg"}:
        return _jpeg_dimensions(data)
    if suffix == ".webp":
        return _webp_dimensions(data)
    return None


def _validated_image(path: Path, *, strict: bool = False,
                     multiview: bool = False) -> tuple[bytes, str]:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise HunyuanError(f"Reference image does not exist: {resolved}")
    suffix = resolved.suffix.lower()
    allowed = {".jpg", ".jpeg", ".png"} if multiview else SUPPORTED_IMAGE_SUFFIXES
    if suffix not in allowed:
        raise HunyuanError("Multi-view requires JPEG/PNG" if multiview else "Image must be JPEG/PNG/WebP")
    if resolved.stat().st_size > 6 * 1024 * 1024:
        raise HunyuanError("Local image exceeds the 6 MiB raw-image limit")
    data = resolved.read_bytes()
    if len(data) > 6 * 1024 * 1024:
        raise HunyuanError("Local image exceeds the 6 MiB raw-image limit")
    dimensions = _image_dimensions(data, suffix)
    if dimensions is None:
        raise HunyuanError(f"Could not read image dimensions: {resolved}")
    lower, upper = (129, 4999) if strict or multiview else (128, 5000)
    if not all(lower <= side <= upper for side in dimensions):
        raise HunyuanError(f"Image dimensions must be {lower}..{upper} pixels per side; got {dimensions}")
    return data, mimetypes.guess_type(resolved.name)[0] or "image/jpeg"


def _https_url(value: str) -> urllib.parse.SplitResult:
    try:
        url = urllib.parse.urlsplit(value)
        if (url.scheme != "https" or not url.hostname or url.username or url.password
                or url.fragment or any(ch.isspace() for ch in value)):
            raise ValueError()
        _ = url.port
        host = url.hostname.lower().rstrip(".")
        if host == "localhost" or host.endswith((".localhost", ".local")):
            raise ValueError()
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            raise ValueError()
        return url
    except ValueError as exc:
        raise HunyuanError("Expected a public HTTPS URL without credentials or fragments") from exc


def _base_url(args: argparse.Namespace) -> str:
    env = "TOKENHUB_BASE_URL" if args.provider == "tokenhub" else "HUNYUAN_3D_BASE_URL"
    default = DEFAULT_BASE_URL if args.provider == "tokenhub" else LEGACY_BASE_URL
    value = (args.base_url or os.environ.get(env) or default).rstrip("/")
    url = _https_url(value)
    if (url.hostname not in API_HOSTS[args.provider] or url.path or url.query
            or url.port not in (None, 443)):
        raise HunyuanError(f"--base-url must be an official {args.provider} origin, without /v1 or a query")
    return value


def validate_options(args: argparse.Namespace) -> None:
    _base_url(args)
    if args.model in {"express", "component"} and args.provider != "tokenhub":
        raise HunyuanError(f"{args.model.title()} is supported only through --provider tokenhub")
    for name in ("http_timeout", "poll_interval", "wait_timeout"):
        value = getattr(args, name, None)
        if value is not None and (not math.isfinite(value) or value <= 0):
            raise HunyuanError(f"--{name.replace('_', '-')} must be finite and positive")
    if getattr(args, "poll_interval", 5.0) < 5.0:
        raise HunyuanError("--poll-interval must be at least 5 seconds")
    if args.command == "check-auth" and args.provider != "tokenhub":
        raise HunyuanError("check-auth is available only for TokenHub")


def build_express_payload(args: argparse.Namespace) -> dict[str, Any]:
    """Express has its own option set; never send professional-only fields."""
    if args.provider != "tokenhub":
        raise HunyuanError("Express is supported only through --provider tokenhub")
    if args.view or args.face_count is not None or args.polygon_type is not None:
        raise HunyuanError("Express does not support --view, --face-count, or --polygon-type")
    if args.generate_type not in {"Normal", "Geometry"}:
        raise HunyuanError("Express supports Normal or Geometry only, not LowPoly/Sketch")
    geometry = args.enable_geometry or args.generate_type == "Geometry"
    if geometry and args.enable_pbr:
        raise HunyuanError("Express geometry is untextured; omit --enable-pbr")
    if geometry and args.result_format == "OBJ":
        raise HunyuanError("Express geometry does not support OBJ; omit --result-format or use GLB")
    prompt = (args.prompt or "").strip()
    has_image = args.image is not None or bool(args.image_url)
    if not prompt and not has_image:
        raise HunyuanError("Provide --prompt, --image, or --image-url")
    if prompt and has_image:
        raise HunyuanError("Express requires exactly one text or image source")
    if len(prompt) > 1024:
        raise HunyuanError("Prompt exceeds 1024 characters")
    if args.command == "image-to-3d" and not has_image:
        raise HunyuanError("image-to-3d requires --image or --image-url")
    if args.image_transport and args.image is None:
        raise HunyuanError("--image-transport requires a local --image")
    if args.image_transport == "data-url":
        raise HunyuanError("Express requires raw image_base64; omit --image-transport")
    payload: dict[str, Any] = {"model": "hy-3d-express"}
    if prompt:
        payload["prompt"] = prompt
    elif args.image is not None:
        data, _ = _validated_image(args.image)
        payload["image_base64"] = base64.b64encode(data).decode("ascii")
    else:
        _https_url(args.image_url)
        payload["image_url"] = args.image_url
    if args.result_format is not None:
        payload["result_format"] = args.result_format.lower()
    if args.enable_pbr:
        payload["enable_pbr"] = True
    if geometry:
        payload["enable_geometry"] = True
    return payload


def build_payload(args: argparse.Namespace) -> dict[str, Any]:
    if args.model == "express":
        return build_express_payload(args)
    if args.enable_geometry:
        raise HunyuanError("--enable-geometry is Express-only; use --generate-type Geometry for professional models")
    if args.result_format in {"OBJ", "GLB", "MP4"}:
        raise HunyuanError("Professional --result-format supports STL/USDZ/FBX; omit it for default OBJ/GLB")
    prompt = (args.prompt or "").strip()
    has_image = args.image is not None or bool(args.image_url)
    legacy = args.provider == "legacy"
    if not prompt and not has_image:
        raise HunyuanError("Provide --prompt, --image, or --image-url")
    if len(prompt) > 1024:
        raise HunyuanError("Prompt exceeds 1024 characters")
    if prompt and has_image and args.generate_type != "Sketch":
        raise HunyuanError("Prompt and image can coexist only with --generate-type Sketch")
    if args.model == "3.1" and args.generate_type in {"LowPoly", "Sketch"}:
        raise HunyuanError(f"Model 3.1 does not support {args.generate_type}; use 3.0 explicitly")
    if args.enable_pbr and args.generate_type == "Geometry":
        raise HunyuanError("Geometry generation does not support PBR materials")
    if args.face_count is not None and not 3000 <= args.face_count <= 1_500_000:
        raise HunyuanError("--face-count must be between 3000 and 1500000")
    if args.face_count is not None and args.generate_type == "LowPoly":
        raise HunyuanError("--face-count is ignored for LowPoly; omit it")
    if args.polygon_type is not None and args.generate_type != "LowPoly":
        raise HunyuanError("--polygon-type is valid only for LowPoly")
    if args.command == "image-to-3d" and not has_image:
        raise HunyuanError("image-to-3d requires --image or --image-url")
    if args.image_url:
        _https_url(args.image_url)
    if args.image_transport and args.image is None:
        raise HunyuanError("--image-transport requires a local --image")
    if not legacy and args.image_transport == "data-url":
        raise HunyuanError("TokenHub requires raw image_base64; omit --image-transport")
    if args.view and (legacy or not has_image or prompt):
        raise HunyuanError("--view requires TokenHub and a main image, without a prompt")

    names = {"Model": "model", "GenerateType": "generate_type", "Prompt": "prompt",
             "EnablePBR": "enable_pbr", "FaceCount": "face_count", "PolygonType": "polygon_type",
             "ResultFormat": "result_format"}
    payload: dict[str, Any] = {"Model": args.model, "GenerateType": args.generate_type}
    if prompt:
        payload["Prompt"] = prompt
    total_raw = 0
    total_encoded = 0
    if args.image is not None:
        data, mime = _validated_image(args.image, strict=legacy, multiview=bool(args.view))
        encoded = base64.b64encode(data).decode("ascii")
        total_raw, total_encoded = len(data), len(encoded)
        if legacy and args.image_transport != "raw-base64":
            payload["ImageUrl"] = {"Url": f"data:{mime};base64,{encoded}"}
        else:
            payload["ImageBase64" if legacy else "image_base64"] = encoded
    elif args.image_url:
        if legacy:
            payload["ImageUrl"] = {"Url": args.image_url}
        else:
            payload["image_url"] = args.image_url
    if args.enable_pbr:
        payload["EnablePBR"] = True
    if args.face_count is not None:
        payload["FaceCount"] = args.face_count
    if args.polygon_type is not None:
        payload["PolygonType"] = args.polygon_type
    if args.result_format is not None:
        payload["ResultFormat"] = args.result_format
    if not legacy:
        payload = {names.get(key, key): value for key, value in payload.items()}
        payload["model"] = "hy-3d-" + args.model
        # Live TokenHub (2026-09-13) rejects documented lowercase "normal".
        # Omit Normal to use the documented service default; preserve other requested modes.
        if args.generate_type == "Normal":
            payload.pop("generate_type", None)
        else:
            payload["generate_type"] = next(key for key, value in TYPE_NAMES.items() if value == args.generate_type)
        if args.result_format:
            payload["result_format"] = args.result_format.lower()

    seen: set[str] = set()
    views = []
    for spec in args.view:
        view, separator, source = spec.partition("=")
        if not separator or view not in VIEW_TYPES or not source:
            raise HunyuanError("--view must be VIEW=FILE_OR_HTTPS_URL; views: " + ", ".join(sorted(VIEW_TYPES)))
        if view in seen:
            raise HunyuanError(f"Duplicate view: {view}")
        seen.add(view)
        if source.lower().startswith(("https:", "http:")):
            _https_url(source)
            encoded = source
        else:
            data, _ = _validated_image(Path(source), multiview=True)
            encoded = base64.b64encode(data).decode("ascii")
            total_raw += len(data)
            total_encoded += len(encoded)
        views.append({"view_type": view, "view_image_base64": encoded})
    if views:
        if total_raw > 6 * 1024 * 1024 or total_encoded > 8 * 1024 * 1024:
            raise HunyuanError("Combined local front and view images exceed 6 MiB raw / 8 MiB encoded")
        payload["multi_view_images"] = views
    return payload


def _api_key(provider: str) -> str:
    names = KEY_ENV_NAMES if provider == "tokenhub" else KEY_ENV_NAMES[1:]
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            if "\r" in value or "\n" in value:
                raise HunyuanError(f"{name} contains a newline")
            return value
    raise HunyuanError("Missing API key; configure " + " or ".join(names) + " in the process environment")


def redact(value: Any, field: str = "") -> Any:
    if isinstance(value, dict):
        return {key: redact(child, key) for key, child in value.items()}
    if isinstance(value, list):
        return [redact(child, field) for child in value]
    if isinstance(value, str):
        if field == "image" and not value.lower().startswith(("https://", "http://")):
            return "<image data omitted>"
        if field == "part_segmentation_info":
            return "<segmentation JSON omitted>"
        if any(word in field.lower() for word in ("base64", "authorization", "api_key")):
            return "<redacted>"
        if value.startswith("data:"):
            return "<image data omitted>"
        for name in KEY_ENV_NAMES:
            key = os.environ.get(name, "").strip()
            if key:
                value = value.replace(key, "<redacted>")
        value = re.sub(r"\bsk-[A-Za-z0-9_-]+", "<redacted>", value)
        value = re.sub(r"(https?://[^\s?\"<>]+)\?[^\s\"<>]*", r"\1?<redacted>", value)
    return value


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HunyuanError("Authenticated API redirect refused; check the documented regional base URL")


class _HTTPSRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _https_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _envelope(response: dict[str, Any]) -> dict[str, Any]:
    wrapped = response.get("Response")
    return wrapped if isinstance(wrapped, dict) else response


def _request_id(response: dict[str, Any]) -> str:
    data = _envelope(response)
    return str(data.get("request_id") or data.get("RequestId") or "")


def _error_detail(data: dict[str, Any], fallback: str) -> str:
    body = _envelope(data)
    error = body.get("error") or body.get("Error")
    if isinstance(error, dict):
        code = error.get("code") or error.get("Code") or "APIError"
        message = error.get("message") or error.get("Message") or fallback
        detail = f"{code}: {message}"
    elif error:
        detail = str(error)
    else:
        code = body.get("error_code") or body.get("ErrorCode") or "APIError"
        message = body.get("error_message") or body.get("ErrorMessage") or body.get("message") or fallback
        detail = f"{code}: {message}"
    request_id = _request_id(data)
    return redact(detail + (f" [request_id={request_id}]" if request_id else ""))


def api_request(endpoint: str, payload: dict[str, Any] | None, args: argparse.Namespace,
                method: str = "POST") -> dict[str, Any]:
    validate_options(args)
    url = _base_url(args) + "/" + endpoint.lstrip("/")
    if args.dry_run:
        print(json.dumps({"provider": args.provider, "method": method, "url": url,
                          "payload": redact(payload)}, ensure_ascii=False, indent=2))
        return {}
    key = _api_key(args.provider)
    request = urllib.request.Request(
        url, data=None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method=method, headers={"Authorization": ("Bearer " if args.provider == "tokenhub" else "") + key,
                                "Content-Type": "application/json", "Accept": "application/json",
                                "User-Agent": "codex-hunyuan-3d-skill/2.0"})
    try:
        with urllib.request.build_opener(_NoRedirect()).open(request, timeout=args.http_timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(body)
            detail = _error_detail(data, "Request rejected") if isinstance(data, dict) else "Request rejected"
        except ValueError:
            detail = "Non-JSON error body omitted"
        hints = {401: "Check the selected provider, API key, and region.",
                 403: "Check account permissions, model access, and region.",
                 429: "Wait for active tasks or quota; do not automatically resubmit."}
        raise HunyuanError(f"API HTTP {exc.code}: {detail} {hints.get(exc.code, '')}".strip()) from exc
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as exc:
        if endpoint.endswith("/submit"):
            advice = "Submission outcome may be unknown. Do not automatically resubmit; check provider task records."
        elif endpoint.endswith("/query"):
            advice = "Resume with query on the same Job ID; do not resubmit."
        else:
            advice = "Retry this read-only check after checking connectivity."
        raise HunyuanError("API connection failed or timed out. " + advice) from exc
    try:
        result = json.loads(raw)
    except ValueError as exc:
        raise HunyuanError("API returned invalid JSON (body omitted); do not automatically resubmit") from exc
    if not isinstance(result, dict):
        raise HunyuanError("API response must be an object; do not automatically resubmit")
    body = _envelope(result)
    if body.get("error") or body.get("Error"):
        raise HunyuanError(_error_detail(result, "API request failed"))
    return result


def _job_id(value: Any) -> str:
    # IDs become directory names and are reused in shell examples.
    value = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", value):
        raise HunyuanError("Job ID must contain only letters, digits, hyphens or underscores (max 128)")
    return value


def submit_task(args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    validate_options(args)  # Validate polling options before any billable request.
    payload = build_payload(args)
    return submit_payload(payload, args)


def submit_payload(payload: dict[str, Any], args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    """Submit one validated model-specific payload through the common transport; never retry."""
    validate_options(args)
    endpoint = "v1/api/3d/submit" if args.provider == "tokenhub" else "v1/ai3d/submit"
    response = api_request(endpoint, payload, args)
    if args.dry_run:
        return "DRY_RUN", response
    body = _envelope(response)
    value = body.get("id") if args.provider == "tokenhub" else body.get("JobId")
    if not value:
        raise HunyuanError("Submit response has no task ID; do not resubmit. "
                           f"Check provider task records. request_id={_request_id(response)}")
    return _job_id(value), response


def query_task(job_id: str, args: argparse.Namespace) -> dict[str, Any]:
    job_id = _job_id(job_id)
    if args.provider == "tokenhub":
        return api_request("v1/api/3d/query", {"model": "hy-3d-" + args.model, "id": job_id}, args)
    return api_request("v1/ai3d/query", {"JobId": job_id}, args)


def status_of(response: dict[str, Any]) -> str:
    body = _envelope(response)
    status = str(body.get("status") or body.get("Status") or "UNKNOWN").upper()
    return {"COMPLETED": "DONE", "FAILED": "FAIL", "IN_PROGRESS": "RUN", "QUEUED": "WAIT"}.get(status, status)


def _raise_failed(response: dict[str, Any]) -> None:
    if status_of(response) == "FAIL":
        raise HunyuanError(_error_detail(response, "The generation job failed"))


def wait_for_job(job_id: str, args: argparse.Namespace,
                 initial: dict[str, Any] | None = None) -> dict[str, Any]:
    validate_options(args)
    deadline = time.monotonic() + args.wait_timeout
    previous = ""
    response = initial
    while True:
        if response is None:
            response = query_task(job_id, args)
        status = status_of(response)
        if status != previous:
            print(f"Job {job_id}: {status}", file=sys.stderr, flush=True)
            previous = status
        if status == "DONE":
            return response
        _raise_failed(response)
        if status not in RUNNING_STATUSES:
            raise HunyuanError(f"Unexpected job status {redact(status)}; resume with query, do not resubmit")
        remaining = deadline - time.monotonic()
        if remaining < args.poll_interval:
            raise HunyuanError(f"Timed out waiting for job {job_id}; resume with query, do not resubmit")
        time.sleep(args.poll_interval)
        response = None


def _safe_name(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    cleaned = (cleaned or fallback)[:180]
    if cleaned.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)],
                                       *[f"LPT{i}" for i in range(1, 10)]}:
        cleaned = "_" + cleaned
    return cleaned


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(1, 10000):
        candidate = path.with_name(f"{path.stem}_{index}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise HunyuanError(f"Could not choose a unique output name for {path}")


def _download(url: str, target: Path, timeout: float) -> Path:
    _https_url(url)
    target.parent.mkdir(parents=True, exist_ok=True)
    target = _unique_path(target)
    # Unique temporary file prevents overlapping downloads from clobbering partial output.
    import tempfile
    descriptor, partial_name = tempfile.mkstemp(prefix=target.name + ".", suffix=".part", dir=target.parent)
    partial = Path(partial_name)
    request = urllib.request.Request(url, headers={"User-Agent": "codex-hunyuan-3d-skill/2.0"})
    try:
        with os.fdopen(descriptor, "wb") as stream:
            with urllib.request.build_opener(_HTTPSRedirect()).open(request, timeout=timeout) as response:
                expected = response.headers.get("Content-Length")
                received = 0
                while chunk := response.read(1024 * 1024):
                    stream.write(chunk)
                    received += len(chunk)
                if received == 0 or (expected is not None and received != int(expected)):
                    raise HunyuanError("Empty or incomplete download")
        partial.replace(target)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return target.resolve()


def _resource_name(url: str) -> str:
    try:
        return Path(urllib.parse.urlsplit(url).path).name
    except ValueError:
        return ""  # _download validates it and reports the individual failed resource.


def download_results(response: dict[str, Any], output_dir: Path, timeout: float) -> list[Path]:
    if status_of(response) != "DONE":
        raise HunyuanError("Results are not ready; query the same task later")
    body = _envelope(response)
    files = body.get("data") if "data" in body else body.get("ResultFile3Ds")
    if not isinstance(files, list) or not files:
        raise HunyuanError("Completed response has no downloadable data/ResultFile3Ds")
    destination = output_dir.expanduser().resolve()
    downloaded: list[Path] = []
    errors: list[str] = []
    previews: set[str] = set()
    for index, item in enumerate(files, start=1):
        if not isinstance(item, dict):
            errors.append(f"Result {index} is not an object")
            continue
        model_url = item.get("url") or item.get("Url")
        model_type = _safe_name(str(item.get("type") or item.get("Type") or item.get("format") or "model").lower(), "model")
        resources = []
        if isinstance(model_url, str):
            name = _resource_name(model_url)
            if name and not Path(name).suffix and model_type in {"stl", "usdz", "fbx", "mp4", "gif", "obj", "glb"}:
                name += "." + model_type
            resources.append((model_url, _safe_name(name, f"model_{index}.{model_type}"), False))
        else:
            errors.append(f"Result {index} has no model URL")
        preview = item.get("preview_image_url") or item.get("PreviewImageUrl")
        if isinstance(preview, str) and preview not in previews:
            previews.add(preview)
            extension = Path(_resource_name(preview)).suffix.lower()
            extension = extension if extension in SUPPORTED_IMAGE_SUFFIXES else ".png"
            resources.append((preview, f"preview_{index}{extension}", True))
        for url, name, is_preview in resources:
            try:
                path = _download(url, destination / name, timeout)
                downloaded.append(path)
                print(f"Downloaded: {path}", file=sys.stderr, flush=True)
            except (OSError, ValueError, HunyuanError, http.client.HTTPException) as exc:
                # Never dump an exception that might contain signed URLs.
                message = f"Result {index} {'preview' if is_preview else 'model'} download failed ({type(exc).__name__})"
                if is_preview:
                    print("Warning: " + message + "; model files remain usable", file=sys.stderr)
                else:
                    errors.append(message)
    if errors:
        raise HunyuanError("; ".join(errors) + f". Keep files in {destination}; query the same job to retry downloads")
    if not downloaded:
        raise HunyuanError("No files downloaded; query the same job, do not resubmit")
    return downloaded


def _print_result(job_id: str, response: dict[str, Any], args: argparse.Namespace,
                  downloaded: list[Path] | None = None) -> None:
    result: dict[str, Any] = {"provider": args.provider, "model": args.model, "job_id": job_id,
                              "status": status_of(response), "response": redact(response)}
    if downloaded is not None:
        result["downloaded"] = [str(path) for path in downloaded]
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


def run(args: argparse.Namespace, *, submitter=None,
        generation_commands=("submit", "generate", "image-to-3d")) -> int:
    """Run a job lifecycle; companion skills may supply their own submission adapter."""
    validate_options(args)
    if args.command == "check-auth":
        result = api_request("v1/models", None, args, method="GET")
        if not args.dry_run:
            models = result.get("data")
            if not isinstance(models, list):
                raise HunyuanError("Model-list response has no data array")
            supported = [item for item in models if isinstance(item, dict)
                         and str(item.get("id", "")).lower().startswith("hy-3d-")]
            print(json.dumps(redact({"authenticated": True, "models": supported}), ensure_ascii=False, indent=2))
        return 0
    if args.command in generation_commands:
        job_id, response = (submitter or submit_task)(args)
        if args.dry_run:
            return 0
        print(f"Submitted job: {job_id} (provider={args.provider}, model={args.model})", file=sys.stderr, flush=True)
        print(f"Resume: query {job_id} --provider {args.provider} --model {args.model} "
              f"--base-url {_base_url(args)} --wait --download", file=sys.stderr, flush=True)
        if args.command == "submit":
            _print_result(job_id, response, args)
            _raise_failed(response)
            return 0
        response = wait_for_job(job_id, args, initial=response if status_of(response) != "UNKNOWN" else None)
        downloaded = None if args.no_download else download_results(response, args.output_dir / job_id, args.http_timeout)
        _print_result(job_id, response, args, downloaded)
        return 0
    job_id = _job_id(args.job_id)
    response = query_task(job_id, args)
    if args.dry_run:
        return 0
    _raise_failed(response)
    if args.wait and status_of(response) not in TERMINAL_STATUSES:
        response = wait_for_job(job_id, args, initial=response)
    downloaded = None
    if args.download:
        downloaded = download_results(response, args.output_dir / job_id, args.http_timeout)
    _print_result(job_id, response, args, downloaded)
    return 0


def main() -> int:
    # UTF-8 avoids Windows code-page failures when output includes Chinese prompts and paths.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        return run(build_parser().parse_args())
    except (HunyuanError, OSError) as exc:
        print(f"error: {redact(str(exc))}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted; use query with the same provider, model and Job ID to resume.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
