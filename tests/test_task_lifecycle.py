import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from threading import Event
from unittest.mock import patch

import worklog
from test_context import CLI, MARKDOWN, SeededContextCase, invoke, item, progress
from worklog_context import record
from worklog_store import WorklogError, connect


def record_ref(record_id):
    return {"type": "RECORD", "target_record_id": record_id}


class TaskLifecycleTest(SeededContextCase):
    def start(self, operation, *, error=None):
        return self.write("task-start", operation, self.identity, task_id="T001",
                          expected_revision=self.task["revision"], error=error)

    def complete(self, operation, task, evidence, *, error=None, **fields):
        return self.write("task-complete", operation, self.identity, task_id=task["task_id"],
                          expected_revision=task["revision"], completed_scope="验证样本",
                          evidence=evidence, error=error, **fields)

    def test_start_and_complete_from_task_and_project_logs(self):
        with closing(connect(self.db, "rw")) as db:
            db.execute("CREATE TRIGGER fail_start BEFORE INSERT ON changes "
                       "BEGIN SELECT RAISE(ABORT, 'injected receipt failure'); END")
        self.start("start", error="storage_error")
        self.assertEqual(self.task_context()["task"]["status"], "TODO")
        self.assertEqual(self.task_context()["task"]["revision"], self.task["revision"])
        with closing(connect(self.db, "rw")) as db:
            db.execute("DROP TRIGGER fail_start")
        started = self.start("start")
        task = started["result"]["task"]
        self.assertEqual(task["status"], "IN_PROGRESS")
        self.assertIsNone(task["active_run"])
        self.assertEqual(task["revision"], self.task["revision"] + 1)
        self.assertEqual(self.start("start"), started)
        self.start("stale-start", error="revision_conflict")
        self.write("task-start", "repeated-start", self.identity, task_id="T001",
                   expected_revision=task["revision"], error="task_state_conflict")
        self.assertEqual(self.task_context()["task"]["revision"], task["revision"])
        # A reader can replay the start or begin at the following snapshot.
        fresh_snapshot = self.call("snapshot")["result"]
        self.assertEqual(fresh_snapshot["tasks"][0]["status"], "IN_PROGRESS")
        changes = ("changes", "--project-id", self.identity["project_id"], "--data-generation",
                   self.identity["data_generation"])
        prefix = self.call(*changes, "--after", "0", "--limit", "1")["result"]
        self.assertEqual([change["kind"] for change in prefix["changes"]], ["task-create"])
        self.assertTrue(prefix["has_more"])
        first_change, = self.call(*changes, "--after", str(prefix["next_after"]))["result"]["changes"]
        self.assertEqual(
            (first_change["kind"], first_change["seq"], first_change["task"]["status"]),
            ("task-start", started["result"]["seq"], "IN_PROGRESS"),
        )
        other = self.write("task-create", "create-later", self.identity, title="另一项任务")
        next_page = self.call(*changes, "--after", str(fresh_snapshot["watermark"]))["result"]
        projected = {**other["result"]["task"], "display_status": "TODO", "archived": False,
                     "priority": None, "blocker_refs": []}
        self.assertEqual(next_page["changes"], [{**other["result"], "task": projected}])

        task_log = progress("任务进展")
        task_proof = self.store("task-log", [task_log], task=task)["result"]["records"][0]
        project_log = progress("项目进展", related_task_id="T001")
        project_proof = self.store("project-log", [project_log])["result"]["records"][0]
        evidence = [record_ref(row["record_id"]) for row in (task_proof, project_proof)]
        before = self.call("summaries", "--type", "record", "--task", "T001")["result"]["items"]

        completed = self.complete("complete", task, evidence)
        done = completed["result"]["task"]
        self.assertEqual(done["status"], "DONE")
        self.assertEqual(done["revision"], task["revision"] + 1)
        self.assertTrue(done["completion"]["completed_at"])
        self.assertEqual(done["completion"]["completed_scope"], "验证样本")
        self.assertEqual(done["completion"]["todo_dispositions"], {})
        self.assertEqual(done["completion"]["evidence"], [
            {"command": "record-get", "id": proof["record_id"]}
            for proof in (task_proof, project_proof)
        ])
        self.assertEqual(self.complete("complete", task, evidence), completed)
        self.write("task-complete", "complete", self.identity, task_id=task["task_id"],
                   expected_revision=task["revision"], completed_scope="已更换完成范围",
                   evidence=evidence, error="operation_conflict")
        self.complete("stale-complete", task, evidence, error="revision_conflict")
        self.assertEqual(self.task_context()["task"]["completion"], done["completion"])
        rows = self.call("summaries", "--type", "task", "--status", "DONE")["result"]["items"]
        self.assertEqual([(row["task_id"], row["status"]) for row in rows],
                         [(task["task_id"], "DONE")])
        after = self.call("summaries", "--type", "record", "--task", "T001")["result"]["items"]
        self.assertEqual(after, before)
        for proof, log in ((task_proof, task_log), (project_proof, project_log)):
            raw = self.call("record-get", proof["record_id"])["result"]
            self.assertEqual(raw["body_md"], log["body_md"])
        self.assertEqual(self.call("record-get", project_proof["record_id"])["result"]["scope"],
                         "PROJECT")

        replacement = self.store("correct-proof", [
            progress("项目进展更正", related_task_id="T001", replaces_id=project_proof["record_id"]),
        ])["result"]["records"][0]
        self.assertEqual(self.task_context()["task"]["completion"], done["completion"])
        raw = self.call("record-get", project_proof["record_id"])["result"]
        self.assertEqual((raw["status"], raw["superseded_by"], raw["body_md"]),
                         ("SUPERSEDED", replacement["record_id"], project_log["body_md"]))

    def test_completion_rejects_invalid_ownership_and_replaced_project_proof(self):
        another_task = self.write("task-create", "other-task", self.identity,
                                  title="其他任务")["result"]["task"]
        wrong_task = self.store("wrong-task-log", [progress("其他任务进展")],
                                task=another_task)["result"]["records"][0]["record_id"]
        goal = self.store("task-goal", [item("GOAL", "任务目标")], task=self.task)
        goal_id = goal["result"]["records"][0]["record_id"]
        owned = self.write("claim", "claim-other", self.identity, task_id=another_task["task_id"],
                           expected_revision=another_task["revision"], client="codex",
                           session_id="other")["result"]["task"]
        wrong_entry = self.write(
            "checkpoint", "other-checkpoint", self.identity, task_id=owned["task_id"],
            expected_revision=owned["revision"], run_id=owned["active_run"]["run_id"],
            summary="其他任务旧进展", body_md=MARKDOWN, next_step="继续其他任务",
        )["result"]["entry"]["entry_id"]
        project_rows = self.store("project-log", [
            progress("项目进展，未关联任务"),
            progress("项目进展，关联其他任务", related_task_id="T002"),
            progress("项目进展，稍后更正", related_task_id="T001"),
        ])["result"]["records"]
        old = project_rows[2]["record_id"]
        task = self.task_context()["task"]
        fresh = self.store("correct-proof", [
            progress("已更正关联进展", related_task_id="T001", replaces_id=old),
        ])["result"]["records"][0]["record_id"]
        self.assertEqual(self.task_context()["task"]["revision"], task["revision"])
        foreign = self.root / "另一个项目"
        foreign.mkdir()
        identity = self.call("init", project=foreign, data={"name": "南星合成项目"})
        foreign_task = self.write("task-create", "make", identity, project=foreign,
                                  title="另一个项目的 T001")["result"]["task"]
        foreign_proof = self.write("record", "proof", identity, project=foreign,
                                   scope="TASK", task_id=foreign_task["task_id"],
                                   expected_revision=foreign_task["revision"],
                                   expected_context_position=0,
                                   records=[progress("另一个项目进展")])["result"]["records"][0]
        before = self.db.read_bytes()
        for record_id in (wrong_task, goal_id, project_rows[0]["record_id"], project_rows[1]["record_id"],
                          old, foreign_proof["record_id"]):
            with self.subTest(record_id=record_id):
                self.complete("finish", task, [record_ref(record_id)], error="invalid_evidence")
        self.complete("finish", task, [{"type": "ENTRY", "target_entry_id": wrong_entry}],
                      error="invalid_evidence")
        self.complete("finish", task, [], error="invalid_input")
        self.assertEqual(self.db.read_bytes(), before)
        done = self.complete("finish", task, [record_ref(fresh)])["result"]["task"]
        self.assertEqual(done["status"], "DONE")
        self.assertEqual(done["completion"]["evidence"], [{"command": "record-get", "id": fresh}])

    def test_blocker_and_dispositions_are_checked_without_closing_todos(self):
        seeded = self.store("task-log", [
            progress("验证结果"),
            item("TODO", "后续复核", details={"status": "OPEN"}),
            item("BLOCKER", "结果待确认", details={
                "status": "OPEN", "blocked_work": "标记完成", "unblock_condition": "确认结果",
            }),
        ], task=self.task)["result"]["records"]
        evidence = [record_ref(seeded[0]["record_id"])]
        todo_id, blocker_id = (row["record_id"] for row in seeded[1:])
        disposition = {todo_id: "下一阶段由复核人处理。"}
        before = self.db.read_bytes()
        self.complete("finish", self.task, evidence, todo_dispositions=disposition,
                      error="task_blocked")
        self.assertEqual(self.db.read_bytes(), before)
        self.store("resolve-blocker", [
            item("BLOCKER", "结果已确认", details={
                "status": "RESOLVED", "blocked_work": "标记完成", "unblock_condition": "确认结果",
                "outcome_md": "已确认合成样本。",
            }, refs=evidence, replaces_id=blocker_id),
        ], task=self.task_context()["task"])
        self.assertEqual(self.task_context()["task"]["status"], "TODO")

        before = self.db.read_bytes()
        self.complete("finish", self.task, evidence, error="todo_disposition_required")
        self.complete("finish", self.task, evidence, todo_dispositions={blocker_id: "已确认。"},
                      error="todo_disposition_required")
        self.assertEqual(self.db.read_bytes(), before)
        done = self.complete("finish", self.task, evidence,
                             todo_dispositions=disposition)["result"]["task"]
        self.assertEqual(done["status"], "DONE")
        self.assertEqual(done["completion"]["todo_dispositions"], disposition)
        todo = self.call("record-get", todo_id)["result"]
        self.assertEqual(todo["status"], "OPEN")

    def test_legacy_entry_completion_closes_run_and_appears_in_public_reads(self):
        owned = self.write("claim", "claim", self.identity, task_id=self.task["task_id"],
                           expected_revision=self.task["revision"], client="codex",
                           session_id="legacy-owner")["result"]["task"]
        logged = self.write("checkpoint", "checkpoint", self.identity, task_id=owned["task_id"],
                            expected_revision=owned["revision"], run_id=owned["active_run"]["run_id"],
                            summary="旧进展", body_md=MARKDOWN,
                            next_step="确认结果")["result"]
        entry_id = logged["entry"]["entry_id"]
        evidence = [{"type": "ENTRY", "target_entry_id": entry_id}]
        old_snapshot = self.call("snapshot")["result"]
        with closing(connect(self.db, "rw")) as db:
            db.execute("CREATE TRIGGER fail_completion BEFORE INSERT ON changes "
                       "BEGIN SELECT RAISE(ABORT, 'injected receipt failure'); END")
        self.complete("complete", logged["task"], evidence, error="storage_error")
        self.assertEqual(self.call("snapshot")["result"], old_snapshot)
        self.assertEqual(self.task_context()["task"]["active_run"], owned["active_run"])
        with closing(connect(self.db, "rw")) as db:
            db.execute("DROP TRIGGER fail_completion")
        completed = self.complete("complete", logged["task"], evidence)
        done = completed["result"]["task"]
        self.assertEqual(done["completion"]["evidence"], [{"command": "entry-get", "id": entry_id}])
        self.assertIsNone(done["active_run"])
        self.assertIsNone(self.task_context()["task"]["active_run"])
        self.assertEqual(self.call("entry-get", entry_id)["result"]["body_md"], MARKDOWN)
        self.write("claim", "claim-again", self.identity, task_id=done["task_id"],
                   expected_revision=done["revision"], client="opencode", session_id="next-owner",
                   error="task_state_conflict")
        projected = {**done, "display_status": "DONE", "archived": False, "priority": None,
                     "blocker_refs": []}
        self.assertEqual(self.call("task-get", "T001")["result"], projected)
        self.assertEqual(self.call("snapshot")["result"]["tasks"], [projected])
        page = self.call("changes", "--project-id", self.identity["project_id"], "--data-generation",
                         self.identity["data_generation"], "--after", str(old_snapshot["watermark"]))
        self.assertEqual(page["result"]["changes"], [{**completed["result"], "task": projected}])

    def test_competing_completions_and_same_operation_retry(self):
        proof = self.store("proof", [progress("样本复测", related_task_id="T001")])
        fields = {**self.identity_fields(self.identity), "task_id": "T001",
                  "expected_revision": self.task["revision"], "completed_scope": "样本复测通过",
                  "evidence": [record_ref(proof["result"]["records"][0]["record_id"])]}
        requests = [{**fields, "operation_id": f"finish-{number}"} for number in (1, 2)]
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(invoke, CLI, self.project, "task-complete", data=request)
                       for request in requests]
            replies = [future.result() for future in futures]
        self.assertEqual(sorted(code for code, _ in replies), [0, 4])
        for request, (code, reply) in zip(requests, replies):
            if code == 0:
                self.assertEqual(self.call("task-complete", data=request), reply)
                self.assertEqual(self.task_context()["task"]["revision"], self.task["revision"] + 1)
            else:
                self.assertEqual(reply["error"]["code"], "revision_conflict")
        with closing(connect(self.db, "ro")) as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM changes WHERE operation_id IN ('finish-1', 'finish-2')"
            ).fetchone()[0], 1)

    def test_waiting_completion_rechecks_project_evidence_after_correction(self):
        original = self.store("proof", [progress("待更正进展", related_task_id="T001")])
        old = original["result"]["records"][0]["record_id"]
        identity = self.identity_fields(self.identity)
        request = {**identity, "task_id": "T001", "expected_revision": self.task["revision"],
                   "operation_id": "waiting-finish", "completed_scope": "复测样本", "evidence": [record_ref(old)]}
        correction = {**identity, "operation_id": "correct-proof", "scope": "PROJECT", "records": [
            progress("更正后进展", related_task_id="T001", replaces_id=old),
        ]}
        waiting = Event()

        def notify_write_attempt(path, mode):
            db = connect(path, mode)

            def trace(statement):
                if statement == "BEGIN IMMEDIATE":
                    waiting.set()

            db.set_trace_callback(trace)
            return db

        args = argparse.Namespace(project=str(self.project), command="task-complete")
        with ThreadPoolExecutor(max_workers=1) as pool:
            with closing(connect(self.db, "rw")) as writer, writer:
                writer.execute("BEGIN IMMEDIATE")
                with patch("worklog.request_json", return_value=request), \
                        patch("worklog.connect", side_effect=notify_write_attempt):
                    completion = pool.submit(worklog.execute, args)
                    self.assertTrue(waiting.wait(4), "Completion did not contend for the write lock.")
                    record(writer, self.identity, correction)
            with self.assertRaises(WorklogError) as rejected:
                completion.result(timeout=8)
            self.assertEqual(rejected.exception.code, "invalid_evidence")
        self.assertEqual(self.task_context()["task"]["revision"], self.task["revision"])
        self.assertEqual(self.call("record-get", old)["result"]["status"], "SUPERSEDED")
        self.assertEqual(self.call("snapshot")["result"]["watermark"], 1)
