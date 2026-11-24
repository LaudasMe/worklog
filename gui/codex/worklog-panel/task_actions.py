"""Codex 双端的任务操作，使用现有 workLog 本地写入规则。"""

from contextlib import closing
from pathlib import Path
import sqlite3

from worklog_store import (
    OPEN_TASK_STATES, SCHEMA_VERSION, TASK_TRANSITIONS, WorklogError, connect,
    effective_blockers, effective_todos, entry_refs, integer, metadata, project_db,
    require_fields, require_project, require_task_state, schema_version, string_field, task_row,
    write_request,
)


ACTION_FIELDS = {
    "task-create": ("title",),
    **{command: () for command in TASK_TRANSITIONS},
    "task-cancel": ("reason",),
    "task-reopen": ("reason",),
    "task-complete": ("completed_scope", "evidence"),
}
COMMON_FIELDS = ("operation_id", "project_id", "data_generation")
TASK_FIELDS = ("task_id", "expected_revision")
MESSAGES = {
    "invalid_input": "请补齐必填项并核对输入后重试。",
    "invalid_evidence": "所选完成依据已失效或不属于这项任务。请刷新后重新选择。",
    "task_blocked": "存在未解除的任务级阻塞。请处理阻塞后再确认完成。",
    "todo_disposition_required": "请刷新任务，并为每条当前开放的待办填写处置说明。",
    "revision_conflict": "任务已变化。请刷新列表后重新确认本次操作。",
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


def completion_options(db, task, expected_revision):
    if task["revision"] != integer(expected_revision, "expected_revision", 1):
        raise WorklogError("revision_conflict", "Task changed since it was read.")
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


def prepare_request(command, request):
    if not isinstance(command, str) or command not in ACTION_FIELDS or not isinstance(request, dict):
        raise WorklogError("invalid_input", "Invalid GUI task operation.")
    required = COMMON_FIELDS + ACTION_FIELDS[command]
    if command != "task-create":
        required += TASK_FIELDS
    optional = ("todo_dispositions",) if command == "task-complete" else ()
    require_fields(request, required, optional)
    payload = dict(request)
    if command == "task-create":
        payload["title"] = string_field(payload, "title").strip()
    if command == "task-reopen":
        payload["evidence"] = [{"type": "USER_CONFIRMATION", "note": string_field(payload, "reason")}]
    return payload


def mutate_task(root, expected_project, command, request):
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
