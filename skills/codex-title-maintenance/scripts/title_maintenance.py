#!/usr/bin/env python3
"""JSON CLI for the Codex-hosted title maintenance workflow (Python 3.11+)."""

from __future__ import annotations

import argparse
import copy
import json
import os
import sqlite3
import sys
from pathlib import Path

if sys.version_info < (3, 11):
    raise SystemExit("需要 Python 3.11 或更高版本。请使用已有 uv Python 或合适的解释器运行此脚本。")

from title_maintenance import config
from title_maintenance.engine import Engine


def parser():
    p = argparse.ArgumentParser(description="Codex 标题维护：本地扫描、配置、账本；应用写入由 skill 编排。")
    p.add_argument("--codex-home", type=Path, help="Codex 数据目录；默认 CODEX_HOME 或 ~/.codex")
    p.add_argument("--data-dir", type=Path, help="独立配置与账本目录；默认 CODEX_HOME/title-maintenance")
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("init", "doctor", "status", "config-show"):
        sub.add_parser(name)
    model_status = sub.add_parser("model-status")
    model_status.add_argument("--thread-id", help="默认检查已绑定的维护任务")
    apply = sub.add_parser("config-apply", help="以 JSON patch 合并、校验并保存用户配置，不直接修改原生调度")
    apply.add_argument("--file", type=Path, required=True)
    control = sub.add_parser("control", help="低层本地开关；完整 start/stop 需 skill 同步原生调度")
    control.add_argument("action", choices=("start", "stop"))
    bind = sub.add_parser("bind")
    bind.add_argument("--automation-id", required=True)
    bind.add_argument("--thread-id", required=True)
    scan = sub.add_parser("scan")
    scan.add_argument("--mode", choices=("full", "incremental"), default="incremental")
    scan.add_argument("--trigger", choices=("manual", "scheduled"), default="manual")
    preview = sub.add_parser("preview")
    preview.add_argument("--mode", choices=("full", "incremental"), default="incremental")
    preview.add_argument("--limit", type=int, default=20)
    preview.add_argument("--offset", type=int, default=0)
    nxt = sub.add_parser("next")
    nxt.add_argument("--run-id", required=True)
    nxt.add_argument("--limit", type=int, default=5)
    context = sub.add_parser("context")
    context.add_argument("--run-id")
    context.add_argument("--thread-id", required=True)
    context.add_argument("--offset", type=int, default=0)
    context.add_argument("--limit", type=int, default=12000)
    propose = sub.add_parser("propose")
    propose.add_argument("--run-id", required=True)
    propose.add_argument("--thread-id", required=True)
    propose.add_argument("--live-file", type=Path, required=True)
    propose.add_argument("--prefix")
    propose.add_argument("--subject")
    propose.add_argument("--summary")
    for name in ("authorize", "confirm", "recover"):
        operation = sub.add_parser(name)
        operation.add_argument("--run-id", required=True)
        operation.add_argument("--intent-id", required=True)
        operation.add_argument("--live-file", type=Path, required=True)
    defer = sub.add_parser("defer")
    defer.add_argument("--run-id", required=True)
    defer.add_argument("--thread-id", required=True)
    defer.add_argument("--reason", required=True)
    finish = sub.add_parser("finish")
    finish.add_argument("--run-id", required=True)
    unprotect = sub.add_parser("unprotect")
    unprotect.add_argument("--thread-id", required=True)
    return p


def read_json(path):
    result = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise ValueError("JSON 文件必须包含一个对象。")
    return result


def merge(current, patch):
    result = copy.deepcopy(current)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge(result[key], value)
        else:
            result[key] = value
    return result


def execute(args):
    home = config.effective_codex_home(args.codex_home)
    data_dir = args.data_dir or Path(os.environ.get("CODEX_TITLE_MAINTENANCE_HOME", home / "title-maintenance"))
    e = Engine(data_dir, home)
    cmd = args.command
    if cmd == "init":
        return e.initialize(Path(__file__).resolve().parent.parent / "defaults")
    if cmd == "doctor":
        return e.doctor()
    if cmd == "status":
        return e.status()
    if cmd == "config-show":
        return e.cfg()
    if cmd == "model-status":
        return e.model_status(args.thread_id)
    if cmd == "config-apply":
        before = e.cfg()
        after = merge(before, read_json(args.file))
        config.validate_config(after)
        old_naming_hash = config.naming_hash(before, e.data_dir)
        new_naming_hash = config.naming_hash(after, e.data_dir)
        config.save_config(e.data_dir, after)
        schedule_changed = any(before["schedule"][k] != after["schedule"][k] for k in ("times", "timezone"))
        with e.connect() as db:
            bound = bool(e.get(db, "automation_id"))
        return {"saved": True, "native_schedule_sync_required": schedule_changed and bound,
                "maintenance_model_sync_required": before["agent"] != after["agent"] and bound,
                "naming_rules_changed": old_naming_hash != new_naming_hash,
                "note": ("已绑定计划的时点或时区变更须同步原生 automation；未自动开启维护。" if bound
                         else "已保存；尚未绑定原生计划，下次 start 时应用，当前不会创建或开启计划。")}
    if cmd == "control":
        return e.control(args.action)
    if cmd == "bind":
        return e.bind(args.automation_id, args.thread_id)
    if cmd == "scan":
        return e.scan(args.mode, args.trigger)
    if cmd == "preview":
        if args.limit < 1 or args.offset < 0:
            raise ValueError("limit 必须为正数，offset 必须非负。")
        return e.preview(args.mode, args.limit, args.offset)
    if cmd == "next":
        if not 1 <= args.limit <= 100:
            raise ValueError("limit 必须在 1–100 之间。")
        return e.next_items(args.run_id, args.limit)
    if cmd == "context":
        return e.context(args.thread_id, args.run_id, args.offset, args.limit)
    if cmd == "propose":
        return e.propose(args.run_id, args.thread_id, read_json(args.live_file), args.prefix, args.subject, args.summary)
    if cmd == "authorize":
        return e.authorize(args.run_id, args.intent_id, read_json(args.live_file))
    if cmd in ("confirm", "recover"):
        return e.confirm(args.run_id, args.intent_id, read_json(args.live_file), recovery=cmd == "recover")
    if cmd == "defer":
        return e.defer(args.run_id, args.thread_id, args.reason)
    if cmd == "finish":
        return e.finish(args.run_id)
    if cmd == "unprotect":
        return e.unprotect(args.thread_id)
    raise ValueError("未知命令。")


def main():
    args = parser().parse_args()
    try:
        print(json.dumps({"ok": True, "result": execute(args)}, ensure_ascii=False, indent=2))
    except (ValueError, OSError, sqlite3.Error, KeyError, TypeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
