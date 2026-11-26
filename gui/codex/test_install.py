"""验证 Codex GUI 独立安装、CLI／Skill 整套替换和恢复。"""

from contextlib import nullcontext, redirect_stdout
from hashlib import sha256
from importlib.util import module_from_spec, spec_from_file_location
from io import BytesIO, StringIO
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

from build_local import REPO, build_full_package, build_release_assets
from install import InstallError, install, find_codex
from package_skill import build_package
from panel_assets import load_panel_assets


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
        self.bundle = self.download / self.package.name.removesuffix("-full.zip")
        release = json.loads((self.bundle / "worklog-panel/release-version.json").read_bytes())
        self.gui_tag = release["version"]
        self.bundled_version = release["bundled_cli_version"]
        self.skill = self.home / ".agents/skills/worklog"
        self.legacy = self.profile / "skills/worklog"
        self.manifest = self.skill / "scripts/release-version.json"

    def existing_skill(self, version, path=None):
        from install import extract_skill

        path = path or self.skill
        extract_skill(self.bundle / f"worklog-skill-{self.bundled_version}.zip", path)
        (path / "old-local-note.txt").write_text("现有安装标记", encoding="utf-8")
        manifest = path / "scripts/release-version.json"
        if version is None:
            manifest.unlink()
        else:
            manifest.write_text(json.dumps({"version": version}), encoding="utf-8")
        return self.fingerprint(path)

    def fingerprint(self, skill=None):
        skill = skill or self.skill
        return {path.relative_to(skill): sha256(path.read_bytes()).hexdigest()
                for path in skill.rglob("*") if path.is_file()}

    def call(self, choice="", *, error=None, enable=None):
        output = StringIO()

        def check_skill_at_enable(*args):
            load_panel_assets(self.skill, "project")
            self.assertFalse(self.legacy.exists())
            return enable(*args) if enable else self.profile / "plugin"

        with (
            patch.dict(os.environ, self.environment),
            patch("builtins.input", return_value=choice, side_effect=EOFError if choice is None else None) as confirmation,
            redirect_stdout(output),
            patch("install.install_plugin", side_effect=check_skill_at_enable) as plugin,
        ):
            with self.assertRaisesRegex(InstallError, error) if error else nullcontext():
                install(self.bundle, self.profile, "codex")
        if error and enable is None:
            plugin.assert_not_called()
        else:
            plugin.assert_called_once()
        if not error:
            self.assertIn("新建本地聊天", output.getvalue())
        return output.getvalue(), confirmation.called

    def cli(self, command):
        return subprocess.run(
            [sys.executable, "-B", str(self.skill / "scripts/worklog.py"), command],
            cwd=self.fixture, env={**os.environ, **self.environment}, capture_output=True,
            check=True, text=True, timeout=15,
        ).stdout

    def assert_installed_release(self, stdout):
        self.assertIn(f"侧栏 {self.gui_tag} 与 CLI／Skill {self.bundled_version} 已安装", stdout)
        self.assertEqual(json.loads(self.cli("release-version"))["result"]["release_version"], self.bundled_version)

    def test_candidate_install_docs_use_requested_versions(self):
        package = build_full_package(self.fixture, candidate_version="2.3.4", candidate_cli_version="v5.6.7")
        root = "worklog-codex-gui-v2.3.4/"
        with ZipFile(package) as archive:
            docs = archive.read(root + "INSTALL.md").decode()
            skill_package = archive.read(root + "worklog-skill-v5.6.7.zip")
        self.assertIn("`gui-codex-v2.3.4` 完整包随附 workLog CLI／Skill `v5.6.7`", docs)
        self.assertIn("进入 `worklog-codex-gui-v2.3.4` 目录", docs)
        with ZipFile(BytesIO(skill_package)) as skill:
            self.assertEqual(json.loads(skill.read("worklog/scripts/release-version.json"))["version"], "v5.6.7")
            self.assertIn("worklog-skill-v5.6.7.zip", skill.read("worklog/INSTALL.md").decode())

    def test_full_package_contains_same_shared_assets_as_standalone_candidate(self):
        with ZipFile(build_full_package(self.fixture, candidate_version="0.3.0", candidate_cli_version="v0.6.0")) as gui:
            root = "worklog-codex-gui-v0.3.0/"
            self.assertNotIn(root + "worklog-panel/panel.html", gui.namelist())
            self.assertNotIn(root + "worklog-panel/gui-resource.json", gui.namelist())
            inner_zip = BytesIO(gui.read(root + "worklog-skill-v0.6.0.zip"))
        with ZipFile(inner_zip) as inner, ZipFile(build_package(self.fixture, candidate_version="v0.6.0")) as standalone:
            for name in ("worklog/gui/panel.html", "worklog/gui-resource.json"):
                self.assertEqual(sha256(inner.read(name)).digest(), sha256(standalone.read(name)).digest())

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
        self.assertIn("新建本地聊天", result.stdout)
        shutil.rmtree(self.download)
        self.assert_installed_release(result.stdout)
        plugin_index = json.loads(subprocess.run([codex, "plugin", "list", "--json"],
                                               cwd=self.fixture, env=env, capture_output=True,
                                               check=True, text=True, timeout=30).stdout)["installed"]
        plugin = next(item for item in plugin_index if item["pluginId"] == "worklog-codex@worklog-codex-local")
        self.assertTrue(plugin["enabled"])
        self.assertEqual(plugin["version"], self.gui_tag.removeprefix("gui-codex-v"))
        plugin_path = Path(plugin["source"]["path"])
        self.assertTrue(plugin_path.is_relative_to(self.profile))
        self.assertTrue((plugin_path / "server.py").is_file())
        mcp = json.loads((plugin_path / ".mcp.json").read_bytes())["mcpServers"]["worklog-codex-panel"]
        self.assertNotIn("cwd", mcp)
        self.assertEqual(Path(mcp["args"][-1]), plugin_path / "server.py")
        self.assertEqual(mcp["env"]["WORKLOG_CODEX_SESSION_HOME"], str(self.profile))
        self.assertFalse((plugin_path / "panel.html").exists())

        def request(*messages):
            payload = "".join(json.dumps({"jsonrpc": "2.0", "id": i, **message}) + "\n"
                              for i, message in enumerate(messages, 1))
            response = subprocess.run(
                [mcp["command"], *mcp["args"]], input=payload, cwd=self.fixture,
                env={**env, **mcp["env"]}, capture_output=True, check=True, text=True, timeout=15,
            )
            return [json.loads(line) for line in response.stdout.splitlines()]

        panel, config_file = self.skill / "gui/panel.html", self.skill / "gui-resource.json"
        tools_request = {"method": "tools/list"}
        resources_request = {"method": "resources/list"}

        def advertised_uris():
            [tools, resources] = request(tools_request, resources_request)
            uris = {entry["name"]: entry["_meta"]["ui"]["resourceUri"]
                    for entry in tools["result"]["tools"] if entry["name"].startswith("open_worklog_")}
            self.assertEqual(set(uris.values()), {entry["uri"] for entry in resources["result"]["resources"]})
            return uris["open_worklog_codex_panel"], uris["open_worklog_dashboard"]

        dashboard_base = "ui://worklog-codex-panel/dashboard"
        panel_uri, prior_dashboard = advertised_uris()
        self.assertEqual(panel_uri, "ui://worklog-codex-panel/panel")
        self.assertEqual(prior_dashboard, f"{dashboard_base}/{sha256(panel.read_bytes()).hexdigest()}")
        panel.write_bytes(panel.read_bytes() + b"\n<!-- isolated installed source marker -->\n")
        data = panel.read_bytes()
        config = json.loads(config_file.read_bytes())
        config["panelSha256"] = sha256(data).hexdigest()
        config_file.write_text(json.dumps(config), encoding="utf-8")
        current_panel, current_dashboard = advertised_uris()
        self.assertEqual(current_panel, panel_uri)
        self.assertEqual(current_dashboard, f"{dashboard_base}/{config['panelSha256']}")
        self.assertNotEqual(prior_dashboard, current_dashboard)
        reads = [{"method": "resources/read", "params": {"uri": uri}}
                 for uri in (panel_uri, current_dashboard, dashboard_base)]
        for message, response, surface in zip(reads, request(*reads), ("project", "dashboard", "dashboard")):
            uri = message["params"]["uri"]
            [content] = response["result"]["contents"]
            self.assertEqual(content["uri"], uri)
            self.assertEqual(content["text"], data.decode().replace(
                'data-surface="project"', f'data-surface="{surface}"', 1,
            ))
        outdated, = request({"method": "resources/read", "params": {"uri": prior_dashboard}})
        self.assertEqual(outdated["error"]["code"], -32000)
        self.assertIn("重新打开 Dashboard", outdated["error"]["message"])

        self.legacy.parent.mkdir()
        shutil.copytree(self.skill, self.legacy)
        for update, code in (({"version": 2}, "shared_resources_incompatible"),
                             ({"panelSha256": "0" * 64}, "shared_resources_unavailable"),
                             (None, "shared_resources_unavailable")):
            if update:
                config_file.write_text(json.dumps({**config, **update}), encoding="utf-8")
            else:
                config_file.unlink()
            self.assertEqual(advertised_uris(), (panel_uri, f"{dashboard_base}/unavailable"))
            unavailable = {"method": "resources/read", "params": {"uri": f"{dashboard_base}/unavailable"}}
            for response in request(*reads, unavailable):
                error = response["error"]
                self.assertEqual(error["data"]["code"], code)
                self.assertIn("请更新 workLog Skill", error["message"])

    def test_real_codex_restores_plugin_state_after_install_failure(self):
        try:
            codex = find_codex(None)
        except InstallError:
            self.skipTest("本机未找到 Codex CLI。")

        baseline = Path(os.environ.get("WORKLOG_ROLLBACK_BASELINE", self.package))

        def open_bundle(archive_path, name):
            folder = self.fixture / name
            with ZipFile(archive_path) as archive:
                archive.extractall(folder)
                bundle = folder / archive.namelist()[0].split("/")[0]
            spec = spec_from_file_location(name, bundle / "install.py")
            module = module_from_spec(spec)
            spec.loader.exec_module(module)
            return module, bundle

        def version(bundle):
            return json.loads((bundle / "worklog-panel/.codex-plugin/plugin.json").read_bytes())["version"]

        old_installer, old_bundle = open_bundle(baseline, "baseline")
        old_version = version(old_bundle)
        candidate_path = os.environ.get("WORKLOG_ROLLBACK_CANDIDATE")
        if candidate_path:
            candidate = Path(candidate_path)
        else:
            series, release = old_version.rsplit(".", 1)
            candidate = build_full_package(self.fixture, candidate_version=f"{series}.{int(release) + 1}")
        new_installer, new_bundle = open_bundle(candidate, "candidate")
        candidate_version = version(new_bundle)
        self.assertNotEqual(candidate_version, old_version)
        run = new_installer.checked_run
        target = "worklog-codex@worklog-codex-local"
        report = {
            "codex_cli": run([codex, "--version"], self.fixture, {**os.environ, **self.environment}).strip(),
            "baseline_version": old_version, "baseline_sha256": sha256(baseline.read_bytes()).hexdigest(),
            "candidate_version": candidate_version, "candidate_sha256": sha256(candidate.read_bytes()).hexdigest(),
            "cases": {},
        }

        cases = (
            ("disabled", "version_mismatch"),
            ("enabled", "version_mismatch"),
            ("legacy", "version_mismatch"),
            ("uninstalled", "version_mismatch"),
            ("never_installed", "version_mismatch"),
            ("never_installed", "add_command_failed"),
        )
        for initial, fault in cases:
            with self.subTest(initial=initial, fault=fault):
                folder = self.fixture / f"{initial}-{fault}"
                profile = folder / "codex"
                profile.mkdir(parents=True)
                environment = {**os.environ, "HOME": str(folder / "home"), "CODEX_HOME": str(profile)}
                config = profile / "config.toml"
                unrelated_setting = '[plugins."other-plugin@other-market"]\nenabled = false\n'
                config.write_text(unrelated_setting, encoding="utf-8")

                def plugin():
                    raw = run([codex, "plugin", "list", "--json"], folder, environment)
                    return next((item for item in json.loads(raw)["installed"] if item["pluginId"] == target), None)

                def marketplace():
                    raw = run([codex, "plugin", "marketplace", "list", "--json"], folder, environment)
                    return next((item for item in json.loads(raw)["marketplaces"]
                                 if item["name"] == "worklog-codex-local"), None)

                def state(entry):
                    if entry is None:
                        return {"installed": False, "enabled": None, "version": None}
                    return {key: entry[key] for key in ("installed", "enabled", "version")}

                current_skill = Path(environment["HOME"]) / ".agents/skills/worklog"
                legacy_skill = profile / "skills/worklog"
                with patch.dict(os.environ, environment), patch("builtins.input", return_value="y"), redirect_stdout(StringIO()):
                    if initial != "never_installed":
                        old_installer.install(old_bundle, profile, codex)
                        if initial == "uninstalled":
                            run([codex, "plugin", "remove", target], folder, environment)
                        elif initial == "disabled":
                            content = config.read_text(encoding="utf-8")
                            config.write_text(content.replace(f'[plugins."{target}"]\nenabled = true',
                                                              f'[plugins."{target}"]\nenabled = false'),
                                              encoding="utf-8")
                        elif initial == "legacy":
                            legacy_skill.parent.mkdir()
                            current_skill.rename(legacy_skill)
                    previous_skills = tuple(self.fingerprint(path) for path in (current_skill, legacy_skill))
                    previous = plugin()
                    previous_market = marketplace()
                    manifest = profile / "worklog/codex/.agents/plugins/marketplace.json"
                    previous_manifest = manifest.read_bytes() if manifest.exists() else None
                    installed = initial in {"disabled", "enabled", "legacy"}
                    self.assertEqual(state(previous), {
                        "installed": installed, "enabled": (initial != "disabled") if installed else None,
                        "version": old_version if installed else None,
                    })
                    self.assertEqual(previous_market is None, initial == "never_installed")
                    observed_candidate = []

                    def inject(args, cwd, env=None):
                        response = run(args, cwd, env)
                        if args[:4] == [codex, "plugin", "add", target] and not observed_candidate:
                            load_panel_assets(current_skill, "project")
                            self.assertFalse(legacy_skill.exists())
                            installed_report = json.loads(response)
                            self.assertEqual(installed_report["version"], candidate_version)
                            cache = Path(installed_report["installedPath"])
                            self.assertTrue(cache.is_relative_to(profile))
                            self.assertTrue((cache / "server.py").is_file())
                            observed_candidate.append(state(plugin()))
                            self.assertEqual(observed_candidate[0], {
                                "installed": True, "enabled": True, "version": candidate_version,
                            })
                            # 模拟安装期间另一方留下的设置，回滚只应修改本插件。
                            with config.open("a", encoding="utf-8") as stream:
                                stream.write("\n# independent edit during installation\n")
                            if fault == "add_command_failed":
                                raise new_installer.InstallError("模拟安装已生效但命令报告错误")
                            return '{"version":"0.0.0"}'
                        return response

                    detail = (
                        "插件版本与完整包不一致" if fault == "version_mismatch" else "安装已生效但命令报告错误"
                    )
                    with patch.object(new_installer, "checked_run", side_effect=inject):
                        with self.assertRaisesRegex(new_installer.InstallError, f"已恢复安装前的插件状态.*{detail}") as caught:
                            new_installer.install(new_bundle, profile, codex)
                    actual = plugin()
                    self.assertEqual(actual, previous)
                    self.assertEqual(marketplace(), previous_market)
                    self.assertEqual(manifest.read_bytes() if manifest.exists() else None, previous_manifest)
                    self.assertEqual(tuple(self.fingerprint(path) for path in (current_skill, legacy_skill)), previous_skills)
                    actual_config = config.read_text(encoding="utf-8")
                    self.assertIn(unrelated_setting, actual_config)
                    self.assertIn("# independent edit during installation", actual_config)
                    report["cases"][f"{initial}-{fault}"] = {
                        "before": state(previous), "during": observed_candidate[0], "after": state(actual),
                        "marketplace_restored": True, "original_manifest_preserved": True,
                        "original_skill_paths_preserved": True,
                        "unrelated_config_preserved": True, "installer_message": str(caught.exception),
                    }

        evidence = os.environ.get("WORKLOG_ROLLBACK_EVIDENCE")
        if evidence:
            Path(evidence).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

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

