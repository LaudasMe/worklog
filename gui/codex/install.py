#!/usr/bin/env python3
"""将 workLog Codex GUI 和随包的 CLI／Skill 安装到本机。"""

import argparse
from contextlib import contextmanager, ExitStack
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import select
import shutil
import signal
import stat
import subprocess
import sys
from tempfile import mkdtemp, NamedTemporaryFile, TemporaryDirectory
from time import monotonic
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


def recover_skill(skill, previous, displaced):
    """仅在成功切换后才移走备份。中断的切换可在重试时恢复。"""
    if not exists(previous):
        return False
    if exists(skill):
        skill.rename(displaced)
    try:
        previous.rename(skill)
    except OSError:
        if exists(displaced):
            displaced.rename(skill)
        raise
    return True


@contextmanager
def activate_skill(skill, candidate, previous, staging, version, legacy, load_panel_assets):
    step = "验证共享资源"
    original_present = candidate is not None and exists(skill)
    legacy_backup = legacy_pending_backup(legacy) if legacy is not None else None
    try:
        if candidate is not None:
            step = "备份原有 CLI／Skill"
            if original_present:
                skill.rename(previous)
            step = "启用新 CLI／Skill"
            candidate.rename(skill)
            step = "验证新 CLI／Skill"
            validate_skill(skill, version)
        load_panel_assets(skill, "project")
        if legacy is not None:
            step = "备份旧路径 CLI／Skill"
            legacy_backup.parent.mkdir(parents=True, exist_ok=True)
            legacy.rename(legacy_backup)
        step = "启用 Codex GUI"
        yield legacy_backup
    except (Exception, KeyboardInterrupt) as error:
        recovery_errors = []
        legacy_moved = legacy_backup is not None and exists(legacy_backup)
        original_moved = exists(previous)
        if legacy_moved:
            try:
                legacy_backup.rename(legacy)
            except OSError as recovery_error:
                recovery_errors.append(f"旧路径的备份位于 {legacy_backup}。{recovery_error}")
        try:
            if original_moved:
                recover_skill(skill, previous, staging / "failed")
            elif candidate is not None and not original_present and not exists(candidate) and exists(skill):
                skill.rename(staging / "failed")
        except OSError as recovery_error:
            if original_moved:
                recovery_errors.append(f"新路径的原备份位于 {previous}。{recovery_error}")
            else:
                recovery_errors.append(f"无法移除新安装的 CLI／Skill {skill}。{recovery_error}")
        if recovery_errors:
            raise InstallError(f"CLI／Skill 安装失败（{step}）并且恢复失败。{error}。{' '.join(recovery_errors)}") from error
        recovery = "已恢复原有 CLI／Skill。" if original_moved or legacy_moved else "已保留安装前的 Skill 状态。"
        raise InstallError(f"CLI／Skill 安装失败（{step}）。{recovery} {error}") from error


def legacy_pending_backup(legacy):
    return legacy.parent.parent / "worklog-skill-backups" / ".worklog-install-pending" / legacy.name


def confirm_replacement(current, bundled, parse_version, *, same_version_changed=False, incompatible=False):
    if current and parse_version(current) >= parse_version(bundled) and not (same_version_changed or incompatible):
        print(f"现有 CLI／Skill {current}，随包版本 {bundled}。沿用现有安装。", flush=True)
        return False
    existing = f"现有 CLI／Skill {current}" if current else "现有版本无法识别"
    print(f"{existing}，随包版本 {bundled}。", flush=True)
    if same_version_changed:
        print("随包与现有的同版本 CLI／Skill 文件有差异。", flush=True)
    return confirm("是否整套替换现有 CLI／Skill？[y/N] ")


def confirm(prompt):
    try:
        return input(prompt).strip().lower() in {"y", "yes"}
    except EOFError:
        return False


def matches_skill(installed, candidate):
    for path in candidate.rglob("*"):
        if path.is_file():
            existing = installed / path.relative_to(candidate)
            if not existing.is_file() or existing.read_bytes() != path.read_bytes():
                return False
    return True


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


