"""验证侧栏的项目隔离、只读详情与更新检查。"""

from argparse import Namespace
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

GUI = Path(__file__).resolve().parent
REPO = GUI.parent.parent
sys.path[:0] = [str(REPO), str(GUI / "worklog-panel"), str(GUI.parent / "shared"), str(REPO / "examples")]
from context_seed import seed
from worklog_context import record, record_get
from worklog_store import (
    WorklogError, connect, initialize, metadata, project_db, read_request, task_position, task_row,
)
from project_panel import PanelError, read_handoff, read_history, task_sections, view_project
from server import respond


class ProjectPanelTests(unittest.TestCase):
    def test_read_projects_and_isolate_handoffs(self):
        with TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            first, second, unrelated = (root / name for name in ("first", "second", "unrelated"))
            for path in (first, second, unrelated):
                path.mkdir()
            for path, name in ((first, "北辰合成项目"), (second, "南星合成项目")):
                initialize(project_db(path), {"name": name})
                seed(path, REPO / "worklog.py", alternate=path == second)
            before = hashlib.sha256(project_db(first).read_bytes()).digest()
            subdir = first / "child"
            subdir.mkdir()
            registry = root / "registry.json"
            with closing(connect(project_db(first), "ro")) as db:
                first_id = metadata(db)["project_id"]
            registry.write_text(json.dumps({"projects": [{"path": str(first), "project_id": first_id}]}))

            with patch("project_panel.registry_path", return_value=registry):
                first_view, first_key = view_project(subdir)
                second_view, second_key = view_project(second)
                first_task = first_view["tasks"][0]
                second_task = second_view["tasks"][0]
                self.assertEqual((first_task["number"], second_task["number"]), ("T001", "T001"))
                self.assertNotEqual(first_task["id"], second_task["id"])
                self.assertEqual(first_task["nextStep"], "复查包含零金额的样本。")
                self.assertEqual(first_task["status"], "BLOCKED")
                self.assertEqual(first_view["projectName"], "北辰合成项目")
                self.assertEqual(second_view["projectName"], "南星合成项目")
                self.assertNotIn("body", first_task["handoff"])
                args = (first_task["id"], first_task["handoff"]["id"])
                self.assertIn("请沿用原证据", read_handoff(first, *args, first_key)["body"])
                with self.assertRaises(PanelError):
                    read_handoff(second, *args, second_key)
                with self.assertRaises(PanelError):
                    read_handoff(first, *args, second_key)
                with self.assertRaises(PanelError):
                    read_history(second, second_task["id"], "entry", args[1], second_key)
                with self.assertRaises(PanelError):
                    task_sections(second, args[0], first_key)
                with self.assertRaises(PanelError):
                    view_project(unrelated)
                (unrelated / ".worklog").symlink_to(second / ".worklog")
                with self.assertRaises(PanelError):
                    view_project(unrelated)
                (subdir / ".git").write_text("gitdir: synthetic-only")
                with self.assertRaises(PanelError):
                    view_project(subdir)
                registry.write_text(json.dumps({"projects": [{"path": str(first), "project_id": "wrong"}]}))
                with self.assertRaisesRegex(WorklogError, "does not match"):
                    view_project(first)
            self.assertEqual(hashlib.sha256(project_db(first).read_bytes()).digest(), before)

    def test_threadless_call_never_reads_a_project(self):
        with patch("server.view_project") as read:
            response = respond({"id": 1, "method": "tools/call", "params": {"name": "open_worklog_codex_panel"}}, {})
        self.assertEqual(response["result"]["structuredContent"]["state"], "unavailable")
        self.assertEqual(response["result"]["content"], [])
        read.assert_not_called()

    def test_stdio_connection_stays_bound_to_one_chat(self):
        request = {"id": 1, "method": "tools/call", "params": {"name": "open_worklog_codex_panel", "_meta": {"threadId": "a"}}}
        panel = {}
        view = {"state": "ready", "projectName": "北辰合成项目", "tasks": []}
        with patch("server.view_project", return_value=(view, ("project", "generation"))) as read:
            first = respond(request, panel)
            request["params"]["_meta"]["threadId"] = "b"
            second = respond(request, panel)
        self.assertEqual(first["result"]["structuredContent"], view)
        self.assertEqual(second["result"]["structuredContent"]["state"], "unavailable")
        read.assert_called_once()

    def test_progress_without_next_step_does_not_resurface_old_step(self):
        with TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            initialize(project_db(root), {"name": "北辰合成项目"})
            task_id = seed(root, REPO / "worklog.py", alternate=False)["task_id"]
            with patch("project_panel.registry_path", return_value=root / "test-registry.json"):
                before, _ = view_project(root)
                with closing(connect(project_db(root), "rw")) as db, db:
                    db.execute("BEGIN IMMEDIATE")
                    project = metadata(db)
                    task = task_row(db, task_id)
                    position = db.execute("SELECT MAX(position) FROM context_records WHERE task_id=?", (task_id,)).fetchone()[0]
                    record(db, project, {
                        **{key: project[key] for key in ("project_id", "data_generation")},
                        "operation_id": str(uuid4()), "task_id": task_id, "scope": "TASK",
                        "expected_revision": task["revision"], "expected_context_position": position,
                        "records": [{
                            "kind": "PROGRESS", "summary": "复查已完成", "body_md": "复查已完成。",
                            "details": {"processed_scope": ["复查"], "impacts": []},
                        }],
                    })
                after, _ = view_project(root)
            self.assertTrue(before["tasks"][0]["nextStep"])
            self.assertIsNone(after["tasks"][0]["nextStep"])
            self.assertEqual(after["tasks"][0]["handoff"], before["tasks"][0]["handoff"])

    def test_history_matches_cli_and_keeps_todos_separate(self):
        with TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            database = project_db(root)
            initialize(database, {"name": "北辰合成项目"})
            seeded = seed(root, REPO / "worklog.py", alternate=False)
            task_id = seeded["task_id"]
            with closing(connect(database, "rw")) as db, db:
                db.execute("BEGIN IMMEDIATE")
                project = metadata(db)
                task = task_row(db, task_id)
                common = {key: project[key] for key in ("project_id", "data_generation")}
                recorded = record(db, project, {
                    **common, "operation_id": str(uuid4()), "scope": "PROJECT", "records": [{
                        "kind": "DECISION", "summary": "未关联项目记录", "body_md": "不应进入历史",
                        "details": {}, "refs": [{"type": "USER_CONFIRMATION", "note": "合成记录"}],
                    }],
                })
                unrelated = recorded["records"][0]["record_id"]
                recorded = record(db, project, {
                    **common, "operation_id": str(uuid4()), "scope": "TASK", "task_id": task_id,
                    "expected_revision": task["revision"], "expected_context_position": task_position(db, task_id),
                    "records": [
                        {
                            "kind": "TODO", "summary": "已核对边界清单", "body_md": "合成处理结果",
                            "details": {"status": "RESOLVED", "outcome_md": "已逐条核对"},
                            "replaces_id": seeded["todo_id"],
                            "refs": [{"type": "USER_CONFIRMATION", "note": "合成确认"}],
                        },
                        {"kind": "TODO", "summary": "仍待核对的项目", "body_md": "合成待办", "details": {"status": "OPEN"}},
                    ],
                })

            before = hashlib.sha256(database.read_bytes()).digest()
            with patch("project_panel.registry_path", return_value=root / "test-registry.json"):
                view, project_key = view_project(root)
                detail = task_sections(root, task_id, project_key)
                history = detail["history"]
                indexed = {row["id"]: row for row in history}
                self.assertEqual({row["kind"] for row in history}, {"PROGRESS", "HANDOFF", "DECISION", "STATUS", "BLOCKER"})
                self.assertEqual([row["at"] for row in history], sorted((row["at"] for row in history), reverse=True))
                self.assertIn(view["tasks"][0]["progress"]["summary"], [row["summary"] for row in history])
                self.assertIn(view["tasks"][0]["handoff"]["id"], indexed)
                self.assertTrue(indexed[seeded["old_decision_id"]]["superseded"])
                self.assertTrue(indexed[seeded["current_decision_id"]]["projectRecord"])
                self.assertIn("项目已准备第二份订单样本", [row["summary"] for row in history])
                self.assertNotIn(unrelated, indexed)
                self.assertNotIn(seeded["todo_id"], indexed)
                for item in recorded["records"]:
                    self.assertNotIn(item["record_id"], indexed)
                self.assertEqual(detail["todos"]["open"], [{
                    "summary": "仍待核对的项目", "status": "OPEN", "outcome": None,
                }])
                self.assertEqual(detail["todos"]["processed"], [{
                    "summary": "已核对边界清单", "status": "RESOLVED", "outcome": "已逐条核对",
                }])

                with closing(connect(database, "ro")) as db:
                    for item in history:
                        self.assertNotIn("body", item)
                        original = read_history(root, task_id, item["source"], item["id"], project_key)
                        if item["source"] == "entry":
                            cli = read_request(db, project, "entry-get", Namespace(entry_id=item["id"]))
                            self.assertEqual((item["summary"], original["body"]), (cli["summary"], cli["body_md"]))
                        elif item["source"] == "record":
                            cli = record_get(db, item["id"], root)
                            self.assertEqual((item["summary"], original["body"]), (cli["summary"], cli["body_md"]))
                        else:
                            self.assertIn(item["summary"], original["body"])
                    with self.assertRaises(PanelError):
                        read_history(root, task_id, "record", unrelated, project_key)
                    with self.assertRaises(PanelError):
                        read_history(root, task_id, "record", recorded["records"][0]["record_id"], project_key)
            self.assertEqual(hashlib.sha256(database.read_bytes()).digest(), before)

    def test_schema_check_stops_before_task_reads(self):
        with TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            initialize(project_db(root), {"name": "数据库兼容性验证"})
            request = {"id": 1, "method": "tools/call",
                       "params": {"name": "open_worklog_codex_panel", "_meta": {"threadId": "test-chat"}}}
            with patch("server.Path.cwd", return_value=root), \
                    patch("project_panel.registry_path", return_value=root / "test-registry.json"):
                for version in (3, 5):
                    with closing(connect(project_db(root), "rw")) as db:
                        db.execute(f"PRAGMA user_version = {version}")
                    before = hashlib.sha256(project_db(root).read_bytes()).digest()
                    with patch("project_panel.project_tasks") as read_tasks:
                        result = respond(request, {})["result"]["structuredContent"]
                    self.assertEqual(result["state"], "unavailable")
                    self.assertEqual(result["schemaVersion"], version)
                    self.assertEqual(result["supportedSchemas"], [4])
                    read_tasks.assert_not_called()
                    self.assertEqual(hashlib.sha256(project_db(root).read_bytes()).digest(), before)

    def test_checks_use_separate_components_without_opening_the_project(self):
        panel = {}

        def call(component, thread_id="test-chat"):
            request = {"id": 2, "method": "tools/call", "params": {
                "name": "check_worklog_update", "arguments": {"component": component},
                "_meta": {"threadId": thread_id},
            }}
            return respond(request, panel)["result"]["structuredContent"]

        def check(component, installed):
            error = component == "codex" and check_version.call_count == 1
            return {
                "status": "unknown" if error or installed is None else "ahead",
                "installed_version": installed,
                "latest_version": None if error else {"codex": "gui-codex-v0.0.1", "cli": "v0.3.0"}[component],
                "error": "GitHub Releases 请求失败" if error else None,
            }

        with TemporaryDirectory() as folder:
            manifest = Path(folder) / "release-version.json"
            manifest.write_text('{"version":"gui-codex-v0.1.0","bundled_cli_version":"v0.4.0"}')
            with patch("server.GUI_MANIFEST", manifest), patch("server.view_project") as read, \
                    patch("server.current_skill_version", side_effect=["v0.5.0", None]) as skill, \
                    patch("server.check_update", side_effect=check) as check_version:
                self.assertEqual(call("codex")["error"], "GitHub Releases 请求失败")
                self.assertEqual(call("cli")["installed_version"], "v0.5.0")
                self.assertEqual(call("codex")["installed_version"], "gui-codex-v0.1.0")
                unavailable_cli = call("cli")
                self.assertEqual((unavailable_cli["installed_version"], unavailable_cli["status"]), (None, "unknown"))
                self.assertEqual(unavailable_cli["latest_version"], "v0.3.0")
                self.assertEqual(call("cli", "other-chat")["state"], "unavailable")
                self.assertEqual([args.args for args in skill.call_args_list], [("test-chat",), ("test-chat",)])
                read.assert_not_called()


if __name__ == "__main__":
    unittest.main()
