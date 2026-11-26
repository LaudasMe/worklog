"""验证 Codex 任务操作的项目边界、确认依据和回执。"""

from contextlib import closing
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

GUI = Path(__file__).resolve().parent
REPO = GUI.parent.parent
sys.path[:0] = [str(REPO), str(GUI.parent / "shared")]
from task_actions import completion_options, mutate_task
from worklog_context import context, record
from worklog_store import (
    WorklogError, connect, initialize, metadata, open_run, project_db, task_position, task_row, write_request,
)


def operation_id():
    return str(uuid4())


def work_record(kind, summary, details, **fields):
    return {"kind": kind, "summary": summary, "body_md": summary, "details": details, **fields}


def progress(summary, **fields):
    return work_record("PROGRESS", summary, {"processed_scope": [summary], "impacts": []}, **fields)


def blocker_record(summary, **fields):
    details = {"status": "OPEN", "blocked_work": "核对样本", "unblock_condition": "提供数据"}
    details.update(fields.pop("details", {}))
    return work_record("BLOCKER", summary, details, **fields)


class TaskActionsTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        container = Path(directory.name).resolve()
        self.first, self.second = container / "甲 项目", container / "乙 项目"
        self.projects = {}
        for root in (self.first, self.second):
            root.mkdir()
            project, _ = initialize(project_db(root), {"name": root.name})
            self.projects[root] = project["project_id"], project["data_generation"]

    def request(self, root, **fields):
        project_id, generation = self.projects[root]
        return {"operation_id": operation_id(), "project_id": project_id, "data_generation": generation, **fields}

    def mutate(self, root, command, **fields):
        return mutate_task(root, self.projects[root], command, self.request(root, **fields))

    def add(self, root, title):
        result = self.mutate(root, "task-create", title=title)
        self.assertEqual(result["state"], "ready")
        return result["task"]

    def cli(self, root, command, task_id, **fields):
        """使用 CLI 的事务入口与业务处理器保存样本记录。"""
        with closing(connect(project_db(root), "rw")) as db, db:
            db.execute("BEGIN IMMEDIATE")
            project = metadata(db)
            task = task_row(db, task_id)
            return write_request(db, project, command, self.request(
                root, task_id=task_id, expected_revision=task["revision"], **fields,
            ))

    def add_records(self, root, task_id, *records):
        with closing(connect(project_db(root), "rw")) as db, db:
            db.execute("BEGIN IMMEDIATE")
            project = metadata(db)
            if task_id is None:
                request = self.request(root, scope="PROJECT", records=list(records))
            else:
                task = task_row(db, task_id)
                active = open_run(db, task_id)
                request = self.request(
                    root, scope="TASK", task_id=task_id, expected_revision=task["revision"],
                    expected_context_position=task_position(db, task_id), records=list(records),
                )
                if active:
                    request["run_id"] = active["run_id"]
            return [row["record_id"] for row in record(db, project, request)["records"]]

    def read_context(self, root, task_id):
        with closing(connect(project_db(root), "ro")) as db:
            return context(db, metadata(db), task_id, root)

    def state(self, root, task_id):
        return self.read_context(root, task_id)["task"]

    def options(self, root, task_id, expected_revision=None):
        with closing(connect(project_db(root), "ro")) as db:
            task = task_row(db, task_id)
            return completion_options(db, task, expected_revision if expected_revision is not None else task["revision"])

    def test_create_isolated_same_number_and_retry_checks_identity(self):
        request = self.request(self.first, title="  甲任务　")
        first = mutate_task(self.first, self.projects[self.first], "task-create", request)
        task = first["task"]
        self.assertEqual(mutate_task(self.first, self.projects[self.first], "task-create", request), first)
        self.assertEqual(self.state(self.first, task["id"])["title"], "甲任务")
        other_task = self.add(self.second, "乙任务")
        self.assertEqual((task["number"], other_task["number"]), ("T001", "T001"))
        self.assertNotEqual(task["id"], other_task["id"])

        cases = [
            (self.first, self.projects[self.first], "task-create", {**request, "title": "另一标题"}, "operation_conflict"),
            (self.first, self.projects[self.first], "task-create", {**request, "title": " \t"}, "invalid_input"),
            (self.second, self.projects[self.second], "task-create", request, "project_mismatch"),
            (self.second, self.projects[self.first], "task-create", request, "project_mismatch"),
            (self.first, self.projects[self.first], "task-create", {**request, "data_generation": operation_id()}, "generation_mismatch"),
            (self.first, self.projects[self.first], "task-start", self.request(self.first, task_id="T001", expected_revision=1), "task_not_found"),
            (self.first, self.projects[self.first], "task-start", self.request(self.first, task_id=other_task["id"], expected_revision=1), "task_not_found"),
        ]
        for root, key, command, payload, code in cases:
            with self.subTest(code=code, command=command):
                self.assertEqual(mutate_task(root, key, command, payload)["code"], code)
        self.assertEqual(self.add(self.first, "甲的第二项")["number"], "T002")
        self.assertEqual(self.state(self.second, other_task["id"])["status"], "TODO")

    def test_lifecycle_retry_stale_revision_cancel_and_reopen(self):
        task = self.add(self.first, "检查操作")
        task_id = task["id"]
        fields = {"task_id": task_id, "expected_revision": task["revision"]}
        first_request = self.request(self.first, **fields)
        start = mutate_task(self.first, self.projects[self.first], "task-start", first_request)
        self.assertEqual(start["task"]["status"], "IN_PROGRESS")
        self.assertIsNone(self.state(self.first, task_id)["active_run"])
        self.assertEqual(mutate_task(self.first, self.projects[self.first], "task-start", first_request), start)
        self.assertEqual(self.mutate(self.first, "task-pause", **fields)["code"], "revision_conflict")

        claim = self.cli(self.first, "claim", task_id, client="codex", session_id="synthetic")
        run = claim["task"]["active_run"]
        fields["expected_revision"] = claim["task"]["revision"]
        paused = self.mutate(self.first, "task-pause", **fields)
        self.assertEqual(paused["task"]["status"], "PAUSED")
        fields["expected_revision"] = paused["task"]["revision"]
        self.assertEqual(self.state(self.first, task_id)["active_run"], run)
        resumed = self.mutate(self.first, "task-resume", **fields)
        self.assertEqual(resumed["task"]["status"], "IN_PROGRESS")
        self.assertEqual(self.state(self.first, task_id)["active_run"], run)
        fields["expected_revision"] = resumed["task"]["revision"]
        todo_id, blocker_id = self.add_records(
            self.first, task_id, work_record("TODO", "稍后复查", {"status": "OPEN"}),
            blocker_record("等待样本"),
        )
        self.assertEqual(self.mutate(self.first, "task-cancel", reason=" ", **fields)["code"], "invalid_input")
        cancelled = self.mutate(self.first, "task-cancel", reason="样本停用", **fields)
        self.assertEqual(cancelled["task"]["status"], "CANCELLED")
        self.assertIsNone(self.state(self.first, task_id)["active_run"])
        fields["expected_revision"] = cancelled["task"]["revision"]
        reopened = self.mutate(self.first, "task-reopen", reason="确认恢复样本", **fields)
        self.assertEqual(reopened["task"]["status"], "TODO")
        state = self.read_context(self.first, task_id)
        history = state["lifecycle_history"]
        self.assertEqual(history[-2]["lifecycle"]["reason"], "样本停用")
        self.assertEqual(history[-1]["lifecycle"]["evidence"], [{"type": "USER_CONFIRMATION", "note": "确认恢复样本"}])
        self.assertEqual({row["record_id"] for row in state["reopen_candidates"]}, {todo_id, blocker_id})
        self.assertEqual(self.options(self.first, task_id)["todos"], [])
        self.assertEqual(self.options(self.first, task_id)["blockers"], [])

    def test_completion_options_proof_todos_blockers_and_reopened_task(self):
        task = self.add(self.first, "默认完成范围")
        task_id = task["id"]
        other_task = self.add(self.second, "乙任务")
        other_progress, = self.add_records(self.second, other_task["id"], progress("另一项目的进展"))
        self.assertEqual(self.options(self.first, task_id)["evidence"], [])

        first_claim = self.cli(self.first, "claim", task_id, client="codex", session_id="first")
        handoff = self.cli(
            self.first, "handoff", task_id, run_id=first_claim["task"]["active_run"]["run_id"],
            summary="合成交接", body_md="合成交接原文", next_step="复验",
        )["entry"]
        second_claim = self.cli(self.first, "claim", task_id, client="codex", session_id="second")
        revision = second_claim["task"]["revision"]
        old, todo_one, todo_two, blocker = self.add_records(
            self.first, task_id, progress("待更正进展"),
            work_record("TODO", "第一条待办", {"status": "OPEN"}),
            work_record("TODO", "第二条待办", {"status": "OPEN"}),
            blocker_record("待确认"),
        )
        corrected, = self.add_records(self.first, task_id, progress("更正进展", replaces_id=old))
        associated, unrelated = self.add_records(
            self.first, None, progress("关联的项目进展", related_task_id=task_id), progress("未关联的项目进展"),
        )
        options = self.options(self.first, task_id, expected_revision=revision)
        self.assertEqual(options["completedScope"], "默认完成范围")
        self.assertEqual([row["ref"] for row in options["evidence"]], [
            {"type": "RECORD", "target_record_id": associated},
            {"type": "RECORD", "target_record_id": corrected},
            {"type": "ENTRY", "target_entry_id": handoff["entry_id"]},
        ])
        self.assertEqual(options["evidence"][-1]["label"], "交接")
        self.assertEqual([row["id"] for row in options["todos"]], [todo_one, todo_two])
        self.assertEqual(options["blockers"], [{"id": blocker, "summary": "待确认"}])
        with self.assertRaises(WorklogError) as conflict:
            self.options(self.first, task_id, expected_revision=revision - 1)
        self.assertEqual(conflict.exception.code, "revision_conflict")

        fields = {
            "task_id": task_id, "expected_revision": revision, "completed_scope": "复核后的完成范围",
            "evidence": [options["evidence"][1]["ref"]],
            "todo_dispositions": {todo_one: "单独跟进", todo_two: "已确认转交"},
        }
        self.assertEqual(self.mutate(self.first, "task-complete", **fields)["code"], "task_blocked")
        for invalid in (old, other_progress, unrelated):
            supplied = {**fields, "evidence": [{"type": "RECORD", "target_record_id": invalid}]}
            self.assertEqual(self.mutate(self.first, "task-complete", **supplied)["code"], "invalid_evidence")
        self.add_records(self.first, task_id, blocker_record(
            "已确认", details={"status": "RESOLVED", "outcome_md": "合成样本已齐"},
            replaces_id=blocker, refs=[{"type": "USER_CONFIRMATION", "note": "合成确认"}],
        ))
        for invalid, code in (
            ({"completed_scope": " "}, "invalid_input"),
            ({"todo_dispositions": {todo_one: "单独跟进", todo_two: " "}}, "invalid_input"),
            ({"todo_dispositions": {todo_one: "单独跟进"}}, "todo_disposition_required"),
            ({"evidence": []}, "invalid_input"),
        ):
            with self.subTest(invalid=invalid):
                self.assertEqual(self.mutate(self.first, "task-complete", **{**fields, **invalid})["code"], code)
        self.assertEqual(self.state(self.first, task_id)["status"], "IN_PROGRESS")
        request = self.request(self.first, **fields)
        done = mutate_task(self.first, self.projects[self.first], "task-complete", request)
        self.assertEqual(done["task"]["status"], "DONE")
        self.assertEqual(mutate_task(self.first, self.projects[self.first], "task-complete", request), done)
        state = self.state(self.first, task_id)
        self.assertEqual(state["completion"]["completed_scope"], fields["completed_scope"])
        self.assertEqual(state["completion"]["evidence"], [{"command": "record-get", "id": corrected}])
        self.assertEqual(state["completion"]["todo_dispositions"], fields["todo_dispositions"])
        self.assertIsNone(state["active_run"])
        reopened = self.mutate(
            self.first, "task-reopen", task_id=task_id, expected_revision=done["task"]["revision"], reason="需要复核",
        )
        self.assertEqual(reopened["task"]["status"], "TODO")
        self.assertEqual([row["id"] for row in self.options(self.first, task_id)["todos"]], [todo_one, todo_two])
        self.assertEqual(self.state(self.second, other_task["id"])["status"], "TODO")

    def test_no_todos_writable_scope_and_retryable_busy_response(self):
        task = self.add(self.first, "无需处置待办")
        record_id, = self.add_records(self.first, task["id"], progress("有保存依据"))
        result = self.mutate(
            self.first, "task-complete", task_id=task["id"], expected_revision=task["revision"],
            completed_scope="完成范围", evidence=[{"type": "RECORD", "target_record_id": record_id}],
        )
        self.assertEqual(result["task"]["status"], "DONE")
        self.assertEqual(self.state(self.first, task["id"])["completion"]["todo_dispositions"], {})

        symlink = self.first.parent / "其他目录"
        symlink.symlink_to(self.first, target_is_directory=True)
        request = self.request(self.first, title="不能写入")
        rejected = mutate_task(symlink, self.projects[self.first], "task-create", request)
        self.assertEqual(rejected["code"], "invalid_project_path")

        busy = sqlite3.OperationalError("database is locked")
        with patch("task_actions.connect", side_effect=busy):
            denied = mutate_task(self.first, self.projects[self.first], "task-create", request)
        self.assertEqual((denied["code"], denied["retryable"]), ("database_busy", True))
        self.assertEqual(mutate_task(self.first, self.projects[self.first], "task-create", request)["task"]["number"], "T002")


if __name__ == "__main__":
    unittest.main()
