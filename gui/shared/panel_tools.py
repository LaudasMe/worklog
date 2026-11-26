"""Shared MCP task tool definitions and dispatch."""

from project_panel import READ_ERRORS, PanelError, read_handoff, read_history, task_reader, task_sections, unavailable
from task_actions import ACTION_FIELDS, action_unavailable, blocker_options, completion_options, mutate_task


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


TASK_TOOLS = {tool["name"]: tool for tool in (HANDOFF, SECTIONS, HISTORY, COMPLETION, BLOCKERS, MUTATE)}


def run_task_tool(tool, root, project, arguments, source):
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
        if tool is BLOCKERS:
            return blocker_options(db, task, arguments["expectedRevision"], source)
        return completion_options(db, task, arguments["expectedRevision"])


def adapter_result(*, data=None, error=None, tool=None):
    if error is not None:
        if ((tool is COMPLETION and getattr(error, "code", None) == "revision_conflict")
                or (tool is BLOCKERS and not isinstance(error, PanelError))):
            data = action_unavailable(error)
        else:
            data = unavailable(error)
    return {"content": [], "structuredContent": data}
