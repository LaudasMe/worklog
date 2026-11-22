"""Use the Codex thread's completed workLog Skill version query as evidence."""

from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import re
import shlex
from uuid import UUID

from worklog_release import parse_version


MAX_ROLLOUT_BYTES = 8 * 1024 * 1024
SKILL_COMMAND_PREFIX = ["env", "WORKLOG_CODEX_SKILL=1"]


def rollout_path(codex_home, thread_id):
    try:
        identity = UUID(thread_id)
        if identity.version != 7 or str(identity) != thread_id:
            return None
        day = datetime.fromtimestamp((identity.int >> 80) / 1000).date()
    except (TypeError, ValueError, AttributeError, OSError, OverflowError):
        return None

    matches = []
    for offset in (0, -1, 1):
        folder = codex_home / "sessions" / (day + timedelta(days=offset)).strftime("%Y/%m/%d")
        matches.extend(folder.glob(f"rollout-*-{thread_id}.jsonl"))
    if len(matches) != 1 or matches[0].is_symlink():
        return None
    return matches[0]


def skill_cli_path(execution):
    command = execution.get("command")
    if not isinstance(command, list) or not all(isinstance(arg, str) for arg in command):
        return None
    if len(command) == 3 and command[1] in ("-c", "-lc"):
        try:
            command = shlex.split(command[2])
        except ValueError:
            return None
    if (
        len(command) != 6 or command[:2] != SKILL_COMMAND_PREFIX
        or not re.fullmatch(r"python3(?:\.\d+)?", Path(command[2]).name)
        or command[3] != "-B" or command[5] != "release-version"
    ):
        return None
    script = Path(command[4])
    if not script.is_absolute() or script.parts[-3:] != ("worklog", "scripts", "worklog.py"):
        return None
    try:
        return str(script.resolve())
    except (OSError, RuntimeError, ValueError):
        return None


def codex_execution(row):
    if not isinstance(row, dict) or row.get("type") != "event_msg":
        return None
    event = row.get("payload")
    if not isinstance(event, dict) or event.get("type") != "item_completed":
        return None
    item = event.get("item")
    if not isinstance(item, dict) or item.get("type") != "CommandExecution":
        return None
    return item


def version_from_execution(execution, script):
    if execution.get("status") != "completed" or execution.get("exit_code") != 0:
        return None
    output = execution.get("aggregated_output")
    if not isinstance(output, str) or len(output) > 4096:
        return None
    try:
        payload = json.loads(output)
    except (ValueError, RecursionError):
        return None
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        return None
    result = payload.get("result")
    if (
        not isinstance(result, dict) or result.get("command") != "release-version"
        or result.get("cli_path") != script or result.get("status") != "recognized"
    ):
        return None
    version = result.get("release_version")
    return version if parse_version(version) is not None else None


def current_skill_version(thread_id, codex_home=None):
    """Return the recorded CLI/Skill release, or None if Codex cannot establish it."""
    if codex_home is None:
        configured_home = os.environ.get("WORKLOG_CODEX_SESSION_HOME") or os.environ.get("CODEX_HOME")
        codex_home = Path(configured_home) if configured_home else Path.home() / ".codex"
    try:
        path = rollout_path(codex_home, thread_id)
        if path is None:
            return None
        with path.open("rb") as rollout:
            header = json.loads(rollout.readline(MAX_ROLLOUT_BYTES))
            if (
                header.get("type") != "session_meta" or header.get("payload", {}).get("id") != thread_id
            ):
                return None
            header_end = rollout.tell()
            start = max(header_end, rollout.seek(0, os.SEEK_END) - MAX_ROLLOUT_BYTES)
            rollout.seek(start)
            if start > header_end:
                rollout.seek(start - 1)
                if rollout.read(1) != b"\n":
                    rollout.readline()
            history = rollout.read(MAX_ROLLOUT_BYTES)
    except (OSError, AttributeError, ValueError, RecursionError):
        return None

    for line in reversed(history.splitlines()):
        try:
            row = json.loads(line)
        except (ValueError, RecursionError):
            continue
        execution = codex_execution(row)
        if execution is not None:
            script = skill_cli_path(execution)
            if script is not None:
                return version_from_execution(execution, script)
    return None
