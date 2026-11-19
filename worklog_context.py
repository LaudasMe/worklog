"""Versioned project and task context stored alongside the legacy read model."""

import hashlib
import json
from pathlib import PurePosixPath
import re

from worklog_store import (
    CONTEXT_VERSION, KINDS, WorklogError, context_supported, integer, json_text, latest_entry, open_run, origin,
    require_fields, require_project, schema_version, string_field, task_row, task_view,
    timestamp, uid,
)
from worklog_summaries import project_tasks

ACTIONS = ("QUESTION", "TODO", "BLOCKER")
ACTION_STATES = ("OPEN", "RESOLVED", "CANCELLED")
DETAILS = {
    "GOAL": ((), ("completion_criteria",)),
    "CONSTRAINT": ((), ()),
    "DECISION": ((), ()),
    "DOCUMENT": (("path", "purpose"), ("locator",)),
    "PROGRESS": (("processed_scope", "impacts"), ("suggested_next_step",)),
    "QUESTION": (("status",), ("outcome_md",)),
    "TODO": (("status",), ("outcome_md",)),
    "BLOCKER": (("status", "blocked_work", "unblock_condition"), ("outcome_md",)),
}
REFERENCE_FIELDS = {
    "FILE": (("path",), ("locator",)),
    "RECORD": ((), ("target_record_id", "batch_index")),
    "ENTRY": (("target_entry_id",), ()),
    "COMMIT": (("commit_hash",), ()),
    "USER_CONFIRMATION": (("note",), ()),
}


def invalid(message):
    raise WorklogError("invalid_input", message)


def choice(obj, key, values):
    if key not in obj:
        invalid(f"{key} is required.")
    value = string_field(obj, key)
    if value not in values:
        invalid(f"{key} must be one of: {', '.join(values)}.")
    return value


def object_value(value, label):
    if not isinstance(value, dict):
        invalid(f"{label} must be an object.")
    return value


def array_value(value, label):
    if not isinstance(value, list):
        invalid(f"{label} must be an array.")
    return value


def relative_path(obj, field):
    value = string_field(obj, field)
    path = PurePosixPath(value)
    if path == PurePosixPath(".") or path.is_absolute() or ".." in path.parts or "\\" in value or "\x00" in value:
        invalid(f"{field} must be a relative project path without '..'.")
    return value


def details_for(item, kind):
    details = object_value(item["details"], "details")
    required, optional = DETAILS[kind]
    require_fields(details, required, optional)
    for key in details:
        if key not in ("processed_scope", "impacts", "completion_criteria"):
            string_field(details, key)
    for key in ("processed_scope", "completion_criteria"):
        if key in details:
            for text in array_value(details[key], key):
                if not isinstance(text, str) or not text.strip():
                    invalid(f"{key} must contain non-empty strings.")
    if kind == "DOCUMENT":
        relative_path(details, "path")
    if kind == "PROGRESS":
        for raw in array_value(details["impacts"], "impacts"):
            impact = object_value(raw, "impact")
            require_fields(impact, ("target_type", "target", "effect"), ("note",))
            if choice(impact, "target_type", ("FILE", "SCOPE")) == "FILE":
                relative_path(impact, "target")
            for key in impact:
                string_field(impact, key)
    if kind in ACTIONS:
        status = choice(details, "status", ACTION_STATES)
        if ("outcome_md" in details) != (status != "OPEN"):
            invalid("Closed records require outcome_md. OPEN records omit it.")
    return details


def find_record(db, record_id):
    row = db.execute("SELECT * FROM context_index WHERE record_id = ?", (record_id,)).fetchone()
    if row is None:
        raise WorklogError("record_not_found", "Context record does not exist.", record_id=record_id)
    return row


