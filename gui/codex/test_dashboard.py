"""Dashboard 与项目侧栏共用的 MCP 数据和任务身份契约。"""

from contextlib import closing
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

GUI = Path(__file__).resolve().parent
sys.path[:0] = [str(GUI.parent.parent), str(GUI / "worklog-panel"), str(GUI.parent / "shared")]

from panel_tools import BLOCKERS, HISTORY, MUTATE, SECTIONS
from server import CONTINUE, DASHBOARD, TOOL, respond
from worklog_context import record, record_get
from worklog_store import connect, initialize, metadata, open_run, project_db, snapshot, task_position, task_row, write_request


class DashboardTests(unittest.TestCase):
    def setUp(self):
        scratch = TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name).resolve()
        self.registry = self.root / "projects.json"
        self.alpha, self.beta = [self.root / name for name in ("甲 项目", "乙 项目")]
        self.keys, self.tasks = {}, {}
        for root in (self.alpha, self.beta):
            root.mkdir()
            key, _ = initialize(project_db(root), {"name": root.name})
            self.keys[root] = key
            self.tasks[root] = self.write(root, "task-create", title=root.name)["task"]["task_id"]
        self.write_registry()
        registry = patch("project_panel.registry_path", return_value=self.registry)
        registry.start()
        self.addCleanup(registry.stop)
        self.global_panel = {}
        self.thread_panel = {}

    def write_registry(self, additional=()):
        projects = [{"path": str(root), "project_id": key["project_id"]} for root, key in self.keys.items()]
        self.registry.write_text(json.dumps({"projects": projects + list(additional)}), encoding="utf-8")

    def write(self, root, command, **fields):
        with closing(connect(project_db(root), "rw")) as db, db:
            db.execute("BEGIN IMMEDIATE")
            project = metadata(db)
            task_id = self.tasks.get(root)
            guards = {}
            if task_id:
                guards = {"task_id": task_id, "expected_revision": task_row(db, task_id)["revision"]}
            request = {**guards, **fields, "operation_id": str(uuid4()),
                       **{k: project[k] for k in ("project_id", "data_generation")}}
            if command == "record":
                active = open_run(db, task_id)
                if active:
                    request["run_id"] = active["run_id"]
                return record(db, project, {
                    **request, "scope": "TASK", "expected_context_position": task_position(db, task_id),
                })
            return write_request(db, project, command, request)

    def handoff(self, root, source, summary, step):
        run = self.write(root, "claim", client="codex", session_id=source)["task"]["active_run"]
        self.write(root, "handoff", run_id=run["run_id"], summary=summary, body_md=summary, next_step=step)

    def progress(self, root, source, summary, step):
        self.write(root, "record", client="codex", session_id=source, records=[{
            "kind": "PROGRESS", "summary": summary, "body_md": summary,
            "details": {"processed_scope": ["样本"], "impacts": [], "suggested_next_step": step},
        }])

    def call(self, tool, args=None, *, folder=None):
        params = {"name": tool["name"], "arguments": args or {}}
        if folder is not None:
            params["_meta"] = {"threadId": "viewing-chat"}
        with patch("server.Path.cwd", return_value=folder or self.root):
            return respond(
                {"id": 1, "method": "tools/call", "params": params},
                self.thread_panel if folder is not None else self.global_panel,
            )["result"]["structuredContent"]

    def by_project(self):
        return {project["path"]: project for project in self.call(DASHBOARD)["projects"]}

    def fingerprints(self):
        files = self.registry, project_db(self.alpha), project_db(self.beta)
        return [sha256(file.read_bytes()).hexdigest() for file in files]

    def test_global_directory_independence_and_read_only(self):
        wrong = self.root / "错误身份"
        wrong.mkdir()
        initialize(project_db(wrong), {"name": "身份不符"})
        self.write_registry([
            {"path": str(wrong), "project_id": "wrong"},
            {"path": str(self.root / "已移除目录"), "project_id": "missing"},
        ])
        before = self.fingerprints()
        views = self.by_project()
        alpha, beta = (views[str(folder)]["tasks"][0] for folder in (self.alpha, self.beta))
        self.assertEqual((alpha["number"], beta["number"]), ("T001", "T001"))
        self.assertNotEqual(alpha["id"], beta["id"])
        self.assertIsNone(alpha["progress"])
        self.assertEqual(views[str(wrong)]["code"], "project_mismatch")
        self.assertEqual(views[str(self.root / "已移除目录")]["code"], "invalid_project_path")
        for folder in (self.root, self.alpha, self.beta, self.alpha):
            self.assertEqual(self.call(DASHBOARD, folder=folder)["projects"], list(views.values()))
        self.assertEqual(self.call(TOOL, folder=self.alpha)["tasks"][0], alpha)
        self.assertEqual(self.fingerprints(), before)

    def test_progress_precedence_handoff_fallback_and_terminal_next_step(self):
        self.handoff(self.beta, "beta-chat", "乙只有交接", "乙交接时下一步")
        self.progress(self.alpha, "alpha-progress", "甲普通进展", "甲进展时下一步")
        self.handoff(self.alpha, "alpha-handoff", "甲更新的交接", "甲当前下一步")
        views = self.by_project()
        a, b = (views[str(folder)]["tasks"][0] for folder in (self.alpha, self.beta))
        self.assertEqual(a["progress"]["summary"], "甲普通进展")
        self.assertEqual(a["progress"]["sessionId"], "alpha-progress")
        self.assertEqual(a["nextStep"], "甲当前下一步")
        self.assertEqual(b["progress"]["summary"], "乙只有交接")
        with closing(connect(project_db(self.alpha), "ro")) as db:
            before = snapshot(db)
        self.progress(self.alpha, "alpha-progress", "甲刷新后进展", "甲刷新后下一步")
        with closing(connect(project_db(self.alpha), "ro")) as db:
            self.assertEqual(snapshot(db), before)
        updated = self.by_project()[str(self.alpha)]["tasks"][0]
        self.assertEqual(updated, self.call(TOOL, folder=self.alpha)["tasks"][0])
        self.assertEqual(updated["progress"]["summary"], "甲刷新后进展")
        self.assertEqual(updated["nextStep"], "甲刷新后下一步")
        self.assertEqual(updated["handoff"]["nextStep"], "甲当前下一步")
        self.write(self.alpha, "task-cancel", reason="隔离样本")
        self.assertIsNone(self.by_project()[str(self.alpha)]["tasks"][0]["nextStep"])

    def test_draft_targets_both_same_number_tasks_and_session_dedup(self):
        self.handoff(self.alpha, "chat-A", "甲交接", "复测")
        self.progress(self.alpha, "chat-A", "A 的进展", "复测")
        self.by_project()
        self.call(TOOL, folder=self.beta)
        before = self.fingerprints()
        for root, folder in ((self.alpha, None), (self.beta, self.beta)):
            args = {"taskId": self.tasks[root]}
            if folder is None:
                args["projectId"] = self.keys[root]["project_id"]
            result = self.call(CONTINUE, args, folder=folder)
            parts = urlsplit(result["url"])
            self.assertEqual((parts.scheme, parts.netloc, parts.path), ("codex", "threads", "/new"))
            params = parse_qs(parts.query)
            self.assertEqual(params["path"], [str(root)])
            self.assertIn(self.keys[root]["project_id"], params["prompt"][0])
            self.assertIn(self.keys[root]["data_generation"], params["prompt"][0])
            self.assertIn(self.tasks[root], params["prompt"][0])
            self.assertIn("T001", params["prompt"][0])
            self.assertIn("授权本聊天持续跟进这项任务", params["prompt"][0])
            self.assertIn("明确确认替换后再写入", params["prompt"][0])
        self.assertEqual(self.fingerprints(), before)
        args = {"taskId": self.tasks[self.alpha], "projectId": self.keys[self.beta]["project_id"]}
        self.assertEqual(self.call(CONTINUE, args)["state"], "unavailable")
        self.write(self.alpha, "claim", client="codex", session_id="chat-B")
        self.progress(self.alpha, "chat-B", "B 的进展", "复测")
        self.call(TOOL, folder=self.alpha)
        args = {"taskId": self.tasks[self.alpha]}
        detail = self.call(SECTIONS, args, folder=self.alpha)
        self.assertEqual(detail["sessions"], [{"client": "codex", "sessionId": s} for s in ("chat-A", "chat-B")])
        self.assertEqual({item["sessionId"] for item in detail["history"] if item.get("sessionId")}, {"chat-A", "chat-B"})
        self.write(self.beta, "task-cancel", reason="候选测试")
        self.assertEqual(self.call(CONTINUE, {"taskId": self.tasks[self.beta], "projectId": self.keys[self.beta]["project_id"]})["code"], "terminal_task")

    def test_reopen_restores_effective_todo_and_retains_completion_history(self):
        self.progress(self.alpha, "alpha-progress", "完成依据", "复测")
        wrote = self.write(self.alpha, "record", records=[{
            "kind": "TODO", "summary": "待核对", "body_md": "待核对", "details": {"status": "OPEN"},
        }])
        todo = wrote["records"][0]["record_id"]
        with closing(connect(project_db(self.alpha), "ro")) as db:
            progress = db.execute(
                "SELECT record_id FROM context_records WHERE task_id = ? AND kind = 'PROGRESS'",
                (self.tasks[self.alpha],),
            ).fetchone()["record_id"]
        completed = self.write(
            self.alpha, "task-complete", completed_scope="已完成范围",
            evidence=[{"type": "RECORD", "target_record_id": progress}],
            todo_dispositions={todo: "本次处置"},
        )
        self.call(TOOL, folder=self.alpha)
        args = {"taskId": self.tasks[self.alpha]}
        detail = self.call(SECTIONS, args, folder=self.alpha)
        self.assertEqual(detail["todos"]["processed"][0]["outcome"], "本次处置")
        original = self.call(HISTORY, {
            **args, "source": "change", "recordId": completed["change_id"],
        }, folder=self.alpha)["body"]
        for value in ("已完成范围", progress, "本次处置"):
            self.assertIn(value, original)
        self.write(self.alpha, "task-reopen", reason="复核", evidence=[{
            "type": "USER_CONFIRMATION", "note": "复核",
        }])
        detail = self.call(SECTIONS, args, folder=self.alpha)
        self.assertEqual(detail["todos"]["open"][0]["summary"], "待核对")
        self.assertEqual(detail["todos"]["processed"], [])

    def test_both_entries_distinguish_disposed_and_resolved_todos_on_done_task(self):
        original = self.write(self.alpha, "record", records=[
            {"kind": "PROGRESS", "summary": "初验通过", "body_md": "初验原文",
             "details": {"processed_scope": ["初验"], "impacts": []}},
            {"kind": "TODO", "summary": "转交校对", "body_md": "校对原文", "details": {"status": "OPEN"}},
            {"kind": "TODO", "summary": "复测通过", "body_md": "复测原文", "details": {"status": "OPEN"}},
            {"kind": "TODO", "summary": "留待二次核对", "body_md": "二次核对原文", "details": {"status": "OPEN"}},
        ])["records"]
        proof, transfer, check, retained = [item["record_id"] for item in original]
        evidence = [{"type": "RECORD", "target_record_id": proof}]
        dispositions = {transfer: "等待交接", check: "等待复测", retained: "留在本任务"}
        completed = self.write(self.alpha, "task-complete", completed_scope="初次验收范围",
                               evidence=evidence, todo_dispositions=dispositions)
        self.write(self.alpha, "record", records=[
            {"kind": "TODO", "summary": "转交校对", "body_md": "同意转交", "replaces_id": transfer, "refs": evidence,
             "details": {"status": "RESOLVED", "outcome_kind": "DISPOSED", "outcome_md": "已转交 T002"}},
            {"kind": "TODO", "summary": "复测通过", "body_md": "完成实测", "replaces_id": check, "refs": evidence,
             "details": {"status": "RESOLVED", "outcome_md": "两个用例均通过"}},
        ])

        self.by_project()
        self.call(TOOL, folder=self.alpha)
        task = {"taskId": self.tasks[self.alpha]}
        targets = ((None, {**task, "projectId": self.keys[self.alpha]["project_id"]}), (self.alpha, task))
        before = self.fingerprints()
        for folder, args in targets:
            with self.subTest(folder=folder):
                detail = self.call(SECTIONS, args, folder=folder)
                self.assertEqual(detail["todos"]["open"], [])
                processed = {item["summary"]: (item["status"], item["outcome"])
                             for item in detail["todos"]["processed"]}
                self.assertEqual(processed, {
                    "转交校对": ("DISPOSED", "已转交 T002"),
                    "复测通过": ("RESOLVED", "两个用例均通过"),
                    "留待二次核对": ("DISPOSED", "留在本任务"),
                })
                history = self.call(HISTORY, {
                    **args, "source": "change", "recordId": completed["change_id"],
                }, folder=folder)
                for original_value in ("初次验收范围", proof, *dispositions.values()):
                    self.assertIn(original_value, history["body"])
        self.assertEqual(self.fingerprints(), before)

    def test_mcp_writes_and_retries_keep_the_two_same_number_tasks_separate(self):
        self.by_project()
        thread_view = self.call(TOOL, folder=self.beta)
        self.assertEqual(thread_view["tasks"][0]["number"], "T001")
        project = self.keys[self.alpha]
        args = {"projectId": project["project_id"], "command": "task-create", "request": {
            "operation_id": str(uuid4()), "project_id": project["project_id"],
            "data_generation": project["data_generation"], "title": "  从总览添加  ",
        }}
        created = self.call(MUTATE, args)
        self.assertEqual(created["task"]["number"], "T002")
        self.assertEqual(created, self.call(MUTATE, args))
        beta = self.keys[self.beta]
        args = {"command": "task-start", "request": {
            "operation_id": str(uuid4()), "project_id": beta["project_id"],
            "data_generation": beta["data_generation"], "task_id": self.tasks[self.beta],
            "expected_revision": thread_view["tasks"][0]["revision"],
        }}
        started = self.call(MUTATE, args, folder=self.beta)
        self.assertEqual(started["task"]["status"], "IN_PROGRESS")
        self.assertEqual(started, self.call(MUTATE, args, folder=self.beta))
        current = self.by_project()
        self.assertEqual([task["title"] for task in current[str(self.alpha)]["tasks"]], ["甲 项目", "从总览添加"])
        self.assertEqual(current[str(self.alpha)]["tasks"][0]["status"], "TODO")
        self.assertEqual(current[str(self.beta)]["tasks"][0]["status"], "IN_PROGRESS")
        args["request"]["operation_id"] = str(uuid4())
        self.assertEqual(self.call(MUTATE, args, folder=self.beta)["code"], "revision_conflict")

    def blocker_target(self, folder=None):
        project = self.call(TOOL, folder=self.alpha) if folder else self.by_project()[str(self.alpha)]
        task = project["tasks"][0]
        scope = {} if folder else {"projectId": project["id"]}
        options = self.call(BLOCKERS, {
            **scope, "taskId": task["id"], "expectedRevision": task["revision"],
        }, folder=folder)
        return scope, task, options

    def test_priority_updates_in_both_panels_and_cli_share_the_project_and_revision_guards(self):
        views = self.by_project()
        alpha, beta = views[str(self.alpha)], views[str(self.beta)]
        task = self.call(TOOL, folder=self.alpha)["tasks"][0]
        self.assertIsNone(task["priority"])
        request = {
            "operation_id": str(uuid4()), "project_id": alpha["id"], "data_generation": alpha["generation"],
            "task_id": task["id"], "expected_revision": task["revision"], "priority": "p1",
        }
        args = {"projectId": alpha["id"], "command": "task-set-priority", "request": request}
        saved = self.call(MUTATE, args)
        self.assertEqual(saved["state"], "ready")
        self.assertEqual(self.call(MUTATE, args), saved)
        for view in (self.by_project()[str(self.alpha)], self.call(TOOL, folder=self.alpha)):
            self.assertEqual(view["tasks"][0]["priority"], "p1")
        self.assertIsNone(self.by_project()[str(self.beta)]["tasks"][0]["priority"])

        def cli(command, *arguments, request=None):
            return json.loads(subprocess.check_output(
                [sys.executable, "-B", str(GUI.parent.parent / "worklog.py"), "--project",
                 str(self.alpha), command, *arguments],
                input=json.dumps(request), text=True,
            ))

        cleared = dict(request, operation_id=str(uuid4()), priority=None)
        self.assertEqual(self.call(MUTATE, {"command": "task-set-priority", "request": cleared},
                                   folder=self.alpha)["code"], "revision_conflict")
        self.assertEqual(cli("context", "--task", task["id"])["result"]["task"]["priority"], "p1")
        cleared["expected_revision"] = saved["task"]["revision"]
        self.assertEqual(self.call(MUTATE, {"command": "task-set-priority", "request": cleared},
                                   folder=self.alpha)["state"], "ready")
        current = cli("context", "--task", task["id"])["result"]["task"]
        self.assertIsNone(current["priority"])
        self.assertTrue(cli("task-set-priority", request=dict(
            request, operation_id=str(uuid4()), priority="p3", expected_revision=current["revision"],
        ))["ok"])
        self.assertEqual(self.call(TOOL, folder=self.alpha)["tasks"][0]["priority"], "p3")
        current = self.by_project()
        self.assertEqual(current[str(self.alpha)]["tasks"][0]["priority"], "p3")
        self.assertEqual(current[str(self.beta)]["tasks"], beta["tasks"])

    def test_entry_titles_and_continue_draft_use_their_selected_languages(self):
        for host, system, expected in (
            ("zh-Hans", "en_US.UTF-8", ("workLog 全局总览", "workLog 项目侧栏")),
            ("en-US", "zh_CN.UTF-8", ("workLog Dashboard", "workLog Project Sidebar")),
            (None, "zh_CN.UTF-8", ("workLog 全局总览", "workLog 项目侧栏")),
            ("ja-JP", "zh_CN.UTF-8", ("workLog Dashboard", "workLog Project Sidebar")),
        ):
            panel = {}
            with patch.dict(os.environ, {"LC_ALL": system}):
                respond({"id": 1, "method": "initialize", "params": {"_meta": {"locale": host}}}, panel)
                result = respond({"id": 2, "method": "tools/list"}, panel)["result"]["tools"]
            titles = {tool["name"]: tool["title"] for tool in result}
            self.assertEqual((titles[DASHBOARD["name"]], titles[TOOL["name"]]), expected)

        dashboard = self.by_project()[str(self.alpha)]
        self.call(TOOL, folder=self.alpha)
        for folder in (None, self.alpha):
            target = {} if folder else {"projectId": dashboard["id"]}
            draft = self.call(CONTINUE, {
                **target, "taskId": self.tasks[self.alpha], "locale": "en",
            }, folder=folder)
            query = parse_qs(urlsplit(draft["url"]).query)
            self.assertEqual(query["path"], [str(self.alpha)])
            self.assertIn("Use the workLog Skill to continue T001", query["prompt"][0])
            self.assertIn(dashboard["generation"], query["prompt"][0])
            self.assertIn(self.tasks[self.alpha], query["prompt"][0])

    def blocker_request(self, target, command="blocker-set", **fields):
        scope, task, options = target
        project = self.keys[self.alpha]
        if options.get("runId"):
            fields.setdefault("run_id", options["runId"])
        return {**scope, "command": command, "request": {
            "operation_id": str(uuid4()), "project_id": project["project_id"],
            "data_generation": project["data_generation"], "task_id": task["id"],
            "expected_revision": task["revision"], "expected_context_position": options.get("contextPosition", 0),
            **fields,
        }}

    def test_blockers_across_surfaces_keep_history_and_restore_base_status(self):
        for state in ("TODO", "IN_PROGRESS"):
            with self.subTest(base=state):
                if state == "IN_PROGRESS":
                    self.write(self.alpha, "task-start")
                ids = []
                for folder, label in ((None, "样本一"), (self.alpha, "样本二")):
                    target = self.blocker_target(folder)
                    before = self.fingerprints()
                    invalid = self.blocker_request(target, blocked_work=label, unblock_condition=" ")
                    self.assertEqual(self.call(MUTATE, invalid, folder=folder)["code"], "invalid_input")
                    self.assertEqual(self.fingerprints(), before)
                    request = self.blocker_request(target, blocked_work=label, unblock_condition="样本到齐")
                    wrote = self.call(MUTATE, request, folder=folder)
                    self.assertEqual(wrote, self.call(MUTATE, request, folder=folder))
                    ids.append(wrote["records"][0]["record_id"])
                    stale = self.blocker_request(target, blocked_work="过期提交", unblock_condition="输入已变化")
                    self.assertEqual(self.call(MUTATE, stale, folder=folder)["code"], "revision_conflict")
                for index, folder in enumerate((self.alpha, None)):
                    for surface in (None, self.alpha):
                        _, task, options = self.blocker_target(surface)
                        self.assertEqual((task["status"], task["lifecycle"]), ("BLOCKED", state))
                        self.assertEqual({row["id"] for row in options["blockers"]}, set(ids[index:]))
                    target = self.blocker_target(folder)
                    before = self.fingerprints()
                    invalid = self.blocker_request(target, "blocker-resolve", blocker_id=ids[index], outcome_md=" ")
                    self.assertEqual(self.call(MUTATE, invalid, folder=folder)["code"], "invalid_input")
                    self.assertEqual(self.fingerprints(), before)
                    request = self.blocker_request(target, "blocker-resolve", blocker_id=ids[index], outcome_md="已确认样本到齐")
                    result = self.call(MUTATE, request, folder=folder)
                    self.assertEqual(result, self.call(MUTATE, request, folder=folder))
                    resolution = result["records"][0]["record_id"]
                    args = {**target[0], "taskId": target[1]["id"]}
                    detail = self.call(SECTIONS, args, folder=folder)
                    entries = {row["id"]: row for row in detail["history"]}
                    self.assertTrue(entries[ids[index]]["superseded"])
                    expected_source = ("codex", "viewing-chat") if folder else ("codex-dashboard", "global")
                    self.assertEqual((entries[resolution]["client"], entries[resolution]["sessionId"]), expected_source)
                    original = self.call(HISTORY, {
                        **args, "recordId": resolution, "source": "record",
                    }, folder=folder)
                    self.assertEqual(original["body"], "已确认样本到齐")
                    with closing(connect(project_db(self.alpha), "ro")) as db:
                        saved = record_get(db, resolution, self.alpha)
                        self.assertEqual(saved["replaces_id"], ids[index])
                        self.assertEqual(saved["refs"], [{"type": "USER_CONFIRMATION", "note": "已确认样本到齐"}])
                for surface in (None, self.alpha):
                    _, task, options = self.blocker_target(surface)
                    self.assertEqual((task["status"], task["lifecycle"]), (state, state))
                    self.assertEqual(options["blockers"], [])
                self.assertEqual(self.by_project()[str(self.beta)]["tasks"][0]["status"], "TODO")

    def test_manual_blockers_require_handoff_and_fresh_run(self):
        own = self.write(self.alpha, "claim", client="codex", session_id="viewing-chat")["task"]["active_run"]
        old = self.blocker_target(self.alpha)
        self.assertEqual(old[2]["runId"], own["run_id"])
        request = self.blocker_request(old, blocked_work="原阻塞", unblock_condition="样本到齐")
        blocker_id = self.call(MUTATE, request, folder=self.alpha)["records"][0]["record_id"]
        self.write(self.alpha, "claim", client="codex", session_id="another-chat", replaces_run_id=own["run_id"])
        before = self.fingerprints()
        for surface in (None, self.alpha):
            target = self.blocker_target(surface)
            self.assertEqual(target[2]["code"], "handoff_required")
            for command, fields in (
                ("blocker-set", {"blocked_work": "另一条", "unblock_condition": "无法写入"}),
                ("blocker-resolve", {"blocker_id": blocker_id, "outcome_md": "无法写入"}),
            ):
                denied = self.blocker_request(target, command, **fields)
                self.assertEqual(self.call(MUTATE, denied, folder=surface)["code"], "handoff_required")
        self.assertEqual(before, self.fingerprints())
        with closing(connect(project_db(self.alpha), "ro")) as db:
            run_id = open_run(db, self.tasks[self.alpha])["run_id"]
        self.write(self.alpha, "handoff", run_id=run_id, summary="交接", body_md="交接依据", next_step="核对")
        for surface in (None, self.alpha):
            target = self.blocker_target(surface)
            self.assertIsNone(target[2]["runId"])
            stale = self.blocker_request(target, "blocker-resolve", blocker_id=blocker_id,
                                         outcome_md="旧接手无效", run_id=own["run_id"])
            self.assertEqual(self.call(MUTATE, stale, folder=surface)["code"], "stale_run")
        fresh = self.blocker_request(self.blocker_target(), "blocker-resolve", blocker_id=blocker_id, outcome_md="已交接")
        self.assertEqual(self.call(MUTATE, fresh)["state"], "ready")
        self.assertEqual(self.blocker_target(self.alpha)[1]["status"], "IN_PROGRESS")

    def test_empty_malformed_and_removed_registry(self):
        self.by_project()
        self.registry.unlink()
        self.assertEqual(self.call(DASHBOARD)["projects"], [])
        self.registry.write_text('{"projects":[]}', encoding="utf-8")
        self.assertEqual(self.call(DASHBOARD)["projects"], [])
        self.registry.write_text("{", encoding="utf-8")
        self.assertEqual(self.call(DASHBOARD)["code"], "invalid_registry")
        self.write_registry()
        self.by_project()
        self.registry.write_text('{"projects":[]}', encoding="utf-8")
        project_id = self.keys[self.alpha]["project_id"]
        request = {"projectId": project_id, "command": "task-create", "request": {
            "operation_id": str(uuid4()), "project_id": project_id,
            "data_generation": self.keys[self.alpha]["data_generation"], "title": "不得写入",
        }}
        self.assertEqual(self.call(MUTATE, request)["code"], "project_mismatch")
        self.assertEqual(self.call(TOOL, folder=self.root)["code"], "not_initialized")

    def test_paused_task_can_resolve_existing_blocker_but_cannot_add_one(self):
        self.write(self.alpha, "task-start")
        request = self.blocker_request(
            self.blocker_target(), blocked_work="待观测", unblock_condition="有测试结果"
        )
        initial = self.call(MUTATE, request)["records"][0]["record_id"]
        self.write(self.alpha, "task-pause")
        target = self.blocker_target(self.alpha)
        self.assertEqual((target[1]["status"], target[1]["blocked"]), ("PAUSED", True))
        self.assertEqual([row["id"] for row in target[2]["blockers"]], [initial])
        disallowed = self.blocker_request(target, blocked_work="新阻塞", unblock_condition="有输入")
        self.assertEqual(self.call(MUTATE, disallowed, folder=self.alpha)["code"], "task_state_conflict")
        resolve = self.blocker_request(target, "blocker-resolve", blocker_id=initial, outcome_md="已有测试结果")
        self.assertEqual(self.call(MUTATE, resolve, folder=self.alpha)["state"], "ready")
        _, task, options = self.blocker_target()
        self.assertEqual((task["status"], task["blocked"], options["blockers"]), ("PAUSED", False, []))


if __name__ == "__main__":
    unittest.main()
