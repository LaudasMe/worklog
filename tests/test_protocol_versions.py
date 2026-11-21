from test_context import ContextCase, LEGACY, MARKDOWN, SeededContextCase, item, source_at
from test_task_lifecycle_upgrade import BLOCKER_DETAILS, resolved_blocker


V2 = ("--protocol-version", "2.0")


class PublicProtocolTest(SeededContextCase):
    def read_v2(self, *args):
        reply = self.call(*V2, *args)
        self.assertEqual(reply["protocol_version"], "2.0")
        self.assertEqual(self.identity_fields(reply), self.identity_fields(self.identity))
        return reply["result"]

    def changes_page(self, after, *, limit=100):
        return self.read_v2("changes", "--project-id", self.identity["project_id"],
                            "--data-generation", self.identity["data_generation"], "--after", str(after),
                            "--limit", str(limit))

    def act(self, kind, operation, task, **data):
        return self.write(kind, operation, self.identity, task_id=task["number"],
                          expected_revision=task["revision"], **data)["result"]["task"]

    def test_public_reads_default_to_2_and_include_blocker_and_priority_changes(self):
        snapshot = self.read_v2("snapshot")
        number = snapshot["watermark"]
        self.assertEqual(self.read_v2("identity")["name"], "北辰合成项目")
        from_zero, = self.changes_page(0)["changes"]
        self.assertEqual(from_zero["task"], snapshot["tasks"][0])

        reported = [item("BLOCKER", "找材料", details=BLOCKER_DETAILS)]
        receipt = self.store("new-blocker", reported, task=self.task)
        self.assertEqual(receipt, self.store("new-blocker", reported, task=self.task))
        blocker = receipt["result"]["records"][0]["record_id"]
        page = self.changes_page(number)
        self.assertEqual(page["watermark"], number + 1)
        change, = page["changes"]
        self.assertEqual(change["task"]["display_status"], "BLOCKED")
        self.assertEqual(change["task"]["blocker_refs"], [{"command": "record-get", "id": blocker}])
        self.assertEqual(self.read_v2("task-get", "T001"), change["task"])

        other = self.write("task-create", "new-task", self.identity, title="核对优先级", priority="p3")["result"]
        self.act("task-set-priority", "priority", other["task"], priority="p0")
        first = self.changes_page(number, limit=1)
        self.assertEqual(first["changes"], [change])
        self.assertTrue(first["has_more"])
        events = self.changes_page(first["next_after"])["changes"]
        self.assertEqual([event["kind"] for event in events], ["task-create", "task-set-priority"])
        self.assertEqual([change["task"]["priority"] for change in events], ["p3", "p0"])

        claimed = self.write("claim", "public-entry-claim", self.identity, task_id=other["task"]["task_id"],
                             expected_revision=events[-1]["task"]["revision"], client="codex",
                             session_id="public-entry")["result"]["task"]
        entry = self.write("checkpoint", "public-entry", self.identity, task_id=claimed["task_id"],
                           expected_revision=claimed["revision"], run_id=claimed["active_run"]["run_id"],
                           summary="原文核验", body_md=MARKDOWN, next_step="复核")["result"]["entry"]
        changes = ("changes", "--project-id", self.identity["project_id"], "--data-generation",
                   self.identity["data_generation"], "--after", str(number), "--limit", "1")
        for command in (("identity",), ("snapshot",), ("task-get", "T001"), ("entry-get", entry["entry_id"]), changes):
            with self.subTest(command=command[0]):
                default = self.call(*command)
                self.assertEqual(default["protocol_version"], "2.0")
                self.assertEqual(default, self.call(*V2, *command))
                self.call("--protocol-version", "1.0", *command, error="invalid_input")

    def test_v2_paged_relay_rebuilds_archived_task_and_refs(self):
        snapshot = self.read_v2("snapshot")
        positions = {}
        latest = snapshot["watermark"]
        reader_key = ("2.0", self.identity["project_id"], self.identity["data_generation"])
        positions[reader_key] = latest
        task = self.act("task-start", "start", self.task)
        task = self.act("task-pause", "pause", task)
        claim = self.write("claim", "claim", self.identity, task_id=task["task_id"],
                           expected_revision=task["revision"], client="codex", session_id="test")["result"]
        claimed = claim["task"]
        run = claimed["active_run"]["run_id"]
        entry_fields = {"task_id": task["task_id"], "run_id": run, "summary": "核对原文", "body_md": MARKDOWN,
                        "next_step": "打开原文"}
        reported = [item("BLOCKER", "补齐材料", details=BLOCKER_DETAILS)]
        recorded = self.store("block-paused", reported, task=claimed, run_id=run)
        self.assertEqual(self.store("block-paused", reported, task=claimed, run_id=run), recorded)
        saved = recorded["result"]["records"][0]
        fixed = [resolved_blocker(saved["record_id"], [{"type": "USER_CONFIRMATION", "note": "材料已补齐"}])]
        self.store("unblock-paused", fixed, task={**claimed, "context_position": saved["position"]}, run_id=run)
        checkpoint = self.write("checkpoint", "log", self.identity, expected_revision=claimed["revision"],
                                **entry_fields)["result"]
        handoff = self.write("handoff", "handoff", self.identity,
                            expected_revision=checkpoint["task"]["revision"], **entry_fields)["result"]
        task = self.act("task-resume", "resume", handoff["task"])
        task = self.act("task-cancel", "cancel", task, reason="演练取消")
        archived = self.act("task-archive", "first-archive", task)
        task = self.act("task-unarchive", "show", archived)
        task = self.act("task-reopen", "reopen", task, reason="重新核对",
                        evidence=[{"type": "ENTRY", "target_entry_id": checkpoint["entry"]["entry_id"]}])
        self.assertEqual(self.call("task-get", task["number"])["result"]["status"], "TODO")
        for event in (claim, checkpoint, handoff):
            with self.subTest(paused_event=event["kind"]):
                self.assertEqual(event["task"]["status"], "PAUSED")
                public, = self.changes_page(event["seq"] - 1, limit=1)["changes"]
                self.assertEqual(public["change_id"], event["change_id"])
                self.assertEqual(public["task"]["display_status"], "PAUSED")
        task = self.act("task-set-priority", "set", task, priority="p2")
        task = self.act("task-set-priority", "clear", task, priority=None)
        task = self.act("task-complete", "complete", task, completed_scope="演练完成",
                        evidence=[{"type": "ENTRY", "target_entry_id": checkpoint["entry"]["entry_id"]}])
        self.act("task-archive", "archive", task)

        tasks = {record["task_id"]: record for record in snapshot["tasks"]}
        refs = {record["entry_id"]: record for record in snapshot["entry_refs"]}
        seen, applied = set(), []

        def apply_page(events):
            for event in events:
                if event["change_id"] in seen:
                    continue
                seen.add(event["change_id"])
                applied.append(event)
                tasks[event["task"]["task_id"]] = event["task"]
                if event["entry"]:
                    refs[event["entry"]["entry_id"]] = event["entry"]

        while True:
            next_page = self.changes_page(positions[reader_key], limit=2)
            apply_page(next_page["changes"])
            apply_page(next_page["changes"])
            positions[reader_key] = next_page["next_after"]
            if not next_page["has_more"]:
                break
        self.assertGreater(len(applied), 10)
        self.assertEqual([event["seq"] for event in applied], sorted({event["seq"] for event in applied}))
        for event in applied:
            self.assertEqual(set(event), {"seq", "change_id", "kind", "committed_at", "task", "entry"})
        blockers = [change for change in applied if change["kind"] == "task-blockers"]
        self.assertEqual(len(blockers), 2)
        self.assertEqual([item["task"]["revision"] for item in blockers], [claimed["revision"]] * 2)
        self.assertEqual([item["task"]["display_status"] for item in blockers], ["PAUSED"] * 2)
        self.assertEqual(blockers[0]["task"]["blocker_refs"], [{"command": "record-get", "id": saved["record_id"]}])
        self.assertEqual(blockers[-1]["task"]["blocker_refs"], [])
        last = self.read_v2("snapshot")
        self.assertEqual(positions[reader_key], last["watermark"])
        self.assertEqual(tasks, {row["task_id"]: row for row in last["tasks"]})
        self.assertEqual(refs, {row["entry_id"]: row for row in last["entry_refs"]})
        self.assertTrue(tasks[task["task_id"]]["archived"])
        self.assertEqual(self.read_v2("task-get", task["number"]), tasks[task["task_id"]])
        self.assertEqual(self.read_v2("entry-get", checkpoint["entry"]["entry_id"])["body_md"], MARKDOWN)


