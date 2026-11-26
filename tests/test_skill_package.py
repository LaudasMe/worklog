from hashlib import sha256
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
RELEASE_VERSION = json.loads((ROOT / "release-version.json").read_bytes())["version"]


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
        self.assertEqual(first.name, f"worklog-skill-{RELEASE_VERSION}.zip")
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
            "worklog/scripts/panel_assets.py": "gui/shared/panel_assets.py",
            "worklog/scripts/panel_bridge.py": "gui/shared/panel_bridge.py",
            "worklog/scripts/panel_tools.py": "gui/shared/panel_tools.py",
            "worklog/scripts/project_panel.py": "gui/shared/project_panel.py",
            "worklog/scripts/task_actions.py": "gui/shared/task_actions.py",
            "worklog/gui/panel.html": "gui/shared/panel.html",
        }
        with ZipFile(first) as archive:
            self.assertIsNone(archive.testzip())
            self.assertCountEqual(archive.namelist(), [*expected, "worklog/gui-resource.json"])
            for member, source in expected.items():
                self.assertEqual(archive.read(member), (ROOT / source).read_bytes())
            self.assertEqual(json.loads(archive.read("worklog/gui-resource.json")), {
                "version": 1, "panelSha256": sha256(archive.read("worklog/gui/panel.html")).hexdigest(),
            })
            instructions = archive.read("worklog/INSTALL.md").decode()
            self.assertIn(f'SKILL_ZIP="/absolute/path/to/{first.name}"', instructions)
            self.assertIn(f"当前发布版本为 `{RELEASE_VERSION}`", instructions)

    def test_shared_resource_adapter_reads_and_validates_assets_from_installed_skill(self):
        with ZipFile(self.build(self.directory)) as archive:
            archive.extractall(self.directory)
        skill = self.directory / "worklog"
        config_path = skill / "gui-resource.json"
        config = json.loads(config_path.read_bytes())
        panel_path = skill / "gui/panel.html"
        source = panel_path.read_bytes()

        def read(surface):
            call = subprocess.run(
                [sys.executable, "-B", str(skill / "scripts/panel_assets.py"),
                 "--skill", str(skill), "--surface", surface],
                cwd=self.directory, capture_output=True, check=False, timeout=15,
            )
            return call.returncode, json.loads(call.stdout)

        for surface in ("project", "dashboard"):
            self.assertEqual(read(surface), (0, {"config": config, "html": source.decode("utf-8").replace(
                'data-surface="project"', f'data-surface="{surface}"', 1,
            )}))

        def assert_error(code):
            status, error = read("dashboard")
            self.assertNotEqual(status, 0)
            self.assertEqual(error["code"], code)
            self.assertIn("请更新 workLog Skill", error["message"])

        config_path.write_text(json.dumps({**config, "version": 2}), encoding="utf-8")
        assert_error("shared_resources_incompatible")
        config_path.write_text(json.dumps(config), encoding="utf-8")
        panel_path.write_bytes(source + b"\n")
        assert_error("shared_resources_unavailable")
        for data in (
            b"\xff",
            source.replace(b'data-surface="project"', b'data-surface="unknown"', 1),
            b"""<!doctype html><html title=' data-surface="project" '><body></body></html>""",
        ):
            panel_path.write_bytes(data)
            config_path.write_text(json.dumps({**config, "panelSha256": sha256(data).hexdigest()}), encoding="utf-8")
            assert_error("shared_resources_unavailable")
        config_path.unlink()
        assert_error("shared_resources_unavailable")

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
        self.assertEqual(version["result"]["release_version"], RELEASE_VERSION)
        self.assertEqual(version["result"]["cli_path"], str(cli.resolve()))
        self.assertNotIn("project_id", version)
        offline = version_command("check-update", project)["result"]
        self.assertEqual(offline["installed_version"], RELEASE_VERSION)
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
