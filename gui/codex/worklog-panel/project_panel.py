"""读取当前聊天或本机登记中的 schema 4 workLog 项目。"""

from collections import Counter
from contextlib import closing, contextmanager
import json
import os
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "runtime"))

from worklog_projects import read_registry, registry_path
from worklog_store import (
    SCHEMA_VERSION, TERMINAL_TASK_STATES, WorklogError, connect,
    effective_todos, entry_refs, latest_entry, metadata, project_db, require_project_id,
    run_history, schema_version, task_row,
)
from worklog_summaries import project_tasks


class PanelError(Exception):
    def __init__(self, message, code="unavailable"):
        super().__init__(message)
        self.code = code


class UnsupportedSchema(PanelError):
    def __init__(self, version):
        self.version = version
        super().__init__(
            f"项目数据库 schema 为 {version}，当前支持 schema {SCHEMA_VERSION}。请核对数据库版本。",
            "unsupported_schema",
        )


def locate_project(cwd):
    registered = read_registry(registry_path())
    cwd = cwd.resolve()
    for root in (cwd, *cwd.parents):
        if str(root) in registered:
            return root, registered[str(root)]["project_id"]
        if project_db(root).is_file():
            return root, None
        if (root / ".git").exists():
            break
    raise PanelError("当前聊天未找到 workLog 项目。请从项目目录建立本地聊天。", "not_initialized")


@contextmanager
def open_project(root, expected_id=None):
    if root.resolve() != root:
        raise PanelError("项目目录已变化。请核对实际目录和 workLog 登记后刷新。", "invalid_project_path")
    database = project_db(root)
    if not database.is_file() or database.resolve() != database:
        raise PanelError("workLog 数据库不可用。请核对项目目录中的数据库后刷新。", "invalid_database")
    with closing(connect(database, "ro")) as db, db:
        db.execute("BEGIN")
        version = schema_version(db)
        if version != SCHEMA_VERSION:
            raise UnsupportedSchema(version)
        project = metadata(db)
        if expected_id is not None:
            require_project_id(project, expected_id)
        yield db, project


