from contextlib import closing
import json

from test_context import ContextCase, MARKDOWN, SeededContextCase, item, progress, source_at
from test_task_lifecycle import record_ref
from test_task_lifecycle_upgrade import BLOCKER_DETAILS, CONTEXT_FILES, HISTORICAL_REVISIONS, resolved_blocker
from worklog_store import connect, schema_version


CONFIRMATION = [{"type": "USER_CONFIRMATION", "note": "用户确认本次需要复查。"}]


def ids(rows):
    return [row["record_id"] for row in rows]


class TaskClosureTest(SeededContextCase):
    def transition(self, command, operation, task, **fields):
        key = task["task_id"] if command == "claim" else task["number"]
        return self.write(command, operation, self.identity, task_id=key,
                          expected_revision=task["revision"], **fields)

    def detail(self, task):
        return self.call("context", "--task", task["number"])["result"]

    def tasks(self, *filters):
        return self.call("summaries", "--type", "task", *filters)["result"]["items"]

    def test_cancel_closes_runs_and_keeps_original_records(self):
        for display_status in ("TODO", "IN_PROGRESS", "PAUSED", "BLOCKED"):
            with self.subTest(display_status=display_status):
                task = self.write("task-create", f"create-{display_status}", self.identity,
                                  title=display_status)["result"]["task"]
                run_id = None
                if display_status != "TODO":
                    task = self.transition("claim", f"claim-{display_status}", task,
                                           client="codex", session_id="old")
                    task = task["result"]["task"]
                    run_id = task["active_run"]["run_id"]
                if display_status == "PAUSED":
                    task = self.transition("task-pause", "pause", task)["result"]["task"]
                prior_entries = []
                if run_id is not None:
                    for index in range(2):
                        logged = self.write(
                            "checkpoint", f"prior-{display_status}-{index}", self.identity,
                            task_id=task["task_id"], expected_revision=task["revision"], run_id=run_id,
                            summary=f"取消前旧日志 {index}", body_md=MARKDOWN, next_step="复核",
                        )["result"]
                        task = logged["task"]
                        prior_entries.append(logged["entry"]["entry_id"])
                records = [progress("取消前进展"), item("TODO", "待处理", details={"status": "OPEN"})]
                if display_status == "BLOCKED":
                    records.append(item("BLOCKER", "缺材料", details=BLOCKER_DETAILS))
                saved = self.store(f"record-{display_status}", records, task=task, run_id=run_id)["result"]
                self.assertEqual(self.detail(task)["task"]["display_status"], display_status)
                operation = f"cancel-{display_status}"
                self.transition("task-cancel", operation, task, error="invalid_input")
                self.transition("task-cancel", operation, task, reason="  ", error="invalid_input")
                self.transition("task-archive", operation, task, error="task_state_conflict")
                cancelled = self.transition("task-cancel", operation, task, reason="样本已撤回")
                self.assertEqual(self.transition("task-cancel", operation, task, reason="样本已撤回"), cancelled)
                self.transition("task-cancel", operation, task, reason="其他原因", error="operation_conflict")
                detail = self.detail(task)
                self.assertEqual(detail["task"]["status"], "CANCELLED")
                self.assertEqual(detail["task"]["revision"], task["revision"] + 1)
                self.assertIsNone(detail["task"]["active_run"])
                self.assertEqual(detail["todos"], [])
                self.assertEqual(detail["blockers"], [])
                self.assertEqual(set(ids(detail["action_history"])), set(ids(saved["records"][1:])))
                self.assertEqual(detail["progress"][0]["record_id"], saved["records"][0]["record_id"])
                self.assertEqual([entry["entry_id"] for entry in detail["entry_refs"]], prior_entries)
                if prior_entries:
                    oldest = self.call("entry-get", prior_entries[0])["result"]
                    self.assertEqual(oldest["body_md"], MARKDOWN)
                self.assertEqual(detail["lifecycle_history"][-1], cancelled["result"])
                self.assertEqual(detail["lifecycle_history"][-1]["lifecycle"]["reason"], "样本已撤回")
                self.assertEqual(self.tasks("--status", "CANCELLED", "--task", task["number"])[0]["status"],
                                 "CANCELLED")
                self.assertEqual(self.tasks("--blocked", "--task", task["number"]), [])
                public = self.call("task-get", task["number"])["result"]
                self.assertEqual((public["status"], public["display_status"], public["blocker_refs"]),
                                 ("CANCELLED", "CANCELLED", []))
                if run_id is not None:
                    run, = detail["run_history"]
                    self.assertEqual((run["run_id"], run["state"], run["closed_at"]),
                                     (run_id, "CLOSED", cancelled["result"]["committed_at"]))
                    for command in ("checkpoint", "handoff"):
                        self.write(command, f"old-{command}-{display_status}", self.identity,
                                   task_id=task["task_id"], expected_revision=detail["task"]["revision"],
                                   run_id=run_id, summary="旧接手", body_md=MARKDOWN, next_step="核对",
                                   error="stale_run")
                    self.store(f"old-run-{display_status}", [progress("旧接手不能补记")],
                               task=detail["task"], run_id=run_id, error="stale_run")

    def test_terminal_archive_filters_and_unarchive_keep_base_state(self):
        log = self.store("progress", [progress("已完成")], task=self.task)["result"]["records"][0]
        done = self.transition("task-complete", "done", self.task, completed_scope="合成样本",
                               evidence=[record_ref(log["record_id"])])["result"]["task"]
        other = self.write("task-create", "other", self.identity, title="取消归档样本")["result"]["task"]
        cancelled = self.transition("task-cancel", "cancel", other, reason="材料已撤回")["result"]["task"]
        archived = []
        for task in (done, cancelled):
            operation = f"archive-{task['number']}"
            receipt = self.transition("task-archive", operation, task)
            self.assertEqual(self.transition("task-archive", operation, task), receipt)
            stored = self.call("context", "--task", task["task_id"])["result"]["task"]
            self.assertEqual(stored["status"], task["status"])
            self.assertTrue(stored["archived"])
            archived.append(stored)
        self.assertEqual(self.tasks(), [])
        self.assertEqual(self.call("context")["result"]["related_tasks"], [])
        self.assertEqual({row["status"] for row in self.tasks("--view", "archived")}, {"DONE", "CANCELLED"})
        page = self.call("summaries", "--type", "task", "--view", "archived", "--limit", "1")["result"]
        self.call("summaries", "--type", "task", "--cursor", page["next_cursor"], error="invalid_input")
        for task in archived:
            self.transition("task-unarchive", f"unarchive-{task['number']}", task)
        self.assertEqual({row["status"] for row in self.tasks()}, {"DONE", "CANCELLED"})
        self.assertEqual(self.tasks("--view", "archived"), [])

    def test_only_selected_items_apply_after_cancellation_and_reopen(self):
        for selected in ("BLOCKER", "TODO"):
            with self.subTest(selected=selected):
                task = self.write("task-create", f"create-{selected}", self.identity,
                                  title=f"仅带回 {selected}")["result"]["task"]
                saved = self.store(f"seed-{selected}", [progress("旧日志"),
                    item("TODO", "历史待办", details={"status": "OPEN"}),
                    item("BLOCKER", "历史阻塞", details=BLOCKER_DETAILS),
                ], task=task)["result"]["records"]
                log, todo, blocker = saved
                cancelled = self.transition("task-cancel", f"cancel-{selected}", task,
                                            reason="暂停本轮验证")["result"]["task"]
                archived = self.transition("task-archive", f"archive-{selected}", cancelled)["result"]["task"]
                operation = f"reopen-{selected}"
                self.transition("task-reopen", operation, archived, reason="复查", evidence=[], error="invalid_input")
                fields = {"reason": "恢复复查", "evidence": CONFIRMATION}
                reopened = self.transition("task-reopen", operation, archived, **fields)
                self.assertEqual(self.transition("task-reopen", operation, archived, **fields), reopened)
                self.assertEqual(ids(reopened["result"]["lifecycle"]["reopen_candidates"]), ids(saved[1:]))
                with closing(connect(self.db, "ro")) as db:
                    row = db.execute("SELECT task_v2_json, visible_in_v1 FROM changes WHERE operation_id = ?",
                                     (operation,)).fetchone()
                    projection = json.loads(row["task_v2_json"])
                    self.assertEqual((projection["display_status"], projection["blocker_refs"], row["visible_in_v1"]),
                                     ("TODO", [], 1))
                detail = self.detail(task)
                self.assertEqual(detail["task"]["number"], task["number"])
                self.assertEqual(detail["task"]["task_id"], task["task_id"])
                self.assertEqual(detail["task"]["display_status"], "TODO")
                self.assertFalse(detail["task"]["archived"])
                self.assertIsNone(detail["task"]["active_run"])
                self.assertEqual(detail["todos"], [])
                self.assertEqual(detail["blockers"], [])
                self.assertEqual(ids(detail["reopen_candidates"]), ids(saved[1:]))
                self.assertFalse(self.call("task-get", task["number"])["result"]["archived"])
                other = self.task
                wrong = item("TODO", "其他任务带回", details={"status": "OPEN"}, carry_forward_id=todo["record_id"])
                self.store(f"wrong-task-{selected}", [wrong], task=other, error="invalid_input")
                wrong["carry_forward_id"] = blocker["record_id"]
                self.store(f"wrong-kind-{selected}", [wrong], task=detail["task"], error="invalid_input")
                self.store(f"replace-history-{selected}", [item(
                    "TODO", "改写原记录", details={"status": "OPEN"}, replaces_id=todo["record_id"],
                )], task=detail["task"], error="record_conflict")

                original = blocker if selected == "BLOCKER" else todo
                original_get = self.call("record-get", original["record_id"])["result"]
                bring = item(selected, "只带回已选项", details=original_get["details"],
                             carry_forward_id=original["record_id"])
                run_id = None
                if selected == "BLOCKER":
                    started = self.transition("claim", "new-owner", detail["task"], client="codex", session_id="new")
                    run_id = started["result"]["task"]["active_run"]["run_id"]
                    detail = self.detail(task)
                    self.store("unowned-carry", [bring], task=detail["task"], error="stale_run")
                operation = f"carry-{selected}"
                current = detail["task"]
                self.store(operation, [bring, bring], task=current, run_id=run_id, error="record_conflict")
                receipt = self.store(operation, [bring], task=current, run_id=run_id)
                self.assertEqual(self.store(operation, [bring], task=current, run_id=run_id), receipt)
                carried = receipt["result"]["records"][0]
                with closing(connect(self.db, "ro")) as db:
                    change = db.execute("SELECT visible_in_v1 FROM changes WHERE operation_id = ?", (operation,)).fetchone()
                    self.assertEqual(change[0] if change else None, 0 if selected == "BLOCKER" else None)
                carried_get = self.call("record-get", carried["record_id"])["result"]
                self.assertEqual(carried_get["refs"][0]["source"], original_get["source"])
                self.assertEqual(self.call("record-get", original["record_id"])["result"], original_get)
                detail = self.detail(task)
                self.assertNotIn(original["record_id"], ids(detail["reopen_candidates"]))
                self.assertEqual(detail["task"]["display_status"], "BLOCKED" if selected == "BLOCKER" else "TODO")
                self.assertEqual(bool(self.tasks("--blocked", "--task", task["number"])), selected == "BLOCKER")
                self.store(f"duplicate-{selected}", [bring], task=detail["task"], run_id=run_id,
                           error="record_conflict")

                fields = {"completed_scope": "合成样本已核对", "evidence": [record_ref(log["record_id"])]}
                operation = f"complete-{selected}"
                error = "task_blocked" if selected == "BLOCKER" else "todo_disposition_required"
                problem = self.transition("task-complete", operation, detail["task"], error=error, **fields)
                list_name = "blockers" if selected == "BLOCKER" else "todos"
                self.assertEqual(ids(problem["error"]["details"][list_name]), [carried["record_id"]])
                if selected == "BLOCKER":
                    self.store("resolve", [resolved_blocker(carried["record_id"], fields["evidence"])],
                               task=detail["task"], run_id=run_id)
                    self.assertEqual(self.detail(task)["task"]["display_status"], "IN_PROGRESS")
                else:
                    fields["todo_dispositions"] = {carried["record_id"]: "后续跟进"}
                completed = self.transition("task-complete", operation, detail["task"], **fields)["result"]["task"]
                self.assertEqual(completed["status"], "DONE")
                self.transition("task-reopen", f"reopen-done-{selected}", completed,
                                reason="再次复查", evidence=fields["evidence"])
                detail = self.detail(task)
                self.assertEqual(ids(detail["todos"]), [carried["record_id"]] if selected == "TODO" else [])
                self.assertEqual(detail["reopen_candidates"], [])


