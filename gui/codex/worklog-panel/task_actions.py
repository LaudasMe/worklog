"""Codex 双端的任务操作，使用现有 workLog 本地写入规则。"""

from contextlib import closing
import json
from pathlib import Path
import sqlite3

from worklog_context import find_record, record
from worklog_store import (
    BLOCKED_DISPLAY_BASE_STATES, OPEN_TASK_STATES, SCHEMA_VERSION, TASK_TRANSITIONS, WorklogError, connect,
    effective_blockers, effective_todos, entry_refs, integer, metadata, open_run, project_db,
    require_fields, require_project, require_task_state, schema_version, string_field, task_position, task_row,
    write_request,
)


ACTION_FIELDS = {
    "task-create": ("title",),
    **{command: () for command in TASK_TRANSITIONS},
    "task-cancel": ("reason",),
    "task-reopen": ("reason",),
    "task-complete": ("completed_scope", "evidence"),
    "blocker-set": ("blocked_work", "unblock_condition"),
    "blocker-resolve": ("blocker_id", "outcome_md"),
}
BLOCKER_COMMANDS = {"blocker-set", "blocker-resolve"}
COMMON_FIELDS = ("operation_id", "project_id", "data_generation")
TASK_FIELDS = ("task_id", "expected_revision")
MESSAGES = {
    "invalid_input": "请补齐必填项并核对输入后重试。",
    "invalid_evidence": "所选完成依据已失效或不属于这项任务。请刷新后重新选择。",
    "task_blocked": "存在未解除的任务级阻塞。请处理阻塞后再确认完成。",
    "todo_disposition_required": "请刷新任务，并为每条当前开放的待办填写处置说明。",
    "revision_conflict": "任务已变化。请刷新列表后重新确认本次操作。",
    "handoff_required": "另一会话正在接手此任务。请先在该会话完成交接，再刷新任务并操作阻塞。",
    "stale_run": "任务的接手状态已变化。请刷新列表后重新确认本次操作。",
    "record_conflict": "这条阻塞已变化。请刷新列表后重新选择。",
    "record_not_found": "这条阻塞已不可用。请刷新列表后重新选择。",
    "task_state_conflict": "任务当前状态不支持此操作。请刷新列表。",
    "task_not_found": "任务已不可用。请刷新列表。",
    "operation_conflict": "这次操作编号已用于其他请求。请刷新列表后重试。",
    "project_mismatch": "项目身份已变化。请核对项目登记并刷新列表。",
    "generation_mismatch": "项目数据已更换。请刷新列表后重试。",
    "upgrade_required": "当前数据库版本不受支持。请先备份，并使用 workLog CLI 升级。",
    "invalid_project_path": "项目目录或数据库已不可用。请检查目录并刷新列表。",
    "database_busy": "项目数据库正忙。可以重试本次操作。",
    "storage_error": "无法写入 workLog 数据库。请检查文件权限和项目目录。",
}


def action_unavailable(error):
    if isinstance(error, WorklogError):
        code = error.code
    elif isinstance(error, sqlite3.Error):
        locked = str(error).startswith(("database is locked", "database table is locked", "database schema is locked"))
        code = "database_busy" if locked else "storage_error"
    elif isinstance(error, OSError):
        code = "storage_error"
    else:
        code = "invalid_input"
    return {
        "state": "unavailable",
        "code": code,
        "message": MESSAGES.get(code, "无法操作这项任务。请检查项目并刷新列表。"),
        "retryable": code == "database_busy",
    }


def require_revision(task, expected_revision):
    if task["revision"] != integer(expected_revision, "expected_revision", 1):
        raise WorklogError("revision_conflict", "Task changed since it was read.")


def completion_options(db, task, expected_revision):
    require_revision(task, expected_revision)
    require_task_state(task, OPEN_TASK_STATES)
    task_id = task["task_id"]
    evidence = [{
        "label": "交接" if entry["kind"] == "HANDOFF" else "进展",
        "summary": entry["summary"], "at": entry["recorded_at"], "client": entry["client"],
        "ref": {"type": "ENTRY", "target_entry_id": entry["entry_id"]},
    } for entry in reversed(entry_refs(db, task_id))]
    # Match worklog_store.completion_sources, including project-level progress
    # explicitly associated with the task. The write revalidates the choice.
    progress = db.execute(
        "SELECT record_id, summary, recorded_at, client FROM context_index"
        " WHERE kind = 'PROGRESS' AND status = 'CURRENT'"
        " AND COALESCE(task_id, related_task_id) = ? ORDER BY position DESC",
        (task_id,),
    )
    evidence.extend({
        "label": "进展", "summary": row["summary"], "at": row["recorded_at"], "client": row["client"],
        "ref": {"type": "RECORD", "target_record_id": row["record_id"]},
    } for row in progress)
    return {
        "state": "ready",
        "completedScope": task["title"],
        "revision": task["revision"],
        "evidence": sorted(evidence, key=lambda item: item["at"], reverse=True),
        "todos": [{"id": row["record_id"], "summary": row["summary"]} for row in effective_todos(db, task_id)],
        "blockers": [{"id": row["record_id"], "summary": row["summary"]} for row in effective_blockers(db, task_id)],
    }


def blocker_run(db, task, source):
    active = open_run(db, task["task_id"])
    if active is not None and (source[0] != "codex" or (active["client"], active["session_id"]) != source):
        raise WorklogError("handoff_required", "Hand off the active task run before editing blockers.")
    return active["run_id"] if active else None