class MigratedProtocolTest(ContextCase):
    def test_v2_migration_bound_and_version_selection(self):
        old = source_at(LEGACY, self.root / "historical")
        at = ("--project", str(self.project))
        identity = self.call(*at, "init", data={"name": "旧版协议起点"}, cli=old)
        one = self.write("task-create", "one", identity, args=at, cli=old, title="历史任务")["result"]
        guard = ("changes", "--project-id", identity["project_id"], "--data-generation", identity["data_generation"])
        for command in (("identity",), ("task-get", "T001"), ("snapshot",), ("entry-get", "unknown"),
                        (*guard, "--after", "0")):
            self.call(*command, error="upgrade_required")
            self.call(*V2, *command, error="upgrade_required")
        out = self.root / "upgrade.sqlite3"
        self.call("backup", "--output", str(out))
        self.call("upgrade", "--backup", str(out))
        stale = self.call(*V2, *guard, "--after", "0", error="cursor_stale")
        self.assertNotIn("result", stale)
        self.assertEqual(stale["error"]["details"], {"min_after": one["seq"]})
        snap = self.call(*V2, "snapshot")["result"]
        self.assertEqual(snap["watermark"], one["seq"])
        self.assertEqual(self.call(*V2, *guard, "--after", str(one["seq"]))["result"]["changes"], [])
        self.call(*V2, *guard, "--after", str(one["seq"] + 1), error="cursor_ahead")
        new = self.write("task-create", "new", identity, title="schema 4 新任务")["result"]
        page = self.call(*V2, *guard, "--after", str(one["seq"]))["result"]
        self.assertEqual([event["seq"] for event in page["changes"]], [new["seq"]])
        self.call(*V2, "changes", "--project-id", "different", "--data-generation", identity["data_generation"],
                  "--after", str(one["seq"]), error="project_mismatch")
        self.call(*V2, "changes", "--project-id", identity["project_id"], "--data-generation", "different",
                  "--after", str(one["seq"]), error="generation_mismatch")
        self.call(*V2, "context", error="invalid_input")
        self.call("--protocol-version", "3.0", "identity", error="invalid_input")
