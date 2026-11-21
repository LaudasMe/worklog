from test_context import ContextCase, LEGACY, SeededContextCase, item, progress, source_at
from test_task_lifecycle_upgrade import BLOCKER_DETAILS


class TaskPriorityTest(SeededContextCase):
    def task_write(self, command, operation, task, **fields):
        return self.write(command, operation, self.identity, task_id=task["number"],
                          expected_revision=task["revision"], **fields)

    def list_tasks(self, *options):
        return self.call("summaries", "--type", "task", *options)["result"]["items"]

    def changes(self, after):
        return self.call("changes", "--project-id", self.identity["project_id"],
                         "--data-generation", self.identity["data_generation"],
                         "--after", str(after))

    def test_create_and_change_each_priority_with_original_creation_receipts(self):
        existing = self.task_context()["task"]
        self.assertIsNone(existing["priority"])
        self.assertIsNone(self.list_tasks()[0]["priority"])
        initial_watermark = self.call("snapshot")["result"]["watermark"]
        created_changes = []
        for value in ("p0", "p1", "p2", "p3"):
            with self.subTest(action="create", priority=value):
                receipt = self.write("task-create", f"create-{value}", self.identity,
                                     title=value, priority=value)["result"]
                number = receipt["task"]["number"]
                projected = {**receipt["task"], "display_status": "TODO", "archived": False,
                             "priority": value, "blocker_refs": []}
                self.assertEqual(self.call("task-get", number)["result"], projected)
                self.assertNotIn("priority", receipt["task"])
                created_changes.append({**receipt, "task": projected})
                self.assertEqual(self.call("context", "--task", number)["result"]["task"]["priority"],
                                 value)
                self.assertEqual([row["number"] for row in self.list_tasks("--priority", value)], [number])
        self.assertEqual(self.changes(initial_watermark)["result"]["changes"], created_changes)
        after = created_changes[-1]["seq"]

        for value in ("p0", "p1", "p2", "p3"):
            with self.subTest(action="set", priority=value):
                written = self.task_write("task-set-priority", f"set-{value}", existing, priority=value)
                self.assertEqual(written, self.task_write("task-set-priority", f"set-{value}", existing,
                                                         priority=value))
                change = written["result"]
                self.assertEqual(change["seq"], after + 1)
                self.assertEqual(change["task"]["priority"], value)
                selected = self.task_context()["task"]
                self.assertEqual((selected["priority"], selected["revision"]),
                                 (value, existing["revision"] + 1))
                self.assertEqual(len(self.list_tasks("--priority", value)), 2)
                projected = {**change["task"], "display_status": "TODO", "archived": False,
                             "blocker_refs": []}
                self.assertEqual(self.changes(after)["result"]["changes"], [{**change, "task": projected}])
                after, existing = change["seq"], selected

        cleared = self.task_write("task-set-priority", "clear", existing, priority=None)["result"]
        self.assertIsNone(cleared["task"]["priority"])
        self.assertEqual(cleared["task"]["revision"], existing["revision"] + 1)
        self.assertIsNone(self.task_context()["task"]["priority"])
        self.assertEqual(len(self.list_tasks("--priority", "p3")), 1)
        public_change, = self.changes(after)["result"]["changes"]
        self.assertEqual((public_change["seq"], public_change["kind"], public_change["task"]["priority"]),
                         (cleared["seq"], "task-set-priority", None))
        self.assertEqual(self.call("snapshot")["result"]["watermark"], cleared["seq"])

    def test_invalid_or_missing_priority_does_not_write(self):
        before = self.call("snapshot")
        for invalid in ("p4", 2, "", [], {"priority": "p0"}):
            with self.subTest(priority=invalid):
                self.write("task-create", "bad-create", self.identity, title="非法", priority=invalid,
                           error="invalid_input")
                self.task_write("task-set-priority", "invalid-set", self.task, priority=invalid,
                                error="invalid_input")
        self.task_write("task-set-priority", "missing", self.task, error="invalid_input")
        self.call("summaries", "--type", "task", "--priority", "p4", error="invalid_input")
        self.call("summaries", "--type", "record", "--priority", "p2", error="invalid_input")
        self.call("summaries", "--type", "record", "--view", "pending", error="invalid_input")
        self.assertEqual(self.call("snapshot"), before)

    def test_pending_range_and_cursor_use_all_task_filters(self):
        running = self.write("task-create", "active", self.identity, title="进行", priority="p1")["result"]["task"]
        running = self.task_write("task-start", "started", running)["result"]["task"]
        paused = self.write("task-create", "suspended", self.identity, title="暂停", priority="p1")["result"]["task"]
        paused = self.task_write("task-start", "start-suspended", paused)["result"]["task"]
        paused = self.task_write("task-pause", "pause", paused)["result"]["task"]
        self.store("block-paused", [item("BLOCKER", "缺材料", details=BLOCKER_DETAILS)], task=paused)
        cancelled = self.write("task-create", "removed", self.identity, title="取消", priority="p1")["result"]["task"]
        cancelled = self.task_write("task-cancel", "cancel", cancelled, reason="验收样本")["result"]["task"]

        all_numbers = {row["number"] for row in self.list_tasks()}
        self.assertEqual(all_numbers, {self.task["number"], running["number"], paused["number"], cancelled["number"]})
        self.assertEqual({row["number"] for row in self.call("context")["result"]["related_tasks"]}, all_numbers)
        self.assertEqual({row["number"] for row in self.list_tasks("--view", "pending")},
                         all_numbers - {cancelled["number"]})
        self.assertEqual([row["number"] for row in self.list_tasks("--view", "pending", "--blocked",
                                                                 "--display-status", "PAUSED", "--priority", "p1")],
                         [paused["number"]])

        summary = ("summaries", "--type", "task")
        pending = (*summary, "--view", "pending")
        opts = (*pending, "--priority", "p1")
        first = self.call(*opts, "--limit", "1")["result"]
        cursor = first["next_cursor"]
        self.assertIsNotNone(cursor)
        second = self.call(*opts, "--cursor", cursor)["result"]
        self.assertEqual({row["number"] for row in first["items"] + second["items"]},
                         {running["number"], paused["number"]})
        self.assertIsNone(second["next_cursor"])
        self.call(*pending, "--priority", "p2", "--cursor", cursor, error="invalid_input")
        self.call(*summary, "--priority", "p1", "--cursor", cursor, error="invalid_input")
        self.task_write("task-set-priority", "change", running, priority="p3")
        self.call(*opts, "--cursor", cursor, error="cursor_stale")

    def test_range_filters_and_priority_survive_archive_and_reopen(self):
        self.task_write("task-set-priority", "priority", self.task, priority="p1")
        current = self.task_context()["task"]
        evidence = self.store("evidence", [progress("合成记录")], task=current)["result"]["records"][0]
        done = self.task_write("task-complete", "complete", current,
                               completed_scope="合成验收",
                               evidence=[{"type": "RECORD", "target_record_id": evidence["record_id"]}])["result"]["task"]
        self.assertEqual(self.list_tasks("--view", "pending"), [])
        self.assertEqual([row["number"] for row in self.list_tasks("--status", "DONE")], [done["number"]])
        archived = self.task_write("task-archive", "archive", done)["result"]["task"]
        self.assertEqual(self.list_tasks(), [])
        self.assertEqual(self.call("context")["result"]["related_tasks"], [])
        found = self.list_tasks("--view", "archived", "--status", "DONE", "--display-status", "DONE",
                                "--priority", "p1")
        self.assertEqual([row["number"] for row in found], [archived["number"]])
        self.assertEqual(self.list_tasks("--view", "archived", "--status", "DONE", "--priority", "p2"), [])
        for key in (archived["task_id"], archived["number"]):
            self.assertEqual(self.call("context", "--task", key)["result"]["task"]["priority"], "p1")
        self.task_write("task-reopen", "reopen", archived, reason="再核对",
                        evidence=[{"type": "USER_CONFIRMATION", "note": "用户要求再核对"}])
        reopened = self.task_context()["task"]
        self.assertEqual((reopened["status"], reopened["priority"]), ("TODO", "p1"))
        self.store("blocking", [item("BLOCKER", "当前缺材料", details=BLOCKER_DETAILS)], task=reopened)
        found = self.list_tasks("--view", "pending", "--blocked", "--status", "TODO",
                                "--display-status", "BLOCKED", "--priority", "p1")
        self.assertEqual([row["number"] for row in found], [reopened["number"]])
        self.assertEqual(self.list_tasks("--view", "archived"), [])
        self.assertEqual(self.call("context")["result"]["related_tasks"][0]["priority"], "p1")


