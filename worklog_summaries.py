"""SQL projections and read-only pagination for saved summaries."""

import base64
import binascii
import json

from worklog_store import (
    CONTEXT_VERSION, KINDS, WorklogError, context_supported, integer, json_text, origin,
    task_row, watermark,
)

TASK_STATES = ("TODO", "IN_PROGRESS", "DONE")
RECORD_STATES = ("OPEN", "RESOLVED", "CANCELLED", "CURRENT", "SUPERSEDED", "RECORDED")


def records_sql(db):
    legacy = """
        SELECT entry_id AS id, kind, 'RECORDED' AS status, 'TASK' AS scope, task_id,
               NULL AS related_task_id, summary, recorded_at, 'entry-get' AS command
        FROM entries
    """
    if not context_supported(db):
        return legacy
    return legacy + """
        UNION ALL
        SELECT record_id, kind, status, scope, task_id, related_task_id, summary, recorded_at, 'record-get'
        FROM context_index
    """


def tasks_sql(db):
    if context_supported(db):
        blocked = """
            EXISTS(SELECT 1 FROM context_index b WHERE b.task_id = t.task_id
                   AND b.kind = 'BLOCKER' AND b.status = 'OPEN')
        """
        updated = """
            MAX(t.updated_at, COALESCE((SELECT MAX(recorded_at) FROM context_records r
                                       WHERE r.task_id = t.task_id), t.updated_at))
        """
    else:
        blocked, updated = "0", "t.updated_at"
    return f"""
        WITH recorded AS ({records_sql(db)})
        SELECT t.task_id AS id, t.number, t.title, t.status, {blocked} AS blocked,
               {updated} AS updated_at, recent.summary, recent.id AS source_id, recent.command
        FROM tasks t
        LEFT JOIN recorded recent ON recent.id = (
            SELECT r.id FROM recorded r WHERE r.task_id = t.task_id
              AND r.kind IN ('PROGRESS', 'HANDOFF') AND r.status IN ('CURRENT', 'RECORDED')
            ORDER BY r.recorded_at DESC, r.id ASC LIMIT 1
        )
    """


def task_summary(row):
    result = dict(row)
    result["task_id"] = result.pop("id")
    result["blocked"] = bool(result["blocked"])
    command, key = result.pop("command"), result.pop("source_id")
    result["source"] = origin(command, key) if key is not None else None
    return result


def project_tasks(db):
    return [task_summary(row) for row in db.execute(tasks_sql(db) + " ORDER BY t.number")]


def record_summary(row):
    result = dict(row)
    result["source"] = origin(result.pop("command"), result.pop("id"))
    key = "entry_id" if result["source"]["command"] == "entry-get" else "record_id"
    result[key] = result["source"]["id"]
    return result


def cursor_text(key, stamp, at, row_id):
    payload = json_text([key, stamp, at, row_id]).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii")


def cursor_position(cursor, key, stamp):
    try:
        if len(cursor) > 4096:
            raise ValueError
        saved_key, saved_stamp, at, row_id = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
        if saved_key != key or not isinstance(at, str) or not isinstance(row_id, str):
            raise ValueError
    except (ValueError, TypeError, binascii.Error, RecursionError) as exc:
        raise WorklogError("invalid_input", "Cursor is malformed or belongs to another query.") from exc
    if saved_stamp != stamp:
        raise WorklogError("cursor_stale", "Project changed since the previous page. Start a new query.")
    return at, row_id


def summaries(db, project, args):
    is_task = args.type == "task"
    states = TASK_STATES if is_task else RECORD_STATES
    if args.status is not None and args.status not in states:
        raise WorklogError("invalid_input", f"Invalid {args.type} status. Expected one of: {', '.join(states)}.")
    if args.kind is not None and (is_task or args.kind not in (*KINDS, "HANDOFF")):
        raise WorklogError("invalid_input", "--kind requires a known record kind and --type record.")
    limit = integer(args.limit, "limit", 1, 500)
    task_id = task_row(db, args.task, allow_number=True)["task_id"] if args.task is not None else None
    identity = [project["project_id"], project["data_generation"]]
    key = [*identity, args.type, args.status, args.kind, task_id, args.blocked]
    latest_position = db.execute("SELECT COALESCE(MAX(position), 0) FROM context_records").fetchone()[0] \
        if context_supported(db) else 0
    stamp = [watermark(db), latest_position]
    time_field = "updated_at" if is_task else "recorded_at"
    conditions, params = [], []
    if task_id is not None:
        conditions.append("id = ?" if is_task else "(task_id = ? OR related_task_id = ?)")
        params.extend([task_id] if is_task else [task_id, task_id])
    if args.status is not None:
        conditions.append("status = ?")
        params.append(args.status)
    elif not is_task:
        conditions.append("status <> 'SUPERSEDED'")
    if args.kind is not None:
        conditions.append("kind = ?")
        params.append(args.kind)
    if args.blocked:
        conditions.append("blocked = 1" if is_task else "(kind = 'BLOCKER' AND status = 'OPEN')")
    if args.cursor is not None:
        at, row_id = cursor_position(args.cursor, key, stamp)
        conditions.append(f"({time_field} < ? OR ({time_field} = ? AND id > ?))")
        params.extend([at, at, row_id])
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    projection = tasks_sql(db) if is_task else records_sql(db)
    rows = db.execute(
        f"SELECT * FROM ({projection}){where} ORDER BY {time_field} DESC, id ASC LIMIT ?",
        (*params, limit + 1),
    ).fetchall()
    page = rows[:limit]
    convert = task_summary if is_task else record_summary
    next_cursor = cursor_text(key, stamp, page[-1][time_field], page[-1]["id"]) if len(rows) > limit else None
    return {"context_version": CONTEXT_VERSION, "type": args.type,
            "items": [convert(row) for row in page], "next_cursor": next_cursor}
