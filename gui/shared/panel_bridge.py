#!/usr/bin/env python3
"""Serve the shared panel's named operations from a trusted desktop adapter."""

import json
from pathlib import Path
import sys

from panel_tools import (
    READ_ERRORS, TASK_TOOLS, PanelError, adapter_result,
    run_task_tool, valid_arguments,
)
from project_panel import dashboard_target, locate_project, open_project, project_key, view_dashboard, view_project
from worklog_release import check_update, read_release_version


CLIENTS = {"dsh", "opencode"}
DASHBOARD = "open_worklog_dashboard"
PROJECT = "open_worklog_codex_panel"
UPDATE = "check_worklog_update"


def read_binding(request):
    if not isinstance(request, dict):
        raise PanelError("请求无效。", "invalid_input")
    client, surface, name = (request.get(key) for key in ("client", "surface", "name"))
    arguments = request.get("arguments")
    if (not isinstance(client, str) or client not in CLIENTS
            or surface not in ("dashboard", "project") or not isinstance(name, str)
            or not isinstance(arguments, dict)):
        raise PanelError("请求无效。", "invalid_input")
    if surface == "dashboard":
        return name, arguments, None, (f"{client}-dashboard", "global")
    session = request.get("sessionId")
    directory = request.get("directory")
    if (not isinstance(session, str) or not session or len(session) > 128
            or not isinstance(directory, str) or not Path(directory).is_absolute()):
        raise PanelError("当前会话已变化。请重新打开项目侧栏。", "stale_view")
    return name, arguments, Path(directory), (client, session)


def call(request):
    tool = None
    try:
        name, arguments, directory, source = read_binding(request)
        dashboard = directory is None
        if name == (DASHBOARD if dashboard else PROJECT) and not arguments:
            data = view_dashboard() if dashboard else view_project(directory)[0]
        elif name == UPDATE and arguments == {"component": "cli"}:
            manifest = Path(__file__).with_name("release-version.json")
            data = {"state": "ready", "component": "cli",
                    **check_update("cli", read_release_version(manifest))}
        else:
            tool = TASK_TOOLS.get(name)
            if tool is None or not valid_arguments(tool, arguments):
                raise PanelError("工具名称或参数无效。", "invalid_input")
            project_id = arguments.get("projectId")
            if dashboard:
                root, project = dashboard_target(project_id)
            else:
                if project_id is not None:
                    raise PanelError("项目侧栏只可操作当前会话关联的项目。", "project_mismatch")
                root, expected_id = locate_project(directory)
                with open_project(root, expected_id) as (_, info):
                    project = project_key(info)
            data = run_task_tool(tool, root, project, arguments, source)
    except READ_ERRORS as error:
        return adapter_result(error=error, tool=tool)
    return adapter_result(data=data)


def main():
    try:
        request = json.load(sys.stdin)
    except (ValueError, UnicodeError):
        result = adapter_result(error=PanelError("无效 JSON。", "invalid_input"))
    else:
        result = call(request)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