def references_for(db, raw_refs, saved):
    refs = []
    for raw in array_value(raw_refs, "refs"):
        ref = object_value(raw, "reference").copy()
        kind = choice(ref, "type", REFERENCE_FIELDS)
        required, optional = REFERENCE_FIELDS[kind]
        require_fields(ref, ("type", *required), optional)
        for key in ref.keys() - {"batch_index"}:
            string_field(ref, key)
        if kind == "FILE":
            relative_path(ref, "path")
        elif kind == "RECORD":
            if ("batch_index" in ref) == ("target_record_id" in ref):
                invalid("RECORD references require exactly one of target_record_id or batch_index.")
            if "batch_index" in ref:
                index = integer(ref.pop("batch_index"), "batch_index", 0, len(saved) - 1)
                ref["target_record_id"] = saved[index]["record_id"]
            else:
                find_record(db, string_field(ref, "target_record_id"))
        elif kind == "ENTRY":
            if db.execute("SELECT 1 FROM entries WHERE entry_id = ?", (ref["target_entry_id"],)).fetchone() is None:
                raise WorklogError("entry_not_found", "Referenced work entry does not exist.",
                                   entry_id=ref["target_entry_id"])
        elif kind == "COMMIT" and not re.fullmatch(r"(?:[0-9a-fA-F]{7,40}|[0-9a-fA-F]{64})", ref["commit_hash"]):
            invalid("commit_hash must be a hexadecimal Git object ID.")
        refs.append(ref)
    return refs


def check_replacement(db, scope, task_id, kind, details, replaces):
    if replaces is not None:
        older = find_record(db, replaces)
        if (older["scope"], older["task_id"], older["kind"]) != (scope, task_id, kind):
            invalid("Replacement must keep the original scope, owning task and kind.")
        if older["superseded_by"] is not None:
            raise WorklogError("record_conflict", "Record already has a replacement.",
                               superseded_by=older["superseded_by"])
        if kind in ACTIONS and older["status"] != "OPEN" and details["status"] != older["status"]:
            raise WorklogError("record_conflict", "A closed item can only be corrected in its current state.")
    elif kind in ACTIONS and details["status"] != "OPEN":
        invalid("New items start OPEN. Close an existing item by replacing it.")
    elif kind == "GOAL" and db.execute(
        "SELECT 1 FROM context_index WHERE scope = ? AND task_id IS ?"
        " AND kind = 'GOAL' AND superseded_by IS NULL", (scope, task_id)
    ).fetchone():
        raise WorklogError("record_conflict", "Replace the current goal explicitly.")


def task_position(db, task_id):
    if not context_supported(db):
        return 0
    return db.execute("SELECT COALESCE(MAX(position), 0) FROM context_records WHERE task_id = ?",
                      (task_id,)).fetchone()[0]


def source_for(db, scope, request):
    client = string_field(request, "client") if "client" in request else None
    session = string_field(request, "session_id") if "session_id" in request else None
    if (client is None) != (session is None):
        invalid("Provide client and session_id together.")
    if scope == "PROJECT":
        return None, (client, session, None)
    task = task_row(db, string_field(request, "task_id"), allow_number=True)
    task_id = task["task_id"]
    active = open_run(db, task_id)
    run_id = string_field(request, "run_id") if "run_id" in request else None
    if run_id != (active["run_id"] if active else None) or (
        active and client is not None and (client, session) != (active["client"], active["session_id"])
    ):
        raise WorklogError("stale_run", "Use the current run and its owning session.", active_run=active)
    expected_revision = integer(request["expected_revision"], "expected_revision", 1)
    expected_position = integer(request["expected_context_position"], "expected_context_position", 0)
    position = task_position(db, task_id)
    if task["revision"] != expected_revision or position != expected_position:
        raise WorklogError("revision_conflict", "Task or task context changed since it was read.",
                           current_revision=task["revision"], current_context_position=position)
    if active:
        client, session = active["client"], active["session_id"]
    return task_id, (client, session, run_id)


