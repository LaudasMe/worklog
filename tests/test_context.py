import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import worklog
from worklog_store import connect, project_db, schema_version
from worklog_upgrade import install_context_schema

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "worklog.py"
LEGACY = "v0.1.0"
MARKDOWN = "# 验证\n\n```python\nassert sum([2, 3]) == 5\n```\n"


def isolated_home(path):
    return {**os.environ, "HOME": str(path), "USERPROFILE": str(path)}


def invoke(cli, directory, *args, data=None, home=None):
    result = subprocess.run(
        [sys.executable, "-B", str(cli), *args], cwd=directory,
        input=json.dumps(data, ensure_ascii=False).encode(), capture_output=True, timeout=15,
        env=isolated_home(home or Path(directory) / ".test-home"),
    )
    if result.stderr:
        raise AssertionError(result.stderr.decode())
    return result.returncode, json.loads(result.stdout)


def source_at(revision, destination, extra_files=()):
    destination.mkdir()
    for name in ("worklog.py", "worklog_store.py", "schema.sql", *extra_files):
        path = ROOT / name
        (destination / name).write_bytes(subprocess.check_output(
            ["git", "show", f"{revision}:{path.name}"], cwd=ROOT,
        ))
    return destination / "worklog.py"


class ContextCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="worklog-context-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.home = self.root / "home"
        self.project = self.root / "合成项目 #1"
        self.project.mkdir()
        self.db = project_db(self.project)

    def call(self, *args, data=None, project=None, cli=CLI, error=None):
        code, reply = invoke(cli, project or self.project, *args, data=data, home=self.home)
        if error is None:
            self.assertEqual(code, 0, reply)
        else:
            self.assertNotEqual(code, 0, reply)
            self.assertEqual(reply["error"]["code"], error, reply)
        return reply

    def identity_fields(self, identity):
        return {key: identity[key] for key in ("project_id", "data_generation")}

    def write(self, command, operation, identity, *, args=(), project=None, cli=CLI, error=None, **fields):
        return self.call(
            *args, command, data={**self.identity_fields(identity), "operation_id": operation, **fields},
            project=project, cli=cli, error=error,
        )


