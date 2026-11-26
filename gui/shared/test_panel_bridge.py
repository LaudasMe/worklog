"""Run the shared operation bridge from the installed Skill against two projects."""

import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from package_skill import build_package


class PanelBridgeTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(prefix="worklog-adapters-")
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name)
        self.env = {**os.environ, "HOME": str(self.home)}
        with ZipFile(build_package(self.home)) as archive:
            archive.extractall(self.home / ".agents/skills")
        self.scripts = self.home / ".agents/skills/worklog/scripts"
        self.a = self.make_project("Alpha")
        self.b = self.make_project("Beta")

    def invoke(self, script, *args, request=None):
        report = subprocess.run(
            [sys.executable, "-B", str(self.scripts / script), *map(str, args)],
            input=json.dumps(request), env=self.env, text=True, capture_output=True, check=False,
        )
        self.assertTrue(report.stdout, report.stderr)
        return json.loads(report.stdout)

    def cli(self, root, *args, request=None):
        report = self.invoke("worklog.py", "--project", root, *args, request=request)
        self.assertTrue(report["ok"], report)
        return report

    def make_project(self, name):
        root = (self.home / name).resolve()
        root.mkdir()
        response = self.cli(root, "init", request={"name": name})
        return root, {key: response[key] for key in ("project_id", "data_generation")}

    def bridge(self, client, surface, name, arguments, project=None):
        binding = {} if project is None else {"sessionId": project[0].name, "directory": str(project[0])}
        return self.invoke("panel_bridge.py", request={
            "client": client, "surface": surface, "name": name, "arguments": arguments, **binding,
        })["structuredContent"]

    def write(self, client, surface, project, command, task=None, **fields):
        ref = {} if task is None else {"task_id": task["id"], "expected_revision": task["revision"]}
        args = {"command": command, "request": {
            "operation_id": str(uuid4()), **project[1], **ref, **fields,
        }}
        if surface == "dashboard":
            args["projectId"] = project[1]["project_id"]
        return self.bridge(client, surface, "mutate_worklog_task", args, project)

    def ready(self, result):
        self.assertEqual(result["state"], "ready", result)
        return result

    def task(self, project, number):
        return self.cli(project[0], "context", "--task", number)["result"]["task"]

    def card(self, client, project):
        view = self.ready(self.bridge(client, "project", "open_worklog_codex_panel", {}, project))
        task, = view["tasks"]
        return task

    def test_two_clients_write_all_operations_with_identity_and_revision_guards(self):
        for client, project in (("dsh", self.a), ("opencode", self.b)):
            with self.subTest(client=client):
                task = self.ready(self.write(client, "dashboard", project, "task-create", title=client))["task"]
                self.assertEqual(task["number"], "T001")
                task = self.ready(self.write(client, "project", project, "task-start", task))["task"]
                older = task
                task = self.ready(self.write(client, "dashboard", project, "task-set-priority", task, priority="p1"))["task"]
                self.assertEqual(self.task(project, "T001")["priority"], "p1")
                self.assertEqual(self.card(client, project)["priority"], "p1")
                failure = self.write(client, "project", project, "task-set-priority", older, priority=None)
                self.assertEqual(failure["code"], "revision_conflict")
                task = self.ready(self.write(client, "project", project, "task-set-priority", task, priority=None))["task"]
                self.assertIsNone(self.card(client, project)["priority"])
                self.assertIsNone(self.task(project, "T001")["priority"])
                request = self.task(project, task["id"])
                block = self.ready(self.write(
                    client, "project", project, "blocker-set", task,
                    expected_context_position=request["context_position"], blocked_work="Wait", unblock_condition="Ready",
                ))
                self.assertEqual(self.card(client, project)["status"], "BLOCKED")
                request = self.task(project, task["id"])
                self.ready(self.write(
                    client, "dashboard", project, "blocker-resolve", self.card(client, project),
                    expected_context_position=request["context_position"],
                    blocker_id=block["records"][0]["record_id"], outcome_md="Ready",
                ))
                self.assertEqual(self.card(client, project)["status"], "IN_PROGRESS")
                task = self.ready(self.write(client, "project", project, "task-pause", self.card(client, project)))["task"]
                task = self.ready(self.write(client, "dashboard", project, "task-resume", task))["task"]
                record = self.cli(project[0], "record", request={
                    "operation_id": str(uuid4()), **project[1], "scope": "TASK", "task_id": task["id"],
                    "expected_revision": task["revision"], "expected_context_position": self.task(project, task["id"])["context_position"],
                    "records": [{"kind": "PROGRESS", "summary": "Validated", "body_md": "Validated",
                                 "details": {"processed_scope": ["Checks"], "impacts": []}}],
                })
                evidence = [{"type": "RECORD", "target_record_id": record["result"]["records"][0]["record_id"]}]
                task = self.card(client, project)
                self.ready(self.write(client, "dashboard", project, "task-complete", task,
                                      completed_scope="Checks", evidence=evidence))
                self.assertEqual(self.task(project, "T001")["status"], "DONE")
                self.assertEqual(self.card(client, project)["status"], "DONE")

        a, b = self.card("dsh", self.a), self.card("opencode", self.b)
        self.assertNotEqual(a["id"], b["id"])
        error = self.write("dsh", "project", self.b, "task-set-priority", a, priority="p0")
        self.assertEqual(error["code"], "task_not_found")
        self.assertEqual(self.card("dsh", self.a), a)
        self.assertEqual(self.card("opencode", self.b), b)


if __name__ == "__main__":
    unittest.main()
