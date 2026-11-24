#!/usr/bin/env python3
"""将 workLog Codex GUI 和随包的 CLI／Skill 安装到本机。"""

import argparse
from contextlib import contextmanager, ExitStack
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
from tempfile import mkdtemp, NamedTemporaryFile
from zipfile import BadZipFile, ZipFile

PLUGIN = "worklog-codex"
MARKET = "worklog-codex-local"
MARKET_FILE = Path(".agents/plugins/marketplace.json")
VERSION_FILE = "release-version.json"
SKILL_VERSION_FILE = Path("scripts") / VERSION_FILE
DESKTOP_CLIENTS = (
    "/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex",
    "/Applications/Codex.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex",
)


class InstallError(Exception):
    pass


def verified_payload(bundle):
    index_bytes = (bundle / "sha256.json").read_bytes()
    index = json.loads(index_bytes)
    if not isinstance(index, dict):
        raise InstallError("完整包校验清单无效。")
    payload = {}
    for name, expected in index.items():
        relative = PurePosixPath(name)
        if relative.is_absolute() or relative.as_posix() != name or ".." in relative.parts:
            raise InstallError("完整包校验清单包含无效路径。")
        source = bundle / name
        if source.resolve() != source or not source.is_file():
            raise InstallError(f"完整包缺少文件或包含链接 {name}。")
        data = source.read_bytes()
        if sha256(data).hexdigest() != expected:
            raise InstallError(f"完整包文件校验失败 {name}。请重新解压。")
        payload[name] = data
    return payload, sha256(index_bytes).hexdigest()[:16]


def checked_run(command, cwd, environment=None):
    result = subprocess.run(command, cwd=cwd, env=environment,
                            capture_output=True, text=True, check=False)
    if result.returncode:
        raise InstallError((result.stderr or result.stdout).strip() or f"进程退出码 {result.returncode}")
    return result.stdout


def validate_skill(skill, expected):
    cli = skill / "scripts/worklog.py"
    report = json.loads(checked_run([sys.executable, "-B", str(cli), "release-version"], skill))
    if report["result"]["release_version"] != expected:
        raise InstallError("随包 CLI／Skill 的发布版本与 GUI 清单不一致。")


def extract_skill(archive_path, target):
    with ZipFile(archive_path) as archive:
        names = set()
        for member in archive.infolist():
            relative = PurePosixPath(member.filename)
            mode = member.external_attr >> 16
            if (
                len(relative.parts) < 2 or relative.parts[0] != "worklog"
                or relative.as_posix() != member.filename or ".." in relative.parts
                or member.is_dir() or stat.S_IFMT(mode) not in {0, stat.S_IFREG}
                or member.filename in names
            ):
                raise InstallError("随包 CLI／Skill 压缩包包含无效文件。")
            names.add(member.filename)
            path = target.joinpath(*relative.parts[1:])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(archive.read(member))
    if not (target / "SKILL.md").is_file():
        raise InstallError("随包 CLI／Skill 缺少 SKILL.md。")


def exists(path):
    return os.path.lexists(path)


@contextmanager
def locked(parent, name):
    parent.mkdir(parents=True, exist_ok=True)
    with (parent / name).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def recover_skill(skill, previous, staging):
    """仅在成功切换后才移走备份。中断的切换可在重试时恢复。"""
    if not exists(previous):
        return False
    displaced = staging / "interrupted"
    if exists(skill):
        skill.rename(displaced)
    try:
        previous.rename(skill)
    except OSError:
        if exists(displaced):
            displaced.rename(skill)
        raise
    return True


def activate_skill(skill, candidate, previous, staging, version):
    step = "备份原有 CLI／Skill"
    original_moved = candidate_moved = False
    try:
        if exists(skill):
            skill.rename(previous)
            original_moved = True
        step = "启用新 CLI／Skill"
        candidate.rename(skill)
        candidate_moved = True
        step = "验证新 CLI／Skill"
        validate_skill(skill, version)
        if original_moved:
            step = "完成切换"
            previous.rename(staging / "retired")
    except (Exception, KeyboardInterrupt) as error:
        try:
            if original_moved:
                recover_skill(skill, previous, staging)
            elif candidate_moved:
                skill.rename(staging / "interrupted")
        except OSError as recovery_error:
            raise InstallError(
                f"CLI／Skill 安装失败（{step}）并且恢复失败。原备份在 {previous}。"
                f" 故障 {error}。恢复错误 {recovery_error}"
            ) from error
        recovery = "已恢复原有 CLI／Skill。" if original_moved else "已保留安装前的状态。"
        raise InstallError(f"CLI／Skill 安装失败（{step}）。{recovery} {error}") from error


