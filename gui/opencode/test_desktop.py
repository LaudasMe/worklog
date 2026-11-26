#!/usr/bin/env python3
"""用 macOS OpenCode Desktop 和本地候选包执行隔离验收。"""

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
from tempfile import TemporaryDirectory
from time import monotonic, sleep
from urllib.parse import urlsplit
from uuid import uuid4
from zipfile import ZipFile

HERE = Path(__file__).resolve().parent


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run(command, *, env, cwd, request=None):
    result = subprocess.run(command, env=env, cwd=cwd, text=True, capture_output=True,
                            input=json.dumps(request, ensure_ascii=False) if request is not None else None,
                            timeout=15, check=False)
    if result.returncode:
        raise RuntimeError(f"{Path(command[0]).name} 退出码 {result.returncode}")
    return result.stdout


def seed_projects(workspace, home, cli):
    fixtures = []
    env = {**os.environ, "HOME": str(home)}
    for suffix in ("a", "b"):
        project = workspace / f"project-{suffix}"
        project.mkdir()
        name = f"Fixture {suffix.upper()}"

        def call(command, *args, request=None):
            return json.loads(run([sys.executable, "-B", str(cli), "--project", str(project), command, *args],
                                  env=env, cwd=workspace, request=request))

        identity = call("init", request={"name": name})
        guards = {key: identity[key] for key in ("project_id", "data_generation")}
        task = call("task-create", request={**guards, "operation_id": str(uuid4()), "title": f"{name} T001"})["result"]["task"]
        context = call("context", "--task", task["task_id"])["result"]["task"]
        call("record", request={**guards, "operation_id": str(uuid4()), "scope": "TASK", "task_id": task["task_id"],
                               "expected_revision": context["revision"], "expected_context_position": context["context_position"],
                               "client": "fixture", "session_id": "desktop-assistance", "records": [{
                                   "kind": "PROGRESS", "summary": "Fixture验收依据", "body_md": "Fixture saved progress",
                                   "details": {"processed_scope": ["fixture"], "impacts": []},
                               }]})
        fixtures.append({**guards, "directory": str(project), "task": task})
    write_json(workspace / "fixtures.json", fixtures)


def install_package(plugin_tar, target):
    required = {"package/package.json", "package/gui-experimental.cjs", "package/README.md"}
    with tarfile.open(plugin_tar, "r:gz") as archive:
        members = archive.getmembers()
        if len(members) != len(required) or {member.name for member in members} != required:
            raise RuntimeError("OpenCode 包文件清单有误。")
        (target / "package").mkdir(parents=True)
        for member in members:
            if not member.isfile():
                raise RuntimeError("OpenCode 包文件必须是普通文件。")
            with archive.extractfile(member) as source:
                (target / member.name).write_bytes(source.read())
    return target / "package"


def stop_service(profile, cli, port, env, cwd):
    config = profile / "config/opencode/service.json"
    state = profile / "state/opencode/service.json"
    if not config.is_file() or not state.is_file():
        return
    endpoint = urlsplit(json.loads(state.read_text())["url"])
    if port == 49374 or endpoint.hostname != "127.0.0.1" or endpoint.port != port or json.loads(config.read_text()).get("port") != port:
        raise RuntimeError("测试后台服务与隔离端口不符。")
    run([str(cli), "service", "stop"], env=env, cwd=cwd)


def desktop_evidence(workspace, profile, cli, app, env):
    with socket.socket() as socket_reservation:
        socket_reservation.bind(("127.0.0.1", 0))
        port = socket_reservation.getsockname()[1]
    run([str(cli), "service", "set", "port", str(port)], env=env, cwd=workspace)
    events = []
    with (workspace / "desktop.log").open("wb") as log:
        instance = subprocess.Popen([str(app)], env=env, cwd=workspace, stdout=log, stderr=subprocess.STDOUT,
                                    start_new_session=True)
        try:
            deadline = monotonic() + 95
            while monotonic() < deadline and instance.poll() is None:
                event_file = workspace / "probe.jsonl"
                if event_file.is_file():
                    try:
                        events = [json.loads(line) for line in event_file.read_text().splitlines()]
                    except json.JSONDecodeError:
                        continue
                    if any(event["phase"] in {"done", "error"} for event in events):
                        break
                sleep(0.2)
        finally:
            if instance.poll() is None:
                os.killpg(instance.pid, signal.SIGTERM)
                try:
                    instance.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(instance.pid, signal.SIGKILL)
                    instance.wait(timeout=5)
            stop_service(profile, cli, port, env, workspace)
    return events


def verify(skill_zip, plugin_tar, app_bundle, evidence_path):
    app = app_bundle / "Contents/MacOS/OpenCode"
    cli = app_bundle / "Contents/Resources/opencode-cli"
    with TemporaryDirectory(prefix="worklog-opencode-desktop-") as temporary:
        workspace = Path(temporary).resolve()
        home = workspace / "home"
        skill = home / ".agents/skills/worklog"
        with ZipFile(skill_zip) as archive:
            archive.extractall(skill.parent)
        package = install_package(plugin_tar, workspace / "installed")
        probe = workspace / "probe"
        probe.mkdir()
        shutil.copyfile(HERE / "test_desktop_probe.cjs", probe / "index.cjs")
        write_json(probe / "package.json", {"name": "worklog-opencode-test-probe", "version": "0.0.0",
                                           "exports": {"./gui-experimental": "./index.cjs"}})
        seed_projects(workspace, home, skill / "scripts/worklog.py")
        profile = workspace / "desktop-profile"
        env = {**os.environ, "OPENCODE_DESKTOP_TEST_ROOT": str(profile),
               "OPENCODE_DISABLE_AUTOUPDATE": "true", "OPENCODE_DISABLE_EXTERNAL_SKILLS": "true"}
        for key in ("config", "data", "cache", "state"):
            directory = profile / key
            directory.mkdir(parents=True)
            env[f"XDG_{key.upper()}_HOME"] = str(directory)
        config = profile / "config/opencode/opencode.json"
        config.parent.mkdir()
        write_json(config, {"update": "disable", "plugins": [
            {"package": probe.as_uri(), "options": {"workspace": str(workspace)}}, package.as_uri(),
        ]})
        env["OPENCODE_CONFIG"] = str(config)
        version = run([str(cli), "--version"], env=env, cwd=workspace).strip()
        events = desktop_evidence(workspace, profile, cli, app, env)
    evidence = {
        "opencode_cli": version, "plugin_sha256": sha256(plugin_tar.read_bytes()).hexdigest(),
        "skill_sha256": sha256(skill_zip.read_bytes()).hexdigest(), "events": events,
    }
    with evidence_path.open("x", encoding="utf-8") as file:
        json.dump(evidence, file, ensure_ascii=False, indent=2)
        file.write("\n")
    if not events or events[-1]["phase"] != "done":
        raise SystemExit(f"OpenCode 隔离桌面验收未完成，见 {evidence_path}")
    print(f"OpenCode 隔离桌面验收通过，见 {evidence_path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skill", required=True, type=Path)
    parser.add_argument("--plugin", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--app", type=Path, default=Path("/Applications/OpenCode.app"))
    args = parser.parse_args()
    verify(args.skill.resolve(), args.plugin.resolve(), args.app.resolve(), args.evidence.resolve())


if __name__ == "__main__":
    main()
