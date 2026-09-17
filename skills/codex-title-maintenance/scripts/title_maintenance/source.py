"""Read-only adapter for the local Codex task index and rollout logs.

The index is an internal format. Unsupported history operations must be
reported instead of treating old or discarded branches as current content.
"""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone as datetime_timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import tomllib
from urllib.parse import quote
from zoneinfo import ZoneInfo


REQUIRED_COLUMNS = {"id", "rollout_path", "created_at", "updated_at", "source", "title", "archived"}
# The index stores integer Unix seconds, never Unix milliseconds. This upper
# bound is the last whole second representable by Python's datetime (year 9999).
MAX_UNIX_SECONDS = 253402300799
AUTOMATION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


class SourceError(ValueError):
    """Source data is unavailable, changing, or unsupported."""


def read_native_automation(codex_home: Path, automation_id: str) -> dict:
    """Read an allowlisted view of one persisted native automation.

    This is a point-in-time view of the local TOML file. It does not prove the
    UI label, scheduler delivery, or the model used by a particular run.
    """
    if not isinstance(automation_id, str) or not AUTOMATION_ID.fullmatch(automation_id):
        raise SourceError("automation_id 格式无效，不能用于读取原生配置")
    path = Path(codex_home) / "automations" / automation_id / "automation.toml"
    try:
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise SourceError(f"无法读取原生 automation {automation_id}: {exc}") from exc
    if raw.get("id") != automation_id:
        raise SourceError("原生 automation 文件中的 id 与账本绑定不一致")
    kind = raw.get("kind")
    if kind not in {"heartbeat", "cron"}:
        raise SourceError("原生 automation kind 不受支持")
    target = raw.get("target")
    normalized_target = None
    if isinstance(target, dict):
        if target.get("type") == "project" and isinstance(target.get("project_id"), str):
            normalized_target = {"type": "project", "project_id": target["project_id"]}
        elif target.get("type") == "thread" and isinstance(target.get("thread_id"), str):
            normalized_target = {"type": "thread", "thread_id": target["thread_id"]}
    if kind == "heartbeat" and normalized_target is None:
        thread_id = raw.get("target_thread_id")
        if isinstance(thread_id, str) and thread_id:
            normalized_target = {"type": "thread", "thread_id": thread_id}
    return {
        "source_path": str(path),
        "id": automation_id,
        "kind": kind,
        "status": raw.get("status") if isinstance(raw.get("status"), str) else None,
        "rrule": raw.get("rrule") if isinstance(raw.get("rrule"), str) else None,
        "target": normalized_target,
        "model": raw.get("model") if isinstance(raw.get("model"), str) else None,
        "reasoning_effort": (
            raw.get("reasoning_effort") if isinstance(raw.get("reasoning_effort"), str) else None
        ),
        "execution_environment": (
            raw.get("execution_environment") if isinstance(raw.get("execution_environment"), str) else None
        ),
    }


