import argparse
from contextlib import closing
import json
from shutil import copyfile
import sqlite3
from unittest.mock import patch

from test_context import ContextCase, LEGACY, MARKDOWN, item, progress, source_at
import worklog
from worklog_store import (
    WorklogError, append_change, connect, project_db, schema_version, timestamp, v2_min_after, watermark,
)
import worklog_upgrade


HISTORICAL_REVISIONS = {1: LEGACY, 2: "v0.2.0", 3: "56fe16b4ebfc76f329a08364aba786f47724cef5"}
CONTEXT_FILES = (
    "worklog_context.py", "worklog_summaries.py", "worklog_upgrade.py", "schema_context.sql",
)
BLOCKER_DETAILS = {"status": "OPEN", "blocked_work": "复查结果", "unblock_condition": "补齐依据"}


def resolved_blocker(blocker_id, evidence):
    return item("BLOCKER", "已补齐材料", details={
        **BLOCKER_DETAILS, "status": "RESOLVED", "outcome_md": "已复查原文。",
    }, refs=evidence, replaces_id=blocker_id)


class TaskLifecycleUpgradeTest(ContextCase):
    def historical_cli(self, version):
        files = CONTEXT_FILES if version >= 2 else ()
        if version == 3:
            files += ("schema_lifecycle.sql",)
        return source_at(HISTORICAL_REVISIONS[version], self.root / f"schema-{version}-cli", files)

    def test_schema_one_upgrade_and_restore(self):
        self.verify_upgrade_and_restore(1)

    def test_schema_two_upgrade_and_restore(self):
        self.verify_upgrade_and_restore(2)

    def test_schema_three_upgrade_and_restore(self):
        self.verify_upgrade_and_restore(3)

    def verify_upgrade_and_restore(self, version):
        old = self.historical_cli(version)
        at = ("--project", str(self.project))
        identity = self.call(*at, "init", data={"name": f"schema {version} 合成库"}, cli=old)
        receipts = []

        def write(command, operation, **fields):
            reply = self.write(command, operation, identity, args=at, cli=old, **fields)
            receipts.append((command, operation, fields, reply))
            return reply["result"]

        waiting = write("task-create", "waiting", title="待开始的旧任务")["task"]
        task = write("task-create", "working", title="已有接手的旧任务")["task"]
        claim = write("claim", "claim", task_id=task["task_id"], expected_revision=task["revision"],
                      client="codex", session_id="old-session")["task"]
        entry_fields = {
            "task_id": task["task_id"], "run_id": claim["active_run"]["run_id"],
            "summary": "工作进展和交接", "body_md": MARKDOWN, "next_step": "复查依据。",
        }
        checkpoint = write("checkpoint", "progress", expected_revision=claim["revision"], **entry_fields)
        handoff = write("handoff", "handoff", expected_revision=checkpoint["task"]["revision"], **entry_fields)
        active = write("claim", "claim-again", task_id=task["task_id"],
                       expected_revision=handoff["task"]["revision"],
                       client="opencode", session_id="old-session-2")["task"]
        entry_id = checkpoint["entry"]["entry_id"]
        handoff_id = handoff["entry"]["entry_id"]
        page = ("changes", "--project-id", identity["project_id"], "--data-generation",
                identity["data_generation"], "--after", "0")
        queries = [
            ("identity",), ("task-get", task["number"]), ("entry-get", entry_id),
            ("entry-get", handoff_id), (*page, "--limit", "5"),
        ]
        if version >= 2:
            record = write("record", "working-context", scope="TASK", task_id=task["task_id"],
                           expected_revision=active["revision"], expected_context_position=0,
                           run_id=active["active_run"]["run_id"], records=[
                               progress("含旧记录引用的进展",
                                        refs=[{"type": "ENTRY", "target_entry_id": entry_id}]),
                               item("TODO", "后续复核", details={"status": "OPEN"}),
                               item("BLOCKER", "暂缺材料", details=BLOCKER_DETAILS),
                           ])
            queries.extend(("record-get", row["record_id"]) for row in record["records"])
        blocker_refs = [{"command": "record-get", "id": record["records"][2]["record_id"]}] if version >= 2 else []
        if version == 3:
            started = write("task-start", "start", task_id=waiting["number"],
                            expected_revision=waiting["revision"])["task"]
            log, todo = write("record", "completion-context", scope="TASK", task_id=waiting["task_id"],
                              expected_revision=started["revision"], expected_context_position=0,
                              records=[progress("完成依据"),
                                       item("TODO", "保留待办", details={"status": "OPEN"})])["records"]
            waiting = write("task-complete", "complete", task_id=waiting["number"],
                            expected_revision=started["revision"], completed_scope="已复核旧库",
                            evidence=[{"type": "RECORD", "target_record_id": log["record_id"]}],
                            todo_dispositions={todo["record_id"]: "留给后续验证"})["task"]
            completion = waiting["completion"]
            old_done_context = self.call(*at, "context", "--task", waiting["number"], cli=old)
            self.assertEqual(old_done_context["result"]["task"]["completion"], completion)
            queries.extend([("record-get", log["record_id"]), ("record-get", todo["record_id"])])
            incompatible = [("task-get", waiting["number"]), ("snapshot",), page]
        else:
            queries.extend([("task-get", waiting["number"]), ("snapshot",), page])
            incompatible = []
            self.write("task-start", "start-on-old-schema", identity, task_id=waiting["number"],
                       expected_revision=waiting["revision"], error="upgrade_required")

        original = [self.call(*at, *query, cli=old) for query in queries]
        for query in incompatible:
            self.call(*at, *query, cli=old, error="protocol_incompatible")
        old_bytes = self.db.read_bytes()
        with closing(connect(self.db, "ro")) as db:
            old_dump = list(db.iterdump())
            old_changes = db.execute(
                "SELECT seq, change_id, operation_id, request_hash, payload FROM changes ORDER BY seq"
            ).fetchall()
            old_watermark = watermark(db)
        for query in (*queries, *incompatible):
            if query[0] != "record-get":
                self.call(*query, error="upgrade_required")
        if version == 3:
            self.assertEqual(self.write("task-complete", "complete", identity, **receipts[-1][2]),
                             {**receipts[-1][3], "protocol_version": "2.0"})

        expected_tasks = [
            {**waiting, "display_status": waiting["status"], "archived": False, "priority": None,
             "blocker_refs": []},
            {**active, "display_status": "BLOCKED" if blocker_refs else active["status"], "archived": False,
             "priority": None, "blocker_refs": blocker_refs},
        ]

        def verify_migrated_read(project_args):
            for query, old_reply in zip(queries, original):
                if query[0] == "changes":
                    self.call(*project_args, *query, error="cursor_stale")
                elif query[0] == "task-get":
                    self.assertEqual(self.call(*project_args, *query)["result"],
                                     next(row for row in expected_tasks if row["number"] == query[1]))
                elif query[0] == "snapshot":
                    self.assertEqual(self.call(*project_args, *query)["result"],
                                     {**old_reply["result"], "tasks": expected_tasks})
                else:
                    self.assertEqual(self.call(*project_args, *query), {**old_reply, "protocol_version": "2.0"})
            snapshot = self.call(*project_args, "snapshot")["result"]
            self.assertEqual(snapshot["tasks"], expected_tasks)
            self.assertEqual(snapshot["watermark"], old_watermark)
            self.assertEqual([entry["entry_id"] for entry in snapshot["entry_refs"]], [entry_id, handoff_id])
            self.assertEqual(self.call(*project_args, "task-get", waiting["number"])["result"], expected_tasks[0])
            self.call(*project_args, *page, error="cursor_stale")
            self.assertEqual(self.call(*project_args, *page[:-1], str(old_watermark))["result"], {
                "changes": [], "next_after": old_watermark, "has_more": False, "watermark": old_watermark,
            })

        backup = self.root / f"schema-{version}.sqlite3"
        self.assertEqual(self.call("backup", "--output", str(backup))["result"]["schema_version"], version)
        backup_bytes = backup.read_bytes()
        with closing(connect(backup, "ro")) as db:
            self.assertEqual(schema_version(db), version)
            self.assertEqual(list(db.iterdump()), old_dump)
            worklog_upgrade.check_integrity(db)

        def fail_after_schema_four(db):
            install(db)
            self.assertEqual(schema_version(db), 4)
            self.assertEqual(v2_min_after(db), old_watermark)
            raise RuntimeError("injected after schema 4 DDL")

        install = worklog_upgrade.install_v4_schema
        args = argparse.Namespace(project=str(self.project), command="upgrade", backup=str(backup))
        with patch("worklog_upgrade.install_v4_schema", side_effect=fail_after_schema_four):
            with self.assertRaisesRegex(RuntimeError, "injected after schema 4 DDL"):
                worklog.execute(args)
        self.assertEqual(self.db.read_bytes(), old_bytes)
        self.assertEqual(backup.read_bytes(), backup_bytes)
        self.assertEqual([self.call(*at, *query, cli=old) for query in queries], original)
        for query in incompatible:
            self.call(*at, *query, cli=old, error="protocol_incompatible")
        for command, operation, fields, original_receipt in receipts:
            self.assertEqual(self.write(command, operation, identity, args=at, cli=old, **fields),
                             original_receipt)

        expected = {"previous_schema_version": version, "schema_version": 4}
        self.assertEqual(self.call("upgrade", "--backup", str(backup))["result"], expected)
        self.assertEqual(backup.read_bytes(), backup_bytes)
        verify_migrated_read(())
        with closing(connect(self.db, "ro")) as db:
            self.assertEqual(schema_version(db), 4)
            self.assertEqual(v2_min_after(db), old_watermark)
            self.assertEqual([tuple(row) for row in db.execute("SELECT priority, archived FROM tasks")],
                             [(None, 0), (None, 0)])
            self.assertEqual(db.execute(
                "SELECT seq, change_id, operation_id, request_hash, payload FROM changes ORDER BY seq"
            ).fetchall(), old_changes)
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM changes WHERE task_v2_json IS NOT NULL OR visible_in_v1 <> 1"
            ).fetchone()[0], 0)
            worklog_upgrade.check_integrity(db)
        for command, operation, fields, original_receipt in receipts:
            self.assertEqual(self.write(command, operation, identity, **fields),
                             {**original_receipt, "protocol_version": "2.0"})
        if version == 3:
            done = self.call("context", "--task", waiting["number"])["result"]["task"]
            self.assertEqual(done["completion"], completion)
            self.assertEqual(self.call("record-get", completion["evidence"][0]["id"])["result"]["body_md"],
                             progress("完成依据")["body_md"])
        self.call("upgrade", "--backup", str(backup), error="already_upgraded")

        restored = self.root / "隔离恢复的合成项目"
        restored.mkdir()
        restored_db = project_db(restored)
        restored_db.parent.mkdir()
        copyfile(backup, restored_db)
        restored_at = ("--project", str(restored))
        self.assertEqual(schema_version_read(restored_db), version)
        self.assertEqual([self.call(*restored_at, *query, cli=old) for query in queries], original)
        if version == 3:
            self.assertEqual(self.call(*restored_at, "context", "--task", waiting["number"], cli=old),
                             old_done_context)
        for command, operation, fields, original_receipt in receipts:
            self.assertEqual(self.write(command, operation, identity, args=restored_at, cli=old, **fields),
                             original_receipt)
        self.assertEqual(self.call(*restored_at, "upgrade", "--backup", str(backup))["result"], expected)
        verify_migrated_read(restored_at)
        if version < 3:
            started = self.write("task-start", "start-after-upgrade", identity,
                                 task_id=waiting["number"], expected_revision=waiting["revision"])["result"]
            self.assertEqual(started["seq"], old_watermark + 1)
            self.assertEqual(started["task"]["status"], "IN_PROGRESS")
        if version == 2:
            log, todo, blocker = record["records"]
            evidence = [{"type": "RECORD", "target_record_id": log["record_id"]}]
            dispositions = {todo["record_id"]: "留给后续验证"}
            self.write("record", "resolve-migrated-blocker", identity, scope="TASK",
                       task_id=task["task_id"], expected_revision=active["revision"],
                       expected_context_position=record["context_position"],
                       run_id=active["active_run"]["run_id"],
                       records=[resolved_blocker(blocker["record_id"], evidence)])
            done = self.write("task-complete", "finish-migrated-task", identity, task_id=task["number"],
                              expected_revision=active["revision"], completed_scope="已复核旧版日志",
                              evidence=evidence, todo_dispositions=dispositions)["result"]["task"]
            self.assertEqual(done["status"], "DONE")
            self.assertEqual(done["completion"]["evidence"], [{"command": "record-get", "id": log["record_id"]}])
            self.assertEqual(done["completion"]["todo_dispositions"], dispositions)
            self.assertEqual(self.call("record-get", log["record_id"])["result"]["body_md"],
                             progress("含旧记录引用的进展")["body_md"])

    def test_schema_three_supports_legacy_writes_and_requires_upgrade_for_blocker_change(self):
        old = self.historical_cli(3)
        identity = self.call("init", data={"name": "schema 3 写入"}, cli=old)
        created = self.write("task-create", "create", identity, cli=old, title="待开始")["result"]
        task = created["task"]
        with self.assertRaises(WorklogError) as failure:
            with closing(connect(self.db, "rw")) as db, db:
                db.execute("BEGIN IMMEDIATE")
                append_change(db, "task-blockers", "hidden", "test-digest", timestamp(), task["task_id"])
        self.assertEqual(failure.exception.code, "upgrade_required")
        fields = {"task_id": task["number"], "expected_revision": task["revision"]}
        receipt = self.write("task-start", "start", identity, **fields)
        self.assertEqual(receipt["result"]["seq"], created["seq"] + 1)
        self.assertEqual(receipt["result"]["task"]["status"], "IN_PROGRESS")
        self.assertEqual(self.write("task-start", "start", identity, **fields), receipt)
        self.assertEqual(self.write("task-start", "start", identity, cli=old, **fields),
                         {**receipt, "protocol_version": "1.0"})
        started = receipt["result"]["task"]
        for command in ("task-pause", "task-resume"):
            self.write(command, command, identity, task_id=started["number"],
                       expected_revision=started["revision"], error="upgrade_required")
        blocker_fields = {
            "scope": "TASK", "task_id": started["task_id"], "expected_revision": started["revision"],
            "expected_context_position": 0,
            "records": [item("BLOCKER", "旧库等待材料", details=BLOCKER_DETAILS)],
        }
        blocker = self.write("record", "blocker", identity, **blocker_fields)
        self.assertEqual(self.write("record", "blocker", identity, cli=old, **blocker_fields),
                         {**blocker, "protocol_version": "1.0"})
        visible = self.call("summaries", "--type", "task", "--display-status", "BLOCKED")["result"]["items"]
        self.assertEqual([row["task_id"] for row in visible], [started["task_id"]])
        self.call("task-get", started["number"], error="upgrade_required")
        self.assertEqual(self.call("task-get", started["number"], cli=old)["result"], started)
        self.assertEqual(self.call("context", "--task", started["number"])["result"]["task"]["status"],
                         "IN_PROGRESS")
        with closing(connect(self.db, "ro")) as db:
            self.assertEqual(schema_version(db), 3)
            self.assertEqual([row["operation_id"] for row in db.execute("SELECT operation_id FROM changes ORDER BY seq")],
                             ["create", "start"])

    def test_upgrade_rejects_backup_made_stale_by_context_only_write(self):
        old = self.historical_cli(2)
        identity = self.call("init", data={"name": "schema 2 过期备份"}, cli=old)
        created = self.write("task-create", "create", identity, cli=old, title="上下文写入")["result"]
        task = created["task"]
        backup = self.root / "before-record.sqlite3"
        self.call("backup", "--output", str(backup))
        log = progress("备份后只写上下文")
        receipt = self.write("record", "new-context", identity, cli=old, scope="TASK",
                             task_id=task["task_id"], expected_revision=task["revision"],
                             expected_context_position=0, records=[log])["result"]
        with closing(connect(self.db, "ro")) as db:
            self.assertEqual(watermark(db), created["seq"])
        self.call("upgrade", "--backup", str(backup), error="invalid_backup")
        self.assertEqual(schema_version_read(self.db), 2)
        self.call("task-get", task["number"], error="upgrade_required")
        self.assertEqual(self.call("task-get", task["number"], cli=old)["result"], task)
        saved = self.call("record-get", receipt["records"][0]["record_id"])["result"]
        self.assertEqual(saved["body_md"], log["body_md"])

    def test_upgrade_rejects_source_file_and_other_project_backup(self):
        old = self.historical_cli(3)
        self.call("init", data={"name": "原项目"}, cli=old)
        self.call("upgrade", "--backup", str(self.db), error="invalid_backup")
        other = self.root / "其他项目"
        other.mkdir()
        self.call("init", data={"name": "其他项目"}, project=other, cli=old)
        backup = self.root / "other.sqlite3"
        self.call("backup", "--output", str(backup), project=other)
        self.call("upgrade", "--backup", str(backup), error="invalid_backup")
        self.assertEqual(schema_version_read(self.db), 3)
        self.call("identity", error="upgrade_required")
        self.assertEqual(self.call("identity", cli=old)["result"]["name"], "原项目")

    def test_new_database_preserves_schema_four_rows_and_replays_commit_projections(self):
        identity = self.call("init", data={"name": "新项目"})
        created = self.write("task-create", "create", identity, title="复查样本")["result"]
        task = created["task"]
        task_id = task["task_id"]
        log = progress("复查依据")
        saved = self.write("record", "blocker", identity, scope="TASK", task_id=task_id,
                           expected_revision=task["revision"], expected_context_position=0,
                           records=[log, item("TODO", "后续复核", details={"status": "OPEN"}),
                                    item("BLOCKER", "待复查", details=BLOCKER_DETAILS)])["result"]
        log_id, todo_id, blocker_id = (row["record_id"] for row in saved["records"])
        with closing(connect(self.db, "rw")) as db, db:
            db.execute("BEGIN IMMEDIATE")
            self.assertEqual(schema_version(db), 4)
            self.assertEqual(v2_min_after(db), 0)
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE tasks SET priority = 'p9' WHERE task_id = ?", (task_id,))
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE tasks SET archived = 1 WHERE task_id = ?", (task_id,))
            appended = append_change(db, "task-blockers", "test-only", "test-digest", timestamp(), task_id)
            self.assertEqual(watermark(db), appended["seq"])
            db.execute("UPDATE tasks SET priority = 'p0' WHERE task_id = ?", (task_id,))
        refs = [{"command": "record-get", "id": blocker_id}]
        projected = {**task, "display_status": "BLOCKED", "archived": False, "priority": None, "blocker_refs": refs}
        self.assertEqual(self.call("task-get", task["number"])["result"], {**projected, "priority": "p0"})
        self.assertEqual(self.call("snapshot")["result"]["watermark"], appended["seq"])
        page = ("changes", "--project-id", identity["project_id"], "--data-generation",
                identity["data_generation"], "--after")
        changes = self.call(*page, str(appended["seq"] - 1))["result"]
        self.assertEqual(changes, {
            "changes": [{**appended, "task": projected}], "has_more": False,
            "next_after": appended["seq"], "watermark": appended["seq"],
        })
        started = self.write("task-start", "start", identity, task_id=task["number"],
                             expected_revision=task["revision"])["result"]
        start_change, = self.call(*page, str(appended["seq"]))["result"]["changes"]
        self.assertEqual(start_change, {**started, "task": {
            **started["task"], "display_status": "BLOCKED", "archived": False,
            "priority": "p0", "blocker_refs": refs,
        }})
        evidence = [{"type": "RECORD", "target_record_id": log_id}]
        self.write("record", "resolve-blocker", identity, scope="TASK", task_id=task_id,
                   expected_revision=started["task"]["revision"],
                   expected_context_position=saved["context_position"],
                   records=[resolved_blocker(blocker_id, evidence)])
        claimed = self.write("claim", "claim", identity, task_id=task_id,
                             expected_revision=started["task"]["revision"],
                             client="codex", session_id="test-session")["result"]["task"]
        dispositions = {todo_id: "安排给下一阶段"}
        complete_fields = {
            "task_id": task["number"], "expected_revision": claimed["revision"],
            "completed_scope": "完成复查", "evidence": evidence, "todo_dispositions": dispositions,
        }
        completion_receipt = self.write("task-complete", "complete", identity, **complete_fields)
        done = completion_receipt["result"]
        completed = {
            "completed_at": done["committed_at"], "completed_scope": "完成复查",
            "evidence": [{"command": "record-get", "id": log_id}], "todo_dispositions": dispositions,
        }
        self.assertEqual(done["task"]["status"], "DONE")
        self.assertEqual(done["task"]["completion"], completed)
        self.assertEqual(self.call("record-get", log_id)["result"]["body_md"], log["body_md"])
        self.assertEqual(self.write("task-complete", "complete", identity, **complete_fields),
                         completion_receipt)
        with closing(connect(self.db, "ro")) as db:
            rows = db.execute(
                "SELECT task_v2_json, visible_in_v1 FROM changes"
                " WHERE operation_id IN ('create', 'test-only', 'start', 'claim', 'complete') ORDER BY seq"
            ).fetchall()
            projected = [
                {**task, "display_status": "TODO", "archived": False, "priority": None, "blocker_refs": []},
                {**task, "display_status": "BLOCKED", "archived": False, "priority": None,
                 "blocker_refs": [{"command": "record-get", "id": blocker_id}]},
                {**started["task"], "display_status": "BLOCKED", "archived": False, "priority": "p0",
                 "blocker_refs": [{"command": "record-get", "id": blocker_id}]},
                {**claimed, "display_status": "IN_PROGRESS", "archived": False, "priority": "p0", "blocker_refs": []},
                {**done["task"], "display_status": "DONE", "archived": False, "priority": "p0", "blocker_refs": []},
            ]
            self.assertEqual([json.loads(row["task_v2_json"]) for row in rows], projected)
            self.assertEqual([row["visible_in_v1"] for row in rows], [1, 0, 1, 1, 1])
            self.assertEqual(v2_min_after(db), 0)


def schema_version_read(path):
    with closing(connect(path, "ro")) as db:
        return schema_version(db)