class CompleteReopenMigrationTest(ContextCase):
    def test_schema_three_completion_reopens_and_preserves_both_original_logs(self):
        old = source_at(HISTORICAL_REVISIONS[3], self.root / "old-cli", CONTEXT_FILES + ("schema_lifecycle.sql",))
        identity = self.call("init", data={"name": "迁移重开样本"}, cli=old)
        created = self.write("task-create", "old-create", identity, cli=old, title="已完成")
        task = created["result"]["task"]
        task_id = task["task_id"]
        source = progress("第一轮验证")
        first_records = self.write("record", "old-log", identity, cli=old, scope="TASK", task_id=task_id,
                                   expected_revision=task["revision"], expected_context_position=0,
                                   records=[source, item("TODO", "待后续验证", details={"status": "OPEN"})])
        log, todo = first_records["result"]["records"]
        fields = {"task_id": task["number"], "expected_revision": task["revision"],
                  "completed_scope": "第一轮完成范围", "evidence": [record_ref(log["record_id"])],
                  "todo_dispositions": {todo["record_id"]: "留给下一轮"}}
        old_done = self.write("task-complete", "old-done", identity, cli=old, **fields)
        done = old_done["result"]["task"]
        later = self.write("record", "post-done-blocker", identity, cli=old, scope="TASK", task_id=task_id,
                           expected_revision=done["revision"], expected_context_position=todo["position"],
                           records=[item("BLOCKER", "完毕后补记阻塞", details=BLOCKER_DETAILS)])["result"]
        blocker, = later["records"]
        for command, section in ((("context", "--task", task_id), "blockers"),
                                 (("context",), "task_blockers")):
            historical = self.call(*command, cli=old)["result"][section]
            current = self.call(*command)["result"][section]
            self.assertEqual(ids(historical), [blocker["record_id"]])
            self.assertEqual(ids(current), ids(historical))
        historical = self.call("summaries", "--type", "task", "--blocked", cli=old)["result"]["items"]
        current = self.call("summaries", "--type", "task", "--blocked")["result"]["items"]
        self.assertEqual([item["task_id"] for item in current], [item["task_id"] for item in historical])
        self.assertEqual([item["task_id"] for item in current], [task_id])
        reopen = {"task_id": task["number"], "expected_revision": done["revision"],
                  "reason": "旧日志记录了待复查范围", "evidence": fields["evidence"]}
        self.write("task-reopen", "reopen", identity, **reopen, error="upgrade_required")
        backup = self.root / "backup.sqlite3"
        self.call("backup", "--output", str(backup))
        self.call("upgrade", "--backup", str(backup))
        with closing(connect(self.db, "ro")) as db:
            self.assertEqual(schema_version(db), 4)
        self.assertEqual(self.write("task-complete", "old-done", identity, **fields),
                         {**old_done, "protocol_version": "2.0"})
        self.write("task-reopen", "reopen", identity, **{**reopen, "reason": ""}, error="invalid_input")
        self.write("task-reopen", "reopen", identity, **{**reopen, "evidence": [record_ref(blocker["record_id"])]},
                   error="invalid_evidence")
        self.write("task-reopen", "reopen", identity, **{**reopen, "evidence": [{"type": "USER_CONFIRMATION"}]},
                   error="invalid_input")
        reopened = self.write("task-reopen", "reopen", identity, **reopen)["result"]
        self.assertEqual(reopened["lifecycle"]["evidence"], [{"command": "record-get", "id": log["record_id"]}])
        self.assertEqual(ids(reopened["lifecycle"]["reopen_candidates"]), [blocker["record_id"]])
        current = self.call("context", "--task", task_id)["result"]
        self.assertEqual(current["task"]["display_status"], "TODO")
        self.assertEqual(current["blockers"], [])
        self.assertEqual(ids(current["todos"]), [todo["record_id"]])
        next_source = progress("第二轮验证")
        stored = self.write("record", "carry-done-blocker", identity, scope="TASK", task_id=task_id,
                            expected_revision=current["task"]["revision"],
                            expected_context_position=current["task"]["context_position"],
                            records=[next_source, item("BLOCKER", "带回复查阻塞", details=BLOCKER_DETAILS,
                                                       carry_forward_id=blocker["record_id"])])["result"]
        next_log, carried = stored["records"]
        completion = {**fields, "expected_revision": current["task"]["revision"],
                      "completed_scope": "第二轮完成范围", "evidence": [record_ref(next_log["record_id"])]}
        self.write("task-complete", "done-again", identity, **completion, error="task_blocked")
        self.write("record", "resolve-done-blocker", identity, scope="TASK", task_id=task_id,
                   expected_revision=current["task"]["revision"], expected_context_position=stored["context_position"],
                   records=[resolved_blocker(carried["record_id"], completion["evidence"])])
        self.write("task-complete", "done-again", identity, **{**completion, "todo_dispositions": {}},
                   error="todo_disposition_required")
        completion["todo_dispositions"] = {todo["record_id"]: "下次再验收"}
        done_again = self.write("task-complete", "done-again", identity, **completion)["result"]
        self.assertEqual(done_again["task"]["number"], done["number"])
        history = self.call("context", "--task", task_id)["result"]["lifecycle_history"]
        self.assertEqual(history, [old_done["result"], reopened, done_again])
        for event, original in ((history[0], source), (history[2], next_source)):
            evidence = event["task"]["completion"]["evidence"][0]
            read = self.call(evidence["command"], evidence["id"])["result"]
            self.assertEqual(read["body_md"], original["body_md"])
        page = self.call("changes", "--project-id", identity["project_id"], "--data-generation",
                         identity["data_generation"], "--after", str(reopened["seq"] - 1))
        change = page["result"]["changes"][0]
        self.assertEqual((change["seq"], change["kind"], change["task"]["status"]),
                         (reopened["seq"], "task-reopen", "TODO"))
        self.assertNotIn("lifecycle", change)
