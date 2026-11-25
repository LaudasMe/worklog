#!/usr/bin/env python3
"""通过 stdio 提供 workLog Dashboard 和项目侧栏。"""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from shlex import quote as shell_quote
import sys
from threading import Lock
from urllib.parse import quote, urlencode

from project_panel import (
    READ_ERRORS, PanelError, locate_project, read_handoff, read_history, registered_target,
    task_reader, task_sections, unavailable, view_dashboard, view_project,
)
from task_actions import ACTION_FIELDS, action_unavailable, blocker_options, completion_options, mutate_task
from codex_skill_version import current_skill_version
from worklog_release import check_update, read_release_version
from worklog_store import TERMINAL_TASK_STATES

PROTOCOL_VERSIONS = {"2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"}
RESOURCE_URI = "ui://worklog-codex-panel/panel"
DASHBOARD_URI = "ui://worklog-codex-panel/dashboard"
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
RESOURCES = {
    uri: {"uri": uri, "name": tool["title"], "mimeType": MIME_TYPE}
    for uri, tool in ((RESOURCE_URI, TOOL), (DASHBOARD_URI, DASHBOARD))
}


def app_tool(name, title, description, properties, *, open_world=False, project=False, write=False):
    return {
        "name": name,
        "title": title,
        "description": description,
        "inputSchema": {
            "type": "object", "required": list(properties),
            "properties": {**properties, **({"projectId": IDENTIFIER} if project else {})},
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": not write, "openWorldHint": open_world},
        "_meta": {"ui": {"visibility": ["app"]}},
    }


IDENTIFIER = {"type": "string", "minLength": 1, "maxLength": 128}
HANDOFF = app_tool(
    "read_worklog_codex_handoff", "读取交接原文", "按侧栏当前任务读取该任务的最近一次交接原文。",
    {"taskId": IDENTIFIER, "entryId": IDENTIFIER}, project=True,
)
SECTIONS = app_tool(
    "read_worklog_task_sections", "读取待办和历史", "读取当前任务的待办和历史摘要。",
    {"taskId": IDENTIFIER}, project=True,
)
HISTORY = app_tool(
    "read_worklog_history", "读取历史原文", "读取当前任务中的一条历史记录。",
    {"taskId": IDENTIFIER, "source": {"type": "string", "enum": ["entry", "record", "change"]},
     "recordId": IDENTIFIER}, project=True,
)
UPDATE = app_tool(
    "check_worklog_update", "检查 workLog 更新", "手动检查一个 workLog 组件的 GitHub 正式版本。",
    {"component": {"type": "string", "enum": ["codex", "cli"]}}, open_world=True,
)
CONTINUE = app_tool(
    "prepare_worklog_continue", "准备继续任务的草稿", "核对任务归属并准备 Codex 新聊天链接。",
    {"taskId": IDENTIFIER}, project=True,
)
COMPLETION = app_tool(
    "read_worklog_completion_options", "读取完成任务的依据与待办", "读取任务的可用完成依据及开放事项。",
    {"taskId": IDENTIFIER, "expectedRevision": {"type": "integer", "minimum": 1}}, project=True,
)
BLOCKERS = app_tool(
    "read_worklog_blocker_options", "读取任务阻塞", "核对接手归属并读取当前任务的生效阻塞。",
    {"taskId": IDENTIFIER, "expectedRevision": {"type": "integer", "minimum": 1}}, project=True,
)
MUTATE = app_tool(
    "mutate_worklog_task", "保存任务操作", "按项目和任务版本校验并保存用户提交的任务操作。",
    {"command": {"type": "string", "enum": list(ACTION_FIELDS)}, "request": {"type": "object"}},
    project=True, write=True,
)
TOOLS = {
    tool["name"]: tool
    for tool in (TOOL, DASHBOARD, HANDOFF, SECTIONS, HISTORY, UPDATE, CONTINUE, COMPLETION, BLOCKERS, MUTATE)
}
GUI_MANIFEST = Path(__file__).resolve().with_name("release-version.json")
THREAD_BINDING_LOCK = Lock()


def update_component(component, thread_id):
    if component == "codex":
        installed = read_release_version(GUI_MANIFEST, "codex")
    else:
        installed = current_skill_version(thread_id)
    return {"state": "ready", "component": component, **check_update(component, installed)}


def valid_arguments(tool, arguments):
    schema = tool["inputSchema"]
    properties = schema["properties"]
    if not isinstance(arguments, dict) or not set(schema.get("required", ())) <= arguments.keys() <= properties.keys():
        return False
    for key, value in arguments.items():
        check = properties[key]
        if check["type"] == "string" and (
            not isinstance(value, str) or not 0 < len(value) <= 128 or value not in check.get("enum", (value,))
        ):
            return False
        if check["type"] == "integer" and (type(value) is not int or value < check["minimum"]):
            return False
        if check["type"] == "object" and not isinstance(value, dict):
            return False
    return True


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


def prepare_continue(root, task, project_key):
    if task["status"] in TERMINAL_TASK_STATES:
        raise PanelError("任务已完成或取消。请刷新任务列表。", "terminal_task")
    path = json.dumps(str(root), ensure_ascii=False)
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
    if tool is MUTATE:
        return mutate_task(root, project, arguments["command"], arguments["request"], source=source)
    task_id = arguments["taskId"]
    if tool is HANDOFF:
        return read_handoff(root, task_id, arguments["entryId"], project, exact=True)
    if tool is SECTIONS:
        return task_sections(root, task_id, project, exact=True)
    if tool is HISTORY:
        return read_history(root, task_id, arguments["source"], arguments["recordId"], project, exact=True)
    with task_reader(root, task_id, project, exact=True) as (db, task):
        if tool is CONTINUE:
            return prepare_continue(root, task, project)
        if tool is BLOCKERS:
            return blocker_options(db, task, arguments["expectedRevision"], source)
        return completion_options(db, task, arguments["expectedRevision"])


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
        if ((tool is COMPLETION and getattr(error, "code", None) == "revision_conflict")
                or (tool is BLOCKERS and not isinstance(error, PanelError))):
            data = action_unavailable(error)
        else:
            data = unavailable(error)
    return {"content": [], "structuredContent": data}



def respond(message, panel):
    if "id" not in message:
        return None

    response = {"jsonrpc": "2.0", "id": message["id"]}
    method = message.get("method")
    params = message.get("params", {})
    if method == "initialize":
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
        result = {"tools": list(TOOLS.values())}
    elif method == "resources/list":
        result = {"resources": list(RESOURCES.values())}
    elif method == "resources/templates/list":
        result = {"resourceTemplates": []}
    elif method == "tools/call":
        result = read_panel(params, panel)
        if result is None:
            return {**response, "error": {"code": -32602, "message": "工具名称或参数无效。"}}
    elif method == "resources/read" and isinstance(params.get("uri"), str) and params["uri"] in RESOURCES:
        uri = params["uri"]
        html = Path(__file__).with_name("panel.html").read_text(encoding="utf-8")
        if uri == DASHBOARD_URI:
            html = html.replace('data-surface="project"', 'data-surface="dashboard"', 1)
        result = {"contents": [{**RESOURCES[uri], "text": html}]}
    elif method == "resources/read":
        return {**response, "error": {"code": -32602, "message": "未知工具、参数或资源。"}}
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