def confirm_replacement(current, bundled, parse_version):
    if current and parse_version(current) >= parse_version(bundled):
        print(f"现有 CLI／Skill {current}，随包版本 {bundled}。沿用现有安装。", flush=True)
        return False
    existing = f"现有 CLI／Skill {current}" if current else "现有版本无法识别"
    print(f"{existing}，随包版本 {bundled}。", flush=True)
    try:
        return input("是否整套替换现有 CLI／Skill？[y/N] ").strip().lower() in {"y", "yes"}
    except EOFError:
        return False


def write_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, prefix=".worklog-", delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(data)
            stream.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def install_plugin(payload, archive_id, version, codex_home, codex):
    root = codex_home / "worklog/codex"
    releases = root / "releases"
    interpreter_id = sha256(os.fsencode(sys.executable)).hexdigest()[:8]
    release = releases / f"{version}-{archive_id}-{interpreter_id}"
    plugin_root = release / "worklog-panel"
    plugin_files = {name: data for name, data in payload.items() if name.startswith("worklog-panel/")}
    plugin_files["worklog-panel/.mcp.json"] = json.dumps({
        "mcpServers": {
            # 兼容插件格式省略 cwd，Codex 会从当前聊天的工作目录启动服务。
            "worklog-codex-panel": {
                "command": sys.executable,
                "args": ["-B", str(plugin_root / "server.py")],
                "env": {"WORKLOG_CODEX_SESSION_HOME": str(codex_home)},
            },
        },
    }, ensure_ascii=False, indent=2).encode()
    releases.mkdir(parents=True, exist_ok=True)
    if not release.exists():
        staging = Path(mkdtemp(prefix=".worklog-", dir=releases))
        try:
            for name, data in plugin_files.items():
                path = staging / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            checked_run([sys.executable, "-B", "-c", "import server"], staging / "worklog-panel")
            staging.rename(release)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
    elif any((release / name).read_bytes() != data for name, data in plugin_files.items()):
        raise InstallError(f"已安装的 GUI 文件校验失败 {release}。")

    manifest = root / MARKET_FILE
    earlier = manifest.read_bytes() if manifest.exists() else None
    market = json.loads(payload[MARKET_FILE.as_posix()])
    if market["name"] != MARKET or market["plugins"][0]["name"] != PLUGIN:
        raise InstallError("完整包的 Codex 插件标识无效。")
    market["plugins"][0]["source"]["path"] = f"./{plugin_root.relative_to(root).as_posix()}"
    environment = {**os.environ, "CODEX_HOME": str(codex_home)}
    command = [codex, "plugin"]
    plugin_target = f"{PLUGIN}@{MARKET}"

    def plugin_is_installed():
        entries = json.loads(checked_run([*command, "list", "--json"], root, environment))["installed"]
        return any(item["pluginId"] == plugin_target for item in entries)

    previous_installed = None
    if earlier is not None:
        try:
            previous_installed = plugin_is_installed()
        except (InstallError, OSError, KeyError, ValueError):
            # A different marketplace may be invalid. Leave the normal install path available.
            pass

    write_atomic(manifest, (json.dumps(market, ensure_ascii=False, indent=2) + "\n").encode())
    added_market = False
    plugin_updated = False
    step = "登记本地插件"
    try:
        checked_run([*command, "marketplace", "add", str(root)], root, environment)
        added_market = earlier is None
        step = "启用 Codex 侧栏"
        report_text = checked_run([*command, "add", plugin_target, "--json"], root, environment)
        plugin_updated = True
        report = json.loads(report_text)
        if "gui-codex-v" + report["version"] != version:
            raise InstallError("Codex 返回的插件版本与完整包不一致。")
    except (Exception, KeyboardInterrupt) as error:
        try:
            if earlier is not None:
                write_atomic(manifest, earlier)
                checked_run([*command, "marketplace", "add", str(root)], root, environment)
                if previous_installed is False:
                    if plugin_is_installed():
                        checked_run([*command, "remove", plugin_target], root, environment)
                else:
                    checked_run([*command, "add", plugin_target, "--json"], root, environment)
            else:
                if plugin_updated:
                    checked_run([*command, "remove", plugin_target], root, environment)
                if added_market:
                    checked_run([*command, "marketplace", "remove", MARKET], root, environment)
                manifest.unlink(missing_ok=True)
        except (InstallError, OSError, KeyError, ValueError) as recovery_error:
            raise InstallError(
                f"Codex 侧栏安装失败（{step}）。{error}。恢复插件登记失败。{recovery_error}"
            ) from error
        raise InstallError(f"Codex 侧栏安装失败（{step}）。{error}") from error
    return plugin_root


