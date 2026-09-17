"""Configuration and time-window helpers for title maintenance."""

from __future__ import annotations

import copy
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import string
import tempfile
import tomllib
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DEFAULT_CONFIG = {
    "config_version": 1,
    "agent": {"model": "gpt-5.6-luna", "reasoning_effort": "low"},
    "schedule": {
        "timezone": "Asia/Shanghai",
        "times": ["10:00", "12:00", "15:00", "17:00", "21:00", "23:00"],
        "launch_window_minutes": 5,
    },
    "scan": {"overlap_minutes": 5},
    "scope": {
        "include_archived": True,
        "targets": ["codex", "chat", "work"],
        "protect_external_titles": True,
        "exclude_thread_ids": [],
    },
    "title": {
        "template": "{prefix}｜{subject}｜{date}",
        "date_source": "latest_assistant",
        "date_format": "%m%d",
        "language": "zh-CN",
        "rules_file": "naming-rules.md",
        "prefixes": [
            "探索", "选型", "环境", "设置", "方案", "实现", "功能",
            "Bug", "测试", "PR", "检索", "Git", "求知", "生活",
        ],
    },
}

REASONING_EFFORTS = (
    "inherit", "none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra",
)
NATIVE_REASONING_EFFORTS = tuple(value for value in REASONING_EFFORTS if value != "inherit")


def effective_codex_home(optional: str | Path | None = None) -> Path:
    """Resolve the caller's override before CODEX_HOME and the usual default."""
    return Path(optional or os.environ.get("CODEX_HOME") or "~/.codex").expanduser().resolve()


def _merged(config: dict, defaults: dict = DEFAULT_CONFIG, location: str = "config") -> dict:
    if not isinstance(config, dict):
        raise ValueError(f"{location} 必须是一个 TOML 表")
    unknown = config.keys() - defaults.keys()
    if unknown:
        raise ValueError(f"{location} 存在未知字段: {', '.join(sorted(unknown))}")
    result = copy.deepcopy(defaults)
    for key, value in config.items():
        result[key] = (
            _merged(value, defaults[key], f"{location}.{key}")
            if isinstance(defaults[key], dict) else copy.deepcopy(value)
        )
    return result


def _nonempty_string(value, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} 必须是非空字符串")


def _string_list(value, field: str, *, nonempty: bool = True) -> None:
    if not isinstance(value, list) or (nonempty and not value):
        raise ValueError(f"{field} 必须是{'非空' if nonempty else ''}字符串数组")
    for entry in value:
        _nonempty_string(entry, field)
    if len(set(value)) != len(value):
        raise ValueError(f"{field} 不能有重复项")


