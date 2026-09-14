#!/usr/bin/env python3
"""Rig and skin existing character models through Tencent TokenHub HY-3D-Rigging."""
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
MOTIONS = dict(enumerate((
    "回旋踢", "左勾拳", "蓄力攻击", "蓄力出拳", "二连击打", "二连击打-2", "后撤", "受击",
    "受击-2", "受击-3", "受击倒地-1", "受击倒地-2", "落地", "沮丧", "割喉", "刺拳",
    "连续击打", "踢腿", "侧踢", "打太极", "后空翻", "蹲姿转体", "走路-1", "走路-2",
    "走路-3", "待机-1", "待机-2", "街舞", "扭扭舞", "左转弯", "右转弯", "慢跑",
    "慢跑-2", "奔跑", "冲刺跑-1", "冲刺跑-2", "冲刺跑-3", "原地跳-1", "滑铲", "向前大跳",
    "向前大跳-2", "跨越", "恐吓", "向前跌倒", "右转", "原地跳-2", "转身", "发送冲击波",
), start=1))


def common_parser():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--provider", choices=("tokenhub",), default="tokenhub")
    parser.add_argument("--model", type=lambda value: value.lower().removeprefix("hy-3d-"),
                        choices=("rigging",), default="rigging")
    parser.add_argument("--base-url", help="Official TokenHub origin; default is Guangzhou")
    parser.add_argument("--http-timeout", type=float, default=60.0)
    parser.add_argument("--dry-run", action="store_true", help="Offline validation; no credentials or API call")
    return parser


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("submit", "rig"):
        sub = commands.add_parser(command, parents=[common_parser()], aliases=["rigging"] if command == "rig" else [])
        sub.add_argument("--file-url", required=True, help="Public HTTPS FBX/GLB character model URL")
        sub.add_argument("--file-type", type=str.lower, choices=("fbx", "glb"), help="Optional input type")
        sub.add_argument("--input-size-bytes", type=int, help="Verified input size; local check only, <=60000000")
        sub.add_argument("--character-type", choices=("humanoid", "non-humanoid"),
                         help="Known character category for local checks only; never sent to API")
        sub.add_argument("--motion-type", type=int, help="Explicit humanoid motion preset ID, 1..48; omit for rigging only")
        if command == "rig":
            h._add_wait_arguments(sub)
            sub.set_defaults(output_dir=Path("hunyuan-3d-animation-output"))
            sub.add_argument("--no-download", action="store_true")
    query = commands.add_parser("query", parents=[common_parser()])
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
    if args.provider != "tokenhub" or args.model != "rigging":
        raise h.HunyuanError("Rigging requires TokenHub and hy-3d-rigging")
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


def submit_task(args):
    return h.submit_payload(build_payload(args), args)


def run(args):
    if args.command == "list-motions":
        print(json.dumps(MOTIONS, ensure_ascii=False, indent=2))
        return 0
    return h.run(args, submitter=submit_task, generation_commands=("submit", "rig", "rigging"))


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
