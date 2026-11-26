#!/usr/bin/env python3
"""通过 stdio 提供 workLog Dashboard 和项目侧栏。"""

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import re
from shlex import quote as shell_quote
import sys
from threading import Lock
from urllib.parse import quote, urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent / "runtime"))

from project_panel import (
    READ_ERRORS, PanelError, locate_project, registered_target, task_reader, view_dashboard, view_project,
)
from panel_tools import (
    IDENTIFIER, TASK_TOOLS, adapter_result, app_tool, run_task_tool, valid_arguments,
)
from codex_skill_version import current_skill_version
from panel_assets import SharedResourceError, load_panel_assets, user_skill_path
from worklog_release import check_update, read_release_version
from worklog_store import TERMINAL_TASK_STATES

PROTOCOL_VERSIONS = {"2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"}
RESOURCE_URI = "ui://worklog-codex-panel/panel"
DASHBOARD_URI = "ui://worklog-codex-panel/dashboard"
DASHBOARD_UNAVAILABLE_URI = f"{DASHBOARD_URI}/unavailable"
MIME_TYPE = "text/html;profile=mcp-app"
TOOL = {
    "name": "open_worklog_codex_panel",
    "title": "workLog 项目侧栏",
    "description": "在当前聊天的侧栏查看 workLog 任务。",
    "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    "annotations": {"readOnlyHint": True, "openWorldHint": False},
    "_meta": {
        "ui": {"resourceUri": RESOURCE_URI, "visibility": ["app"]},
        "openai/ui": {"entrypoints": [{"type": "thread"}]},
    },
}
DASHBOARD = {
    **TOOL,
    "name": "open_worklog_dashboard",
    "title": "workLog Dashboard",
    "description": "查看本机已登记项目的任务。",
    "_meta": {
        "ui": {"resourceUri": DASHBOARD_URI, "visibility": ["app"]},
        "openai/ui": {"entrypoints": [{"type": "global"}]},
    },
}
ENTRY_TITLES = {
    TOOL["name"]: {"zh-Hans": "workLog 项目侧栏", "en": "workLog Project Sidebar"},
    DASHBOARD["name"]: {"zh-Hans": "workLog 全局总览", "en": "workLog Dashboard"},
}
UPDATE = app_tool(
    "check_worklog_update", "检查 workLog 更新", "手动检查一个 workLog 组件的 GitHub 正式版本。",
    {"component": {"type": "string", "enum": ["codex", "cli"]}}, open_world=True,
)
CONTINUE = app_tool(
    "prepare_worklog_continue", "准备继续任务的草稿", "核对任务归属并准备 Codex 新聊天链接。",
    {"taskId": IDENTIFIER}, project=True,
)
CONTINUE["inputSchema"]["properties"]["locale"] = {"type": "string", "enum": ["zh-Hans", "en"]}
TOOLS = {tool["name"]: tool for tool in (TOOL, DASHBOARD, UPDATE, CONTINUE)}
TOOLS.update(TASK_TOOLS)
GUI_MANIFEST = Path(__file__).resolve().with_name("release-version.json")
THREAD_BINDING_LOCK = Lock()


def update_component(component, thread_id):
    if component == "codex":
        installed = read_release_version(GUI_MANIFEST, "codex")
    else:
        installed = current_skill_version(thread_id)
    return {"state": "ready", "component": component, **check_update(component, installed)}


def bind_thread(params, panel):
    metadata = params.get("_meta")
    thread_id = metadata.get("threadId") if isinstance(metadata, dict) else None
    if not isinstance(thread_id, str) or not thread_id.strip():
        raise PanelError("请在已建立的本地项目聊天中打开 workLog。")
    with THREAD_BINDING_LOCK:
        if panel.setdefault("threadId", thread_id) != thread_id:
            raise PanelError("此侧栏已关联另一条聊天，请重新打开面板。")
    return thread_id


def target_project(params, panel, project_id):
    if project_id is not None:
        selected = panel.get("dashboard", {}).get(project_id)
        if selected is None:
            raise PanelError("此项目当前不可用。请先刷新 Dashboard。", "project_mismatch")
        root, expected_project = selected
        registered_target(root, expected_project)
        return root, expected_project

    bind_thread(params, panel)
    if "project" not in panel:
        raise PanelError("请先刷新任务列表。", "project_mismatch")
    root, registered_id = locate_project(Path.cwd())
    expected_project = panel["project"]
    if root != panel.get("root") or registered_id not in (None, expected_project[0]):
        raise PanelError("当前聊天对应的项目已变化。请先刷新侧栏。", "project_mismatch")
    return root, expected_project