class UpgradeTest(ContextCase):
    def test_legacy_read_backup_upgrade_rollback_and_retry(self):
        old = source_at(LEGACY, self.root / "v0.1.0")
        at = ("--project", str(self.project))
        identity = self.call(*at, "init", data={"name": "v0.1.0 合成库"}, cli=old)
        task = self.write("task-create", "create", identity, args=at, cli=old,
                          title="验证旧库")["result"]["task"]
        owner = self.write("claim", "claim", identity, args=at, cli=old, task_id=task["task_id"],
                           expected_revision=task["revision"], client="codex",
                           session_id="legacy-session")["result"]["task"]
        payload = {"task_id": task["task_id"], "run_id": owner["active_run"]["run_id"],
                   "summary": "旧阶段", "body_md": MARKDOWN, "next_step": "复查中文和代码块。"}
        progress = self.write("checkpoint", "legacy-progress", identity, args=at, cli=old,
                              expected_revision=owner["revision"], **payload)["result"]
        handoff = self.write("handoff", "legacy-handoff", identity, args=at, cli=old,
                             expected_revision=progress["task"]["revision"], **payload)["result"]

        original_db = self.db.read_bytes()
        old_queries = [
            ("identity",), ("task-get", "T001"), ("snapshot",),
            ("entry-get", progress["entry"]["entry_id"]), ("entry-get", handoff["entry"]["entry_id"]),
            ("changes", "--project-id", identity["project_id"], "--data-generation",
             identity["data_generation"], "--after", "0"),
        ]
        before = [self.call(*at, *query, cli=old) for query in old_queries]
        for query in old_queries:
            self.call(*query, error="upgrade_required")
        self.assertEqual(self.db.read_bytes(), original_db)

        backup = self.root / "backup.sqlite3"
        self.call("upgrade", "--backup", str(backup), error="invalid_backup")
        self.call("backup", "--output", str(backup))
        self.call("backup", "--output", str(backup), error="backup_exists")
        original_backup = backup.read_bytes()

        def fail_after_ddl(db):
            install_context_schema(db)
            raise RuntimeError("injected failure after DDL and user_version")

        parsed = argparse.Namespace(project=str(self.project), command="upgrade", backup=str(backup))
        with patch("worklog_upgrade.install_context_schema", side_effect=fail_after_ddl):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                worklog.execute(parsed)
        self.assertEqual(self.db.read_bytes(), original_db)
        with closing(connect(self.db, "ro")) as db:
            self.assertEqual(schema_version(db), 1)
            self.assertIsNone(db.execute(
                "SELECT 1 FROM sqlite_master WHERE name = 'context_records'"
            ).fetchone())
        self.assertEqual([self.call(*at, *query, cli=old) for query in old_queries], before)
        for query in old_queries:
            self.call(*query, error="upgrade_required")
        self.assertEqual(self.call("upgrade", "--backup", str(backup))["result"],
                         {"previous_schema_version": 1, "schema_version": 4})
        self.assertEqual(backup.read_bytes(), original_backup)
        identity_reply, task_reply, snapshot_reply, *entry_replies = before[:-1]
        self.assertEqual(self.call("identity"), {**identity_reply, "protocol_version": "2.0"})
        projected = {**task_reply["result"], "display_status": "IN_PROGRESS", "archived": False,
                     "priority": None, "blocker_refs": []}
        self.assertEqual(self.call("task-get", "T001")["result"], projected)
        baseline = snapshot_reply["result"]
        self.assertEqual(self.call("snapshot")["result"], {**baseline, "tasks": [projected]})
        for query, original in zip(old_queries[3:5], entry_replies):
            self.assertEqual(self.call(*query), {**original, "protocol_version": "2.0"})
        page = old_queries[-1]
        self.call(*page, error="cursor_stale")
        self.assertEqual(self.call(*page[:-1], str(baseline["watermark"]))["result"]["changes"], [])
        self.assertEqual(self.call("entry-get", handoff["entry"]["entry_id"])["result"]["body_md"],
                         MARKDOWN)
        self.assertEqual(self.write("handoff", "legacy-handoff", identity,
                                    expected_revision=progress["task"]["revision"], **payload)["result"],
                         handoff)
        self.call("upgrade", "--backup", str(backup), error="already_upgraded")

    def test_stale_backup_is_rejected(self):
        old = source_at(LEGACY, self.root / "v0.1.0")
        at = ("--project", str(self.project))
        identity = self.call(*at, "init", data={"name": "旧库"}, cli=old)
        backup = self.root / "old.sqlite3"
        self.call("backup", "--output", str(backup))
        self.write("task-create", "next", identity, args=at, cli=old, title="备份后写入")
        self.call("upgrade", "--backup", str(backup), error="invalid_backup")
        self.call("task-get", "T001", error="upgrade_required")
        self.assertEqual(self.call(*at, "task-get", "T001", cli=old)["result"]["title"], "备份后写入")

    def test_read_uninitialized_directory_leaves_no_files(self):
        self.call("identity", error="not_initialized")
        self.call("task-get", "T001", error="not_initialized")
        self.assertEqual(list(self.project.iterdir()), [])


def item(kind, summary, *, details=None, refs=None, **fields):
    return {"kind": kind, "summary": summary, "body_md": f"## {summary}\n\n合成原文。\n",
            "details": details or {}, "refs": refs or [], **fields}


def progress(summary, *paths, **fields):
    return item("PROGRESS", summary, details={
        "processed_scope": [summary],
        "impacts": [{"target_type": "FILE", "target": path, "effect": "验证"} for path in paths],
    }, **fields)


class SeededContextCase(ContextCase):
    def setUp(self):
        super().setUp()
        self.identity = self.call("init", data={"name": "北辰合成项目"})
        self.task = self.write("task-create", "make", self.identity, title="复测")["result"]["task"]

    def store(self, operation, items, *, task=None, run_id=None, error=None, project=None):
        args = {"scope": "PROJECT", "records": items}
        if task:
            args.update(scope="TASK", task_id=task["task_id"], expected_revision=task["revision"],
                        expected_context_position=task.get("context_position", 0))
        if run_id:
            args["run_id"] = run_id
        return self.write("record", operation, self.identity, error=error, project=project, **args)

    def task_context(self):
        return self.call("context", "--task", "T001")["result"]