def validate_config(config: dict) -> None:
    """Validate a possibly partial configuration without mutating it."""
    value = _merged(config)
    if type(value["config_version"]) is not int or value["config_version"] != 1:
        raise ValueError("config_version 必须为 1")
    agent = value["agent"]
    _nonempty_string(agent["model"], "agent.model")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]*", agent["model"]):
        raise ValueError("agent.model 必须是模型标识或 inherit，不能包含空白或控制字符")
    if not isinstance(agent["reasoning_effort"], str) or agent["reasoning_effort"] not in REASONING_EFFORTS:
        raise ValueError("agent.reasoning_effort 必须为 inherit/none/minimal/low/medium/high/xhigh/max/ultra；宿主支持情况需另行核验")
    schedule = value["schedule"]
    _nonempty_string(schedule["timezone"], "schedule.timezone")
    try:
        ZoneInfo(schedule["timezone"])
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("schedule.timezone 必须是可用的 IANA 时区") from exc
    _string_list(schedule["times"], "schedule.times")
    minutes = []
    for item in schedule["times"]:
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", item):
            raise ValueError("schedule.times 必须使用 24 小时 HH:MM 格式")
        hour, minute = map(int, item.split(":"))
        minutes.append(hour * 60 + minute)
    if len({minute % 60 for minute in minutes}) > 1:
        raise ValueError("schedule.times 的分钟值必须相同，避免原生调度将小时和分钟组合成额外时点")
    window = schedule["launch_window_minutes"]
    if type(window) is not int or not 1 <= window <= 1440:
        raise ValueError("schedule.launch_window_minutes 必须是 1..1440 的整数")
    minutes.sort()
    if any(end - start < window for start, end in zip(minutes, minutes[1:] + [minutes[0] + 1440])):
        raise ValueError("schedule 中的启动窗口不能重叠（包括跨午夜的窗口）")
    overlap = value["scan"]["overlap_minutes"]
    if type(overlap) is not int or overlap < 0:
        raise ValueError("scan.overlap_minutes 必须是非负整数")
    scope = value["scope"]
    for key in ("include_archived", "protect_external_titles"):
        if type(scope[key]) is not bool:
            raise ValueError(f"scope.{key} 必须是布尔值")
    _string_list(scope["targets"], "scope.targets")
    if set(scope["targets"]) - {"codex", "chat", "work"}:
        raise ValueError("scope.targets 只支持 codex、chat、work")
    _string_list(scope["exclude_thread_ids"], "scope.exclude_thread_ids", nonempty=False)
    title = value["title"]
    for key in ("template", "date_source", "date_format", "language", "rules_file"):
        _nonempty_string(title[key], f"title.{key}")
    if title["date_source"] != "latest_assistant":
        raise ValueError("title.date_source 当前只支持 latest_assistant")
    if any(c in title["template"] for c in "\r\n\x00"):
        raise ValueError("title.template 必须是单行文本")
    try:
        fields = []
        for _, field, format_spec, conversion in string.Formatter().parse(title["template"]):
            if field is not None:
                if field not in {"prefix", "subject", "date"} or format_spec or conversion:
                    raise ValueError("title.template 只允许无格式修饰的 {prefix}、{subject}、{date}")
                fields.append(field)
        if fields.count("subject") != 1 or len(fields) != len(set(fields)):
            raise ValueError("title.template 必须恰好包含一个 {subject}，其他字段不能重复")
    except ValueError as exc:
        raise ValueError(f"title.template 无效: {exc}") from exc
    if re.search(r"%(?:[^aAbBcdHIjmMpSUwWxXyYZfzVGu%]|$)", title["date_format"]):
        raise ValueError("title.date_format 包含不支持的 strftime 指令")
    if any(c in title["date_format"] for c in "\r\n\x00"):
        raise ValueError("title.date_format 必须是单行文本")
    rules = Path(title["rules_file"])
    if rules.is_absolute() or ".." in rules.parts or rules == Path("."):
        raise ValueError("title.rules_file 必须是个人数据目录内的相对文件路径")
    _string_list(title["prefixes"], "title.prefixes")
    if any(any(c in prefix for c in "\r\n\x00｜") for prefix in title["prefixes"]):
        raise ValueError("title.prefixes 不能包含换行、空字符或分隔符 ｜")


def load_config(data_dir: Path) -> dict:
    path = Path(data_dir) / "config.toml"
    try:
        with path.open("rb") as handle:
            value = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"无法读取配置 {path}: {exc}") from exc
    validate_config(value)
    return _merged(value)


def _toml_value(value) -> str:
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if type(value) is bool:
        return "true" if value else "false"
    if type(value) is int:
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    raise ValueError(f"不支持的 TOML 值类型: {type(value).__name__}")


def save_config(data_dir: Path, config: dict) -> None:
    """Validate and atomically save standard TOML, leaving existing data untouched on failure."""
    validate_config(config)
    config = _merged(config)
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    lines = []
    for key, value in config.items():
        if not isinstance(value, dict):
            lines.append(f"{key} = {_toml_value(value)}")
    for key, value in config.items():
        if isinstance(value, dict):
            lines.extend(["", f"[{key}]"])
            lines.extend(f"{entry} = {_toml_value(item)}" for entry, item in value.items())
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=data_dir, delete=False) as handle:
            temporary_path = Path(handle.name)
            handle.write("\n".join(lines) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, data_dir / "config.toml")
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _rules_path(config: dict, data_dir: Path) -> Path:
    path = Path(data_dir) / config["title"]["rules_file"]
    if not path.resolve().is_relative_to(Path(data_dir).resolve()):
        raise ValueError("命名规则文件不能指向个人数据目录之外")
    return path