def restore_disabled_plugin(codex, root, environment, plugin_target):
    # Codex CLI 的 plugin add 总会启用插件。交由 Codex 修改这个配置值，保留其他设置。
    initialize = {"id": 0, "method": "initialize",
                  "params": {"clientInfo": {"name": "worklog-installer", "version": "1"}}}
    write_config = {
        "id": 1, "method": "config/value/write", "params": {
            "keyPath": f"plugins.{plugin_target}.enabled",
            "value": False,
            "mergeStrategy": "replace",
        },
    }
    with subprocess.Popen(
        [codex, "app-server", "--listen", "stdio://"], cwd=root, env=environment,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    ) as server:
        def send(*messages):
            server.stdin.write("".join(json.dumps(message) + "\n" for message in messages).encode())
            server.stdin.flush()

        try:
            send(initialize)
            deadline = monotonic() + 15
            pending = b""
            while monotonic() < deadline:
                if not select.select([server.stdout], [], [], max(0, deadline - monotonic()))[0]:
                    break
                chunk = os.read(server.stdout.fileno(), 8192)
                if not chunk:
                    break
                *lines, pending = (pending + chunk).split(b"\n")
                for line in lines:
                    reply = json.loads(line)
                    if reply.get("id") == 0:
                        if "error" in reply:
                            raise InstallError(f"Codex 配置服务初始化失败。{reply['error']}")
                        send({"method": "initialized"}, write_config)
                    elif reply.get("id") == 1:
                        if reply.get("result", {}).get("status") == "ok":
                            return
                        raise InstallError(f"Codex 未能恢复插件停用状态。{reply.get('error', reply.get('result'))}")
            raise InstallError("等待 Codex 恢复插件停用状态时未收到回执。")
        finally:
            server.stdin.close()
            try:
                server.wait(timeout=2)
            except subprocess.TimeoutExpired:
                server.kill()


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

    def plugin_state():
        raw = checked_run([*command, "list", "--marketplace", MARKET, "--json"], root, environment)
        entries = json.loads(raw)["installed"]
        return next(
            ({key: item[key] for key in ("version", "enabled")} for item in entries
             if item["pluginId"] == plugin_target),
            None,
        )

    def registered_market():
        raw = checked_run([*command, "marketplace", "list", "--json"], root, environment)
        entries = json.loads(raw)["marketplaces"]
        return next((item for item in entries if item["name"] == MARKET), None)

    previous = plugin_state()
    marketplace = registered_market()
    if marketplace is not None and Path(marketplace["root"]).resolve() != root:
        raise InstallError("workLog 的本地 marketplace 已登记到另一目录。请先处理已有登记。")
    if previous is not None and (earlier is None or marketplace is None):
        raise InstallError("找不到已安装插件的原 marketplace 清单，无法安全升级。")

    def restore_plugin():
        if previous is None and plugin_state() is not None:
            checked_run([*command, "remove", plugin_target], root, environment)
        if marketplace is None and registered_market() is not None:
            checked_run([*command, "marketplace", "remove", MARKET], root, environment)
        if earlier is None:
            manifest.unlink(missing_ok=True)
        else:
            write_atomic(manifest, earlier)
        if previous is not None:
            checked_run([*command, "add", plugin_target, "--json"], root, environment)
            if not previous["enabled"]:
                restore_disabled_plugin(codex, root, environment, plugin_target)
        if plugin_state() != previous or registered_market() != marketplace:
            raise InstallError("回查结果与安装前的 Codex 插件或 marketplace 状态不一致。")

    step = "写入本地插件清单"
    try:
        write_atomic(manifest, (json.dumps(market, ensure_ascii=False, indent=2) + "\n").encode())
        step = "登记本地插件"
        checked_run([*command, "marketplace", "add", str(root)], root, environment)
        step = "启用 Codex 侧栏"
        report_text = checked_run([*command, "add", plugin_target, "--json"], root, environment)
        report = json.loads(report_text)
        if "gui-codex-v" + report["version"] != version:
            raise InstallError("Codex 返回的插件版本与完整包不一致。")
    except (Exception, KeyboardInterrupt) as error:
        try:
            restore_plugin()
        except (InstallError, OSError, KeyError, ValueError) as recovery_error:
            raise InstallError(
                f"Codex 侧栏安装失败（{step}）。{error}。恢复插件登记失败。{recovery_error}"
            ) from error
        raise InstallError(f"Codex 侧栏安装失败（{step}）。已恢复安装前的插件状态。{error}") from error
    return plugin_root