def add_record(db, scope, task_id, source, raw, saved, at):
    item = object_value(raw, "record")
    optional = ("refs", "replaces_id", "related_task_id") if scope == "PROJECT" else ("refs", "replaces_id")
    require_fields(item, ("kind", "summary", "body_md", "details"), optional)
    kind = choice(item, "kind", KINDS)
    summary, body = (string_field(item, key) for key in ("summary", "body_md"))
    details = details_for(item, kind)
    refs = references_for(db, item.get("refs", []), saved)
    if (kind in ("DECISION", "CONSTRAINT") or details.get("outcome_md")) and not refs:
        invalid("Decisions, constraints and closed items require evidence in refs.")
    related = None
    if "related_task_id" in item:
        related = task_row(db, string_field(item, "related_task_id"), allow_number=True)["task_id"]
    replaces = string_field(item, "replaces_id") if "replaces_id" in item else None
    check_replacement(db, scope, task_id, kind, details, replaces)
    record_id = uid()
    position = db.execute(
        "INSERT INTO context_records (record_id, scope, task_id, related_task_id, kind, summary,"
        " body_md, details_json, action_status, replaces_id, recorded_at, client, session_id, run_id)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (record_id, scope, task_id, related, kind, summary, body, json_text(details),
         details.get("status"), replaces, at, *source),
    ).lastrowid
    db.executemany("INSERT INTO context_record_refs VALUES (?, ?, ?)",
                   [(record_id, index, json_text(ref)) for index, ref in enumerate(refs)])
    return {"record_id": record_id, "position": position, "kind": kind, "replaces_id": replaces}


def record(db, project, request):
    if not context_supported(db):
        raise WorklogError("upgrade_required", "Back up and explicitly upgrade before saving context records.")
    scope = choice(request, "scope", ("PROJECT", "TASK")) if "scope" in request else None
    common = ("project_id", "data_generation", "operation_id", "scope", "records")
    required = ("task_id", "expected_revision", "expected_context_position") if scope == "TASK" else ()
    optional = ("client", "session_id", "run_id") if scope == "TASK" else ("client", "session_id")
    require_fields(request, common + required, optional)
    require_project(project, string_field(request, "project_id"), string_field(request, "data_generation"))
    operation_id = string_field(request, "operation_id")
    digest = hashlib.sha256(json_text(["record", request]).encode("utf-8")).hexdigest()
    prior = db.execute("SELECT * FROM context_operations WHERE operation_id = ?", (operation_id,)).fetchone()
    if prior:
        if prior["request_hash"] != digest:
            raise WorklogError("operation_conflict", "Operation ID already belongs to a different request.")
        return json.loads(prior["receipt"])
    if db.execute("SELECT 1 FROM changes WHERE operation_id = ?", (operation_id,)).fetchone():
        raise WorklogError("operation_conflict", "Operation ID already belongs to a legacy request.")
    raw_records = array_value(request["records"], "records")
    if not raw_records:
        invalid("records must contain at least one item.")
    task_id, source = source_for(db, scope, request)
    at = timestamp()
    saved = []
    for raw in raw_records:
        saved.append(add_record(db, scope, task_id, source, raw, saved, at))
    result = {
        "context_version": CONTEXT_VERSION, "operation_id": operation_id, "committed_at": at,
        "scope": scope, "task_id": task_id, "records": saved,
        "context_position": saved[-1]["position"] if task_id else None,
    }
    db.execute("INSERT INTO context_operations VALUES (?, ?, ?)", (operation_id, digest, json_text(result)))
    return result


def availability(root, path):
    try:
        source = (root / path).resolve()
        return "AVAILABLE" if source.is_relative_to(root) and source.is_file() else "NEEDS_CHECK"
    except (OSError, RuntimeError):
        return "NEEDS_CHECK"


def record_view(db, row, root, include_body=False):
    result = dict(row)
    body = result.pop("body_md")
    result.pop("action_status")
    details = json.loads(result.pop("details_json"))
    if result["kind"] == "DOCUMENT":
        details["availability"] = availability(root, details["path"])
    refs = []
    for ref_row in db.execute(
        "SELECT reference_json FROM context_record_refs WHERE record_id = ? ORDER BY ordinal",
        (row["record_id"],)
    ):
        ref = json.loads(ref_row[0])
        if ref["type"] == "FILE":
            ref["availability"] = availability(root, ref["path"])
        elif ref["type"] == "RECORD":
            ref["source"] = origin("record-get", ref["target_record_id"])
        elif ref["type"] == "ENTRY":
            ref["source"] = origin("entry-get", ref["target_entry_id"])
        refs.append(ref)
    result.update(details=details, refs=refs, source=origin("record-get", row["record_id"]))
    if include_body:
        result["body_md"] = body
    return result