def user_skill_path(codex_home):
    current = Path.home() / ".agents/skills/worklog"
    former = codex_home / "skills/worklog"
    current_present = exists(current) or exists(current.parent / ".worklog-install-previous")
    former_present = exists(former) or exists(former.parent / ".worklog-install-previous")
    if current_present and former_present and current.parent.resolve() != former.parent.resolve():
        return current, former
    return (former if former_present else current), None


def install(bundle, codex_home, codex):
    payload, archive_id = verified_payload(bundle)
    plugin_manifest = json.loads(payload[f"worklog-panel/{VERSION_FILE}"])
    sys.path.insert(0, str(bundle / "worklog-panel/runtime"))
    from worklog_release import parse_version, read_release_version

    gui = plugin_manifest["version"]
    bundled = plugin_manifest["bundled_cli_version"]
    if parse_version(gui, "codex") is None or parse_version(bundled) is None:
        raise InstallError("完整包的版本清单无效。")
    with ExitStack() as locks:
        locks.enter_context(locked(codex_home, ".worklog-codex-install.lock"))
        skill, alternate = user_skill_path(codex_home)
        locks.enter_context(locked(skill.parent, ".worklog-install.lock"))
        previous = skill.parent / ".worklog-install-previous"
        staging = Path(mkdtemp(prefix=".worklog-install-", dir=skill.parent))
        try:
            if recover_skill(skill, previous, staging):
                print("已恢复上次安装中断前的 CLI／Skill。", flush=True)
            candidate = staging / "candidate"
            archive = staging / "skill.zip"
            archive.write_bytes(payload[f"worklog-skill-{bundled}.zip"])
            extract_skill(archive, candidate)
            validate_skill(candidate, bundled)
            if alternate is None:
                replace = not exists(skill) or confirm_replacement(
                    read_release_version(skill / SKILL_VERSION_FILE), bundled, parse_version,
                )
            else:
                print(f"发现两套用户级 CLI／Skill，保留 {skill} 和 {alternate}。随包版本 {bundled}。", flush=True)
                replace = False
            plugin_root = install_plugin(payload, archive_id, gui, codex_home, codex)
            if replace:
                try:
                    activate_skill(skill, candidate, previous, staging, bundled)
                except InstallError as error:
                    raise InstallError(f"侧栏已安装。{error}") from error
                print(f"侧栏 {gui} 与 CLI／Skill {bundled} 已安装。")
            else:
                print("侧栏已安装，CLI／Skill 沿用现有安装。")
            print(f"侧栏目录 {plugin_root}\nCLI／Skill 目录 {skill}")
            print("请在 Codex 新建本地聊天，再使用 workLog。")
        finally:
            shutil.rmtree(staging, ignore_errors=True)


def find_codex(selected):
    if selected:
        executable = Path(selected).expanduser().resolve()
        if executable.is_file() and os.access(executable, os.X_OK):
            return str(executable)
        raise InstallError(f"无法执行指定的 Codex CLI {executable}。")
    available = shutil.which("codex")
    if available:
        return str(Path(available).resolve())
    for candidate in DESKTOP_CLIENTS:
        if os.access(candidate, os.X_OK):
            return candidate
    raise InstallError("找不到 Codex CLI。请先安装 Codex 桌面端，或用 --codex 指定 CLI 路径。")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex", help="Codex CLI 可执行文件路径")
    args = parser.parse_args()
    try:
        codex_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser().resolve()
        install(Path(__file__).resolve().parent, codex_home, find_codex(args.codex))
    except (InstallError, OSError, BadZipFile, KeyError, ValueError) as error:
        print(f"安装失败。{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