def _connect_readonly(path: Path) -> sqlite3.Connection:
    # immutable=1 would skip the live WAL and can silently read stale state.
    connection = sqlite3.connect(f"file:{quote(str(path.resolve()), safe='/')}?mode=ro", uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def _columns(connection: sqlite3.Connection) -> set[str]:
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(threads)")}
    if not REQUIRED_COLUMNS <= columns:
        raise SourceError(f"Codex 任务索引格式不受支持，缺少字段: {', '.join(sorted(REQUIRED_COLUMNS - columns))}")
    return columns


def discover_database(codex_home: Path) -> Path:
    """Choose a single compatible index; do not guess between migration copies."""
    candidates, errors = [], []
    for path in sorted(Path(codex_home).glob("state*.sqlite")):
        try:
            with closing(_connect_readonly(path)) as connection:
                _columns(connection)
            candidates.append(path)
        except (sqlite3.Error, SourceError) as exc:
            errors.append(f"{path.name}: {exc}")
    if len(candidates) > 1:
        raise SourceError("发现多个兼容的 Codex 任务索引，无法判断哪个正在使用: " + ", ".join(path.name for path in candidates))
    if not candidates:
        detail = "; ".join(errors) if errors else "没有 state*.sqlite 文件"
        raise SourceError(f"未发现可用 Codex 任务索引: {detail}")
    return candidates[0]


def is_main_task(row: dict) -> bool:
    if row.get("parent_thread_id") or row.get("parentThreadId") or row.get("ephemeral"):
        return False
    source = row.get("source")
    try:
        parsed = json.loads(source) if isinstance(source, str) else source
    except (ValueError, TypeError):
        parsed = source

    def has_subagent(value) -> bool:
        if isinstance(value, dict):
            return any("subagent" in str(key).replace("_", "").lower() or has_subagent(item) for key, item in value.items())
        if isinstance(value, list):
            return any(has_subagent(item) for item in value)
        return isinstance(value, str) and "subagent" in value.replace("_", "").lower()

    if has_subagent(parsed):
        return False
    # A standalone cron run is a user-visible top-level task. It may be named
    # by a later run once it is idle; the live read/write gates protect the
    # currently running task. Heartbeat and internal rows remain out of scope.
    if row.get("thread_source") not in (None, "", "user", "automation"):
        return False
    if isinstance(parsed, str) and parsed.replace("_", "").lower() in {"heartbeat", "internal"}:
        return False
    return True


def _validate_index_timestamps(row: dict) -> None:
    for field in ("created_at", "updated_at"):
        value = row.get(field)
        if type(value) is not int or not 0 <= value <= MAX_UNIX_SECONDS:
            raise SourceError(f"任务 {row.get('id', '?')} 的 {field} 必须是有效的整数 Unix 秒，不能使用毫秒")


def scan_metadata(db_path: Path, lower_bound: int | None, include_archived: bool, excluded_ids: set) -> list[dict]:
    """Read a short consistent index snapshot; equal timestamps are included."""
    try:
        with closing(_connect_readonly(Path(db_path))) as connection:
            columns = _columns(connection)
            selected = sorted(REQUIRED_COLUMNS | (columns & {
                "name", "thread_source", "history_mode", "parent_thread_id", "parentThreadId", "ephemeral",
            }))
            predicates, parameters = [], []
            if lower_bound is not None:
                if type(lower_bound) is not int:
                    raise SourceError("增量扫描下界必须是整数 Unix 秒")
                predicates.append("updated_at >= ?")
                parameters.append(lower_bound)
            where = " WHERE " + " AND ".join(predicates) if predicates else ""
            connection.execute("BEGIN")
            rows = [dict(row) for row in connection.execute(
                f"SELECT {', '.join(selected)} FROM threads{where} ORDER BY updated_at DESC, id DESC",
                parameters,
            )]
            connection.commit()
    except sqlite3.Error as exc:
        raise SourceError(f"无法读取 Codex 任务索引: {exc}") from exc
    for row in rows:
        _validate_index_timestamps(row)
    # Keep the timestamp range as the only indexed predicate. Some source
    # versions have a separate archived index that otherwise causes a full scan.
    return [
        row for row in rows
        if row["id"] not in excluded_ids and is_main_task(row)
        and (include_archived or not row["archived"])
    ]


def _timestamp(value) -> float | None:
    if type(value) in (int, float):
        if not math.isfinite(value) or not 0 <= value <= MAX_UNIX_SECONDS:
            raise SourceError("消息时间必须是有效的 Unix 秒，不能使用毫秒或非有限数值")
        return float(value)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                return parsed.timestamp()
        except ValueError:
            pass
    return None


def _visible_message(payload: dict) -> tuple[str, str] | None:
    if payload.get("type") != "message" or payload.get("role") not in {"user", "assistant"}:
        return None
    if payload.get("role") == "assistant" and payload.get("phase") in {"analysis", "reasoning"}:
        return None
    blocks = payload.get("content")
    if not isinstance(blocks, list):
        return None
    text = "\n".join(
        block["text"] for block in blocks
        if isinstance(block, dict) and block.get("type") in {"input_text", "output_text", "text"}
        and isinstance(block.get("text"), str)
    )
    return (payload["role"], text) if text.strip() else None


def read_conversation(row: dict, timezone: str) -> dict:
    """Return visible naming context and a title-independent content fingerprint.

    A false history_supported result is not eligible for renaming. The caller
    should retain it as unsupported/pending, rather than generating a title.
    """
    ZoneInfo(timezone)
    _validate_index_timestamps(row)
    path = Path(row["rollout_path"])
    try:
        before = path.stat()
        content = path.read_bytes()
        after = path.stat()
    except OSError as exc:
        raise SourceError(f"无法读取任务 {row['id']} 的对话日志: {exc}") from exc
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
        raise SourceError(f"任务 {row['id']} 的对话日志正在变化，请下次重试")
    if content and not content.endswith(b"\n"):
        raise SourceError(f"任务 {row['id']} 的对话日志尾行尚未完成，请下次重试")
    messages, active_turns, unsupported = [], set(), []
    for line_number, line in enumerate(content.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except (ValueError, UnicodeDecodeError) as exc:
            raise SourceError(f"任务 {row['id']} 的对话日志第 {line_number} 行不是完整 JSON") from exc
        if not isinstance(record, dict) or not isinstance(record.get("payload"), dict):
            raise SourceError(f"任务 {row['id']} 的对话日志第 {line_number} 行结构不受支持")
        payload, record_type = record["payload"], record.get("type")
        if record_type == "session_meta":
            if payload.get("forked_from_id") or payload.get("forked_from") or payload.get("parent_thread_id"):
                unsupported.append("fork_history_requires_branch_validation")
            metadata_id = payload.get("id")
            if metadata_id and metadata_id != row["id"]:
                unsupported.append("rollout_thread_id_mismatch")
        elif record_type in {"rollback", "thread_rolled_back"}:
            unsupported.append("rollback_history_not_supported")
        elif record_type == "event_msg":
            event_type = payload.get("type")
            turn_id = payload.get("turn_id") or "unknown"
            if event_type == "task_started":
                # Turns on one task run serially. A newer start supersedes a
                # terminal event lost when an older turn's process crashed.
                active_turns.clear()
                active_turns.add(turn_id)
            elif event_type in {"task_complete", "turn_aborted"}:
                if turn_id == "unknown":
                    active_turns.clear()
                else:
                    active_turns.discard(turn_id)
                    active_turns.discard("unknown")
            elif event_type in {"thread_rolled_back", "rollback", "history_replaced"}:
                unsupported.append("rollback_history_not_supported")
        elif record_type == "compacted":
            # Compaction changes model context, not the original visible mainline.
            # replacement_history is a duplicate retained context and is not appended.
            if not messages:
                unsupported.append("compacted_history_missing_original_messages")
        elif record_type == "response_item":
            visible = _visible_message(payload)
            if visible is not None:
                instant = _timestamp(record.get("timestamp"))
                messages.append({
                    "role": visible[0], "text": visible[1],
                    "timestamp": datetime.fromtimestamp(instant, datetime_timezone.utc).isoformat() if instant is not None else None,
                })
    material = [{"role": message["role"], "text": message["text"]} for message in messages]
    fingerprint = hashlib.sha256(json.dumps(material, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    latest_activity_at, date_source = None, None
    for role in ("assistant", "user"):
        times = [_timestamp(message["timestamp"]) for message in messages if message["role"] == role]
        times = [instant for instant in times if instant is not None]
        if times:
            latest_activity_at, date_source = max(times), role
            break
    if latest_activity_at is None:
        latest_activity_at, date_source = _timestamp(row.get("created_at")), "created_at"
    if latest_activity_at is None:
        raise SourceError(f"任务 {row['id']} 没有可用的消息时间或创建时间")
    return {
        "thread_id": row["id"],
        "fingerprint": fingerprint,
        "latest_activity_at": latest_activity_at,
        "date_source": date_source,
        "messages": messages,
        "active": bool(active_turns),
        "history_supported": not unsupported,
        "unsupported_reason": ",".join(dict.fromkeys(unsupported)) if unsupported else None,
    }
