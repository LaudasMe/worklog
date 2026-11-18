#!/usr/bin/env python3
"""Run workLog commands using a JSON request on stdin and a JSON reply on stdout."""

import argparse
from contextlib import closing
import json
import sqlite3
import sys

from worklog_store import (PROTOCOL_VERSION, WorklogError, connect, initialize,
                           json_text, metadata, project_db, read_request, write_request)

WRITES = ("task-create", "claim", "checkpoint", "handoff")
EXIT_CODES = {
    "invalid_input": 2, "invalid_project_path": 2,
    "not_initialized": 3, "task_not_found": 3, "entry_not_found": 3,
    "already_initialized": 4, "claim_conflict": 4, "stale_run": 4, "revision_conflict": 4,
    "operation_conflict": 4, "project_mismatch": 4, "generation_mismatch": 4, "cursor_ahead": 4,
    "database_busy": 5,
}


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise WorklogError("invalid_input", message)


def arguments(argv):
    parser = Parser(description=__doc__)
    parser.add_argument("--project", required=True, help="existing project directory")
    parser.add_argument("--version", action="version", version=f"workLog CLI protocol {PROTOCOL_VERSION}")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("init", *WRITES, "identity", "snapshot"):
        commands.add_parser(command)
    commands.add_parser("task-get").add_argument("task", help="task ID or T001-style number")
    commands.add_parser("entry-get").add_argument("entry_id")
    changes = commands.add_parser("changes")
    changes.add_argument("--project-id", required=True)
    changes.add_argument("--data-generation", required=True)
    changes.add_argument("--after", type=int, required=True)
    changes.add_argument("--limit", type=int, default=100)
    return parser.parse_args(argv)


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
    path = project_db(args.project)
    command = args.command
    if command == "init":
        return initialize(path, request_json())
    if not path.is_file():
        raise WorklogError("not_initialized", "No workLog database at the supplied project location.")
    writing = command in WRITES
    request = request_json() if writing else None
    with closing(connect(path, "rw" if writing else "ro")) as db, db:
        db.execute("BEGIN IMMEDIATE" if writing else "BEGIN")
        project = metadata(db)
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
        project, result = execute(arguments(argv))
    except WorklogError as error:
        return error_reply(error)
    except sqlite3.Error as error:
        if str(error).startswith(("database is locked", "database table is locked", "database schema is locked")):
            return error_reply(WorklogError("database_busy", "Database lock timed out. Retry the request."))
        return error_reply(WorklogError("storage_error", "SQLite operation failed.", reason=str(error)))
    except OSError as error:
        return error_reply(WorklogError("storage_error", "Local file operation failed.", reason=str(error)))
    print(json_text({"ok": True, "protocol_version": PROTOCOL_VERSION,
                     "project_id": project["project_id"], "data_generation": project["data_generation"],
                     "result": result}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
