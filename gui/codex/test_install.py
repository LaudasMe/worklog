"""验证 Codex GUI 独立安装、CLI／Skill 整套替换和恢复。"""

from contextlib import redirect_stdout
from hashlib import sha256
from io import StringIO
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from build_local import REPO, build_release_assets
from install import InstallError, install, find_codex


class BundleInstallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        output = TemporaryDirectory(prefix="worklog-gui-test-build-")
        cls.addClassCleanup(output.cleanup)
        cls.package, cls.launcher, cls.checksums = build_release_assets(Path(output.name))

    def setUp(self):
        temporary = TemporaryDirectory(prefix="worklog-gui-test-install-")
        self.addCleanup(temporary.cleanup)
        self.fixture = Path(temporary.name).resolve()
        self.home = self.fixture / "home"
        self.home.mkdir()
        self.profile = self.fixture / "codex"
        self.environment = {"HOME": str(self.home), "CODEX_HOME": str(self.profile)}
        self.download = self.fixture / "from-download"
        with ZipFile(self.package) as archive:
            archive.extractall(self.download)
        self.bundle = self.download / "worklog-codex-gui-v0.1.0"
        self.skill = self.home / ".agents/skills/worklog"
        self.manifest = self.skill / "scripts/release-version.json"

    def existing_skill(self, version):
        from install import extract_skill

        extract_skill(self.bundle / "worklog-skill-v0.4.0.zip", self.skill)
        (self.skill / "old-local-note.txt").write_text("现有安装标记", encoding="utf-8")
        if version is None:
            self.manifest.unlink()
        else:
            self.manifest.write_text(json.dumps({"version": version}), encoding="utf-8")
        return self.fingerprint()

    def fingerprint(self):
        return {path.relative_to(self.skill): sha256(path.read_bytes()).hexdigest()
                for path in self.skill.rglob("*") if path.is_file()}

    def call(self, choice=""):
        output = StringIO()
        with patch.dict(os.environ, self.environment), patch("builtins.input", return_value=choice) as confirmation:
            with redirect_stdout(output), patch("install.install_plugin", return_value=self.profile / "plugin") as plugin:
                install(self.bundle, self.profile, "codex")
        self.assertTrue(plugin.called)
        self.assertIn("新建本地聊天", output.getvalue())
        return output.getvalue(), confirmation.called

    def cli(self, command):
        return subprocess.run(
            [sys.executable, "-B", str(self.skill / "scripts/worklog.py"), command],
            cwd=self.fixture, env={**os.environ, **self.environment}, capture_output=True,
            check=True, text=True, timeout=15,
        ).stdout

    def test_real_codex_installs_from_extracted_zip(self):
        try:
            codex = find_codex(None)
        except InstallError:
            self.skipTest("本机未找到 Codex CLI。")
        env = {**os.environ, **self.environment}
        args = [sys.executable, "-B", str(self.bundle / "install.py"), "--codex", codex]
        result = subprocess.run(args, input="", cwd=self.fixture, env=env, capture_output=True,
                                text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("侧栏 gui-codex-v0.1.0 与 CLI／Skill v0.4.0 已安装", result.stdout)
        self.assertIn("新建本地聊天", result.stdout)
        shutil.rmtree(self.download)
        self.assertEqual(json.loads(self.cli("release-version"))["result"]["release_version"], "v0.4.0")
        plugin_index = json.loads(subprocess.run([codex, "plugin", "list", "--json"],
                                               cwd=self.fixture, env=env, capture_output=True,
                                               check=True, text=True, timeout=30).stdout)["installed"]
        plugin = next(item for item in plugin_index if item["pluginId"] == "worklog-codex@worklog-codex-local")
        self.assertTrue(plugin["enabled"])
        self.assertEqual(plugin["version"], "0.1.0")
        plugin_path = Path(plugin["source"]["path"])
        self.assertTrue(plugin_path.is_relative_to(self.profile))
        self.assertTrue((plugin_path / "server.py").is_file())
        mcp = json.loads((plugin_path / ".mcp.json").read_bytes())["mcpServers"]["worklog-codex-panel"]
        self.assertNotIn("cwd", mcp)
        self.assertEqual(Path(mcp["args"][-1]), plugin_path / "server.py")
        self.assertEqual(mcp["env"]["WORKLOG_CODEX_SESSION_HOME"], str(self.profile))

    def run_readme_install(self, choice="", *, archive=None):
        if sys.platform != "darwin":
            self.skipTest("安装脚本面向 macOS。")
        if archive is None:
            try:
                find_codex(None)
            except InstallError:
                self.skipTest("本机未找到 Codex CLI。")

        temporary_downloads = self.fixture / "temporary downloads"
        temporary_downloads.mkdir(exist_ok=True)
        mock_bin = self.fixture / "test bin"
        mock_bin.mkdir(exist_ok=True)
        curl = mock_bin / "curl"
        curl.write_text(
            """#!/usr/bin/env python3
import os
from pathlib import Path
import sys

url = next(arg for arg in sys.argv[1:] if arg.startswith("https://"))
if url == os.environ["WORKLOG_TEST_README_LAUNCHER_URL"]:
    asset_name = "install-codex.sh"
else:
    base, asset_name = url.rsplit("/", 1)
    assert base in {
        "https://github.com/LaudasMe/worklog/releases/download/gui-codex-v0.1.0",
        "https://github.com/floohubAI/worklog/releases/download/gui-codex-v0.1.0",
    } and asset_name == "worklog-codex-gui-v0.1.0-full.zip", url
asset = {
    "install-codex.sh": os.environ["WORKLOG_TEST_LAUNCHER"],
    "worklog-codex-gui-v0.1.0-full.zip": os.environ["WORKLOG_TEST_ARCHIVE"],
}[asset_name]
target = Path(sys.argv[sys.argv.index("--output") + 1])
target.write_bytes(Path(asset).read_bytes())
with Path(os.environ["WORKLOG_TEST_DOWNLOADS"]).open("a", encoding="utf-8") as log:
    print(asset_name, file=log)
""",
            encoding="utf-8",
        )
        curl.chmod(0o755)
        readme = (REPO / "README.md").read_text(encoding="utf-8")
        # README 跟随正式发布，测试安装包由当前源码生成。
        launcher_pattern = re.compile(
            r"https://github\.com/floohubAI/worklog/releases/download/"
            r"gui-codex-v[0-9]+\.[0-9]+\.[0-9]+/install-codex\.sh"
        )
        launcher_url = next(
            line for line in map(str.strip, readme.splitlines())
            if launcher_pattern.fullmatch(line)
        )
        download_log = self.fixture / "download-paths.txt"
        download_log.write_text("", encoding="utf-8")
        env = {
            **os.environ, **self.environment,
            "PATH": f"{mock_bin}{os.pathsep}{os.environ.get('PATH', os.defpath)}",
            "TMPDIR": str(temporary_downloads),
            "WORKLOG_TEST_LAUNCHER": str(self.launcher),
            "WORKLOG_TEST_README_LAUNCHER_URL": launcher_url,
            "WORKLOG_TEST_ARCHIVE": str(archive or self.package),
            "WORKLOG_TEST_DOWNLOADS": str(download_log),
        }
        with TemporaryDirectory(dir=temporary_downloads) as staged:
            launcher_path = Path(staged) / self.launcher.name
            subprocess.run(
                ["curl", "-fsSL", launcher_url, "--output", str(launcher_path)],
                cwd=self.fixture, env=env, check=True, capture_output=True, timeout=45,
            )
            result = subprocess.run(
                ["/bin/bash", str(launcher_path)], input=choice, cwd=self.fixture,
                env=env, capture_output=True, text=True, timeout=45,
            )
        downloaded = download_log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(downloaded, [self.launcher.name, self.package.name], result.stderr or result.stdout)
        self.assertEqual(list(temporary_downloads.glob("worklog-codex-install.*")), [])
        return result

    def test_readme_command_installs_release_in_a_clean_profile(self):
        checksums = self.checksums.read_text(encoding="utf-8").splitlines()
        self.assertEqual(checksums, [
            f"{sha256(p.read_bytes()).hexdigest()}  {p.name}" for p in (self.package, self.launcher)
        ])
        result = self.run_readme_install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("侧栏 gui-codex-v0.1.0 与 CLI／Skill v0.4.0 已安装", result.stdout)
        self.assertEqual(json.loads(self.cli("release-version"))["result"]["release_version"], "v0.4.0")
        assets = list((self.profile / "worklog/codex/releases").glob("*/worklog-panel/server.py"))
        self.assertEqual(len(assets), 1)

    def test_readme_command_preserves_old_skill_until_confirmation(self):
        before = self.existing_skill("v0.3.0")
        retained = self.run_readme_install("\n")
        self.assertEqual(retained.returncode, 0, retained.stderr)
        self.assertIn("是否整套替换现有 CLI／Skill", retained.stdout)
        self.assertIn("CLI／Skill 沿用现有安装", retained.stdout)
        self.assertEqual(self.fingerprint(), before)

        replaced = self.run_readme_install("y\n")
        self.assertEqual(replaced.returncode, 0, replaced.stderr)
        self.assertIn("是否整套替换现有 CLI／Skill", replaced.stdout)
        self.assertEqual(json.loads(self.cli("release-version"))["result"]["release_version"], "v0.4.0")
        self.assertFalse((self.skill / "old-local-note.txt").exists())

    def test_readme_command_rejects_wrong_checksum_before_installing(self):
        before = self.existing_skill("v0.3.0")
        tampered = self.fixture / "tampered.zip"
        tampered.write_bytes(self.package.read_bytes() + b"tampered")
        result = self.run_readme_install("y\n", archive=tampered)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SHA-256 校验失败", result.stderr)
        self.assertNotIn("是否整套替换", result.stdout)
        self.assertFalse(self.profile.exists())
        self.assertEqual(self.fingerprint(), before)

    def test_preserves_newer_known_version(self):
        before = self.existing_skill("v0.5.0")
        output, prompted = self.call("y")
        self.assertFalse(prompted)
        self.assertIn("v0.5.0，随包版本 v0.4.0", output)
        self.assertIn("侧栏已安装，CLI／Skill 沿用现有安装", output)
        self.assertEqual(self.fingerprint(), before)

    def test_older_version_requires_confirmation_to_replace_whole_skill(self):
        before = self.existing_skill("v0.3.0")
        output, prompted = self.call()
        self.assertTrue(prompted)
        self.assertIn("v0.3.0，随包版本 v0.4.0", output)
        self.assertEqual(self.fingerprint(), before)
        self.call("y")
        self.assertEqual(json.loads(self.cli("release-version"))["result"]["release_version"], "v0.4.0")
        self.assertFalse((self.skill / "old-local-note.txt").exists())

    def test_preserves_unknown_by_default_and_replaces_if_confirmed(self):
        before = self.existing_skill(None)
        output, prompted = self.call()
        self.assertTrue(prompted)
        self.assertIn("现有版本无法识别，随包版本 v0.4.0", output)
        self.assertEqual(self.fingerprint(), before)
        self.call("yes")
        self.assertEqual(json.loads(self.cli("release-version"))["result"]["release_version"], "v0.4.0")

    def test_failure_during_rename_restores_previous_cli_and_skill(self):
        before = self.existing_skill("v0.3.0")
        rename = Path.rename

        def fail_activation(path, destination):
            if path.name == "candidate" and destination == self.skill:
                raise OSError("模拟启用时磁盘写入失败")
            return rename(path, destination)

        with patch.object(Path, "rename", fail_activation):
            with self.assertRaisesRegex(InstallError, "启用新 CLI／Skill.*已恢复原有 CLI／Skill"):
                self.call("y")
        self.assertEqual(self.fingerprint(), before)
        self.assertEqual(json.loads(self.cli("release-version"))["result"]["release_version"], "v0.3.0")
        self.assertEqual(self.cli("--version").strip(), "workLog CLI protocol 2.0")
        self.call("y")
        self.assertEqual(json.loads(self.cli("release-version"))["result"]["release_version"], "v0.4.0")

    def test_recover_interrupted_replacement_before_retry(self):
        before = self.existing_skill("v0.3.0")
        backup = self.skill.parent / ".worklog-install-previous"
        self.skill.rename(backup)
        self.existing_skill("v0.4.0")
        output, prompted = self.call()
        self.assertTrue(prompted)
        self.assertIn("已恢复上次安装中断前的 CLI／Skill", output)
        self.assertEqual(self.fingerprint(), before)
        self.assertFalse(backup.exists())

    def test_incomplete_bundle_stops_before_mutating_install(self):
        before = self.existing_skill("v0.3.0")
        (self.bundle / "worklog-panel/panel.html").unlink()
        with self.assertRaisesRegex(InstallError, "完整包缺少文件"):
            self.call("y")
        self.assertFalse(self.profile.exists())
        self.assertEqual(self.fingerprint(), before)


if __name__ == "__main__":
    unittest.main()
