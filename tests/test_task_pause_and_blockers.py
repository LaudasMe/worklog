from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
from unittest.mock import patch

import worklog
from test_context import CLI, MARKDOWN, SeededContextCase, invoke, item, progress
from test_task_lifecycle import record_ref
from test_task_lifecycle_upgrade import BLOCKER_DETAILS, resolved_blocker
from worklog_store import connect


class TaskPauseAndBlockersTest(SeededContextCase):
    def transition(self, command, operation, task, *, error=None):
        return self.write(command, operation, self.identity, task_id=task["number"],
                          expected_revision=task["revision"], error=error)

    def task_summaries(self, *filters):
        return self.call("summaries", "--type", "task", *filters)["result"]

    def change_rows(self, operation_id):
        with closing(connect(self.db, "ro")) as db:
            return db.execute("SELECT * FROM changes WHERE operation_id = ?", (operation_id,)).fetchall()

    def test_blockers_change_the_display_and_compose_with_summary_filters(self):
        working = self.write("task-create", "working", self.identity, title="进行中的任务")["result"]["task"]
        working = self.transition("task-start", "start", working)["result"]["task"]
        snapshot = self.call("snapshot")["result"]
        self.store("project-blocker", [
            item("BLOCKER", "项目级阻塞", details=BLOCKER_DETAILS, related_task_id="T001"),
        ])
        self.assertEqual(self.task_summaries("--blocked")["items"], [])
        blockers = {}
        blocker_changes = []
        for task in (self.task, working):
            number = task["number"]
            blocker = item("BLOCKER", f"{number} 需要补齐材料", details=BLOCKER_DETAILS)
            receipt = self.store(f"block-{number}", [blocker], task=task)
            blockers[number] = receipt["result"]["records"][0]["record_id"]
            self.assertEqual(self.store(f"block-{number}", [blocker], task=task), receipt)
            rows = self.change_rows(f"block-{number}")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["visible_in_v1"], 0)
            self.assertEqual(json.loads(rows[0]["payload"])["task"], task)
            projected = {
                **task, "display_status": "BLOCKED", "archived": False, "priority": None,
                "blocker_refs": [{"command": "record-get", "id": blockers[number]}],
            }
            self.assertEqual(json.loads(rows[0]["task_v2_json"]), projected)
            blocker_changes.append({
                **json.loads(rows[0]["payload"]), "seq": rows[0]["seq"],
                "change_id": rows[0]["change_id"], "task": projected,
            })
            detail = self.call("context", "--task", number)["result"]
            self.assertEqual(detail["task"]["display_status"], "BLOCKED")
            self.assertEqual(detail["blockers"][0]["details"]["unblock_condition"], "补齐依据")
            self.assertEqual(detail["blockers"][0]["body_md"], blocker["body_md"])
            self.assertEqual(self.call("task-get", number)["result"], projected)
        self.assertEqual(self.change_rows("project-blocker"), [])
        self.assertEqual(self.call("snapshot")["result"], {
            **snapshot, "tasks": [change["task"] for change in blocker_changes],
            "watermark": snapshot["watermark"] + 2,
        })
        page = self.call("changes", "--project-id", self.identity["project_id"], "--data-generation",
                         self.identity["data_generation"], "--after", str(snapshot["watermark"]))["result"]
        self.assertEqual(page, {
            "changes": blocker_changes, "next_after": snapshot["watermark"] + 2, "has_more": False,
            "watermark": snapshot["watermark"] + 2,
        })

        blocked = self.task_summaries("--display-status", "BLOCKED", "--blocked")["items"]
        self.assertEqual({(row["number"], row["status"]) for row in blocked},
                         {("T001", "TODO"), ("T002", "IN_PROGRESS")})
        self.assertEqual([row["number"] for row in self.task_summaries(
            "--display-status", "BLOCKED", "--status", "TODO", "--task", "T001",
        )["items"]], ["T001"])
        first = self.task_summaries("--display-status", "BLOCKED", "--blocked", "--limit", "1")
        cursor = first["next_cursor"]
        self.assertIsNotNone(cursor)
        second = self.task_summaries("--display-status", "BLOCKED", "--blocked", "--cursor", cursor)
        self.assertEqual(first["items"] + second["items"], blocked)
        for value in ((), ("--display-status", "TODO")):
            self.call("summaries", "--type", "task", "--blocked", *value, "--cursor", cursor,
                      error="invalid_input")
        self.call("summaries", "--type", "record", "--display-status", "BLOCKED", error="invalid_input")
        record_rows = self.call("summaries", "--type", "record", "--task", "T001", "--blocked")["result"]["items"]
        self.assertEqual({row["scope"] for row in record_rows}, {"TASK", "PROJECT"})

        evidence = [{"type": "USER_CONFIRMATION", "note": "合成样本已补齐。"}]
        for number, blocker_id in blockers.items():
            task = self.call("context", "--task", number)["result"]["task"]
            operation = f"resolve-{number}"
            resolution = [resolved_blocker(blocker_id, evidence)]
            receipt = self.store(operation, resolution, task=task)
            self.assertEqual(self.store(operation, resolution, task=task), receipt)
            row, = self.change_rows(operation)
            projection = json.loads(row["task_v2_json"])
            self.assertEqual(projection["display_status"], task["status"])
            self.assertEqual(projection["blocker_refs"], [])
            self.assertEqual(projection["revision"], task["revision"])
            self.assertEqual(projection["updated_at"], task["updated_at"])
            detail = self.call("context", "--task", number)["result"]
            self.assertEqual(detail["task"]["display_status"], task["status"])
            self.assertEqual(detail["blockers"], [])
        self.assertEqual(self.task_summaries("--blocked")["items"], [])
        self.assertEqual(self.call("snapshot")["result"], {**snapshot, "watermark": snapshot["watermark"] + 4})
        self.call("summaries", "--type", "task", "--display-status", "BLOCKED", "--blocked",
                  "--cursor", cursor, error="cursor_stale")

    def test_pause_keeps_owner_and_blockers_then_resumes_and_completes(self):
        for command in ("task-pause", "task-resume"):
            self.transition(command, command, self.task, error="task_state_conflict")
        owner = self.write("claim", "owner", self.identity, task_id=self.task["task_id"],
                           expected_revision=self.task["revision"], client="codex",
                           session_id="owner")["result"]["task"]
        run_id = owner["active_run"]["run_id"]
        records = [
            progress("合成样本进展"),
            item("TODO", "后续复核", details={"status": "OPEN"}),
            item("BLOCKER", "材料待补齐", details=BLOCKER_DETAILS),
        ]
        receipt = self.store("work", records, task=owner, run_id=run_id)["result"]
        log_id, todo_id, blocker_id = (row["record_id"] for row in receipt["records"])
        paused = self.transition("task-pause", "pause", owner)
        current = paused["result"]["task"]
        self.assertEqual(current["active_run"], owner["active_run"])
        self.assertEqual(current["revision"], owner["revision"] + 1)
        self.assertEqual(self.task_context()["task"]["display_status"], "PAUSED")
        self.assertEqual(self.task_context()["blockers"][0]["record_id"], blocker_id)
        self.assertEqual(self.task_summaries("--display-status", "BLOCKED")["items"], [])
        self.assertEqual(self.task_summaries("--blocked", "--status", "PAUSED")["items"][0]["display_status"],
                         "PAUSED")
        projected = {**current, "display_status": "PAUSED", "archived": False, "priority": None,
                     "blocker_refs": [{"command": "record-get", "id": blocker_id}]}
        self.assertEqual(self.call("task-get", "T001")["result"], projected)
        self.assertEqual(self.call("snapshot")["result"]["tasks"], [projected])
        self.transition("task-pause", "pause-again", current, error="task_state_conflict")
        checkpoint = self.write("checkpoint", "while-paused", self.identity,
                                task_id=current["task_id"], expected_revision=current["revision"],
                                run_id=run_id, summary="暂停期间核对", body_md=MARKDOWN,
                                next_step="补齐材料")["result"]["task"]
        self.assertEqual(checkpoint["status"], "PAUSED")
        resumed = self.transition("task-resume", "resume", checkpoint)
        current = resumed["result"]["task"]
        self.assertEqual(current["active_run"], owner["active_run"])
        self.assertEqual(self.task_context()["task"]["display_status"], "BLOCKED")
        self.transition("task-resume", "resume-again", current, error="task_state_conflict")

        self.transition("task-pause", "pause-second-time", current)
        current = self.task_context()["task"]
        fields = dict(task_id="T001", expected_revision=current["revision"],
                      completed_scope="已核对合成样本", evidence=[record_ref(log_id)],
                      todo_dispositions={todo_id: "进入下一阶段复核。"})
        self.write("task-complete", "complete", self.identity, **fields, error="task_blocked")
        self.store("resolve-while-paused", [resolved_blocker(blocker_id, fields["evidence"])],
                   task=current, run_id=run_id)
        row, = self.change_rows("resolve-while-paused")
        projection = json.loads(row["task_v2_json"])
        self.assertEqual((row["visible_in_v1"], projection["display_status"], projection["blocker_refs"]),
                         (0, "PAUSED", []))
        done = self.write("task-complete", "complete", self.identity, **fields)
        self.assertEqual(done["result"]["task"]["status"], "DONE")
        self.assertIsNone(self.task_context()["task"]["active_run"])
        self.assertEqual(self.task_context()["task"]["completion"], done["result"]["task"]["completion"])
        self.assertEqual(self.write("task-complete", "complete", self.identity, **fields), done)
        self.assertEqual(self.transition("task-pause", "pause", owner), paused)
        self.assertEqual(self.transition("task-resume", "resume", checkpoint), resumed)

    def test_paused_handoff_has_one_claim_winner_and_rejects_the_old_run(self):
        owner = self.write("claim", "initial", self.identity, task_id=self.task["task_id"],
                           expected_revision=self.task["revision"], client="codex",
                           session_id="old")["result"]["task"]
        paused = self.transition("task-pause", "pause", owner)["result"]
        handoff_fields = dict(task_id=owner["task_id"], expected_revision=paused["task"]["revision"],
                              run_id=owner["active_run"]["run_id"], summary="暂停任务的交接",
                              body_md=MARKDOWN, next_step="等待恢复")
        handoff = self.write("handoff", "handoff", self.identity, **handoff_fields)
        released = handoff["result"]["task"]
        self.assertEqual(released["status"], "PAUSED")
        self.assertIsNone(released["active_run"])
        self.assertEqual(self.call("entry-get", handoff["result"]["entry"]["entry_id"])["result"]["body_md"],
                         MARKDOWN)
        requests = [
            {**self.identity_fields(self.identity), "operation_id": f"claim-{client}", "client": client,
             "session_id": f"new-{client}", "task_id": released["task_id"], "expected_revision": released["revision"]}
            for client in ("codex", "opencode")
        ]
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(invoke, CLI, self.project, "claim", data=request) for request in requests]
            replies = [future.result() for future in futures]
        self.assertEqual(sorted(code for code, _ in replies), [0, 4])
        for request, (code, reply) in zip(requests, replies):
            if code == 0:
                claimed = reply["result"]["task"]
                self.assertEqual(claimed["status"], "PAUSED")
                self.assertEqual(claimed["revision"], released["revision"] + 1)
                self.assertEqual(self.task_context()["task"]["active_run"], claimed["active_run"])
                self.assertEqual(self.call("claim", data=request), reply)
            else:
                self.assertEqual(reply["error"]["code"], "revision_conflict")
        self.assertEqual(self.write("handoff", "handoff", self.identity, **handoff_fields), handoff)
        self.write("checkpoint", "late-checkpoint", self.identity, **handoff_fields, error="stale_run")
        self.store("late-context", [progress("旧接手的进展")], task=self.task_context()["task"],
                   run_id=handoff_fields["run_id"], error="stale_run")
        changes = ("changes", "--project-id", self.identity["project_id"], "--data-generation",
                   self.identity["data_generation"], "--limit", "1")
        for after, kind in ((paused["seq"], "handoff"), (handoff["result"]["seq"], "claim")):
            page = self.call(*changes, "--after", str(after))["result"]
            change, = page["changes"]
            self.assertEqual((change["seq"], change["kind"], change["task"]["status"]),
                             (after + 1, kind, "PAUSED"))

    def test_net_zero_batch_and_rollback_when_blocker_change_cannot_be_saved(self):
        blocker = item("BLOCKER", "短暂阻塞", details=BLOCKER_DETAILS)
        request = {**self.identity_fields(self.identity), "operation_id": "net-zero", "scope": "TASK",
                   "task_id": "T001", "expected_revision": self.task["revision"], "expected_context_position": 0,
                   "records": [blocker, resolved_blocker("known-id", [{"type": "RECORD", "batch_index": 0}])]}
        args = worklog.arguments(["--project", str(self.project), "record"])
        with patch("worklog.request_json", return_value=request), \
                patch("worklog_context.uid", side_effect=("known-id", "resolved-id")):
            _, saved = worklog.execute(args)
        self.assertEqual(self.call("record", data=request)["result"], saved)
        self.assertEqual(self.change_rows("net-zero"), [])
        self.assertEqual(self.task_context()["blockers"], [])
        task = self.task_context()["task"]
        with closing(connect(self.db, "rw")) as db:
            db.execute("CREATE TRIGGER fail_blocker_change BEFORE INSERT ON changes "
                       "WHEN NEW.visible_in_v1 = 0 "
                       "BEGIN SELECT RAISE(ABORT, 'injected event failure'); END")
        batch = [blocker, item("BLOCKER", "另一个待补齐的条件", details=BLOCKER_DETAILS)]
        self.store("open-blocker", batch, task=task, error="storage_error")
        self.assertEqual(self.task_context()["task"], task)
        self.assertEqual(self.task_context()["blockers"], [])
        with closing(connect(self.db, "rw")) as db:
            self.assertIsNone(db.execute(
                "SELECT 1 FROM context_operations WHERE operation_id = 'open-blocker'"
            ).fetchone())
            db.execute("DROP TRIGGER fail_blocker_change")
        opened = self.store("open-blocker", batch, task=task)
        self.assertEqual(self.store("open-blocker", batch, task=task), opened)
        row, = self.change_rows("open-blocker")
        self.assertEqual(json.loads(row["payload"])["kind"], "task-blockers")
        self.assertEqual(json.loads(row["task_v2_json"])["blocker_refs"], [
            {"command": "record-get", "id": record["record_id"]}
            for record in opened["result"]["records"]
        ])
