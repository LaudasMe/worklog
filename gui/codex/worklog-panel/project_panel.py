"""只读取当前 Codex 聊天对应的 workLog 项目。"""

from contextlib import closing, contextmanager
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "runtime"))

from worklog_projects import read_registry, registry_path
from worklog_store import (
    SCHEMA_VERSION, TERMINAL_TASK_STATES, WorklogError, connect,
    effective_todos, entry_refs, latest_entry, metadata, project_db, require_project_id,
    schema_version, task_row,
)
from worklog_summaries import project_tasks


class PanelError(Exception):
    pass


class UnsupportedSchema(PanelError):
    def __init__(self, version):
        self.version = version
        super().__init__(f"当前项目的数据库 schema 为 {version}。侧栏支持的版本为 {SCHEMA_VERSION}。")


def locate_project(cwd):
    registered = {Path(path).resolve(): row["project_id"] for path, row in read_registry(registry_path()).items()}
    cwd = cwd.resolve()
    for root in (cwd, *cwd.parents):
        if root in registered:
            return root, registered[root]
        if project_db(root).is_file():
            return root, None
        if (root / ".git").exists():
            break
    raise PanelError("当前聊天目录未对应到 workLog 项目。请从项目目录建立本地聊天。")


@contextmanager
def project_reader(cwd):
    root, expected_id = locate_project(cwd)
    database = project_db(root)
    if not database.is_file() or database.resolve() != database:
        raise PanelError("当前项目的 workLog 数据库不可用。")
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
        "SELECT summary, recorded_at, client, details_json FROM context_index"
        " WHERE task_id = ? AND kind = 'PROGRESS' AND status = 'CURRENT'"
        " ORDER BY recorded_at DESC, position DESC LIMIT 1",
        (task_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "summary": row["summary"], "recorded_at": row["recorded_at"], "client": row["client"],
        "next_step": json.loads(row["details_json"]).get("suggested_next_step"),
    }


def newest(*records):
    return max((record for record in records if record is not None),
               key=lambda record: record["recorded_at"], default=None)


def task_card(db, task):
    task_id = task["task_id"]
    recent_entry = latest_entry(db, task_id)
    handoff = recent_entry if recent_entry and recent_entry["kind"] == "HANDOFF" else latest_entry(db, task_id, "HANDOFF")
    legacy_progress = recent_entry if recent_entry and recent_entry["kind"] == "PROGRESS" else latest_entry(db, task_id, "PROGRESS")
    saved_progress = latest_progress_record(db, task_id)
    progress = newest(saved_progress, legacy_progress)
    next_record = newest(saved_progress, recent_entry) if task["status"] not in TERMINAL_TASK_STATES else None
    return {
        "id": task_id,
        "number": task["number"],
        "title": task["title"],
        "status": task["display_status"],
        "progress": {"summary": progress["summary"], "at": progress["recorded_at"], "client": progress["client"]} if progress else None,
        "nextStep": next_record["next_step"] if next_record else None,
        "handoff": {
            "id": handoff["entry_id"], "summary": handoff["summary"], "at": handoff["recorded_at"],
            "client": handoff["client"], "nextStep": handoff["next_step"],
        } if handoff else None,
    }


def view_project(cwd):
    with project_reader(cwd) as (db, project):
        view = {
            "state": "ready",
            "projectName": project["name"],
            "tasks": [task_card(db, task) for task in project_tasks(db)],
        }
        return view, project_key(project)


def project_key(project):
    return project["project_id"], project["data_generation"]


@contextmanager
def task_reader(cwd, task_id, expected_project):
    with project_reader(cwd) as (db, project):
        if project_key(project) != expected_project:
            raise PanelError("当前项目数据已更换，请刷新任务列表后再试。")
        try:
            task = task_row(db, task_id)
        except WorklogError as error:
            if error.code == "task_not_found":
                raise PanelError("这项任务已变化或不可用，请刷新任务列表后再试。") from error
            raise
        if task["archived"]:
            raise PanelError("这项任务已归档，请刷新任务列表。")
        yield db, task


def read_handoff(cwd, task_id, entry_id, expected_project):
    with task_reader(cwd, task_id, expected_project) as (db, _):
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
        if command in ("task-archive", "task-unarchive"):
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
        if command == "task-complete" and task.get("completion"):
            details.extend(["", task["completion"]["completed_scope"]])
        yield ({
            "id": change_id, "kind": "STATUS", "source": "change",
            "summary": summary, "at": change["committed_at"], "client": None, "superseded": False,
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
        outcome = json.loads(row["details_json"]).get("outcome_md")
        if status == "OPEN":
            if key in dispositions:
                status, outcome = "DISPOSED", dispositions[key]
            elif key not in open_ids:
                status = "INACTIVE"
        item = {"summary": row["summary"], "status": status, "outcome": outcome}
        (open_items if status == "OPEN" else processed_items).append(item)
    return {"open": open_items, "processed": processed_items}


def history_view(db, task_id, changes):
    history = [{
        "id": row["entry_id"], "kind": row["kind"], "source": "entry",
        "summary": row["summary"], "at": row["recorded_at"], "client": row["client"], "superseded": False,
    } for row in entry_refs(db, task_id)]
    rows = db.execute(
        "SELECT record_id, kind, scope, summary, recorded_at, client, superseded_by FROM context_index"
        " WHERE (task_id = ? OR related_task_id = ?) AND kind IN ('PROGRESS', 'DECISION')",
        (task_id, task_id),
    )
    history.extend({
        "id": row["record_id"], "kind": row["kind"], "source": "record",
        "summary": row["summary"], "at": row["recorded_at"], "client": row["client"],
        "projectRecord": row["scope"] == "PROJECT", "superseded": row["superseded_by"] is not None,
    } for row in rows)
    history.extend(item for item, _ in status_events(changes))
    return sorted(history, key=lambda item: item["at"], reverse=True)


def task_sections(cwd, task_id, expected_project):
    with task_reader(cwd, task_id, expected_project) as (db, task):
        changes = list(task_changes(db, task_id))
        return {
            "state": "ready",
            "todos": todos_view(db, task, changes),
            "history": history_view(db, task_id, changes),
        }


def read_history(cwd, task_id, source, record_id, expected_project):
    with task_reader(cwd, task_id, expected_project) as (db, _):
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
                " AND record_id = ? AND kind IN ('PROGRESS', 'DECISION')",
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
        return {"state": "unavailable", "message": str(error), "schemaVersion": error.version,
                "supportedSchemas": [SCHEMA_VERSION]}
    if isinstance(error, PanelError):
        message = str(error)
    elif isinstance(error, WorklogError) and error.code == "project_mismatch":
        message = "数据库身份与 workLog 项目登记不一致。请核对项目登记。"
    elif isinstance(error, sqlite3.Error):
        message = "workLog 数据库读取失败。请检查数据库是否可读、格式是否完整，再刷新侧栏。"
    elif isinstance(error, OSError):
        message = "无法访问当前项目的 workLog 文件。请检查文件和读取权限，再刷新侧栏。"
    else:
        message = "workLog 项目数据或登记格式无法读取。请检查项目后再刷新侧栏。"
    return {"state": "unavailable", "message": message}


READ_ERRORS = (PanelError, WorklogError, sqlite3.Error, OSError, ValueError)