class RecordsTest(SeededContextCase):
    def test_current_context_sources_closed_items_and_corrected_impacts(self):
        own = self.store("seed", [
            item("GOAL", "复测两份样本", details={"completion_criteria": ["两次结果一致"]}),
            progress("第一轮", "docs/upgrade-check.md"),
            item("QUESTION", "是否单列交接测试", details={"status": "OPEN"}),
            item("TODO", "复测第二份", details={"status": "OPEN"}),
            item("BLOCKER", "缺第二份样本", details={
                "status": "OPEN", "blocked_work": "第二次验证", "unblock_condition": "收到样本",
            }),
        ], task=self.task)["result"]["records"]
        proof = {"type": "RECORD", "target_record_id": own[1]["record_id"]}
        project = self.store("project", [
            item("GOAL", "项目目标"),
            item("CONSTRAINT", "限本地", refs=[{"type": "FILE", "path": "docs/scope.md"}]),
            item("DECISION", "采用 SQLite", refs=[proof], related_task_id="T001"),
            item("DOCUMENT", "范围", details={"path": "docs/scope.md", "purpose": "确定范围"}),
            progress("项目阶段", related_task_id="T001"),
        ])["result"]["records"]
        start = self.task_context()
        self.assertEqual(start["unrecorded"], ["decisions"])
        self.assertEqual(start["goal"]["details"]["completion_criteria"], ["两次结果一致"])
        self.assertEqual(start["goal"]["source"]["id"], own[0]["record_id"])
        self.assertEqual(start["blockers"][0]["task_id"], self.task["task_id"])
        self.assertEqual(start["blockers"][0]["scope"], "TASK")
        self.assertEqual({row["kind"] for row in start["project_context"]},
                         {"CONSTRAINT", "DECISION", "DOCUMENT", "PROGRESS"})
        pctx = self.call("context")["result"]
        self.assertEqual(pctx["documents"][0]["details"]["availability"], "NEEDS_CHECK")
        (self.project / "docs").mkdir()
        (self.project / "docs/scope.md").write_text("## 本地合成项目\n", encoding="utf-8")
        self.assertEqual(self.call("context")["result"]["documents"][0]["details"]["availability"],
                         "AVAILABLE")
        self.assertEqual(pctx["task_blockers"][0]["record_id"], own[4]["record_id"])
        self.assertEqual(pctx["decisions"][0]["related_task_id"], self.task["task_id"])
        self.assertIsNone(pctx["decisions"][0]["task_id"])
        self.assertEqual(pctx["decisions"][0]["refs"][0]["source"]["id"], own[1]["record_id"])
        self.assertEqual(pctx["decisions"][0]["record_id"], project[2]["record_id"])

        done_proof = [{"type": "RECORD", "batch_index": 0}]
        batch = [
            progress("第二轮", "tests/test_upgrade.py"),
            item("TODO", "复测已完成", details={"status": "RESOLVED", "outcome_md": "两次结果一致"},
                 replaces_id=own[3]["record_id"], refs=done_proof),
            item("BLOCKER", "已取得第二份样本", details={
                "status": "RESOLVED", "blocked_work": "第二次验证",
                "unblock_condition": "收到样本", "outcome_md": "第二份样本已验证",
            }, replaces_id=own[4]["record_id"], refs=done_proof),
        ]
        previously = self.db.read_bytes()
        self.store("batch", [*batch, item("TODO", "故障", details={"status": "WRONG"})],
                   task=start["task"], error="invalid_input")
        self.assertEqual(self.db.read_bytes(), previously)
        self.assertEqual(self.task_context()["blockers"], start["blockers"])
        receipt = self.store("batch", batch, task=start["task"])
        self.assertEqual(self.store("batch", batch, task=start["task"]), receipt)
        self.store("batch", [progress("变更参数")], task=start["task"], error="operation_conflict")
        self.store("other-batch", batch, task=start["task"], error="revision_conflict")
        end = self.task_context()
        self.assertEqual(end["todos"], [])
        self.assertEqual(end["blockers"], [])
        self.assertNotIn("blockers", end["unrecorded"])
        self.assertEqual(end["open_questions"][0]["record_id"], own[2]["record_id"])
        self.assertEqual(self.call("context")["result"]["task_blockers"], [])
        todo = self.call("record-get", receipt["result"]["records"][1]["record_id"])["result"]
        self.assertEqual(todo["refs"][0]["target_record_id"], receipt["result"]["records"][0]["record_id"])
        self.assertEqual(todo["details"]["outcome_md"], "两次结果一致")

        self.store("no-impact", [progress("第三轮")], task=end["task"])
        no_impact = self.task_context()
        self.assertEqual(no_impact["impacts"]["latest"]["value"], [])
        self.assertEqual({row["target"] for row in no_impact["impacts"]["history"]},
                         {"docs/upgrade-check.md", "tests/test_upgrade.py"})
        self.store("correction", [progress("更正第一轮", "docs/upgrade-results.md",
                                           replaces_id=own[1]["record_id"])], task=no_impact["task"])
        corrected = self.task_context()
        self.assertEqual({row["target"] for row in corrected["impacts"]["history"]},
                         {"docs/upgrade-results.md", "tests/test_upgrade.py"})
        old = self.call("record-get", own[1]["record_id"])["result"]
        self.assertEqual(old["details"]["impacts"][0]["target"], "docs/upgrade-check.md")
        self.assertIn("第一轮", old["body_md"])
        self.assertEqual(old["status"], "SUPERSEDED")
        self.store("stale-correction", [progress("过期更正", replaces_id=old["record_id"])],
                   task=corrected["task"], error="record_conflict")
        self.store("bad-goal", [item("GOAL", "重复目标")], task=corrected["task"],
                   error="record_conflict")

    def test_active_run_guard_and_retry_after_takeover(self):
        owner = self.write("claim", "claim", self.identity, task_id=self.task["task_id"],
                           expected_revision=self.task["revision"], client="codex",
                           session_id="writer")["result"]["task"]
        run = owner["active_run"]["run_id"]
        items = [progress("接手期间写回")]
        self.store("save", items, task=owner, error="stale_run")
        first = self.store("save", items, task=owner, run_id=run)
        self.write("claim", "replace", self.identity, task_id=self.task["task_id"],
                   expected_revision=owner["revision"], replaces_run_id=run,
                   client="opencode", session_id="next-writer")
        self.assertEqual(self.store("save", items, task=owner, run_id=run), first)
        self.store("old", items, task=owner, run_id=run, error="stale_run")
        saved = self.call("record-get", first["result"]["records"][0]["record_id"])["result"]
        self.assertEqual((saved["client"], saved["session_id"], saved["run_id"]),
                         ("codex", "writer", run))

    def test_record_scope_identity_and_absent_context(self):
        second = self.root / "南星"
        second.mkdir()
        other = self.call("init", project=second, data={"name": "南星合成项目"})
        task_b = self.write("task-create", "make", other, project=second,
                            title="另一项目复测")["result"]["task"]
        bytes_b = project_db(second).read_bytes()
        self.store("wrong", [progress("项目错误")], task=self.task, project=second,
                   error="project_mismatch")
        self.store("update", [progress("只写北辰")], task=self.task)
        self.assertEqual(project_db(second).read_bytes(), bytes_b)
        self.assertEqual(self.task_context()["progress"][0]["summary"], "只写北辰")
        self.assertEqual(self.call("context", "--task", "T001", project=second)["result"]["task"]["task_id"],
                         task_b["task_id"])
        self.assertNotEqual(self.task["task_id"], task_b["task_id"])
        self.assertEqual(self.call("context", project=second)["result"]["progress"], [])
        outside = self.root / "未初始化"
        outside.mkdir()
        self.call("context", project=outside, error="not_initialized")
        self.call("record-get", "absent", project=outside, error="not_initialized")
        self.assertEqual(list(outside.iterdir()), [])


