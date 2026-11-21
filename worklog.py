#!/usr/bin/env python3
"""Run workLog commands using a JSON request on stdin and a JSON reply on stdout."""

import argparse
from contextlib import closing
import json
import sqlite3
import sys

from worklog_context import context, record, record_get

from worklog_store import (
    CAPABILITIES, PROTOCOL_VERSION, TASK_LIFECYCLE_WRITES, TASK_PRIORITIES, WorklogError,
    connect, initialize, json_text, metadata, project_db, read_request, write_request,
)
from worklog_projects import list_projects, move_project, register_new_project, register_project, remove_project
from worklog_upgrade import backup, upgrade
from worklog_summaries import summaries

WRITES = ("task-create", *TASK_LIFECYCLE_WRITES, "task-set-priority", "claim", "checkpoint", "handoff", "record")
EXIT_CODES = {
    "invalid_input": 2, "invalid_project_path": 2,
    "not_initialized": 3, "task_not_found": 3, "entry_not_found": 3, "record_not_found": 3,
    "already_initialized": 4, "claim_conflict": 4, "stale_run": 4, "revision_conflict": 4,
    "operation_conflict": 4, "project_mismatch": 4, "generation_mismatch": 4, "cursor_ahead": 4,
    "backup_exists": 4, "already_upgraded": 4, "invalid_backup": 4,
    "upgrade_required": 4, "record_conflict": 4, "cursor_stale": 4,
    "task_state_conflict": 4, "invalid_evidence": 4, "task_blocked": 4,
    "todo_disposition_required": 4,
    "project_not_registered": 3, "invalid_registry": 4, "project_registration_failed": 4,
    "database_busy": 5,
}


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise WorklogError("invalid_input", message)


def arguments(argv):
    parser = Parser(description=__doc__)
    parser.add_argument("--project", default=".", help="existing project directory (default: current directory)")
    parser.add_argument("--version", action="version", version=f"workLog CLI protocol {PROTOCOL_VERSION}")
    parser.add_argument("--protocol-version", choices=(PROTOCOL_VERSION,),
                        help=f"explicitly select the public read protocol (default: {PROTOCOL_VERSION})")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("init", *WRITES, "identity", "snapshot"):
        commands.add_parser(command)
    backup_command = commands.add_parser("backup")
    backup_command.add_argument("--output", required=True)
    backup_command.add_argument("--project-id", help="verify the selected project's identity")
    upgrade_command = commands.add_parser("upgrade")
    upgrade_command.add_argument("--backup", required=True)
    upgrade_command.add_argument("--project-id", help="verify the selected project's identity")
    registry = commands.add_parser("projects").add_subparsers(dest="projects_command", required=True)
    registry.add_parser("list")
    registry.add_parser("register")
    move = registry.add_parser("move")
    move.add_argument("--from", dest="previous_project", required=True)
    move.add_argument("--project-id", required=True)
    registry.add_parser("remove").add_argument("--project-id", required=True)
    commands.add_parser("task-get").add_argument("task", help="task ID or T001-style number")
    commands.add_parser("entry-get").add_argument("entry_id")
    commands.add_parser("context").add_argument("--task")
    commands.add_parser("record-get").add_argument("record_id")
    summary = commands.add_parser("summaries")
    summary.add_argument("--type", choices=("task", "record"), required=True)
    summary.add_argument("--task")
    summary.add_argument("--status")
    summary.add_argument("--display-status")
    summary.add_argument("--view", choices=("pending", "archived"))
    summary.add_argument("--priority", choices=TASK_PRIORITIES)
    summary.add_argument("--kind")
    summary.add_argument("--blocked", action="store_true")
    summary.add_argument("--cursor")
    summary.add_argument("--limit", type=int, default=100)
    changes = commands.add_parser("changes")
    changes.add_argument("--project-id", required=True)
    changes.add_argument("--data-generation", required=True)
    changes.add_argument("--after", type=int, required=True)
    changes.add_argument("--limit", type=int, default=100)
    args = parser.parse_args(argv)
    if args.protocol_version is not None and args.command not in CAPABILITIES:
        parser.error("Protocol 2.0 is available only for public read commands.")
    return args


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key.")
        result[key] = value
    return result


def request_json():
    try:
        request = json.loads(sys.stdin.buffer.read().decode("utf-8"), object_pairs_hook=unique_keys)
        if not isinstance(request, dict):
            raise ValueError("Expected a JSON object.")
        # Reject invalid Unicode and non-JSON numeric values before opening a write transaction.
        json_text(request).encode("utf-8")
        return request
    except (ValueError, RecursionError) as exc:
        raise WorklogError("invalid_input", "Expected one valid UTF-8 JSON object on stdin.") from exc


def execute(args):
    command = args.command
    if command == "projects":
        if args.projects_command == "list":
            return None, list_projects()
        if args.projects_command == "register":
            return None, register_project(args.project)
        if args.projects_command == "move":
            return None, move_project(args.previous_project, args.project, args.project_id)
        return None, remove_project(args.project, args.project_id)
    path = project_db(args.project)
    if command == "init":
        return initialize(path, request_json(), on_create=register_new_project)
    if not path.is_file():
        raise WorklogError("not_initialized", "No workLog database at the supplied project location.")
    if command == "backup":
        return backup(path, args.output, expected_id=getattr(args, "project_id", None))
    if command == "upgrade":
        return upgrade(path, args.backup, expected_id=getattr(args, "project_id", None))
    writing = command in WRITES
    request = request_json() if writing else None
    with closing(connect(path, "rw" if writing else "ro")) as db, db:
        db.execute("BEGIN IMMEDIATE" if writing else "BEGIN")
        project = metadata(db)
        if command == "record":
            return project, record(db, project, request)
        if command == "context":
            return project, context(db, project, args.task, path.parent.parent)
        if command == "record-get":
            return project, record_get(db, args.record_id, path.parent.parent)
        if command == "summaries":
            return project, summaries(db, project, args)
        if writing:
            return project, write_request(db, project, command, request)
        return project, read_request(db, project, command, args)


def error_reply(error):
    print(json_text({"ok": False, "error": {
        "code": error.code, "message": str(error), "details": error.details,
    }}))
    return EXIT_CODES.get(error.code, 6)


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        args = arguments(argv)
        project, result = execute(args)
    except WorklogError as error:
        return error_reply(error)
    except sqlite3.Error as error:
        if str(error).startswith(("database is locked", "database table is locked", "database schema is locked")):
            return error_reply(WorklogError("database_busy", "Database lock timed out. Retry the request."))
        return error_reply(WorklogError("storage_error", "SQLite operation failed.", reason=str(error)))
    except OSError as error:
        return error_reply(WorklogError("storage_error", "Local file operation failed.", reason=str(error)))
    envelope = {"ok": True, "protocol_version": PROTOCOL_VERSION, "result": result}
    if project is not None:
        envelope.update(project_id=project["project_id"], data_generation=project["data_generation"])
    print(json_text(envelope))
    return 0


if __name__ == "__main__":
    sys.exit(main())
