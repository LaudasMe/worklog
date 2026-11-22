import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from zipfile import ZipFile

from test_context import isolated_home

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
        self.assertEqual(first.name, "worklog-skill-v0.4.0.zip")
        self.assertEqual(first.read_bytes(), second.read_bytes())
        expected = {
            "worklog/SKILL.md": "skills/worklog/SKILL.md",
            "worklog/INSTALL.md": "docs/skill.md",
            "worklog/references/commands.md": "skills/worklog/references/commands.md",
            "worklog/scripts/schema.sql": "schema.sql",
            "worklog/scripts/schema_context.sql": "schema_context.sql",
            "worklog/scripts/schema_lifecycle.sql": "schema_lifecycle.sql",
            "worklog/scripts/schema_v4.sql": "schema_v4.sql",
            "worklog/scripts/worklog.py": "worklog.py",
            "worklog/scripts/worklog_store.py": "worklog_store.py",
            "worklog/scripts/worklog_context.py": "worklog_context.py",
            "worklog/scripts/worklog_summaries.py": "worklog_summaries.py",
            "worklog/scripts/worklog_upgrade.py": "worklog_upgrade.py",
            "worklog/scripts/worklog_projects.py": "worklog_projects.py",
            "worklog/scripts/worklog_release.py": "worklog_release.py",
            "worklog/scripts/release-version.json": "release-version.json",
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
        version = subprocess.check_output(
            [sys.executable, "-B", str(cli), "--version"],
            cwd=self.directory, text=True, timeout=15,
        )
        self.assertEqual(version.strip(), "workLog CLI protocol 2.0")

        def call(*args, request=None):
            encoded = json.dumps(request, ensure_ascii=False).encode("utf-8")
            result = subprocess.run(
                [sys.executable, "-B", str(cli), "--project", str(project), *args],
                cwd=self.directory, input=encoded, capture_output=True, timeout=15, check=False,
                env=isolated_home(self.directory / "home"),
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
        self.assertEqual(task["protocol_version"], "2.0")
        self.assertEqual(task["result"], {
            **receipt["result"]["task"], "display_status": "TODO", "archived": False,
            "priority": None, "blocker_refs": [],
        })
        status, explicit = call("--protocol-version", "2.0", "task-get", "T001")
        self.assertEqual((status, explicit), (0, task))
        status, retired = call("--protocol-version", "1.0", "task-get", "T001")
        self.assertNotEqual(status, 0)
        self.assertEqual(retired["error"]["code"], "invalid_input")
        self.assertEqual(call("snapshot")[1]["result"]["watermark"], 1)

    def test_version_commands_only_read_the_installation(self):
        installed = self.directory / "installed"
        with ZipFile(self.build(self.directory / "dist")) as archive:
            archive.extractall(installed)
        cli = installed / "worklog/scripts/worklog.py"
        environment = isolated_home(self.directory / "home")
        environment["https_proxy"] = "http://127.0.0.1:1"
        environment["no_proxy"] = ""
        project = self.directory / "project"
        project.mkdir()
        subprocess.run(
            [sys.executable, "-B", str(cli), "init"],
            cwd=project, env=environment, input=b'{"name":"Synthetic project"}',
            capture_output=True, check=True, timeout=10,
        )
        database = project / ".worklog/worklog.sqlite3"

        def files_at(path):
            return {file: file.read_bytes() for file in path.rglob("*") if file.is_file()}

        def version_command(command, cwd):
            return json.loads(subprocess.check_output(
                [sys.executable, str(cli), command], cwd=cwd, env=environment, timeout=15,
            ))

        before_files, before_database = files_at(installed), database.read_bytes()
        version = version_command("release-version", self.directory)
        self.assertEqual(version["protocol_version"], "2.0")
        self.assertEqual(version["result"]["release_version"], "v0.4.0")
        self.assertEqual(version["result"]["cli_path"], str(cli.resolve()))
        self.assertNotIn("project_id", version)
        offline = version_command("check-update", project)["result"]
        self.assertEqual(offline["installed_version"], "v0.4.0")
        self.assertIsNone(offline["latest_version"])
        self.assertEqual(offline["status"], "unknown")
        self.assertTrue(offline["error"])
        self.assertFalse((self.directory / ".worklog").exists())
        self.assertEqual(files_at(installed), before_files)
        self.assertEqual(database.read_bytes(), before_database)

        cli.with_name("release-version.json").unlink()
        unknown = version_command("release-version", self.directory)["result"]
        self.assertIsNone(unknown["release_version"])
        self.assertEqual(unknown["message"], "无法识别")