class LegacyTaskPriorityTest(ContextCase):
    def test_local_reads_and_writes_continue_before_upgrade(self):
        old_cli = source_at(LEGACY, self.root / "historical")
        at = ("--project", str(self.project))
        identity = self.call(*at, "init", data={"name": "旧库"}, cli=old_cli)
        old = self.write("task-create", "old-task", identity, args=at, cli=old_cli,
                         title="旧任务")["result"]["task"]
        self.write("task-create", "requires-upgrade", identity, title="指定优先级", priority="p2",
                   error="upgrade_required")
        self.write("task-set-priority", "requires-upgrade", identity, task_id=old["number"],
                   expected_revision=old["revision"], priority=None, error="upgrade_required")

        opts = ("summaries", "--type", "task", "--view", "pending")
        self.assertEqual([row["priority"] for row in self.call(*opts)["result"]["items"]], [None])
        self.assertEqual(self.call(*opts, "--priority", "p2")["result"]["items"], [])
        self.assertIsNone(self.call("context", "--task", old["number"])["result"]["task"]["priority"])

        another = self.write("task-create", "another", identity, title="旧库继续创建")["result"]["task"]
        self.assertNotIn("priority", another)
        backup = self.root / "pre-priority.sqlite3"
        self.call("backup", "--output", str(backup))
        self.call("upgrade", "--backup", str(backup))

        newest = self.write("task-create", "new-task", identity, title="升级后省略优先级")["result"]["task"]
        explicit = self.write("task-create", "new-null", identity, title="显式未设置", priority=None)["result"]["task"]
        for task in (old, another, newest, explicit):
            self.assertIsNone(self.call("context", "--task", task["number"])["result"]["task"]["priority"])
        self.assertEqual([row["priority"] for row in self.call(*opts)["result"]["items"]], [None] * 4)
        self.assertEqual(self.call(*opts, "--priority", "p2")["result"]["items"], [])
        for task in (old, newest):
            self.write("task-set-priority", f"set-{task['number']}", identity, task_id=task["task_id"],
                       expected_revision=task["revision"], priority="p2")
        self.assertEqual({row["number"] for row in self.call(*opts, "--priority", "p2")["result"]["items"]},
                         {old["number"], newest["number"]})
        for task in (old, newest):
            self.write("task-set-priority", f"clear-{task['number']}", identity, task_id=task["task_id"],
                       expected_revision=task["revision"] + 1, priority=None)
            self.assertIsNone(self.call("context", "--task", task["number"])["result"]["task"]["priority"])
        self.assertEqual(self.call(*opts, "--priority", "p2")["result"]["items"], [])
