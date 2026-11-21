"""Storage and business rules for a single local workLog project."""

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
from uuid import uuid4

PROTOCOL_VERSION = "2.0"
CONTEXT_VERSION = "1.0"
KINDS = ("GOAL", "CONSTRAINT", "DECISION", "DOCUMENT", "PROGRESS", "QUESTION", "TODO", "BLOCKER")
LEGACY_SCHEMA_VERSION = 1
CONTEXT_SCHEMA_VERSION = 2
LIFECYCLE_SCHEMA_VERSION = 3
SCHEMA_VERSION = 4
TASK_TRANSITIONS = {
    "task-start": ("TODO", "IN_PROGRESS"),
    "task-pause": ("IN_PROGRESS", "PAUSED"),
    "task-resume": ("PAUSED", "IN_PROGRESS"),
}
TASK_VISIBILITY_WRITES = ("task-archive", "task-unarchive")
TASK_CLOSURE_WRITES = ("task-cancel", "task-reopen", *TASK_VISIBILITY_WRITES)
TASK_LIFECYCLE_WRITES = (*TASK_TRANSITIONS, "task-complete", *TASK_CLOSURE_WRITES)
BLOCKED_DISPLAY_BASE_STATES = ("TODO", "IN_PROGRESS")
OPEN_TASK_STATES = (*BLOCKED_DISPLAY_BASE_STATES, "PAUSED")
TERMINAL_TASK_STATES = ("DONE", "CANCELLED")
TASK_PRIORITIES = ("p0", "p1", "p2", "p3")
TASK_ACTIONS = ("TODO", "BLOCKER")

OPEN_TASK_ACTIONS_SQL = f"""
    SELECT item.* FROM context_index item
    WHERE item.task_id IS NOT NULL AND item.kind IN {TASK_ACTIONS} AND item.status = 'OPEN'
"""
# Reopen saves the context position at which old items stopped applying. A DONE
# reopen keeps the TODO scope, including any cutoff from an earlier cancellation.
CURRENT_TASK_ACTIONS_SQL = OPEN_TASK_ACTIONS_SQL + """
      AND item.position > COALESCE((
          SELECT json_extract(payload, '$.lifecycle.context_position') FROM changes
          WHERE json_extract(payload, '$.task.task_id') = item.task_id
            AND json_extract(payload, '$.kind') = 'task-reopen'
            AND (item.kind = 'BLOCKER' OR json_extract(payload, '$.lifecycle.previous_status') = 'CANCELLED')
          ORDER BY seq DESC LIMIT 1
      ), 0)
"""
EFFECTIVE_BLOCKERS_SQL = f"""
    SELECT b.* FROM ({CURRENT_TASK_ACTIONS_SQL}) b JOIN tasks owner ON owner.task_id = b.task_id
    WHERE b.kind = 'BLOCKER' AND owner.status IN {OPEN_TASK_STATES}
"""
EFFECTIVE_TODOS_SQL = f"""
    SELECT a.* FROM ({CURRENT_TASK_ACTIONS_SQL}) a JOIN tasks owner ON owner.task_id = a.task_id
    WHERE a.kind = 'TODO' AND owner.status <> 'CANCELLED'
"""
TASK_BASE_FIELDS = ("task_id", "number", "title", "status", "revision", "created_at", "updated_at")
CAPABILITIES = ["identity", "task-get", "snapshot", "changes", "entry-get"]
MAX_SEQUENCE = 2**63 - 1
ENTRY_FIELDS = (
    "e.entry_id, e.task_id, e.run_id, e.kind, e.summary, e.next_step,"
    " e.recorded_at, r.client, r.session_id"
)


class WorklogError(Exception):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code = code
        self.details = details


