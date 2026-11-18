import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "scripts" / "package_skill.py"


class SkillPackageTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="worklog-skill-")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def build(self, output):
        path = subprocess.check_output(
            [sys.executable, "-B", str(BUILDER), "--output-dir", str(output)],
            cwd=self.directory, text=True, timeout=15,
        ).strip()
        return Path(path)

    def test_package_reproducible_and_only_contains_tracked_runtime_sources(self):
        first = self.build(self.directory / "first")
        second = self.build(self.directory / "second")
        self.assertEqual(first.name, "worklog-skill-v0.1.0.zip")
        self.assertEqual(first.read_bytes(), second.read_bytes())
        expected = {
            "worklog/SKILL.md": "skills/worklog/SKILL.md",
            "worklog/INSTALL.md": "docs/skill.md",
            "worklog/references/commands.md": "skills/worklog/references/commands.md",
            "worklog/scripts/schema.sql": "schema.sql",
            "worklog/scripts/worklog.py": "worklog.py",
            "worklog/scripts/worklog_store.py": "worklog_store.py",
        }
        with ZipFile(first) as archive:
            self.assertIsNone(archive.testzip())
            self.assertCountEqual(archive.namelist(), expected)
            for member, source in expected.items():
                self.assertEqual(archive.read(member), (ROOT / source).read_bytes())

    def test_cli_works_after_install_from_another_directory(self):
        installed = self.directory / "caller/.agents/skills"
        project = self.directory / "另一个项目"
        project.mkdir()
        with ZipFile(self.build(self.directory / "dist")) as archive:
            archive.extractall(installed)
        cli = installed / "worklog/scripts/worklog.py"

        def call(command, *args, request=None):
            encoded = json.dumps(request, ensure_ascii=False).encode("utf-8")
            result = subprocess.run(
                [sys.executable, "-B", str(cli), "--project", str(project), command, *args],
                cwd=self.directory, input=encoded, capture_output=True, timeout=15, check=False,
            )
            self.assertEqual(result.stderr, b"")
            return result.returncode, json.loads(result.stdout)

        status, absent = call("identity")
        self.assertEqual(status, 3)
        self.assertEqual(absent["error"]["code"], "not_initialized")
        self.assertEqual(list(project.iterdir()), [])

        status, identity = call("init", request={"name": "安装包验收"})
        self.assertEqual(status, 0)
        request = {
            "project_id": identity["project_id"], "data_generation": identity["data_generation"],
            "operation_id": "make-task", "title": "已安装 CLI",
        }
        status, receipt = call("task-create", request=request)
        self.assertEqual(status, 0)
        status, task = call("task-get", "T001")
        self.assertEqual(status, 0)
        self.assertEqual(task["protocol_version"], "1.0")
        self.assertEqual(task["result"], receipt["result"]["task"])
        self.assertEqual(call("snapshot")[1]["result"]["watermark"], 1)
