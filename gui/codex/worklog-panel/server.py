#!/usr/bin/env python3
"""通过 stdio 在 Codex 侧栏提供当前项目的只读数据。"""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
from threading import Lock

from project_panel import (
    READ_ERRORS, PanelError, read_handoff, read_history, task_sections, unavailable, view_project,
)
from codex_skill_version import current_skill_version
from worklog_release import check_update, read_release_version

PROTOCOL_VERSIONS = {"2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"}
RESOURCE_URI = "ui://worklog-codex-panel/panel"
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
RESOURCE = {"uri": RESOURCE_URI, "name": "workLog 项目侧栏", "mimeType": MIME_TYPE}


def app_tool(name, title, description, properties, *, open_world=False):
    return {
        "name": name,
        "title": title,
        "description": description,
        "inputSchema": {
            "type": "object", "required": list(properties), "properties": properties, "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": open_world},
        "_meta": {"ui": {"visibility": ["app"]}},
    }


IDENTIFIER = {"type": "string", "minLength": 1, "maxLength": 128}
HANDOFF = app_tool(
    "read_worklog_codex_handoff", "读取交接原文", "按侧栏当前任务读取该任务的最近一次交接原文。",
    {"taskId": IDENTIFIER, "entryId": IDENTIFIER},
)
SECTIONS = app_tool(
    "read_worklog_task_sections", "读取待办和历史", "读取当前任务的待办和历史摘要。",
    {"taskId": IDENTIFIER},
)
HISTORY = app_tool(
    "read_worklog_history", "读取历史原文", "读取当前任务中的一条历史记录。",
    {"taskId": IDENTIFIER, "source": {"type": "string", "enum": ["entry", "record", "change"]}, "recordId": IDENTIFIER},
)
UPDATE = app_tool(
    "check_worklog_update", "检查 workLog 更新", "手动检查一个 workLog 组件的 GitHub 正式版本。",
    {"component": {"type": "string", "enum": ["codex", "cli"]}}, open_world=True,
)
TOOLS = {tool["name"]: tool for tool in (TOOL, HANDOFF, SECTIONS, HISTORY, UPDATE)}
GUI_MANIFEST = Path(__file__).resolve().with_name("release-version.json")
THREAD_BINDING_LOCK = Lock()


def update_component(component, thread_id):
    if component == "codex":
        installed = read_release_version(GUI_MANIFEST, "codex")
    else:
        installed = current_skill_version(thread_id)
    return {"state": "ready", "component": component, **check_update(component, installed)}


def valid_arguments(tool, arguments):
    properties = tool["inputSchema"]["properties"]
    return (
        isinstance(arguments, dict) and arguments.keys() == properties.keys()
        and all(isinstance(value, str) and 0 < len(value) <= 128
                and value in properties[key].get("enum", (value,)) for key, value in arguments.items())
    )


def read_panel(params, panel):
    name = params.get("name")
    tool = TOOLS.get(name) if isinstance(name, str) else None
    arguments = params.get("arguments", {})
    if tool is None or not valid_arguments(tool, arguments):
        return None

    try:
        metadata = params.get("_meta")
        thread_id = metadata.get("threadId") if isinstance(metadata, dict) else None
        if not isinstance(thread_id, str) or not thread_id.strip():
            raise PanelError("请在已建立的本地项目聊天中打开 workLog。")
        with THREAD_BINDING_LOCK:
            if panel.setdefault("threadId", thread_id) != thread_id:
                raise PanelError("此侧栏已关联另一条聊天，请重新打开面板。")
        if tool is UPDATE:
            data = update_component(arguments["component"], thread_id)
        elif tool is TOOL:
            panel.pop("project", None)
            data, panel["project"] = view_project(Path.cwd())
        else:
            if "project" not in panel:
                raise PanelError("请先刷新任务列表，再读取任务详情。")
            cwd, task_id, project = Path.cwd(), arguments["taskId"], panel["project"]
            if tool is HANDOFF:
                data = read_handoff(cwd, task_id, arguments["entryId"], project)
            elif tool is SECTIONS:
                data = task_sections(cwd, task_id, project)
            else:
                data = read_history(cwd, task_id, arguments["source"], arguments["recordId"], project)
    except READ_ERRORS as error:
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
        result = {"resources": [RESOURCE]}
    elif method == "resources/templates/list":
        result = {"resourceTemplates": []}
    elif method == "tools/call":
        result = read_panel(params, panel)
        if result is None:
            return {**response, "error": {"code": -32602, "message": "工具名称或参数无效。"}}
    elif method == "resources/read" and params.get("uri") == RESOURCE_URI:
        html = Path(__file__).with_name("panel.html").read_text(encoding="utf-8")
        result = {"contents": [{"uri": RESOURCE_URI, "mimeType": MIME_TYPE, "text": html}]}
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