def install(bundle, codex_home, codex):
    payload, archive_id = verified_payload(bundle)
    plugin_manifest = json.loads(payload[f"worklog-panel/{VERSION_FILE}"])
    sys.path.insert(0, str(bundle / "worklog-panel/runtime"))
    from panel_assets import SharedResourceError, load_panel_assets, user_skill_path
    from worklog_release import parse_version, read_release_version

    gui = plugin_manifest["version"]
    bundled = plugin_manifest["bundled_cli_version"]
    if parse_version(gui, "codex") is None or parse_version(bundled) is None:
        raise InstallError("完整包的版本清单无效。")
    with ExitStack() as locks:
        locks.enter_context(locked(codex_home, ".worklog-codex-install.lock"))
        skill, former = user_skill_path(), codex_home / "skills/worklog"
        locks.enter_context(locked(skill.parent, ".worklog-install.lock"))
        previous = skill.parent / ".worklog-install-previous"
        staging = Path(mkdtemp(prefix=".worklog-install-", dir=skill.parent))
        try:
            if recover_skill(skill, previous, staging / "interrupted"):
                print("已恢复上次安装中断前的 CLI／Skill。", flush=True)
            legacy = None
            if former.parent.is_dir() and former.parent.resolve() != skill.parent.resolve():
                locks.enter_context(locked(former.parent, ".worklog-install.lock"))
                pending = legacy_pending_backup(former)
                if exists(pending):
                    if exists(former):
                        raise InstallError(f"旧路径和上次安装的待恢复备份同时存在。备份位于 {pending}。")
                    pending.rename(former)
                    print("已恢复上次安装中断前的旧路径 CLI／Skill。", flush=True)
                legacy_previous = former.parent / ".worklog-install-previous"
                if exists(legacy_previous):
                    with TemporaryDirectory(prefix=".worklog-recovery-", dir=former.parent) as legacy_staging:
                        recover_skill(former, legacy_previous, Path(legacy_staging) / "interrupted")
                    print("已恢复上次安装中断前的旧路径 CLI／Skill。", flush=True)
                if exists(former):
                    legacy = former
                    for path in (skill, legacy):
                        version = read_release_version(path / SKILL_VERSION_FILE) or (
                            "无法识别" if exists(path) else "尚未安装"
                        )
                        print(f"CLI／Skill 目录 {path}，版本 {version}。", flush=True)
                    if not confirm("是否使用新路径，并将旧路径的 CLI／Skill 移出加载目录？[y/N] "):
                        raise InstallError("已取消迁移。保留原有 CLI／Skill 和 Codex GUI。")
            candidate = staging / "candidate"
            archive = staging / "skill.zip"
            archive.write_bytes(payload[f"worklog-skill-{bundled}.zip"])
            extract_skill(archive, candidate)
            validate_skill(candidate, bundled)
            try:
                load_panel_assets(candidate, "project")
            except SharedResourceError as error:
                raise InstallError(str(error)) from error
            replace = not exists(skill)
            if not replace:
                resource_error = None
                try:
                    load_panel_assets(skill, "project")
                except SharedResourceError as error:
                    resource_error = error
                    print(str(error), flush=True)
                current = read_release_version(skill / SKILL_VERSION_FILE)
                replace = confirm_replacement(
                    current, bundled, parse_version,
                    same_version_changed=current == bundled and not matches_skill(skill, candidate),
                    incompatible=resource_error is not None,
                )
                if resource_error is not None and not replace:
                    raise InstallError(str(resource_error)) from resource_error
            with activate_skill(skill, candidate if replace else None, previous, staging, bundled,
                                legacy, load_panel_assets) as legacy_backup:
                plugin_root = install_plugin(payload, archive_id, gui, codex_home, codex)
            if exists(previous):
                previous.rename(staging / "retired")
            if legacy_backup is not None:
                completed = Path(mkdtemp(prefix="worklog-", dir=legacy_backup.parent.parent))
                legacy_backup = legacy_backup.rename(completed / legacy_backup.name)
                print(f"旧路径的 CLI／Skill 已备份至 {legacy_backup}。")
            if replace:
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
    def interrupt(_signum, _frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupt)
    try:
        codex_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser().resolve()
        install(Path(__file__).resolve().parent, codex_home, find_codex(args.codex))
    except (InstallError, OSError, BadZipFile, KeyError, ValueError) as error:
        print(f"安装失败。{error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("安装已中断。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