class SummariesTest(SeededContextCase):
    def test_filters_sorting_ties_and_read_only_paging(self):
        owner = self.write("claim", "claim-summary", self.identity, task_id=self.task["task_id"],
                           expected_revision=self.task["revision"], client="codex",
                           session_id="origin")["result"]["task"]
        run = owner["active_run"]["run_id"]
        legacy = self.write("handoff", "handoff-summary", self.identity, task_id=self.task["task_id"],
                            expected_revision=owner["revision"], run_id=run, summary="旧交接摘要",
                            body_md=MARKDOWN, next_step="等待验证")["result"]
        self.write("task-create", "empty", self.identity, title="没有摘要")
        tasks = self.call("summaries", "--type", "task")["result"]["items"]
        baseline = self.call("snapshot")["result"]
        self.assertEqual([row["number"] for row in tasks], ["T002", "T001"])
        self.assertIsNone(tasks[0]["summary"])
        self.assertIsNone(tasks[0]["source"])
        self.assertEqual(tasks[1]["source"], {"command": "entry-get", "id": legacy["entry"]["entry_id"]})
        self.assertEqual(tasks[1]["summary"], "旧交接摘要")
        self.assertEqual(self.call("summaries", "--type", "task", "--status", "TODO")
                         ["result"]["items"], tasks[:1])
        blocker = {"status": "OPEN", "blocked_work": "复测", "unblock_condition": "收到样本"}
        first = self.store("summaries-seed", [
            progress("有效进展"), item("BLOCKER", "样本缺失", details=blocker),
            item("QUESTION", "尚待确认", details={"status": "OPEN"}),
        ], task=legacy["task"])["result"]["records"]
        self.store("associated", [progress("项目进展", related_task_id="T001")])
        only_task = self.call("summaries", "--type", "task", "--blocked")["result"]["items"]
        self.assertEqual([(row["number"], row["status"], row["summary"]) for row in only_task],
                         [("T001", "IN_PROGRESS", "有效进展")])
        open_items = self.call("summaries", "--type", "record", "--blocked")["result"]["items"]
        self.assertEqual([row["record_id"] for row in open_items], [first[1]["record_id"]])
        before = self.db.read_bytes()
        opts = ("summaries", "--type", "record", "--task", "T001")
        expected = self.call(*opts)["result"]["items"]
        self.assertEqual(expected[0]["scope"], "PROJECT")
        self.assertIsNone(expected[0]["task_id"])
        self.assertEqual(expected[0]["related_task_id"], self.task["task_id"])
        self.assertEqual([row["record_id"] for row in expected[1:4]],
                         sorted(row["record_id"] for row in first))
        rows, cursor = [], None
        while True:
            page = self.call(*opts, "--limit", "2", *(("--cursor", cursor) if cursor else ()))["result"]
            rows.extend(page["items"])
            cursor = page["next_cursor"]
            if cursor is None:
                break
        self.assertEqual(rows, expected)
        self.assertFalse(any("body_md" in row or "details" in row for row in rows))
        token = self.call(*opts, "--limit", "1")["result"]["next_cursor"]
        self.call(*opts, "--status", "OPEN", "--cursor", token, error="invalid_input")
        self.call(*opts, "--cursor", "bad-token", error="invalid_input")
        self.call(*opts, "--status", "NOPE", error="invalid_input")
        self.call("summaries", "--type", "task", "--task", "T999", error="task_not_found")
        self.assertEqual(self.db.read_bytes(), before)

        self.store("resolve", [
            item("BLOCKER", "已收到样本", details={**blocker, "status": "RESOLVED", "outcome_md": "已取得"},
                 refs=[{"type": "RECORD", "target_record_id": first[0]["record_id"]}],
                 replaces_id=first[1]["record_id"]),
        ], task=self.task_context()["task"])
        self.call(*opts, "--cursor", token, error="cursor_stale")
        self.assertEqual(self.call(*opts, "--blocked")["result"]["items"], [])
        self.assertEqual(self.call("summaries", "--type", "task", "--blocked")["result"]["items"], [])
        self.assertEqual([row["kind"] for row in self.call(*opts, "--status", "OPEN")["result"]["items"]],
                         ["QUESTION"])
        self.assertEqual([row["summary"] for row in self.call(*opts, "--status", "RESOLVED")["result"]["items"]],
                         ["已收到样本"])
        self.assertEqual([row["record_id"] for row in self.call(*opts, "--status", "SUPERSEDED")["result"]["items"]],
                         [first[1]["record_id"]])
        self.assertNotIn("样本缺失", [row["summary"] for row in self.call(*opts)["result"]["items"]])
        self.assertEqual(self.call(*opts, "--status", "CANCELLED")["result"]["items"], [])
        self.assertEqual(self.call("snapshot")["result"], {**baseline, "watermark": baseline["watermark"] + 2})

    def test_summaries_before_upgrade_and_uninitialized(self):
        outside = self.root / "outside"
        outside.mkdir()
        self.call("summaries", "--type", "task", project=outside, error="not_initialized")
        self.assertEqual(list(outside.iterdir()), [])
        old = source_at(LEGACY, self.root / "v0.1.0")
        args = ("--project", str(outside))
        legacy = self.call(*args, "init", cli=old, data={"name": "旧库"}, project=outside)
        self.write("task-create", "legacy-make", legacy, args=args, cli=old, title="旧任务")
        self.assertEqual(self.call("summaries", "--type", "task", project=outside)["result"]["items"][0]["title"],
                         "旧任务")
        self.write("record", "will-upgrade", legacy, project=outside, error="upgrade_required",
                   scope="PROJECT", records=[item("GOAL", "按需升级")])