def record_get(db, record_id, root):
    if not context_supported(db):
        raise WorklogError("record_not_found", "This project has no context records.", record_id=record_id)
    return {"context_version": CONTEXT_VERSION,
            **record_view(db, find_record(db, record_id), root, include_body=True)}


def newest_first(db, root, condition, params=()):
    if not context_supported(db):
        return []
    rows = db.execute("SELECT * FROM context_index WHERE superseded_by IS NULL AND " + condition
                      + " ORDER BY position DESC", params)
    return [record_view(db, row, root) for row in rows]


def evidence_value(value, row):
    return {"value": value, "source": row["source"], "refs": row["refs"]}


def impact_view(progress, task_history):
    history = {}
    for row in progress:
        for impact in row["details"]["impacts"]:
            key = (impact["target_type"], impact["target"])
            target = history.setdefault(key, {"target_type": key[0], "target": key[1], "evidence": []})
            target["evidence"].append(evidence_value(impact, row))
    latest = progress[0] if progress else None
    if task_history and (latest is None or task_history[0]["recorded_at"] > latest["recorded_at"]):
        latest_impact = {"value": None, "source": task_history[0]["source"], "refs": []}
    else:
        latest_impact = evidence_value(latest["details"]["impacts"], latest) if latest else None
    return {"latest": latest_impact, "history": list(history.values()),
            "legacy_unrecorded": [row["source"] for row in task_history]}


def context(db, project, task_key, root):
    task = task_row(db, task_key, allow_number=True) if task_key is not None else None
    task_id = task["task_id"] if task is not None else None
    own = newest_first(db, root, "scope = 'PROJECT'" if task_id is None else "task_id = ?",
                       () if task_id is None else (task_id,))
    buckets = {kind: [row for row in own if row["kind"] == kind] for kind in KINDS}
    progress = buckets["PROGRESS"]
    goals = buckets["GOAL"]
    legacy = []
    if task_id:
        legacy = [row for row in (latest_entry(db, task_id, "PROGRESS"),
                                 latest_entry(db, task_id, "HANDOFF")) if row is not None]
        legacy.sort(key=lambda row: (row["recorded_at"], row["entry_id"]), reverse=True)
        for row in legacy:
            row["source"] = origin("entry-get", row["entry_id"])
            row["impacts_recorded"] = False
    result = {
        "context_version": CONTEXT_VERSION, "schema_version": schema_version(db),
        "project_name": project["name"], "scope": "TASK" if task_id else "PROJECT",
        "goal": goals[0] if goals else None,
        "processed_scope": [evidence_value(value, row) for row in progress
                            for value in row["details"]["processed_scope"]],
        "decisions": buckets["DECISION"], "impacts": impact_view(progress, legacy),
        "open_questions": [row for row in buckets["QUESTION"] if row["status"] == "OPEN"],
        "todos": [row for row in buckets["TODO"] if row["status"] == "OPEN"],
        "blockers": [row for row in buckets["BLOCKER"] if row["status"] == "OPEN"],
        "constraints": buckets["CONSTRAINT"], "documents": buckets["DOCUMENT"],
        "progress": progress, "legacy_latest": legacy,
    }
    sections = {"goal": "GOAL", "processed_scope": "PROGRESS", "impacts": "PROGRESS",
                "decisions": "DECISION", "open_questions": "QUESTION", "todos": "TODO",
                "blockers": "BLOCKER"}
    result["unrecorded"] = [
        name for name, kind in sections.items()
        if not buckets[kind] or (name == "processed_scope" and not result[name])
    ]
    if task_id:
        result["task"] = task_view(db, task)
        result["task"]["context_position"] = task_position(db, task_id)
        result["project_context"] = newest_first(
            db, root, "scope = 'PROJECT' AND (kind IN ('CONSTRAINT', 'DECISION', 'DOCUMENT')"
            " OR (kind = 'PROGRESS' AND related_task_id = ?))", (task_id,)
        )
    else:
        result["related_tasks"] = project_tasks(db)
        result["task_blockers"] = newest_first(
            db, root, "scope = 'TASK' AND kind = 'BLOCKER' AND status = 'OPEN'"
        )
    return result