def json_text(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def uid():
    return str(uuid4())


def project_db(root):
    path = Path(root).expanduser().resolve()
    if not path.is_dir():
        raise WorklogError("invalid_project_path", "Project location must be an existing directory.")
    return path / ".worklog" / "worklog.sqlite3"


def connect(path, mode):
    db = sqlite3.connect(f"{path.as_uri()}?mode={mode}", uri=True, timeout=5,
                         isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def schema_version(db):
    return db.execute("PRAGMA user_version").fetchone()[0]


def context_supported(db):
    return schema_version(db) >= CONTEXT_SCHEMA_VERSION


def origin(command, key):
    return {"command": command, "id": key}


def metadata(db):
    if not LEGACY_SCHEMA_VERSION <= schema_version(db) <= SCHEMA_VERSION:
        raise WorklogError("unsupported_schema", "Unsupported workLog database schema.")
    project = db.execute("SELECT project_id, data_generation, name FROM project").fetchone()
    if project is None:
        raise WorklogError("invalid_database", "Project identity is missing.")
    return dict(project)


def require_fields(request, required, optional=()):
    missing = sorted(set(required) - request.keys())
    unknown = sorted(request.keys() - set(required) - set(optional))
    if missing or unknown:
        raise WorklogError("invalid_input", "Unexpected or missing request fields.",
                           missing=missing, unknown=unknown)


def string_field(request, key):
    value = request[key]
    if not isinstance(value, str) or not value.strip():
        raise WorklogError("invalid_input", f"{key} must be a non-empty string.")
    return value


def integer(value, key, minimum, maximum=MAX_SEQUENCE):
    if type(value) is not int or not minimum <= value <= maximum:
        raise WorklogError("invalid_input", f"{key} must be an integer from {minimum} to {maximum}.")
    return value


def priority_value(value):
    if value is not None and value not in TASK_PRIORITIES:
        raise WorklogError("invalid_input", "priority must be p0, p1, p2, p3, or null.")
    return value


def identity(project):
    return {"name": project["name"], "capabilities": CAPABILITIES}


def initialize(path, request, *, on_create=None):
    require_fields(request, ("name",))
    name = string_field(request, "name")
    path.parent.mkdir(mode=0o700, exist_ok=True)
    created = False
    if not path.is_file():
        schema = "\n".join(
            Path(__file__).with_name(name).read_text(encoding="utf-8")
            for name in ("schema.sql", "schema_context.sql", "schema_lifecycle.sql", "schema_v4.sql")
        )
        fd, filename = tempfile.mkstemp(prefix=".init-", dir=path.parent)
        os.close(fd)
        temporary = Path(filename)
        try:
            with closing(connect(temporary, "rw")) as db:
                db.executescript("BEGIN IMMEDIATE;\n" + schema)
                db.execute("INSERT INTO project VALUES (1, ?, ?, ?)", (uid(), uid(), name))
                db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                db.commit()
            # Publish a complete database without overwriting a concurrent initializer.
            try:
                os.link(temporary, path)
                created = True
            except FileExistsError:
                pass
        finally:
            temporary.unlink()
    with closing(connect(path, "ro")) as db:
        project = metadata(db)
    if project["name"] != name:
        raise WorklogError("already_initialized", "Directory already contains a different project name.")
    if created and on_create is not None:
        on_create(path.parent.parent, project)
    return project, identity(project)


def task_row(db, key, *, allow_number=False):
    if allow_number and re.fullmatch(r"T[0-9]+", key):
        task = db.execute("SELECT * FROM tasks WHERE number = ?", (key,)).fetchone()
    else:
        task = db.execute("SELECT * FROM tasks WHERE task_id = ?", (key,)).fetchone()
    if task is None:
        raise WorklogError("task_not_found", "Task does not exist.", task=key)
    return task


def open_run(db, task_id):
    row = db.execute(
        "SELECT run_id, client, session_id, opened_at FROM task_runs"
        " WHERE task_id = ? AND state = 'OPEN'", (task_id,),
    ).fetchone()
    return dict(row) if row else None


def close_run(db, task_id, at):
    db.execute("UPDATE task_runs SET state = 'CLOSED', closed_at = ? WHERE task_id = ? AND state = 'OPEN'",
               (at, task_id))


def run_history(db, task_id):
    return [dict(row) for row in db.execute(
        "SELECT run_id, client, session_id, state, opened_at, closed_at FROM task_runs"
        " WHERE task_id = ? ORDER BY opened_at, rowid", (task_id,),
    )]


def entry_query(columns):
    return f"SELECT {columns} FROM entries e JOIN task_runs r ON r.run_id = e.run_id"


def entry_refs(db, task_id=None):
    query = entry_query(ENTRY_FIELDS)
    if task_id is not None:
        return [dict(row) for row in db.execute(query + " WHERE e.task_id = ? ORDER BY e.position", (task_id,))]
    return [dict(row) for row in db.execute(query + " ORDER BY e.position")]


def latest_entry(db, task_id, kind=None):
    query = entry_query(ENTRY_FIELDS) + " WHERE e.task_id = ?"
    args = (task_id,)
    if kind is not None:
        query += " AND e.kind = ?"
        args += (kind,)
    row = db.execute(query + " ORDER BY e.position DESC LIMIT 1", args).fetchone()
    return dict(row) if row else None


def task_view(db, task):
    task_id = task["task_id"]
    latest = latest_entry(db, task_id)
    result = {
        **{key: task[key] for key in TASK_BASE_FIELDS},
        "active_run": open_run(db, task_id),
        "latest_entry": latest,
        "latest_handoff": latest_entry(db, task_id, "HANDOFF"),
        "next_step": latest["next_step"] if latest else None,
    }
    if "completion_json" in task.keys() and task["completion_json"] is not None:
        result["completion"] = json.loads(task["completion_json"])
    return result


def effective_blockers_sql(db):
    if schema_version(db) < SCHEMA_VERSION:
        return f"SELECT b.* FROM ({OPEN_TASK_ACTIONS_SQL}) b WHERE b.kind = 'BLOCKER'"
    return EFFECTIVE_BLOCKERS_SQL


def effective_blockers(db, task_id=None):
    if not context_supported(db):
        return []
    query = effective_blockers_sql(db)
    params = ()
    if task_id is not None:
        query += " AND b.task_id = ?"
        params = (task_id,)
    return db.execute(query + " ORDER BY b.position", params).fetchall()


def effective_todos(db, task_id):
    projection = EFFECTIVE_TODOS_SQL
    if schema_version(db) < SCHEMA_VERSION:
        projection = f"SELECT * FROM ({OPEN_TASK_ACTIONS_SQL}) WHERE kind = 'TODO'"
    return db.execute(
        f"SELECT * FROM ({projection}) WHERE task_id = ? ORDER BY position",
        (task_id,),
    ).fetchall()


def task_position(db, task_id):
    if not context_supported(db):
        return 0
    return db.execute("SELECT COALESCE(MAX(position), 0) FROM context_records WHERE task_id = ?",
                      (task_id,)).fetchone()[0]


def task_lifecycle_history(db, task_id):
    return [change_view(row) for row in db.execute(
        f"SELECT * FROM changes WHERE json_extract(payload, '$.task.task_id') = ?"
        f" AND json_extract(payload, '$.kind') IN {TASK_LIFECYCLE_WRITES} ORDER BY seq", (task_id,),
    )]


def available_reopen_candidates(db, task_id):
    event = db.execute(
        "SELECT json_extract(payload, '$.lifecycle') FROM changes"
        " WHERE json_extract(payload, '$.task.task_id') = ?"
        " AND json_extract(payload, '$.kind') = 'task-reopen' ORDER BY seq DESC LIMIT 1", (task_id,),
    ).fetchone()
    if event is None:
        return []
    lifecycle = json.loads(event[0])
    carried = {row[0] for row in db.execute(
        "SELECT json_extract(ref.reference_json, '$.target_record_id')"
        " FROM context_records item JOIN context_record_refs ref ON ref.record_id = item.record_id"
        " WHERE item.task_id = ? AND item.position > ?"
        " AND json_extract(ref.reference_json, '$.relation') = 'CARRY_FORWARD'",
        (task_id, lifecycle["context_position"]),
    )}
    return [candidate for candidate in lifecycle["reopen_candidates"] if candidate["record_id"] not in carried]


def task_display_status(status, blocked):
    return "BLOCKED" if blocked and status in BLOCKED_DISPLAY_BASE_STATES else status


def task_v2_view(db, task, base):
    blocker_refs = [origin("record-get", row["record_id"])
                    for row in effective_blockers(db, task["task_id"])]
    return {
        **base,
        "display_status": task_display_status(task["status"], blocker_refs),
        "archived": bool(task["archived"]),
        "priority": task["priority"],
        "blocker_refs": blocker_refs,
    }


def create_task(db, request, at):
    title = string_field(request, "title")
    priority = priority_value(request.get("priority"))
    last = db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    task_id = uid()
    db.execute("INSERT INTO tasks (task_id, number, title, status, revision, created_at, updated_at)"
               " VALUES (?, ?, ?, 'TODO', 1, ?, ?)",
               (task_id, f"T{last + 1:03}", title, at, at))
    if priority is not None:
        db.execute("UPDATE tasks SET priority = ? WHERE task_id = ?", (priority, task_id))
    return task_id, None


def claim_task(db, request, task_id, active, at):
    client = string_field(request, "client")
    session = string_field(request, "session_id")
    replaces = string_field(request, "replaces_run_id") if "replaces_run_id" in request else None
    active_id = active["run_id"] if active else None
    if replaces != active_id:
        raise WorklogError("claim_conflict", "Read the current task and confirm any active takeover.",
                           active_run=active)
    if active:
        db.execute("UPDATE task_runs SET state = 'SUPERSEDED', closed_at = ? WHERE run_id = ?",
                   (at, active_id))
    db.execute("INSERT OR IGNORE INTO client_sessions VALUES (?, ?)", (client, session))
    db.execute("INSERT INTO task_runs VALUES (?, ?, ?, ?, ?, 'OPEN', ?, NULL)",
               (uid(), task_id, client, session, os.getcwd(), at))
    db.execute("UPDATE tasks SET status = 'IN_PROGRESS' WHERE task_id = ? AND status = 'TODO'", (task_id,))


def require_task_state(task, allowed):
    if task["status"] not in allowed:
        raise WorklogError("task_state_conflict", "This operation is unavailable in the current task state.",
                           status=task["status"], expected=list(allowed))


def completion_sources(db, task_id, evidence, *, allow_confirmation=False):
    if not isinstance(evidence, list) or not evidence:
        raise WorklogError("invalid_input", "evidence must be a non-empty array of work records.")
    sources = []
    for ref in evidence:
        if allow_confirmation and isinstance(ref, dict) and ref.get("type") == "USER_CONFIRMATION":
            require_fields(ref, ("type", "note"))
            sources.append({"type": "USER_CONFIRMATION", "note": string_field(ref, "note")})
            continue
        if not isinstance(ref, dict) or ref.get("type") not in ("RECORD", "ENTRY"):
            raise WorklogError("invalid_input", "Each completion reference must select a RECORD or ENTRY.")
        if ref["type"] == "RECORD":
            require_fields(ref, ("type", "target_record_id"))
            key = string_field(ref, "target_record_id")
            row = db.execute(
                "SELECT 1 FROM context_index WHERE record_id = ? AND kind = 'PROGRESS'"
                " AND status = 'CURRENT' AND COALESCE(task_id, related_task_id) = ?", (key, task_id),
            ).fetchone()
            command = "record-get"
        else:
            require_fields(ref, ("type", "target_entry_id"))
            key = string_field(ref, "target_entry_id")
            row = db.execute("SELECT 1 FROM entries WHERE entry_id = ? AND task_id = ?",
                             (key, task_id)).fetchone()
            command = "entry-get"
        if row is None:
            raise WorklogError("invalid_evidence", "Choose an existing, current work record for this task.",
                               source=origin(command, key))
        sources.append(origin(command, key))
    return sources


def complete_task(db, task, request, at):
    require_task_state(task, OPEN_TASK_STATES)
    task_id = task["task_id"]
    completed_scope = string_field(request, "completed_scope")
    sources = completion_sources(db, task_id, request["evidence"])
    dispositions = request.get("todo_dispositions", {})
    if not isinstance(dispositions, dict):
        raise WorklogError("invalid_input", "todo_dispositions must be an object keyed by record ID.")
    for key in dispositions:
        string_field(dispositions, key)

    blockers = [{key: row[key] for key in ("record_id", "kind", "summary")}
                for row in effective_blockers(db, task_id)]
    if blockers:
        raise WorklogError("task_blocked", "Resolve or cancel the open task blockers before completing.",
                           blockers=blockers)
    todos = [{key: row[key] for key in ("record_id", "kind", "summary")}
             for row in effective_todos(db, task_id)]
    if dispositions.keys() != {todo["record_id"] for todo in todos}:
        raise WorklogError("todo_disposition_required", "Supply a disposition for each current open task TODO.",
                           todos=todos)

    completion = {"completed_at": at, "completed_scope": completed_scope, "evidence": sources,
                  "todo_dispositions": dispositions}
    close_run(db, task_id, at)
    db.execute("UPDATE tasks SET status = 'DONE', completion_json = ? WHERE task_id = ?",
               (json_text(completion), task_id))


def change_task_lifecycle(db, command, task, request, at):
    task_id = task["task_id"]
    previous_status = task["status"]
    if command == "task-cancel":
        require_task_state(task, OPEN_TASK_STATES)
        reason = string_field(request, "reason")
        close_run(db, task_id, at)
        db.execute("UPDATE tasks SET status = 'CANCELLED' WHERE task_id = ?", (task_id,))
        return {"previous_status": previous_status, "reason": reason}

    require_task_state(task, TERMINAL_TASK_STATES)
    if command in TASK_VISIBILITY_WRITES:
        archived = command == "task-archive"
        if archived == bool(task["archived"]):
            raise WorklogError("task_state_conflict", "Task already has the requested archive state.",
                               archived=archived)
        db.execute("UPDATE tasks SET archived = ? WHERE task_id = ?", (archived, task_id))
        return {"archived": archived}

    reason = string_field(request, "reason")
    evidence = completion_sources(db, task_id, request["evidence"], allow_confirmation=True)
    rows = db.execute(
        f"SELECT record_id, kind, status, summary FROM ({CURRENT_TASK_ACTIONS_SQL})"
        " WHERE task_id = ? AND (kind = 'BLOCKER' OR ? = 'CANCELLED') ORDER BY position",
        (task_id, previous_status),
    )
    candidates = [{**dict(row), "source": origin("record-get", row["record_id"])} for row in rows]
    db.execute("UPDATE tasks SET status = 'TODO', archived = 0, completion_json = NULL WHERE task_id = ?",
               (task_id,))
    return {"previous_status": previous_status, "archived": False, "reason": reason, "evidence": evidence,
            "context_position": task_position(db, task_id), "reopen_candidates": candidates}


def record_entry(db, command, request, task_id, run_id, at):
    summary, body, next_step = (string_field(request, key)
                                for key in ("summary", "body_md", "next_step"))
    entry_id = uid()
    kind = "HANDOFF" if command == "handoff" else "PROGRESS"
    db.execute(
        "INSERT INTO entries (entry_id, task_id, run_id, kind, summary, body_md, next_step, recorded_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (entry_id, task_id, run_id, kind, summary, body, next_step, at),
    )
    if command == "handoff":
        close_run(db, task_id, at)
    return latest_entry(db, task_id)


def require_project_id(project, expected_id):
    if expected_id is not None and project["project_id"] != expected_id:
        raise WorklogError("project_mismatch", "Project ID does not match. Check the selected path and identity.",
                           expected_project_id=expected_id, actual_project_id=project["project_id"])


def require_project(project, project_id, data_generation):
    if project["project_id"] != project_id:
        raise WorklogError("project_mismatch", "Project ID differs from the requested identity.")
    if project["data_generation"] != data_generation:
        raise WorklogError("generation_mismatch", "Data generation changed. Read a fresh snapshot.")


def write_request(db, project, command, request):
    common = ("operation_id", "project_id", "data_generation")
    task_fields = ("task_id", "expected_revision")
    lifecycle = command in TASK_LIFECYCLE_WRITES
    setting_priority = command == "task-set-priority"
    minimum_schema = LIFECYCLE_SCHEMA_VERSION if command in ("task-start", "task-complete") else SCHEMA_VERSION
    if lifecycle and schema_version(db) < minimum_schema:
        raise WorklogError("upgrade_required", "Back up and upgrade before changing task status.")
    if (setting_priority or (command == "task-create" and "priority" in request)) \
            and schema_version(db) < SCHEMA_VERSION:
        raise WorklogError("upgrade_required", "Back up and upgrade before setting or clearing task priority.")
    if command == "task-create":
        require_fields(request, common + ("title",), ("priority",))
    elif setting_priority:
        require_fields(request, common + task_fields + ("priority",))
    elif command in (*TASK_TRANSITIONS, *TASK_VISIBILITY_WRITES):
        require_fields(request, common + task_fields)
    elif command == "task-complete":
        require_fields(request, common + task_fields + ("completed_scope", "evidence"), ("todo_dispositions",))
    elif command == "task-cancel":
        require_fields(request, common + task_fields + ("reason",))
    elif command == "task-reopen":
        require_fields(request, common + task_fields + ("reason", "evidence"))
    elif command == "claim":
        require_fields(request, common + task_fields + ("client", "session_id"), ("replaces_run_id",))
    else:
        require_fields(request, common + task_fields + ("run_id", "summary", "body_md", "next_step"))
    operation = string_field(request, "operation_id")
    require_project(project, string_field(request, "project_id"),
                    string_field(request, "data_generation"))

    digest = hashlib.sha256(json_text([command, request]).encode("utf-8")).hexdigest()
    prior = db.execute("SELECT * FROM changes WHERE operation_id = ?", (operation,)).fetchone()
    if prior:
        if prior["request_hash"] != digest:
            raise WorklogError("operation_conflict", "Operation ID already belongs to a different request.")
        return change_view(prior)
    if context_supported(db) and db.execute(
        "SELECT 1 FROM context_operations WHERE operation_id = ?", (operation,)
    ).fetchone():
        raise WorklogError("operation_conflict", "Operation ID already belongs to a context request.")

    at = timestamp()
    lifecycle_details = None
    if command == "task-create":
        task_id, entry = create_task(db, request, at)
    else:
        expected_revision = integer(request["expected_revision"], "expected_revision", 1)
        task = task_row(db, string_field(request, "task_id"), allow_number=lifecycle or setting_priority)
        task_id = task["task_id"]
        active = open_run(db, task_id)
        run_id = string_field(request, "run_id") if command in ("checkpoint", "handoff") else None
        if run_id is not None and (active is None or active["run_id"] != run_id):
            raise WorklogError("stale_run", "The supplied run is no longer the active task run.",
                               active_run=active)
        if task["revision"] != expected_revision:
            raise WorklogError("revision_conflict", "Task changed since it was read.",
                               current_revision=task["revision"], active_run=active)
        entry = None
        if command in TASK_TRANSITIONS:
            previous, status = TASK_TRANSITIONS[command]
            require_task_state(task, (previous,))
            db.execute("UPDATE tasks SET status = ? WHERE task_id = ?", (status, task_id))
        elif command == "task-complete":
            complete_task(db, task, request, at)
        elif command in TASK_CLOSURE_WRITES:
            lifecycle_details = change_task_lifecycle(db, command, task, request, at)
        elif setting_priority:
            db.execute("UPDATE tasks SET priority = ? WHERE task_id = ?",
                       (priority_value(request["priority"]), task_id))
        elif command == "claim":
            require_task_state(task, OPEN_TASK_STATES)
            claim_task(db, request, task_id, active, at)
        else:
            entry = record_entry(db, command, request, task_id, run_id, at)
        db.execute("UPDATE tasks SET revision = revision + 1, updated_at = ? WHERE task_id = ?",
                   (at, task_id))

    return append_change(db, command, operation, digest, at, task_id, entry=entry, lifecycle=lifecycle_details)


def append_change(db, kind, operation_id, request_hash, at, task_id, *, entry=None, lifecycle=None):
    version = schema_version(db)
    if version < SCHEMA_VERSION and kind == "task-blockers":
        raise WorklogError("upgrade_required", "Back up and upgrade before saving blocker changes.")
    task = task_row(db, task_id)
    base = task_view(db, task)
    if kind == "task-set-priority":
        base["priority"] = task["priority"]
    payload = {"kind": kind, "committed_at": at, "task": base, "entry": entry}
    if lifecycle is not None:
        payload["lifecycle"] = lifecycle
    params = (uid(), operation_id, request_hash, json_text(payload))
    if version >= SCHEMA_VERSION:
        # Keep the historical schema 4 field stable for existing database copies.
        seq = db.execute(
            "INSERT INTO changes (change_id, operation_id, request_hash, payload, visible_in_v1)"
            " VALUES (?, ?, ?, ?, ?)", (*params, kind != "task-blockers"),
        ).lastrowid
        # A reopen's own context boundary determines which blockers enter its projection.
        db.execute("UPDATE changes SET task_v2_json = ? WHERE seq = ?",
                   (json_text(task_v2_view(db, task, base)), seq))
    else:
        seq = db.execute(
            "INSERT INTO changes (change_id, operation_id, request_hash, payload) VALUES (?, ?, ?, ?)",
            params,
        ).lastrowid
    return change_view(db.execute("SELECT * FROM changes WHERE seq = ?", (seq,)).fetchone())


def change_view(row):
    return {"seq": row["seq"], "change_id": row["change_id"], **json.loads(row["payload"])}


def watermark(db):
    return db.execute("SELECT COALESCE(MAX(seq), 0) FROM changes").fetchone()[0]


def v2_min_after(db):
    return db.execute("SELECT v2_min_after FROM protocol_state WHERE singleton = 1").fetchone()[0]


def public_task_view(db, task):
    return task_v2_view(db, task, task_view(db, task))


def snapshot(db):
    at = watermark(db)
    tasks = [public_task_view(db, task) for task in db.execute("SELECT * FROM tasks ORDER BY rowid")]
    return {"watermark": at, "tasks": tasks, "entry_refs": entry_refs(db)}


def changes(db, project, args):
    require_project(project, args.project_id, args.data_generation)
    after = integer(args.after, "after", 0)
    limit = integer(args.limit, "limit", 1, 500)
    minimum = v2_min_after(db)
    if after < minimum:
        raise WorklogError("cursor_stale", "Cursor predates the protocol 2.0 history. Read a fresh snapshot.",
                           min_after=minimum)
    high = watermark(db)
    if after > high:
        raise WorklogError("cursor_ahead", "Cursor exceeds the current watermark.", watermark=high)
    rows = db.execute(
        "SELECT * FROM changes WHERE seq > ? ORDER BY seq LIMIT ?",
        (after, limit),
    )
    result = []
    for row in rows:
        change = change_view(row)
        change["task"] = json.loads(row["task_v2_json"])
        change.pop("lifecycle", None)
        result.append(change)
    cursor = result[-1]["seq"] if result else after
    return {"changes": result, "next_after": cursor, "has_more": cursor < high, "watermark": high}


def read_request(db, project, command, args):
    if schema_version(db) < SCHEMA_VERSION:
        raise WorklogError("upgrade_required", "Back up and upgrade before using public protocol 2.0.")
    if command == "identity":
        return identity(project)
    if command == "task-get":
        return public_task_view(db, task_row(db, args.task, allow_number=True))
    if command == "snapshot":
        return snapshot(db)
    if command == "changes":
        return changes(db, project, args)
    row = db.execute(entry_query(ENTRY_FIELDS + ", e.body_md") + " WHERE e.entry_id = ?",
                     (args.entry_id,)).fetchone()
    if row is None:
        raise WorklogError("entry_not_found", "Work entry does not exist.", entry_id=args.entry_id)
    return dict(row)
