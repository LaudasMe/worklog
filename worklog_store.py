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

PROTOCOL_VERSION = "1.0"
SCHEMA_VERSION = 1
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


def metadata(db):
    if db.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
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


def identity(project):
    return {"name": project["name"], "capabilities": CAPABILITIES}


def initialize(path, request):
    require_fields(request, ("name",))
    name = string_field(request, "name")
    path.parent.mkdir(mode=0o700, exist_ok=True)
    if not path.is_file():
        schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
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
            except FileExistsError:
                pass
        finally:
            temporary.unlink()
    with closing(connect(path, "ro")) as db:
        project = metadata(db)
    if project["name"] != name:
        raise WorklogError("already_initialized", "Directory already contains a different project name.")
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


def entry_query(columns):
    return f"SELECT {columns} FROM entries e JOIN task_runs r ON r.run_id = e.run_id"


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
    return {
        **dict(task),
        "active_run": open_run(db, task_id),
        "latest_entry": latest,
        "latest_handoff": latest_entry(db, task_id, "HANDOFF"),
        "next_step": latest["next_step"] if latest else None,
    }


def create_task(db, request, at):
    title = string_field(request, "title")
    last = db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    task_id = uid()
    db.execute("INSERT INTO tasks VALUES (?, ?, ?, 'TODO', 1, ?, ?)",
               (task_id, f"T{last + 1:03}", title, at, at))
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
    db.execute("UPDATE tasks SET status = 'IN_PROGRESS' WHERE task_id = ?", (task_id,))


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
        db.execute("UPDATE task_runs SET state = 'CLOSED', closed_at = ? WHERE run_id = ?",
                   (at, run_id))
    return latest_entry(db, task_id)


def require_project(project, project_id, data_generation):
    if project["project_id"] != project_id:
        raise WorklogError("project_mismatch", "Project ID differs from the requested identity.")
    if project["data_generation"] != data_generation:
        raise WorklogError("generation_mismatch", "Data generation changed. Read a fresh snapshot.")


def write_request(db, project, command, request):
    common = ("operation_id", "project_id", "data_generation")
    task_fields = ("task_id", "expected_revision")
    if command == "task-create":
        require_fields(request, common + ("title",))
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

    at = timestamp()
    if command == "task-create":
        task_id, entry = create_task(db, request, at)
    else:
        task_id = string_field(request, "task_id")
        expected_revision = integer(request["expected_revision"], "expected_revision", 1)
        task = task_row(db, task_id)
        active = open_run(db, task_id)
        run_id = None if command == "claim" else string_field(request, "run_id")
        if run_id is not None and (active is None or active["run_id"] != run_id):
            raise WorklogError("stale_run", "The supplied run is no longer the active task run.",
                               active_run=active)
        if task["revision"] != expected_revision:
            raise WorklogError("revision_conflict", "Task changed since it was read.",
                               current_revision=task["revision"], active_run=active)
        if command == "claim":
            claim_task(db, request, task_id, active, at)
            entry = None
        else:
            entry = record_entry(db, command, request, task_id, run_id, at)
        db.execute("UPDATE tasks SET revision = revision + 1, updated_at = ? WHERE task_id = ?",
                   (at, task_id))

    payload = {"kind": command, "committed_at": at,
               "task": task_view(db, task_row(db, task_id)), "entry": entry}
    seq = db.execute(
        "INSERT INTO changes (change_id, operation_id, request_hash, payload) VALUES (?, ?, ?, ?)",
        (uid(), operation, digest, json_text(payload)),
    ).lastrowid
    return change_view(db.execute("SELECT * FROM changes WHERE seq = ?", (seq,)).fetchone())


def change_view(row):
    return {"seq": row["seq"], "change_id": row["change_id"], **json.loads(row["payload"])}


def watermark(db):
    return db.execute("SELECT COALESCE(MAX(seq), 0) FROM changes").fetchone()[0]


def snapshot(db):
    at = watermark(db)
    tasks = [task_view(db, task) for task in db.execute("SELECT * FROM tasks ORDER BY rowid")]
    entries = [dict(row) for row in db.execute(entry_query(ENTRY_FIELDS) + " ORDER BY e.position")]
    return {"watermark": at, "tasks": tasks, "entry_refs": entries}


def changes(db, project, args):
    require_project(project, args.project_id, args.data_generation)
    after = integer(args.after, "after", 0)
    limit = integer(args.limit, "limit", 1, 500)
    high = watermark(db)
    if after > high:
        raise WorklogError("cursor_ahead", "Cursor exceeds the current watermark.", watermark=high)
    rows = db.execute("SELECT * FROM changes WHERE seq > ? ORDER BY seq LIMIT ?", (after, limit))
    result = [change_view(row) for row in rows]
    cursor = result[-1]["seq"] if result else after
    return {"changes": result, "next_after": cursor, "has_more": cursor < high, "watermark": high}


def read_request(db, project, command, args):
    if command == "identity":
        return identity(project)
    if command == "task-get":
        return task_view(db, task_row(db, args.task, allow_number=True))
    if command == "snapshot":
        return snapshot(db)
    if command == "changes":
        return changes(db, project, args)
    row = db.execute(entry_query(ENTRY_FIELDS + ", e.body_md") + " WHERE e.entry_id = ?",
                     (args.entry_id,)).fetchone()
    if row is None:
        raise WorklogError("entry_not_found", "Work entry does not exist.", entry_id=args.entry_id)
    return dict(row)