def initialize(data_dir: Path, defaults_dir: Path) -> dict:
    """Copy missing templates only; reinitialization never replaces personal settings."""
    data_dir, defaults_dir = Path(data_dir), Path(defaults_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    config_path = data_dir / "config.toml"
    if not config_path.exists():
        with (defaults_dir / "config.toml").open("rb") as handle:
            template = tomllib.load(handle)
        save_config(data_dir, template)
    config = load_config(data_dir)
    rules_path = _rules_path(config, data_dir)
    if not rules_path.exists():
        rules_path.parent.mkdir(parents=True, exist_ok=True)
        with rules_path.open("x", encoding="utf-8") as handle:
            handle.write((defaults_dir / "naming-rules.md").read_text(encoding="utf-8"))
    return config


def naming_hash(config: dict, data_dir: Path) -> str:
    validate_config(config)
    config = _merged(config)
    try:
        rules = _rules_path(config, data_dir).read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"无法读取命名规则: {exc}") from exc
    material = {"title": config["title"], "rules": rules, "timezone": config["schedule"]["timezone"]}
    return hashlib.sha256(json.dumps(material, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def schedule_slot(config: dict, now: datetime) -> str | None:
    """Return the local scheduled slot containing now; its end is exclusive.

    The slot key deliberately identifies a local date/time, so a DST repeated
    hour does not run twice. Nonexistent local times never create a slot.
    """
    validate_config(config)
    config = _merged(config)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("schedule_slot 的 now 必须包含时区")
    schedule = config["schedule"]
    zone = ZoneInfo(schedule["timezone"])
    local_now = now.astimezone(zone)
    window = schedule["launch_window_minutes"] * 60
    for day in (local_now.date(), local_now.date() - timedelta(days=1)):
        for slot_time in schedule["times"]:
            hour, minute = map(int, slot_time.split(":"))
            for fold in (0, 1):
                start = datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone, fold=fold)
                round_trip = datetime.fromtimestamp(start.timestamp(), zone)
                if (round_trip.date(), round_trip.hour, round_trip.minute, round_trip.fold) != (day, hour, minute, fold):
                    continue
                elapsed = now.timestamp() - start.timestamp()
                if 0 <= elapsed < window:
                    return f"{day.isoformat()}T{slot_time}@{schedule['timezone']}"
    return None


def daily_times_from_rrule(rrule: str) -> list[str]:
    """Normalize the native cron shape supported by this skill.

    The native file is evidence about persisted automation configuration, not
    a general RRULE parser. Reject extra constraints instead of silently
    claiming that a more complex rule matches the local daily schedule.
    """
    if not isinstance(rrule, str) or not rrule.strip():
        raise ValueError("原生 cron 缺少可核验的每日计划")
    fields = {}
    for part in rrule.split(";"):
        if "=" not in part:
            raise ValueError("原生 cron 计划格式不受支持")
        key, value = part.split("=", 1)
        key = key.strip().upper()
        value = value.strip().upper()
        if not key or not value or key in fields:
            raise ValueError("原生 cron 计划格式不受支持")
        fields[key] = value
    if set(fields) - {"FREQ", "INTERVAL", "BYHOUR", "BYMINUTE"}:
        raise ValueError("原生 cron 含当前适配器未核验的计划约束")
    if fields.get("FREQ") != "DAILY" or fields.get("INTERVAL", "1") != "1":
        raise ValueError("原生 cron 当前只支持每日一次周期定义")
    try:
        hours = [int(value) for value in fields["BYHOUR"].split(",")]
        minutes = [int(value) for value in fields["BYMINUTE"].split(",")]
    except (KeyError, ValueError) as exc:
        raise ValueError("原生 cron 缺少有效的小时或分钟") from exc
    if (not hours or len(hours) != len(set(hours)) or any(not 0 <= value <= 23 for value in hours)
            or len(minutes) != 1 or not 0 <= minutes[0] <= 59):
        raise ValueError("原生 cron 的小时或分钟范围不受支持")
    return [f"{hour:02d}:{minutes[0]:02d}" for hour in sorted(hours)]