def blocker_options(db, task, expected_revision, source):
    require_revision(task, expected_revision)
    require_task_state(task, OPEN_TASK_STATES)
    run_id = blocker_run(db, task, source)
    task_id = task["task_id"]
    blockers = []
    for row in effective_blockers(db, task_id):
        details = json.loads(row["details_json"])
        blockers.append({
            "id": row["record_id"], "summary": row["summary"], "at": row["recorded_at"], "client": row["client"],
            "blockedWork": details["blocked_work"], "unblockCondition": details["unblock_condition"],
        })
    return {
        "state": "ready",
        "contextPosition": task_position(db, task_id),
        "runId": run_id,
        "blockers": blockers,
    }


def blocker_item(db, command, payload):
    if command == "blocker-set":
        work = string_field(payload, "blocked_work")
        condition = string_field(payload, "unblock_condition")
        return {
            "kind": "BLOCKER", "summary": work, "body_md": f"{work}\n\n解除条件：{condition}",
            "details": {"status": "OPEN", "blocked_work": work, "unblock_condition": condition},
        }
    original = find_record(db, string_field(payload, "blocker_id"))
    if (original["task_id"], original["scope"], original["kind"]) != (payload["task_id"], "TASK", "BLOCKER"):
        raise WorklogError("record_not_found", "Blocker does not belong to this task.")
    reason = string_field(payload, "outcome_md")
    details = json.loads(original["details_json"])
    return {
        "kind": "BLOCKER", "summary": f"已解除：{original['summary']}", "body_md": reason,
        "details": {
            "status": "RESOLVED", "blocked_work": details["blocked_work"],
            "unblock_condition": details["unblock_condition"], "outcome_md": reason,
        },
        "replaces_id": original["record_id"],
        "refs": [{"type": "USER_CONFIRMATION", "note": reason}],
    }


def mutate_blocker(db, project, task, command, payload, source):
    operation_id = string_field(payload, "operation_id")
    if not db.execute("SELECT 1 FROM context_operations WHERE operation_id = ?", (operation_id,)).fetchone():
        if task["archived"]:
            raise WorklogError("task_not_found", "Task is archived.")
        allowed = BLOCKED_DISPLAY_BASE_STATES if command == "blocker-set" else OPEN_TASK_STATES
        require_task_state(task, allowed)
        blocker_run(db, task, source)
        if command == "blocker-resolve" and not any(
            row["record_id"] == payload["blocker_id"] for row in effective_blockers(db, task["task_id"])
        ):
            raise WorklogError("record_conflict", "Select a currently active blocker.")
    fields = COMMON_FIELDS + TASK_FIELDS + ("expected_context_position",)
    request = {key: payload[key] for key in fields}
    if "run_id" in payload:
        request["run_id"] = payload["run_id"]
    result = record(db, project, {
        **request, "scope": "TASK", "client": source[0], "session_id": source[1],
        "records": [blocker_item(db, command, payload)],
    })
    return {"state": "ready", "operationId": operation_id, "records": result["records"]}


def prepare_request(command, request):
    if not isinstance(command, str) or command not in ACTION_FIELDS or not isinstance(request, dict):
        raise WorklogError("invalid_input", "Invalid GUI task operation.")
    required = COMMON_FIELDS + ACTION_FIELDS[command]
    if command != "task-create":
        required += TASK_FIELDS
    optional = ("todo_dispositions",) if command == "task-complete" else ()
    if command in BLOCKER_COMMANDS:
        required += ("expected_context_position",)
        optional = ("run_id",)
    require_fields(request, required, optional)
    payload = dict(request)
    if command == "task-create":
        payload["title"] = string_field(payload, "title").strip()
    if command == "task-reopen":
        payload["evidence"] = [{"type": "USER_CONFIRMATION", "note": string_field(payload, "reason")}]
    return payload


def mutate_task(root, expected_project, command, request, *, source=None):
    """Write to a management directory already resolved by the calling panel."""
    try:
        payload = prepare_request(command, request)
        root = Path(root)
        database = project_db(root)
        if database.parent.parent != root or not database.is_file() or database.resolve() != database:
            raise WorklogError("invalid_project_path", "The selected database path is unavailable.")
        with closing(connect(database, "rw")) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if schema_version(db) != SCHEMA_VERSION:
                raise WorklogError("upgrade_required", "The GUI requires schema 4.")
            project = metadata(db)
            require_project(project, *expected_project)
            require_project(project, string_field(payload, "project_id"), string_field(payload, "data_generation"))
            operation_id = string_field(payload, "operation_id")
            if command != "task-create":
                # The GUI sends the internal ID. The CLI also accepts T001-style
                # numbers, which must not be used as a GUI write target.
                task = task_row(db, string_field(payload, "task_id"))
                if command in BLOCKER_COMMANDS:
                    if source is None:
                        raise WorklogError("invalid_input", "Missing workLog panel source.")
                    return mutate_blocker(db, project, task, command, payload, source)
                if task["archived"] and not db.execute(
                    "SELECT 1 FROM changes WHERE operation_id = ?", (operation_id,),
                ).fetchone():
                    raise WorklogError("task_not_found", "Task is archived.")
            result = write_request(db, project, command, payload)
        task = result["task"]
        return {
            "state": "ready",
            "operationId": operation_id,
            "task": {"id": task["task_id"], **{key: task[key] for key in ("number", "status", "revision")}},
        }
    except (WorklogError, sqlite3.Error, OSError, ValueError) as error:
        return action_unavailable(error)