def prepare_continue(root, task, project_key, language):
    if task["status"] in TERMINAL_TASK_STATES:
        raise PanelError("任务已完成或取消。请刷新任务列表。", "terminal_task")
    path = json.dumps(str(root), ensure_ascii=False)
    if language == "en":
        prompt = (
            f'Use the workLog Skill to continue {task["number"]}. I authorize this chat to follow the task '
            'and save verified progress, concrete to-do items, and blockers. '
            f'Confirm the actual working directory (pwd -P) is {path}, and pass '
            f'--project {shell_quote(str(root))} explicitly to the workLog CLI. '
            f'Verify project ID {project_key[0]}, data generation {project_key[1]}, and task ID {task["task_id"]}. '
            'If the directory or identity differs, tell me and stop. Otherwise, read the latest progress, next step, '
            'to-do items, blockers, and current claim. If another chat or client owns an active run, '
            'explain how to hand it off and wait for the handoff or my explicit approval before writing.'
        )
    else:
        prompt = (
            f'请使用 workLog Skill 继续 {task["number"]}。我授权本聊天持续跟进这项任务，保存确认过的阶段进展、具体待办和阻塞。'
            f'请先核对当前聊天的实际工作目录（pwd -P）为 {path}，并用 workLog CLI 显式指定 --project {shell_quote(str(root))}。'
            f'核对项目 ID {project_key[0]}、数据代次 {project_key[1]} 和任务 ID {task["task_id"]}。'
            '如目录或身份不符，请告知我并停止。核对一致后，读取任务的最新进展、下一步、待办、阻塞和接手状态。'
            '有其他会话或客户端有效接手时，请先说明如何交接，待交接或我明确确认替换后再写入。'
        )
    query = urlencode({"path": str(root), "prompt": prompt}, quote_via=quote)
    return {"state": "ready", "url": f"codex://threads/new?{query}"}


def task_operation(tool, params, panel, arguments):
    project_id = arguments.get("projectId")
    root, project = target_project(params, panel, project_id)
    source = ("codex-dashboard", "global") if project_id is not None else ("codex", panel["threadId"])
    if tool is CONTINUE:
        with task_reader(root, arguments["taskId"], project, exact=True) as (_, task):
            return prepare_continue(root, task, project, arguments.get("locale", "zh-Hans"))
    return run_task_tool(tool, root, project, arguments, source)


def read_panel(params, panel):
    name = params.get("name")
    tool = TOOLS.get(name) if isinstance(name, str) else None
    arguments = params.get("arguments", {})
    if tool is None or not valid_arguments(tool, arguments):
        return None

    try:
        if tool is DASHBOARD:
            panel.pop("dashboard", None)
            data = view_dashboard()
            panel["dashboard"] = {
                group["id"]: (Path(group["path"]), (group["id"], group["generation"]))
                for group in data["projects"] if group["state"] == "ready"
            }
        elif tool is UPDATE:
            data = update_component(arguments["component"], bind_thread(params, panel))
        elif tool is TOOL:
            bind_thread(params, panel)
            panel.pop("project", None)
            data, panel["project"] = view_project(Path.cwd())
            panel["root"] = Path(data["path"]) if "path" in data else Path.cwd()
        else:
            data = task_operation(tool, params, panel, arguments)
    except READ_ERRORS as error:
        return adapter_result(error=error, tool=tool)
    return adapter_result(data=data)


def entry_language(params):
    meta = params.get("_meta")
    host = meta.get("locale") if isinstance(meta, dict) else None
    preferred = next((value.strip() for value in (host, os.getenv("LC_ALL"), os.getenv("LC_MESSAGES"), os.getenv("LANG"))
                      if isinstance(value, str) and value.strip()), "")
    tag = preferred.split(".", 1)[0].lower().replace("_", "-")
    return "zh-Hans" if tag in {"zh", "zh-cn", "zh-sg", "zh-hans"} or tag.startswith("zh-hans-") else "en"