launcher_url = os.environ["WORKLOG_TEST_README_LAUNCHER_URL"]
base = launcher_url.rsplit("/", 1)[0] + "/"
url = next(arg for arg in sys.argv[1:] if arg.startswith("https://"))
if url == launcher_url:
    asset_name = "install-codex.sh"
else:
    asset_name = os.environ["WORKLOG_TEST_ARCHIVE_NAME"]
    assert url == base + asset_name, url
asset = {
    "install-codex.sh": os.environ["WORKLOG_TEST_LAUNCHER"],
    os.environ["WORKLOG_TEST_ARCHIVE_NAME"]: os.environ["WORKLOG_TEST_ARCHIVE"],
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
        launcher_pattern = re.compile(
            r"https://github\.com/floohubAI/worklog/releases/download/"
            rf"{re.escape(self.gui_tag)}/install-codex\.sh"
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
            "WORKLOG_TEST_ARCHIVE_NAME": self.package.name,
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
        self.assert_installed_release(result.stdout)
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
        self.assertEqual(json.loads(self.cli("release-version"))["result"]["release_version"], self.bundled_version)
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
        major = int(self.bundled_version.removeprefix("v").split(".")[0])
        newer_version = f"v{major + 1}.0.0"
        before = self.existing_skill(newer_version)
        output, prompted = self.call("y")
        self.assertFalse(prompted)
        self.assertIn(f"{newer_version}，随包版本 {self.bundled_version}", output)
        self.assertIn("侧栏已安装，CLI／Skill 沿用现有安装", output)
        self.assertEqual(self.fingerprint(), before)

    def test_older_version_requires_confirmation_to_replace_whole_skill(self):
        before = self.existing_skill("v0.3.0")
        output, prompted = self.call()
        self.assertTrue(prompted)
        self.assertIn(f"v0.3.0，随包版本 {self.bundled_version}", output)
        self.assertEqual(self.fingerprint(), before)
        self.call("y")
        self.assertEqual(json.loads(self.cli("release-version"))["result"]["release_version"], self.bundled_version)
        self.assertFalse((self.skill / "old-local-note.txt").exists())

    def test_same_version_changed_files_require_confirmation(self):
        before = self.existing_skill(self.bundled_version)
        output, prompted = self.call()
        self.assertTrue(prompted)
        self.assertIn("同版本 CLI／Skill 文件有差异", output)
        self.assertEqual(self.fingerprint(), before)
        self.call("y")
        self.assertFalse((self.skill / "old-local-note.txt").exists())
        _, prompted = self.call()
        self.assertFalse(prompted)

    def test_preserves_unknown_by_default_and_replaces_if_confirmed(self):
        before = self.existing_skill(None)
        output, prompted = self.call()
        self.assertTrue(prompted)
        self.assertIn(f"现有版本无法识别，随包版本 {self.bundled_version}", output)
        self.assertEqual(self.fingerprint(), before)
        self.call("yes")
        self.assertEqual(json.loads(self.cli("release-version"))["result"]["release_version"], self.bundled_version)

    def test_migrates_legacy_only_after_confirmation_and_backs_it_up_outside_skill_paths(self):
        before = self.existing_skill("v0.3.0", self.legacy)
        output, prompted = self.call("y")
        self.assertTrue(prompted)
        self.assertIn(f"{self.skill}，版本 尚未安装", output)
        self.assertIn(f"{self.legacy}，版本 v0.3.0", output)
        self.assert_installed_release(output)
        [backup] = (self.profile / "worklog-skill-backups").glob("*/worklog")
        self.assertEqual(self.fingerprint(backup), before)
        self.assertIn(str(backup), output)

    def test_migration_keeps_compatible_new_path_when_both_are_installed(self):
        current = self.existing_skill("v9.0.0")
        previous = self.existing_skill("v0.3.0", self.legacy)
        output, prompted = self.call("y")
        self.assertTrue(prompted)
        self.assertIn(f"{self.skill}，版本 v9.0.0", output)
        self.assertIn(f"{self.legacy}，版本 v0.3.0", output)
        self.assertEqual(self.fingerprint(), current)
        [backup] = (self.profile / "worklog-skill-backups").glob("*/worklog")
        self.assertEqual(self.fingerprint(backup), previous)

    def test_declined_migration_and_eof_keep_both_installations(self):
        current = self.existing_skill(self.bundled_version)
        previous = self.existing_skill("v0.3.0", self.legacy)
        for choice in ("n", None):
            with self.subTest(choice=choice):
                _, prompted = self.call(choice, error="已取消迁移")
                self.assertTrue(prompted)
                self.assertEqual(self.fingerprint(), current)
                self.assertEqual(self.fingerprint(self.legacy), previous)
                self.assertFalse((self.profile / "worklog-skill-backups").exists())

    def test_incompatible_or_missing_resources_prevent_gui_enable_until_skill_is_updated(self):
        config = self.skill / "gui-resource.json"
        for broken in (None, '{"version":2}'):
            with self.subTest(broken=broken):
                self.existing_skill("v9.0.0")
                if broken is None:
                    config.unlink()
                else:
                    config.write_text(broken, encoding="utf-8")
                original = self.fingerprint()
                self.call(error="请更新 workLog Skill")
                self.assertEqual(self.fingerprint(), original)
                output, _ = self.call("y")
                self.assert_installed_release(output)

    def test_failure_at_final_resource_check_restores_old_install_before_gui_enable(self):
        previous = self.existing_skill("v0.3.0", self.legacy)
        rename = Path.rename

        def damage_installed_panel(path, destination):
            moved = rename(path, destination)
            if path.name == "candidate" and destination == self.skill:
                (destination / "gui/panel.html").unlink()
            return moved

        with patch.object(Path, "rename", damage_installed_panel):
            self.call("y", error="请更新 workLog Skill")
        self.assertFalse(self.skill.exists())
        self.assertEqual(self.fingerprint(self.legacy), previous)

    def test_failure_to_move_legacy_backup_restores_both_installations(self):
        current = self.existing_skill("v0.3.0")
        previous = self.existing_skill("v0.4.0", self.legacy)
        rename = Path.rename

        def fail_backup(path, destination):
            if path == self.legacy:
                raise OSError("模拟移动旧安装失败")
            return rename(path, destination)

        with patch.object(Path, "rename", fail_backup):
            self.call("y", error="备份旧路径 CLI／Skill.*已恢复原有 CLI／Skill")
        self.assertEqual(self.fingerprint(), current)
        self.assertEqual(self.fingerprint(self.legacy), previous)

    def test_gui_enable_failure_restores_new_and_old_skill_installations(self):
        current = self.existing_skill("v0.3.0")
        previous = self.existing_skill("v0.4.0", self.legacy)

        def fail_enable(*_):
            self.assertEqual(json.loads(self.cli("release-version"))["result"]["release_version"], self.bundled_version)
            raise InstallError("模拟 GUI 启用失败")

        self.call("y", error="已恢复原有 CLI／Skill.*模拟 GUI 启用失败", enable=fail_enable)
        self.assertEqual(self.fingerprint(), current)
        self.assertEqual(self.fingerprint(self.legacy), previous)

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
        self.assertEqual(json.loads(self.cli("release-version"))["result"]["release_version"], self.bundled_version)

    def test_recover_interrupted_replacement_before_retry(self):
        before = self.existing_skill("v0.3.0")
        backup = self.skill.parent / ".worklog-install-previous"
        self.skill.rename(backup)
        self.existing_skill(self.bundled_version)
        output, prompted = self.call()
        self.assertTrue(prompted)
        self.assertIn("已恢复上次安装中断前的 CLI／Skill", output)
        self.assertEqual(self.fingerprint(), before)
        self.assertFalse(backup.exists())

    def test_recovers_old_path_interrupted_install_before_migration_prompt(self):
        before = self.existing_skill("v0.3.0", self.legacy)
        backup = self.legacy.parent / ".worklog-install-previous"
        self.legacy.rename(backup)
        self.existing_skill(self.bundled_version, self.legacy)
        output, _ = self.call("n", error="已取消迁移")
        self.assertIn("已恢复上次安装中断前的旧路径", output)
        self.assertEqual(self.fingerprint(self.legacy), before)
        self.assertFalse(self.skill.exists())

    def test_interrupt_after_legacy_backup_move_restores_both_originals(self):
        current = self.existing_skill("v0.3.0")
        legacy = self.existing_skill("v0.4.0", self.legacy)
        rename = Path.rename

        def interrupt_after_move(path, destination):
            moved = rename(path, destination)
            if path == self.legacy:
                raise KeyboardInterrupt
            return moved

        with patch.object(Path, "rename", interrupt_after_move):
            self.call("y", error="备份旧路径 CLI／Skill.*已恢复原有 CLI／Skill")
        self.assertEqual(self.fingerprint(), current)
        self.assertEqual(self.fingerprint(self.legacy), legacy)

    def test_incomplete_bundle_stops_before_mutating_install(self):
        before = self.existing_skill("v0.3.0")
        (self.bundle / f"worklog-skill-{self.bundled_version}.zip").unlink()
        with self.assertRaisesRegex(InstallError, "完整包缺少文件"):
            self.call("y")
        self.assertFalse(self.profile.exists())
        self.assertEqual(self.fingerprint(), before)


if __name__ == "__main__":
    unittest.main()