def latest_progress_record(db, task_id):
    row = db.execute(
        "SELECT summary, recorded_at, client, session_id, details_json FROM context_index"
        " WHERE task_id = ? AND kind = 'PROGRESS' AND status = 'CURRENT'"
        " ORDER BY recorded_at DESC, position DESC LIMIT 1",
        (task_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "summary": row["summary"], "recorded_at": row["recorded_at"], "client": row["client"],
        "session_id": row["session_id"],
        "next_step": json.loads(row["details_json"]).get("suggested_next_step"),
    }


def newest(*records):
    return max((record for record in records if record is not None),
               key=lambda record: record["recorded_at"], default=None)


def task_card(db, task, revision):
    task_id = task["task_id"]
    recent_entry = latest_entry(db, task_id)
    handoff = recent_entry if recent_entry and recent_entry["kind"] == "HANDOFF" else latest_entry(db, task_id, "HANDOFF")
    legacy_progress = recent_entry if recent_entry and recent_entry["kind"] == "PROGRESS" else latest_entry(db, task_id, "PROGRESS")
    saved_progress = latest_progress_record(db, task_id)
    progress = newest(saved_progress, legacy_progress) or handoff
    next_record = newest(saved_progress, recent_entry) if task["status"] not in TERMINAL_TASK_STATES else None
    return {
        "id": task_id,
        "number": task["number"],
        "title": task["title"],
        "status": task["display_status"],
        "lifecycle": task["status"],
        "blocked": task["blocked"],
        "revision": revision,
        "updatedAt": task["updated_at"],
        "progress": {
            "summary": progress["summary"], "at": progress["recorded_at"],
            "client": progress["client"], "sessionId": progress["session_id"],
        } if progress else None,
        "nextStep": next_record["next_step"] if next_record else None,
        "handoff": {
            "id": handoff["entry_id"], "summary": handoff["summary"], "at": handoff["recorded_at"],
            "client": handoff["client"], "sessionId": handoff["session_id"], "nextStep": handoff["next_step"],
        } if handoff else None,
    }


def project_view(root, expected_id=None):
    with open_project(root, expected_id) as (db, project):
        revisions = {row["task_id"]: row["revision"] for row in db.execute(
            "SELECT task_id, revision FROM tasks WHERE archived = 0"
        )}
        database = project_db(root)
        return {
            "state": "ready",
            "id": project["project_id"],
            "generation": project["data_generation"],
            "projectName": project["name"],
            "path": str(root),
            "writable": os.access(database, os.W_OK) and os.access(database.parent, os.W_OK | os.X_OK),
            "tasks": [task_card(db, task, revisions[task["task_id"]]) for task in project_tasks(db)],
        }, project_key(project)


def view_project(cwd):
    return project_view(*locate_project(cwd))


def view_dashboard():
    registry = registry_path()
    try:
        registered = read_registry(registry)
    except WorklogError:
        raise PanelError("workLog 项目登记格式有误。请检查登记文件后刷新。", "invalid_registry") from None
    except OSError:
        raise PanelError("无法访问 workLog 项目登记。请检查文件读取权限后刷新。", "registry_unreadable") from None

    projects = []
    for path, item in sorted(registered.items()):
        expected_id = item["project_id"]
        root = Path(path)
        try:
            view, _ = project_view(root, expected_id)
        except READ_ERRORS as error:
            view = {**unavailable(error), "id": expected_id, "path": path,
                    "projectName": root.name or path, "writable": False}
        projects.append(view)
    counts = Counter(view["id"] for view in projects if view["state"] == "ready")
    for index, view in enumerate(projects):
        if view["state"] == "ready" and counts[view["id"]] > 1:
            projects[index] = {
                "id": view["id"], "path": view["path"], "projectName": view["projectName"], "writable": False,
                **unavailable(PanelError("多个登记目录读取到相同项目 ID。请核对项目登记后刷新。", "duplicate_project")),
            }
    return {"state": "ready", "projects": projects}


def registered_target(root, expected_project):
    registered = read_registry(registry_path())
    item = registered.get(str(root))
    if item is None or item["project_id"] != expected_project[0]:
        raise PanelError("项目登记已变化。请刷新 Dashboard 后再试。", "project_mismatch")



def project_key(project):
    return project["project_id"], project["data_generation"]


@contextmanager
def task_reader(cwd, task_id, expected_project, *, exact=False):
    root, registered_id = (cwd, expected_project[0]) if exact else locate_project(cwd)
    with open_project(root, registered_id) as (db, project):
        if project_key(project) != expected_project:
            raise PanelError("当前项目数据已更换，请刷新任务列表后再试。", "project_mismatch")
        try:
            task = task_row(db, task_id)
        except WorklogError as error:
            if error.code == "task_not_found":
                raise PanelError("这项任务已变化或不可用，请刷新任务列表后再试。") from error
            raise
        if task["archived"]:
            raise PanelError("这项任务已归档，请刷新任务列表。")
        yield db, task


def read_handoff(cwd, task_id, entry_id, expected_project, *, exact=False):
    with task_reader(cwd, task_id, expected_project, exact=exact) as (db, _):
        handoff = latest_entry(db, task_id, "HANDOFF")
        if not handoff or handoff["entry_id"] != entry_id:
            raise PanelError("这条交接已变化或不可用，请刷新任务列表后再试。")
        row = db.execute("SELECT body_md FROM entries WHERE task_id = ? AND entry_id = ? AND kind = 'HANDOFF'",
                         (task_id, entry_id)).fetchone()
        return {"state": "ready", "body": row["body_md"], "nextStep": handoff["next_step"]}


TASK_STATUS_LABELS = {
    "TODO": "待开始",
    "IN_PROGRESS": "进行中",
    "BLOCKED": "受阻",
    "PAUSED": "已暂停",
    "DONE": "已完成",
    "CANCELLED": "已取消",
}


def task_changes(db, task_id):
    rows = db.execute(
        "SELECT change_id, payload, task_v2_json FROM changes"
        " WHERE json_extract(payload, '$.task.task_id') = ? ORDER BY seq",
        (task_id,),
    )
    for row in rows:
        change = json.loads(row["payload"])
        projection = row["task_v2_json"]
        display = json.loads(projection)["display_status"] if projection else change["task"]["status"]
        yield row["change_id"], change, display


def status_events(changes):
    previous_base = previous_display = None
    for change_id, change, display in changes:
        task = change["task"]
        base = task["status"]
        command = change["kind"]
        claim = task.get("active_run") if command == "claim" else None
        if claim:
            summary = "接手任务"
        elif command in ("task-archive", "task-unarchive"):
            summary = "任务已归档" if command == "task-archive" else "任务已恢复显示"
        elif previous_display and (display != previous_display or base != previous_base):
            before, after = (previous_display, display) if display != previous_display else (previous_base, base)
            summary = f"{TASK_STATUS_LABELS[before]} → {TASK_STATUS_LABELS[after]}"
        else:
            summary = None
        previous_base, previous_display = base, display
        if summary is None:
            continue
        details = [summary, f"操作：{command}"]
        reason = change.get("lifecycle", {}).get("reason")
        if reason:
            details.extend(["", reason])
        if claim:
            details.extend(["", f"来源会话：{claim['client']} · {claim['session_id']}"])
        completion = task.get("completion") if command == "task-complete" else None
        if completion:
            details.extend(["", "完成范围", completion["completed_scope"], "", "完成依据"])
            details.extend(f"{item['command']} {item['id']}" for item in completion["evidence"])
            if completion["todo_dispositions"]:
                details.extend(["", "待办处置", *completion["todo_dispositions"].values()])
        yield ({
            "id": change_id, "kind": "STATUS", "source": "change",
            "summary": summary, "at": change["committed_at"],
            "client": claim["client"] if claim else None, "sessionId": claim["session_id"] if claim else None,
            "superseded": False,
        }, "\n".join(details))


def todos_view(db, task, changes):
    task_id = task["task_id"]
    open_ids = {row["record_id"] for row in effective_todos(db, task_id)}
    dispositions = {}
    for _, change, _ in changes:
        if change["kind"] == "task-complete":
            dispositions.update(change["task"].get("completion", {}).get("todo_dispositions", {}))
    open_items, processed_items = [], []
    rows = db.execute(
        "SELECT record_id, summary, status, details_json FROM context_index"
        " WHERE task_id = ? AND kind = 'TODO' AND superseded_by IS NULL"
        " ORDER BY recorded_at DESC, position DESC",
        (task_id,),
    )
    for row in rows:
        key = row["record_id"]
        status = row["status"]
        details = json.loads(row["details_json"])
        outcome = details.get("outcome_md")
        if status == "OPEN":
            if key in dispositions and (task["status"] == "DONE" or key not in open_ids):
                status, outcome = "DISPOSED", dispositions[key]
            elif key not in open_ids:
                status = "INACTIVE"
        elif status == "RESOLVED" and details.get("outcome_kind") == "DISPOSED":
            status = "DISPOSED"
        item = {"summary": row["summary"], "status": status, "outcome": outcome}
        (open_items if status == "OPEN" else processed_items).append(item)
    return {"open": open_items, "processed": processed_items}


def history_view(db, task_id, changes):
    history = [{
        "id": row["entry_id"], "kind": row["kind"], "source": "entry",
        "summary": row["summary"], "at": row["recorded_at"], "client": row["client"],
        "sessionId": row["session_id"], "superseded": False,
    } for row in entry_refs(db, task_id)]
    rows = db.execute(
        "SELECT record_id, kind, scope, summary, recorded_at, client, session_id, superseded_by FROM context_index"
        " WHERE (task_id = ? OR related_task_id = ?) AND kind IN ('PROGRESS', 'DECISION', 'BLOCKER')",
        (task_id, task_id),
    )
    history.extend({
        "id": row["record_id"], "kind": row["kind"], "source": "record",
        "summary": row["summary"], "at": row["recorded_at"], "client": row["client"],
        "sessionId": row["session_id"], "projectRecord": row["scope"] == "PROJECT", "superseded": row["superseded_by"] is not None,
    } for row in rows)
    history.extend(item for item, _ in status_events(changes))
    return sorted(history, key=lambda item: item["at"], reverse=True)


def participating_sessions(db, task_id):
    pairs = {(row["client"], row["session_id"]) for row in run_history(db, task_id)}
    pairs.update(tuple(row) for row in db.execute(
        "SELECT client, session_id FROM context_records"
        " WHERE (task_id = ? OR related_task_id = ?) AND kind = 'PROGRESS'",
        (task_id, task_id),
    ))
    return [{"client": client, "sessionId": session} for client, session in sorted(
        (client, session) for client, session in pairs if client and session
    )]


def task_sections(cwd, task_id, expected_project, *, exact=False):
    with task_reader(cwd, task_id, expected_project, exact=exact) as (db, task):
        changes = list(task_changes(db, task_id))
        return {
            "state": "ready",
            "todos": todos_view(db, task, changes),
            "history": history_view(db, task_id, changes),
            "sessions": participating_sessions(db, task_id),
        }


def read_history(cwd, task_id, source, record_id, expected_project, *, exact=False):
    with task_reader(cwd, task_id, expected_project, exact=exact) as (db, _):
        if source == "entry":
            row = db.execute(
                "SELECT body_md, kind, next_step FROM entries WHERE task_id = ? AND entry_id = ?",
                (task_id, record_id),
            ).fetchone()
            if row:
                return {"state": "ready", "body": row["body_md"],
                        "nextStep": row["next_step"] if row["kind"] == "HANDOFF" else None}
        elif source == "record":
            row = db.execute(
                "SELECT body_md FROM context_records WHERE (task_id = ? OR related_task_id = ?)"
                " AND record_id = ? AND kind IN ('PROGRESS', 'DECISION', 'BLOCKER')",
                (task_id, task_id, record_id),
            ).fetchone()
            if row:
                return {"state": "ready", "body": row["body_md"]}
        elif source == "change":
            for item, body in status_events(task_changes(db, task_id)):
                if item["id"] == record_id:
                    return {"state": "ready", "body": body}
        raise PanelError("这条历史记录不可用，请刷新任务列表后再试。")


def unavailable(error):
    if isinstance(error, UnsupportedSchema):
        return {"state": "unavailable", "code": error.code, "message": str(error), "schemaVersion": error.version,
                "supportedSchemas": [SCHEMA_VERSION]}
    code = getattr(error, "code", "unavailable")
    if isinstance(error, PanelError):
        message = str(error)
    elif isinstance(error, WorklogError) and error.code == "project_mismatch":
        message = "数据库身份与 workLog 项目登记不一致。请核对项目登记。"
    elif code == "invalid_project_path":
        message = "找不到登记的项目目录。请恢复目录或修正项目登记后刷新。"
    elif code == "invalid_registry":
        message = "无法读取 workLog 项目登记。请检查登记文件的格式和读取权限后刷新。"
    elif isinstance(error, sqlite3.Error):
        message = "workLog 数据库读取失败。请检查数据库是否可读、格式是否完整，再刷新。"
    elif isinstance(error, OSError):
        message = "无法访问当前项目的 workLog 文件。请检查文件和读取权限，再刷新。"
    else:
        message = "workLog 项目数据或登记格式无法读取。请检查项目后再刷新。"
    return {"state": "unavailable", "code": code, "message": message}


READ_ERRORS = (PanelError, WorklogError, sqlite3.Error, OSError, ValueError, RuntimeError)