def dashboard_resource_uri():
    # Codex retains the global app resource by URI across launches.
    try:
        assets = load_panel_assets(user_skill_path(), "dashboard")
    except SharedResourceError:
        return DASHBOARD_UNAVAILABLE_URI
    return f"{DASHBOARD_URI}/{assets['config']['panelSha256']}"


def resource_surface(uri):
    if uri == RESOURCE_URI:
        return "project"
    if not isinstance(uri, str):
        return None
    base, _, digest = uri.rpartition("/")
    if uri in (DASHBOARD_URI, DASHBOARD_UNAVAILABLE_URI) or (
        base == DASHBOARD_URI and re.fullmatch(r"[0-9a-f]{64}", digest)
    ):
        return "dashboard"
    return None


def entry_tool(tool, language, dashboard_uri):
    if tool is DASHBOARD:
        metadata = tool["_meta"]
        tool = {**tool, "_meta": {**metadata, "ui": {**metadata["ui"], "resourceUri": dashboard_uri}}}
    titles = ENTRY_TITLES.get(tool["name"])
    return {**tool, "title": titles[language]} if titles else tool


def described_resource(uri, panel):
    tool = TOOL if uri == RESOURCE_URI else DASHBOARD
    language = panel.get("entryLanguage", entry_language({}))
    return {"uri": uri, "name": ENTRY_TITLES[tool["name"]][language], "mimeType": MIME_TYPE}


def respond(message, panel):
    if "id" not in message:
        return None

    response = {"jsonrpc": "2.0", "id": message["id"]}
    method = message.get("method")
    params = message.get("params", {})
    if method == "initialize":
        panel["entryLanguage"] = entry_language(params)
        requested = params.get("protocolVersion")
        protocol_version = requested if isinstance(requested, str) and requested in PROTOCOL_VERSIONS else "2025-11-25"
        result = {
            "protocolVersion": protocol_version,
            "capabilities": {"tools": {}, "resources": {}},
            "serverInfo": {"name": "worklog-codex-panel", "version": "0.1.0"},
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        language = panel.get("entryLanguage", entry_language({}))
        dashboard_uri = dashboard_resource_uri()
        result = {"tools": [entry_tool(tool, language, dashboard_uri) for tool in TOOLS.values()]}
    elif method == "resources/list":
        uris = (RESOURCE_URI, dashboard_resource_uri())
        result = {"resources": [described_resource(uri, panel) for uri in uris]}
    elif method == "resources/templates/list":
        result = {"resourceTemplates": []}
    elif method == "tools/call":
        result = read_panel(params, panel)
        if result is None:
            return {**response, "error": {"code": -32602, "message": "工具名称或参数无效。"}}
    elif method == "resources/read":
        uri = params.get("uri")
        surface = resource_surface(uri)
        if surface is None:
            return {**response, "error": {"code": -32602, "message": "未知工具、参数或资源。"}}
        try:
            assets = load_panel_assets(user_skill_path(), surface)
        except SharedResourceError as error:
            return {**response, "error": {"code": -32000, "message": str(error), "data": error.response()}}
        if surface == "dashboard" and uri not in (DASHBOARD_URI, f"{DASHBOARD_URI}/{assets['config']['panelSha256']}"):
            return {**response, "error": {"code": -32000, "message": "页面版本已变化。请重新打开 Dashboard。"}}
        result = {"contents": [{**described_resource(uri, panel), "text": assets["html"]}]}
    else:
        return {**response, "error": {"code": -32601, "message": "不支持此方法。"}}
    return {**response, "result": result}


def main():
    panel = {}
    output_lock = Lock()

    def send(response):
        if response is None:
            return
        line = (json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8")
        with output_lock:
            sys.stdout.buffer.write(line)
            sys.stdout.buffer.flush()

    def send_update(request):
        send(respond(request, panel))

    with ThreadPoolExecutor(max_workers=2) as updates:
        for line in sys.stdin.buffer:
            try:
                message = json.loads(line)
            except ValueError:
                send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "无效 JSON。"}})
                continue
            if (
                not isinstance(message, dict)
                or message.get("jsonrpc") != "2.0"
                or not isinstance(message.get("method"), str)
                or not isinstance(message.get("params", {}), dict)
            ):
                send({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "无效请求。"}})
                continue
            if message["method"] == "tools/call" and message.get("params", {}).get("name") == UPDATE["name"]:
                updates.submit(send_update, message)
            else:
                send(respond(message, panel))

if __name__ == "__main__":
    main()
