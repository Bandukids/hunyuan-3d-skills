#!/usr/bin/env python3
"""Rig characters or generate motion from text through Tencent TokenHub."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys
import urllib.parse


def load_client():
    path = Path(__file__).resolve().parents[2] / "hunyuan-3d-generator" / "scripts" / "hunyuan_3d.py"
    if not path.is_file():
        raise RuntimeError("Install hunyuan-3d-generator beside hunyuan-3d-animation")
    spec = importlib.util.spec_from_file_location("_hunyuan_animation_shared_client", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if getattr(module, "COMMON_CLIENT_API_VERSION", None) != 1:
        raise RuntimeError("Update adjacent hunyuan-3d-generator; shared client API version 1 is required")
    return module


h = load_client()
MAX_INPUT_BYTES = 60_000_000  # Conservative decimal interpretation of the guide's unspecified 60mb.
RIGGING_OPTIONS = ("file_url", "file_type", "input_size_bytes", "character_type", "motion_type")
MOTION_OPTIONS = ("prompt", "duration", "enable_mesh", "enable_rewrite", "enable_duration_est", "retarget_file_json")
MOTIONS = dict(enumerate((
    "回旋踢", "左勾拳", "蓄力攻击", "蓄力出拳", "二连击打", "二连击打-2", "后撤", "受击",
    "受击-2", "受击-3", "受击倒地-1", "受击倒地-2", "落地", "沮丧", "割喉", "刺拳",
    "连续击打", "踢腿", "侧踢", "打太极", "后空翻", "蹲姿转体", "走路-1", "走路-2",
    "走路-3", "待机-1", "待机-2", "街舞", "扭扭舞", "左转弯", "右转弯", "慢跑",
    "慢跑-2", "奔跑", "冲刺跑-1", "冲刺跑-2", "冲刺跑-3", "原地跳-1", "滑铲", "向前大跳",
    "向前大跳-2", "跨越", "恐吓", "向前跌倒", "右转", "原地跳-2", "转身", "发送冲击波",
), start=1))


def common_parser(model="rigging"):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--provider", choices=("tokenhub",), default="tokenhub")
    parser.add_argument("--model", type=lambda value: value.lower().removeprefix("hy-3d-"),
                        choices=("rigging", "motion"), default=model)
    parser.add_argument("--base-url", help="Official TokenHub origin; default is Guangzhou")
    parser.add_argument("--http-timeout", type=float, default=60.0)
    parser.add_argument("--dry-run", action="store_true", help="Offline validation; no credentials or API call")
    return parser


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("submit", "rig", "motion"):
        aliases = {"rig": ["rigging"], "motion": ["text-to-motion"]}.get(command, [])
        sub = commands.add_parser(command, parents=[common_parser("motion" if command == "motion" else "rigging")], aliases=aliases)
        if command in {"submit", "rig"}:
            sub.add_argument("--file-url", required=command == "rig", help="Rigging: public HTTPS FBX/GLB character model URL")
            sub.add_argument("--file-type", type=str.lower, choices=("fbx", "glb"), help="Rigging: optional input type")
            sub.add_argument("--input-size-bytes", type=int, help="Rigging: verified input size; local check only, <=60000000")
            sub.add_argument("--character-type", choices=("humanoid", "non-humanoid"),
                             help="Rigging: known character category for local checks only; never sent to API")
            sub.add_argument("--motion-type", type=int, help="Rigging: humanoid motion preset ID, 1..48; omit for rigging only")
        if command in {"submit", "motion"}:
            sub.add_argument("--prompt", required=command == "motion", help="Motion: description, 1..128 characters")
            sub.add_argument("--duration", type=int, help="Motion: seconds, 1..12; service default 5")
            sub.add_argument("--retarget-file-json", type=Path, metavar="JSON_FILE",
                             help="Motion: confirmed native retarget_file object; inner schema is not documented")
            for field, negative, help_text in (
                ("enable_mesh", "no-mesh", "Include skinned mesh in output FBX; service default true"),
                ("enable_rewrite", "no-rewrite", "Expand the input prompt; service default false"),
                ("enable_duration_est", "no-duration-est", "Estimate duration from prompt; service default false"),
            ):
                group = sub.add_mutually_exclusive_group()
                group.add_argument("--" + field.replace("_", "-"), dest=field, action="store_true", help="Motion: " + help_text)
                group.add_argument("--" + negative, dest=field, action="store_false")
                sub.set_defaults(**{field: None})
        if command != "submit":
            h._add_wait_arguments(sub)
            sub.set_defaults(output_dir=Path("hunyuan-3d-animation-output"))
            sub.add_argument("--no-download", action="store_true")
    query = commands.add_parser("query", parents=[common_parser()],
                                help="Resume with original model: rigging (default) or motion")
    query.add_argument("job_id")
    query.add_argument("--wait", action="store_true")
    query.add_argument("--download", action="store_true")
    h._add_wait_arguments(query)
    query.set_defaults(output_dir=Path("hunyuan-3d-animation-output"))
    commands.add_parser("check-auth", parents=[common_parser()], help="Read model catalog without a generation task")
    commands.add_parser("list-motions", help="Print the documented humanoid motion preset IDs offline")
    return parser


def build_payload(args):
    h.validate_options(args)
    if args.provider != "tokenhub" or args.model not in {"rigging", "motion"}:
        raise h.HunyuanError("Animation requires TokenHub and model rigging or motion")
    if args.command in {"rig", "rigging"} and args.model != "rigging":
        raise h.HunyuanError("rig/rigging requires --model rigging; use motion for text-to-motion")
    if args.command in {"motion", "text-to-motion"} and args.model != "motion":
        raise h.HunyuanError("motion/text-to-motion requires --model motion; use rig for character rigging")
    if args.model == "motion":
        return build_motion_payload(args)
    if any(getattr(args, field, None) is not None for field in MOTION_OPTIONS):
        raise h.HunyuanError("Text-to-motion options require --model motion")
    if not args.file_url:
        raise h.HunyuanError("Rigging requires --file-url; use submit --model motion --prompt for text-to-motion")
    url = h._https_url(args.file_url)
    suffix = Path(urllib.parse.unquote(url.path)).suffix.lower()
    if suffix and suffix not in {".fbx", ".glb"}:
        raise h.HunyuanError("Rigging input must be FBX or GLB, not OBJ/ZIP/GLTF or a local path")
    if args.file_type is not None and args.file_type not in {"fbx", "glb"}:
        raise h.HunyuanError("--file-type must be fbx or glb")
    if args.file_type and suffix and args.file_type != suffix[1:]:
        raise h.HunyuanError("--file-type conflicts with the model URL extension")
    size = args.input_size_bytes
    if size is not None and (isinstance(size, bool) or not isinstance(size, int) or not 0 < size <= MAX_INPUT_BYTES):
        raise h.HunyuanError("--input-size-bytes must be an integer from 1 to 60000000")
    if args.character_type not in {None, "humanoid", "non-humanoid"}:
        raise h.HunyuanError("--character-type must be humanoid or non-humanoid")
    motion = args.motion_type
    if motion is not None:
        if isinstance(motion, bool) or not isinstance(motion, int) or motion not in MOTIONS:
            raise h.HunyuanError("--motion-type must be an integer from 1 to 48; use list-motions")
        if args.character_type == "non-humanoid":
            raise h.HunyuanError("Non-humanoid models do not support motion templates; omit --motion-type")
    file_3d = {"url": args.file_url}
    if args.file_type:
        file_3d["type"] = args.file_type
    payload = {"model": "hy-3d-rigging", "file_3d": file_3d}
    if motion is not None:
        payload["motion_type"] = motion
    return payload


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("Non-finite JSON value")


def build_motion_payload(args):
    if any(getattr(args, field, None) is not None for field in RIGGING_OPTIONS):
        raise h.HunyuanError("Motion does not accept rigging file URLs, input metadata or motion template IDs")
    prompt = (args.prompt or "").strip()
    if not 1 <= len(prompt) <= 128:
        raise h.HunyuanError("Motion prompt must contain 1..128 characters")
    payload = {"model": "hy-3d-motion", "prompt": prompt}
    if args.duration is not None:
        if isinstance(args.duration, bool) or not isinstance(args.duration, int) or not 1 <= args.duration <= 12:
            raise h.HunyuanError("--duration must be an integer from 1 to 12 seconds")
        payload["duration"] = args.duration
    for field in ("enable_mesh", "enable_rewrite", "enable_duration_est"):
        value = getattr(args, field)
        if value is not None:
            if not isinstance(value, bool):
                raise h.HunyuanError(f"{field} must be a boolean")
            payload[field] = value
    if args.retarget_file_json is not None:
        try:
            data = json.loads(args.retarget_file_json.expanduser().resolve().read_text(encoding="utf-8-sig"),
                              object_pairs_hook=_unique_object, parse_constant=_reject_constant)
            json.dumps(data, allow_nan=False)
        except (OSError, UnicodeError, ValueError, RecursionError) as exc:
            raise h.HunyuanError("Retarget file must be readable UTF-8 JSON with unique keys and finite values") from exc
        if not isinstance(data, dict) or not data:
            raise h.HunyuanError("Retarget file must contain a non-empty native JSON object")
        # The guide documents only OBJECT; preserve supplied provider fields, never invent a nested schema.
        payload["retarget_file"] = data
    return payload


def submit_task(args):
    return h.submit_payload(build_payload(args), args)


def run(args):
    if args.command == "list-motions":
        print(json.dumps(MOTIONS, ensure_ascii=False, indent=2))
        return 0
    return h.run(args, submitter=submit_task, generation_commands=("submit", "rig", "rigging", "motion", "text-to-motion"))


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
