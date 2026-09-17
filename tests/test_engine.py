"""Behavior tests against synthetic SQLite indexes and rollout logs only."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "codex-title-maintenance"
sys.path.insert(0, str(SKILL / "scripts"))

from title_maintenance import config, source
from title_maintenance.engine import Engine


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="title-maintenance-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.home = self.base / "synthetic-codex-home"
        self.home.mkdir()
        self.data = self.base / "private-state"
        self.time = datetime(2026, 9, 17, 2, 1, tzinfo=timezone.utc)
        self.engine = Engine(self.data, self.home, now=lambda: self.time)
        self.engine.initialize(SKILL / "defaults")
        self.index = self.home / "state_5.sqlite"
        with closing(sqlite3.connect(self.index)) as db, db:
            db.execute("""CREATE TABLE threads (
                id TEXT PRIMARY KEY, rollout_path TEXT NOT NULL,
                created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
                source TEXT NOT NULL, title TEXT NOT NULL, archived INTEGER NOT NULL,
                parent_thread_id TEXT, thread_source TEXT, ephemeral INTEGER DEFAULT 0
            )""")

    def add_task(self, task_id="task-a", *, title="原始标题", archived=False,
                 active=False, updated=None, source_kind="cli", parent=None,
                 thread_source="user", ephemeral=False, assistant="设计一套可配置的本地改名工具。"):
        log = self.home / f"{task_id}.jsonl"
        at = self.time - timedelta(minutes=1)
        records = [
            {"timestamp": at.isoformat(), "type": "session_meta", "payload": {"id": task_id}},
            {"timestamp": at.isoformat(), "type": "event_msg", "payload": {"type": "task_started", "turn_id": "turn-1"}},
            self.message("user", "请设计本地任务标题维护方案。", at),
            self.message("assistant", assistant, at + timedelta(seconds=5)),
        ]
        if not active:
            records.append({"timestamp": at.isoformat(), "type": "event_msg", "payload": {"type": "task_complete", "turn_id": "turn-1"}})
        log.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8")
        with closing(sqlite3.connect(self.index)) as db, db:
            db.execute("INSERT INTO threads VALUES (?,?,?,?,?,?,?,?,?,?)", (
                task_id, str(log), int(at.timestamp()), int(updated if updated is not None else self.time.timestamp()),
                source_kind, title, int(archived), parent, thread_source, int(ephemeral),
            ))
        return task_id

    @staticmethod
    def message(role, text, at):
        return {"timestamp": at.isoformat(), "type": "response_item", "payload": {
            "type": "message", "role": role, "content": [{"type": "output_text" if role == "assistant" else "input_text", "text": text}],
        }}

    def append(self, task_id, record, *, touch=True):
        with (self.home / f"{task_id}.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        if touch:
            self.set_source(task_id, updated_at=int(self.time.timestamp()))

    def set_source(self, task_id, **changes):
        allowed = {"title", "updated_at", "archived", "source", "parent_thread_id", "thread_source", "ephemeral"}
        assert set(changes) <= allowed
        sql = ", ".join(f"{key}=?" for key in changes)
        with closing(sqlite3.connect(self.index)) as db, db:
            db.execute(f"UPDATE threads SET {sql} WHERE id=?", (*changes.values(), task_id))

    def row(self, table, key, value):
        assert table in {"threads", "runs", "rename_journal", "contexts"}
        assert key in {"id", "thread_id"}
        with self.engine.connect() as db:
            result = db.execute(f"SELECT * FROM {table} WHERE {key}=?", (value,)).fetchone()
        return dict(result) if result else None

    def live(self, task_id="task-a", status="idle"):
        with closing(sqlite3.connect(self.index)) as db, db:
            title = db.execute("SELECT title FROM threads WHERE id=?", (task_id,)).fetchone()[0]
        return {"thread_id": task_id, "title": title, "status": status}

    def start(self, **kwargs):
        result = self.engine.scan(**kwargs)
        self.assertIn("run_id", result, result)
        return result["run_id"]

    def proposal(self, run_id, task_id="task-a", *, subject="本地任务标题维护方案"):
        self.engine.context(task_id, run_id, limit=100000)
        return self.engine.propose(run_id, task_id, self.live(task_id), prefix="探索",
                                   subject=subject, summary="设计可配置的本地任务标题维护工具。")

    def apply_fixture(self, run_id, proposal):
        """Simulate the host tool by changing the fixture database, never a real app."""
        auth = self.engine.authorize(run_id, proposal["intent_id"], self.live(proposal["thread_id"]))
        self.set_source(proposal["thread_id"], title=auth["arguments"]["title"], updated_at=int(self.time.timestamp()))
        return self.engine.confirm(run_id, proposal["intent_id"], self.live(proposal["thread_id"]))

    def initial_rename(self, task_id="task-a"):
        run_id = self.start()
        proposal = self.proposal(run_id, task_id)
        self.apply_fixture(run_id, proposal)
        self.engine.finish(run_id)
        return proposal

    def enable(self):
        self.engine.bind("fixture-automation", "fixture-maintenance")
        self.engine.control("start")

    def cli(self, *arguments):
        return subprocess.run([
            sys.executable, str(SKILL / "scripts" / "title_maintenance.py"),
            "--codex-home", str(self.home), "--data-dir", str(self.data), *arguments,
        ], check=False, capture_output=True, text=True)

    def test_scan_uses_start_watermark_and_includes_equal_boundary(self):
        started = int(self.time.timestamp())
        self.add_task(updated=started)
        original = source.scan_metadata

        def delayed_scan(*args, **kwargs):
            rows = original(*args, **kwargs)
            self.time += timedelta(seconds=30)
            return rows

        with patch("title_maintenance.engine.source.scan_metadata", side_effect=delayed_scan):
            run_id = self.start()
        self.assertEqual(self.engine.status()["watermark"], started)
        self.engine.finish(run_id)
        self.add_task("at-boundary", updated=started - 300)
        self.add_task("below-boundary", updated=started - 301)
        run = self.engine.scan()
        self.assertEqual(run["lower_bound"], started - 300)
        self.assertIsNotNone(self.row("threads", "id", "at-boundary"))
        self.assertIsNone(self.row("threads", "id", "below-boundary"))

    def test_failed_discovery_does_not_advance_watermark(self):
        self.add_task()
        run_id = self.start()
        old = self.engine.status()["watermark"]
        self.engine.finish(run_id)
        self.time += timedelta(hours=2)
        with patch("title_maintenance.engine.source.scan_metadata", side_effect=source.SourceError("fixture failure")):
            with self.assertRaises(ValueError):
                self.engine.scan()
        self.assertEqual(self.engine.status()["watermark"], old)
        self.assertEqual(self.engine.status()["recent_runs"][0]["status"], "failed")

    def test_busy_pending_survives_advanced_watermark(self):
        task_id = self.add_task(active=True)
        run_id = self.start()
        self.assertTrue(self.engine.next_items(run_id)["items"][0]["active_hint"])
        self.engine.defer(run_id, task_id, "Fixture live status is inProgress")
        self.assertEqual(self.row("threads", "id", task_id)["status"], "deferred")
        self.engine.finish(run_id)
        self.time += timedelta(hours=2)
        still_busy = self.start()
        self.assertTrue(self.engine.next_items(still_busy)["items"][0]["active_hint"])
        self.engine.defer(still_busy, task_id, "Fixture live status is still inProgress")
        self.engine.finish(still_busy)
        self.time += timedelta(hours=2)
        self.append(task_id, {"timestamp": self.time.isoformat(), "type": "event_msg",
                              "payload": {"type": "task_complete", "turn_id": "turn-1"}}, touch=False)
        result = self.engine.scan()
        self.assertEqual(result["discovered"], 0)
        self.assertEqual(self.engine.next_items(result["run_id"])["items"][0]["thread_id"], task_id)

    def test_completed_new_turn_replaces_old_start_without_terminal_event(self):
        task_id = self.add_task(active=True)
        self.append(task_id, {"timestamp": self.time.isoformat(), "type": "event_msg",
                              "payload": {"type": "task_started", "turn_id": "turn-2"}})
        self.append(task_id, self.message("assistant", "崩溃之后的新一轮已经正常完成。", self.time))
        self.append(task_id, {"timestamp": self.time.isoformat(), "type": "event_msg",
                              "payload": {"type": "task_complete", "turn_id": "turn-2"}})
        run_id = self.start()
        self.assertEqual(self.engine.next_items(run_id)["items"][0]["thread_id"], task_id)
        proposal = self.proposal(run_id)
        self.assertTrue(self.apply_fixture(run_id, proposal)["confirmed"])

    def test_crashed_last_turn_can_recover_when_fresh_live_status_is_idle(self):
        self.add_task(active=True)
        run_id = self.start()
        self.assertTrue(self.engine.next_items(run_id)["items"][0]["active_hint"])
        proposal = self.proposal(run_id)
        result = self.apply_fixture(run_id, proposal)
        self.assertTrue(result["confirmed"])
        self.assertFalse(result["new_content_pending"])
        self.assertEqual(self.row("threads", "id", "task-a")["status"], "done")

    def test_active_or_unknown_live_state_prevents_proposal_and_authorization(self):
        self.add_task(active=False)
        run_id = self.start()
        self.engine.context("task-a", run_id, limit=100000)
        for status in ("inProgress", "waitingForInput", "unknown", None):
            with self.subTest(stage="propose", status=status), self.assertRaises(ValueError):
                self.engine.propose(run_id, "task-a", self.live(status=status), "探索", "标题", "整个任务摘要")
        proposal = self.proposal(run_id)
        for status in ("inProgress", "waitingForInput", "unknown", None):
            with self.subTest(stage="authorize", status=status), self.assertRaises(ValueError):
                self.engine.authorize(run_id, proposal["intent_id"], self.live(status=status))
        self.assertTrue(self.apply_fixture(run_id, proposal)["confirmed"])

    def test_own_rename_metadata_change_does_not_request_new_model_context(self):
        self.add_task()
        previous = self.initial_rename()
        self.time += timedelta(hours=2)
        self.set_source("task-a", updated_at=int(self.time.timestamp()))
        run_id = self.start()
        self.assertEqual(self.engine.next_items(run_id)["items"][0]["action"], "check")
        result = self.engine.propose(run_id, "task-a", self.live())
        self.assertTrue(result["unchanged"])
        self.assertEqual(result["title"], previous["new_title"])
        self.assertEqual(self.row("threads", "id", "task-a")["status"], "done")

    def test_external_title_change_is_protected(self):
        self.add_task()
        self.initial_rename()
        self.time += timedelta(hours=2)
        self.set_source("task-a", title="用户主动设置的标题", updated_at=int(self.time.timestamp()))
        run_id = self.start()
        self.assertTrue(self.engine.propose(run_id, "task-a", self.live())["protected"])
        self.assertEqual(self.row("threads", "id", "task-a")["status"], "protected")
        self.engine.finish(run_id)
        run_id = self.start(mode="full")
        self.assertEqual(self.engine.next_items(run_id)["items"], [])
        self.assertEqual(self.live()["title"], "用户主动设置的标题")

    def test_journal_recovers_write_applied_before_crash(self):
        self.add_task(archived=True)
        first = self.start()
        proposal = self.proposal(first)
        self.engine.authorize(first, proposal["intent_id"], self.live())
        self.set_source("task-a", title=proposal["new_title"])
        self.time += timedelta(minutes=16)
        second = self.start()
        item = self.engine.next_items(second)["items"][0]
        self.assertEqual(item["action"], "recover")
        result = self.engine.confirm(second, item["intent_id"], self.live(), recovery=True)
        self.assertTrue(result["confirmed"])
        self.assertTrue(result["archive_state_preserved"])
        self.assertEqual(self.row("rename_journal", "id", item["intent_id"])["status"], "confirmed")
        self.assertEqual(self.row("threads", "id", "task-a")["status"], "done")

    def test_journal_recovers_unapplied_and_conflicting_titles(self):
        for final_title, expected in [(None, "not_applied"), ("外部修改", "protected")]:
            with self.subTest(expected=expected):
                task_id = "task-" + expected
                self.add_task(task_id)
                first = self.start()
                proposal = self.proposal(first, task_id)
                self.engine.authorize(first, proposal["intent_id"], self.live(task_id))
                if final_title is not None:
                    self.set_source(task_id, title=final_title)
                self.engine.finish(first)
                second = self.start()
                result = self.engine.confirm(second, proposal["intent_id"], self.live(task_id), recovery=True)
                self.assertEqual(result["recovered"], expected)
                self.assertEqual(self.row("threads", "id", task_id)["status"], "pending" if final_title is None else "protected")
                self.engine.finish(second)

    def test_cannot_propose_before_continuously_reading_all_context(self):
        self.add_task(assistant="需要完整读取的文字。" * 100)
        run_id = self.start()
        with self.assertRaises(ValueError):
            self.engine.propose(run_id, "task-a", self.live(), "探索", "标题", "摘要")
        part = self.engine.context("task-a", run_id, limit=20)
        with self.assertRaises(ValueError):
            self.engine.context("task-a", run_id, offset=100, limit=20)
        with self.assertRaises(ValueError):
            self.engine.propose(run_id, "task-a", self.live(), "探索", "标题", "摘要")
        self.engine.context("task-a", run_id, offset=part["next_offset"], limit=100000)
        result = self.engine.propose(run_id, "task-a", self.live(), "探索", "标题", "摘要")
        self.assertIn("intent_id", result)

    def test_changed_content_invalidates_prepared_write(self):
        self.add_task()
        run_id = self.start()
        proposal = self.proposal(run_id)
        self.append("task-a", self.message("user", "新增的重要范围变更。", self.time))
        with self.assertRaises(ValueError):
            self.engine.authorize(run_id, proposal["intent_id"], self.live())

    def test_subject_whitespace_is_normalized_before_journal_and_confirmation(self):
        self.add_task()
        run_id = self.start()
        proposal = self.proposal(run_id, subject="A  B")
        self.assertEqual(proposal["new_title"], "探索｜A B｜0917")
        result = self.apply_fixture(run_id, proposal)
        self.assertTrue(result["confirmed"])
        self.assertEqual(self.row("threads", "id", "task-a")["last_auto_title"], "探索｜A B｜0917")

    def test_title_limit_counts_utf16_units_instead_of_unicode_codepoints(self):
        self.add_task()
        run_id = self.start()
        self.engine.context("task-a", run_id, limit=100000)
        subject = "😀" * 27
        title = "探索｜" + subject + "｜0917"
        self.assertLess(len(title), 60)
        self.assertGreater(len(title.encode("utf-16-le")) // 2, 60)
        with self.assertRaises(ValueError):
            self.engine.propose(run_id, "task-a", self.live(), "探索", subject, "整个任务的主线摘要")
        with self.engine.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM rename_journal").fetchone()[0], 0)

    def test_start_stop_and_window_apply_to_scheduled_runs_only(self):
        self.add_task()
        self.assertEqual(self.engine.scan(trigger="scheduled"), {"skipped": "disabled"})
        self.enable()
        self.time = self.time.replace(minute=5)
        self.assertEqual(self.engine.scan(trigger="scheduled"), {"skipped": "outside_launch_window"})
        self.time = self.time.replace(minute=1)
        run_id = self.start(trigger="scheduled")
        proposal = self.proposal(run_id)
        self.engine.control("stop")
        with self.assertRaises(ValueError):
            self.engine.authorize(run_id, proposal["intent_id"], self.live())
        self.engine.finish(run_id)
        self.assertEqual(self.engine.scan(trigger="scheduled"), {"skipped": "disabled"})
        manual = self.start()
        self.engine.finish(manual)
        self.enable()
        self.assertEqual(self.engine.scan(trigger="scheduled"), {"skipped": "slot_already_claimed"})

    def test_scheduled_model_mismatch_preserves_watermark_and_skip_priority(self):
        self.add_task()
        first = self.start()
        self.engine.finish(first)
        watermark = self.engine.status()["watermark"]
        self.add_task("fixture-maintenance")
        with closing(sqlite3.connect(self.index)) as db, db:
            db.execute("ALTER TABLE threads ADD COLUMN model TEXT")
            db.execute("ALTER TABLE threads ADD COLUMN reasoning_effort TEXT")
            db.execute("UPDATE threads SET model='fixture-wrong-model',reasoning_effort='low' WHERE id='fixture-maintenance'")
        self.engine.bind("fixture-automation", "fixture-maintenance")
        self.time += timedelta(hours=2)
        self.assertEqual(self.engine.scan(trigger="scheduled"), {"skipped": "disabled"})
        self.engine.control("start")
        self.time = self.time.replace(minute=5)
        self.assertEqual(self.engine.scan(trigger="scheduled"), {"skipped": "outside_launch_window"})
        self.time = self.time.replace(minute=1)
        result = self.engine.scan(trigger="scheduled")
        self.assertEqual(result["skipped"], "maintenance_model_mismatch")
        self.assertEqual(self.engine.status()["watermark"], watermark)
        self.assertEqual(len(self.engine.status()["recent_runs"]), 1)
        with closing(sqlite3.connect(self.index)) as db, db:
            db.execute("UPDATE threads SET model=?,reasoning_effort=? WHERE id='fixture-maintenance'",
                       (self.engine.cfg()["agent"]["model"], self.engine.cfg()["agent"]["reasoning_effort"]))
        successful = self.start(trigger="scheduled")
        self.engine.finish(successful)
        with closing(sqlite3.connect(self.index)) as db, db:
            db.execute("UPDATE threads SET model='fixture-wrong-model' WHERE id='fixture-maintenance'")
        self.assertEqual(self.engine.scan(trigger="scheduled"), {"skipped": "slot_already_claimed"})

    def test_only_one_run_holds_lease_and_expired_run_cannot_write(self):
        self.add_task()
        first = self.start()
        proposal = self.proposal(first)
        other = Engine(self.data, self.home, now=lambda: self.time)
        self.assertEqual(other.scan(), {"skipped": "another_run_is_active"})
        self.time += timedelta(minutes=16)
        second = other.scan()
        self.assertIn("run_id", second)
        with self.assertRaises(ValueError):
            self.engine.authorize(first, proposal["intent_id"], self.live())
        self.assertEqual(self.row("runs", "id", first)["status"], "expired")

    def test_rule_change_reevaluates_old_task_outside_incremental_range(self):
        self.add_task()
        self.initial_rename()
        self.time += timedelta(hours=2)
        unchanged = self.start()
        self.assertTrue(self.engine.propose(unchanged, "task-a", self.live())["unchanged"])
        self.engine.finish(unchanged)
        self.time += timedelta(hours=2)
        (self.data / "naming-rules.md").write_text("新规则：标题需要说明最终交付物。\n", encoding="utf-8")
        result = self.engine.scan()
        self.assertEqual(result["discovered"], 0)
        self.assertEqual(self.engine.next_items(result["run_id"])["items"][0]["action"], "name")
        context = self.engine.context("task-a", result["run_id"], limit=100000)
        self.assertIn("请设计本地任务标题维护方案。", context["text"])
        self.assertNotIn("已处理主线摘要", context["text"])

    def test_rule_change_after_incremental_context_requires_reading_full_context(self):
        self.add_task()
        self.initial_rename()
        self.time += timedelta(hours=2)
        self.append("task-a", self.message("user", "增加可分享的安装入口。", self.time))
        run_id = self.start()
        incremental = self.engine.context("task-a", run_id, limit=100000)
        self.assertIn("已处理主线摘要", incremental["text"])
        (self.data / "naming-rules.md").write_text("新规则：重新审视完整任务的分类。\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.engine.propose(run_id, "task-a", self.live(), "方案", "任务维护工具", "新的主线摘要")
        full = self.engine.context("task-a", run_id, limit=100000)
        self.assertIn("请设计本地任务标题维护方案。", full["text"])
        self.assertIn("intent_id", self.engine.propose(run_id, "task-a", self.live(), "方案", "任务维护工具", "新的主线摘要"))

    def test_schedule_change_requires_rebinding_but_window_change_does_not(self):
        self.enable()
        self.engine.control("stop")
        cfg = self.engine.cfg()
        cfg["schedule"]["launch_window_minutes"] = 8
        config.save_config(self.data, cfg)
        self.assertTrue(self.engine.control("start")["enabled"])
        self.engine.control("stop")
        cfg["schedule"]["times"] = ["09:00", "18:00"]
        config.save_config(self.data, cfg)
        with self.assertRaises(ValueError):
            self.engine.control("start")
        self.engine.bind("fixture-automation", "fixture-maintenance")
        self.assertTrue(self.engine.control("start")["enabled"])

    def test_scope_filters_archived_excluded_maintenance_and_internal_tasks(self):
        self.add_task("normal")
        self.add_task("archived", archived=True)
        self.add_task("excluded")
        self.add_task("maintenance")
        self.add_task("subagent", source_kind=json.dumps({"subagent": {"thread_spawn": {}}}))
        self.add_task("child", parent="normal")
        self.add_task("automation", thread_source="automation")
        self.add_task("ephemeral", ephemeral=True)
        cfg = self.engine.cfg()
        cfg["scope"]["exclude_thread_ids"] = ["excluded"]
        config.save_config(self.data, cfg)
        self.engine.bind("fixture-automation", "maintenance")
        run_id = self.start(mode="full")
        self.assertEqual({item["thread_id"] for item in self.engine.next_items(run_id)["items"]}, {"normal", "archived"})
        self.engine.finish(run_id)
        cfg["scope"]["include_archived"] = False
        config.save_config(self.data, cfg)
        run_id = self.start()
        self.assertEqual({item["thread_id"] for item in self.engine.next_items(run_id)["items"]}, {"normal"})

    def test_removing_codex_source_does_not_scan_or_preview_codex(self):
        self.add_task()
        cfg = self.engine.cfg()
        cfg["scope"]["targets"] = ["chat", "work"]
        config.save_config(self.data, cfg)
        with self.assertRaises(ValueError):
            self.engine.scan()
        self.assertEqual(self.engine.preview()["total_candidates"], 0)
        self.assertEqual(self.engine.status()["counts"], {})

    def test_existing_pending_item_is_rechecked_against_internal_scope(self):
        self.add_task()
        run_id = self.start()
        self.set_source("task-a", parent_thread_id="other-task")
        self.assertEqual(self.engine.next_items(run_id)["items"], [])
        with self.assertRaises(ValueError):
            self.engine.context("task-a", run_id)

    def test_narrower_scope_and_new_maintenance_binding_keep_incremental_mode(self):
        self.add_task("normal")
        self.add_task("newly-excluded")
        self.add_task("maintenance")
        first = self.start()
        self.engine.finish(first)
        cfg = self.engine.cfg()
        cfg["scope"]["include_archived"] = False
        cfg["scope"]["exclude_thread_ids"] = ["newly-excluded"]
        config.save_config(self.data, cfg)
        self.engine.bind("fixture-automation", "maintenance")
        self.time += timedelta(hours=2)
        self.assertEqual(self.engine.preview()["mode"], "incremental")
        result = self.engine.scan()
        self.assertEqual(result["mode"], "incremental")
        self.assertEqual({item["thread_id"] for item in self.engine.next_items(result["run_id"])["items"]}, {"normal"})

    def test_enabling_archived_scope_forces_full_discovery_of_old_tasks(self):
        self.add_task("old-archived", archived=True, updated=int(self.time.timestamp()) - 7200)
        cfg = self.engine.cfg()
        cfg["scope"]["include_archived"] = False
        config.save_config(self.data, cfg)
        first = self.start()
        self.engine.finish(first)
        self.assertIsNone(self.row("threads", "id", "old-archived"))
        cfg["scope"]["include_archived"] = True
        config.save_config(self.data, cfg)
        self.time += timedelta(hours=2)
        self.assertEqual(self.engine.preview()["mode"], "full")
        result = self.engine.scan()
        self.assertEqual(result["mode"], "full")
        self.assertIsNotNone(self.row("threads", "id", "old-archived"))

    def test_removing_exclusion_forces_full_discovery_of_old_tasks(self):
        self.add_task("old-excluded", updated=int(self.time.timestamp()) - 7200)
        cfg = self.engine.cfg()
        cfg["scope"]["exclude_thread_ids"] = ["old-excluded"]
        config.save_config(self.data, cfg)
        first = self.start()
        self.engine.finish(first)
        self.assertIsNone(self.row("threads", "id", "old-excluded"))
        cfg["scope"]["exclude_thread_ids"] = []
        config.save_config(self.data, cfg)
        self.time += timedelta(hours=2)
        self.assertEqual(self.engine.preview()["mode"], "full")
        result = self.engine.scan()
        self.assertEqual(result["mode"], "full")
        self.assertIsNotNone(self.row("threads", "id", "old-excluded"))

    def test_preview_does_not_modify_watermark_or_create_run(self):
        self.add_task()
        before = self.engine.status()
        result = self.engine.preview(mode="full")
        self.assertEqual(result["total_candidates"], 1)
        self.assertEqual(self.engine.status(), before)

    def test_cli_scan_and_next_share_durable_state_across_processes(self):
        self.add_task()
        scan = self.cli("scan", "--mode", "full")
        self.assertEqual(scan.returncode, 0, scan.stderr)
        run_id = json.loads(scan.stdout)["result"]["run_id"]
        nxt = self.cli("next", "--run-id", run_id)
        self.assertEqual(nxt.returncode, 0, nxt.stderr)
        self.assertEqual(json.loads(nxt.stdout)["result"]["items"][0]["thread_id"], "task-a")
        end = self.cli("finish", "--run-id", run_id)
        self.assertEqual(end.returncode, 0, end.stderr)
        self.assertEqual(json.loads(end.stdout)["result"]["recent_runs"][0]["status"], "finished")

    def test_cli_invalid_configuration_fails_without_overwriting_previous_settings(self):
        before = (self.data / "config.toml").read_bytes()
        patch_file = self.base / "invalid-config-patch.json"
        patch_file.write_text(json.dumps({"schedule": {"launch_window_minutes": 0}}), encoding="utf-8")
        result = self.cli("config-apply", "--file", str(patch_file))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(json.loads(result.stderr)["ok"])
        self.assertEqual((self.data / "config.toml").read_bytes(), before)

    def test_unbound_config_change_does_not_sync_or_mutate_observed_model(self):
        self.add_task()
        with closing(sqlite3.connect(self.index)) as db, db:
            db.execute("ALTER TABLE threads ADD COLUMN model TEXT")
            db.execute("ALTER TABLE threads ADD COLUMN reasoning_effort TEXT")
            db.execute("UPDATE threads SET model='fixture-old-model',reasoning_effort='low' WHERE id='task-a'")
        patch_file = self.base / "model-config-patch.json"
        patch_file.write_text(json.dumps({
            "agent": {"model": "fixture-requested-model", "reasoning_effort": "medium"},
            "schedule": {"times": ["09:00", "18:00"]},
        }), encoding="utf-8")
        changed = self.cli("config-apply", "--file", str(patch_file))
        self.assertEqual(changed.returncode, 0, changed.stderr)
        applied = json.loads(changed.stdout)["result"]
        self.assertFalse(applied["native_schedule_sync_required"])
        self.assertFalse(applied["maintenance_model_sync_required"])
        status = self.cli("model-status", "--thread-id", "task-a")
        self.assertEqual(status.returncode, 0, status.stderr)
        observed = json.loads(status.stdout)["result"]
        self.assertEqual(observed["requested"], {"model": "fixture-requested-model", "reasoning_effort": "medium"})
        self.assertEqual(observed["persisted_model"], "fixture-old-model")
        self.assertEqual(observed["persisted_reasoning_effort"], "low")
        self.assertFalse(observed["matches_persisted_metadata"])
        with closing(sqlite3.connect(self.index)) as db:
            self.assertEqual(db.execute("SELECT model,reasoning_effort FROM threads WHERE id='task-a'").fetchone(),
                             ("fixture-old-model", "low"))
        self.assertFalse(self.engine.status()["enabled"])
        self.assertIsNone(self.engine.status()["automation_id"])


if __name__ == "__main__":
    unittest.main()
