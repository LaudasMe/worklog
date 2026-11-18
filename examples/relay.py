"""Replay the synthetic example using only workLog's public CLI."""

import argparse
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
CLI = HERE.parent / "worklog.py"
SAMPLE = json.loads((HERE / "sample.json").read_text(encoding="utf-8"))


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


class Relay:
    def __init__(self, project):
        self.project = str(Path(project).resolve())

    def call(self, command, *options, data=None, error=None):
        argv = [sys.executable, str(CLI), "--project", self.project, command, *options]
        raw = None if data is None else json.dumps(data, ensure_ascii=False).encode("utf-8")
        process = subprocess.run(argv, input=raw, capture_output=True, check=False)
        reply = json.loads(process.stdout)
        require((process.returncode == 0) == (error is None), repr(reply))
        require(reply["ok"] == (error is None), repr(reply))
        if error:
            require(reply["error"]["code"] == error, repr(reply))
        print(json.dumps({"argv": [command, *options], "request": data,
                          "exit": process.returncode, "reply": reply}, ensure_ascii=False), flush=True)
        return reply

    def write(self, command, project, op, *, task=None, **fields):
        data = {"project_id": project["project_id"], "data_generation": project["data_generation"],
                "operation_id": op, **fields}
        if task is not None:
            data.update(task_id=task["task_id"], expected_revision=task["revision"])
        return self.call(command, data=data)

    def start(self, client, session):
        project = self.call("init", data={"name": SAMPLE["project_name"]})
        created = self.write("task-create", project, "relay-create", title=SAMPLE["task_title"])
        claimed = self.write("claim", project, "relay-claim-a", task=created["result"]["task"],
                             client=client, session_id=session)
        task = claimed["result"]["task"]
        run_id = task["active_run"]["run_id"]
        checkpoint = self.write("checkpoint", project, "relay-progress-a", task=task,
                                run_id=run_id, **SAMPLE["progress"])
        self.write("handoff", project, "relay-handoff-a", task=checkpoint["result"]["task"],
                   run_id=run_id, **SAMPLE["handoff"])

    def read_handoff(self):
        project = self.call("task-get", "T001")
        task = project["result"]
        entries = self.call("snapshot")["result"]["entry_refs"]
        first = next(entry for entry in entries
                     if entry["task_id"] == task["task_id"] and entry["kind"] == "PROGRESS")
        progress = self.call("entry-get", first["entry_id"])["result"]
        handoff = self.call("entry-get", task["latest_handoff"]["entry_id"])["result"]
        require(progress["body_md"] == SAMPLE["progress"]["body_md"], "Progress Markdown changed.")
        require(handoff["body_md"] == SAMPLE["handoff"]["body_md"], "Handoff Markdown changed.")
        require(handoff["next_step"] == SAMPLE["handoff"]["next_step"], "Handoff next step changed.")
        return project, task

    def resume(self, client, session):
        project, task = self.read_handoff()
        claimed = self.write("claim", project, "relay-claim-b", task=task,
                             client=client, session_id=session)
        task = claimed["result"]["task"]
        self.write("checkpoint", project, "relay-progress-b", task=task,
                   run_id=task["active_run"]["run_id"], **SAMPLE["continued"])

    def verify(self):
        project, task = self.read_handoff()
        page = self.call("changes", "--project-id", project["project_id"],
                         "--data-generation", project["data_generation"], "--after", "0")
        events = page["result"]["changes"]
        require([event["kind"] for event in events] ==
                ["task-create", "claim", "checkpoint", "handoff", "claim", "checkpoint"],
                "Unexpected change history.")
        require(all(event["task"]["task_id"] == task["task_id"] for event in events),
                "Tasks diverged across the relay.")
        first_claim = events[1]["task"]
        request = {
            "project_id": project["project_id"], "data_generation": project["data_generation"],
            "operation_id": "relay-progress-a", "task_id": task["task_id"],
            "expected_revision": first_claim["revision"],
            "run_id": first_claim["active_run"]["run_id"], **SAMPLE["progress"],
        }
        replay = self.call("checkpoint", data=request)
        require(replay["result"] == events[2], "Successful retry did not reproduce its receipt.")
        self.call("checkpoint", data={**request, "operation_id": "relay-stale-rejected"},
                  error="stale_run")
        snapshot = self.call("snapshot")["result"]
        require(snapshot["watermark"] == 6 and len(snapshot["entry_refs"]) == 3,
                "Retry or rejected write changed the database.")
        continued = self.call("entry-get", task["latest_entry"]["entry_id"])["result"]
        require(continued["body_md"] == SAMPLE["continued"]["body_md"], "Continued Markdown changed.")
        require(task["next_step"] == SAMPLE["continued"]["next_step"], "Task's latest next step changed.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("start", "resume"):
        role = commands.add_parser(command)
        role.add_argument("--client", required=True)
        role.add_argument("--session", required=True)
    commands.add_parser("read")
    commands.add_parser("verify")
    args = parser.parse_args()
    relay = Relay(args.project)
    if args.command in ("start", "resume"):
        getattr(relay, args.command)(args.client, args.session)
    elif args.command == "read":
        relay.read_handoff()
    else:
        relay.verify()


if __name__ == "__main__":
    main()
