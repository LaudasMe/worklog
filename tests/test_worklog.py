import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
from pathlib import Path
from select import select
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import worklog
import worklog_store as store
from test_context import LEGACY, invoke, isolated_home, source_at

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "worklog.py"
SAMPLE = json.loads((ROOT / "examples" / "sample.json").read_text(encoding="utf-8"))


class ProjectCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="worklog-test-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.env = isolated_home(self.root / "home")
        self.project = self.root / "合成项目 #1"
        self.caller = self.root / "another caller"
        self.project.mkdir()
        self.caller.mkdir()

    def call(self, command, *args, data=None, project=None, cwd=None, code=0, error=None, raw=None):
        if raw is None and data is not None:
            raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        process = subprocess.run(
            [sys.executable, str(CLI), "--project", str(project or self.project), command, *args],
            input=raw, capture_output=True, cwd=cwd or self.caller, timeout=15, env=self.env,
        )
        self.assertEqual(process.returncode, code, (process.stdout, process.stderr))
        self.assertEqual(process.stderr, b"")
        reply = json.loads(process.stdout)
        self.assertIs(reply["ok"], code == 0)
        if error:
            self.assertEqual(reply["error"]["code"], error)
        return reply

    def initialize(self, project=None):
        return self.call("init", data={"name": "合成项目"}, project=project)

    def upgraded_legacy_database(self):
        cli = source_at(LEGACY, self.root / "v0.1.0")
        code, legacy = invoke(cli, self.project, "--project", str(self.project), "init",
                              data={"name": "合成项目"})
        self.assertEqual(code, 0, legacy)
        backup = self.root / "legacy.sqlite3"
        self.call("backup", "--output", str(backup))
        self.call("upgrade", "--backup", str(backup))
        self.assertEqual(self.call("identity"), {**legacy, "protocol_version": "2.0"})
        return legacy

    def request(self, identity, operation, task=None, **fields):
        request = {key: identity[key] for key in ("project_id", "data_generation")}
        request.update(operation_id=operation, **fields)
        if task:
            request.update(task_id=task["task_id"], expected_revision=task["revision"])
        return request

    def create(self, identity, project=None, operation="create"):
        request = self.request(identity, operation, title=SAMPLE["task_title"])
        return self.call("task-create", data=request, project=project)["result"]["task"]

    def claim(self, identity, task, operation="claim", **fields):
        request = self.request(identity, operation, task, client="clientA", session_id="sessionA", **fields)
        return self.call("claim", data=request)["result"]["task"]

    def record(self, identity, task, operation, kind):
        return self.request(identity, operation, task, run_id=task["active_run"]["run_id"], **SAMPLE[kind])

    def page(self, identity, cursor, **options):
        limit = options.pop("limit", 1)
        return self.call("changes", "--project-id", identity["project_id"], "--data-generation",
                         identity["data_generation"], "--after", str(cursor), "--limit", str(limit),
                         **options)

    def test_uninitialized_read_commands_do_not_create_data(self):
        commands = [
            ("identity",), ("snapshot",), ("task-get", "T001"), ("entry-get", "unknown"),
            ("changes", "--project-id", "unknown", "--data-generation", "unknown", "--after", "0"),
        ]
        for command, *args in commands:
            with self.subTest(command=command):
                self.call(command, *args, code=3, error="not_initialized")
                self.assertEqual(list(self.project.iterdir()), [])

    def test_init_is_explicit_and_idempotent_even_with_competing_processes(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            one = pool.submit(self.initialize)
            two = pool.submit(self.initialize)
            self.assertEqual(one.result(), two.result())
        self.call("init", data={"name": "different"}, code=4, error="already_initialized")
        self.assertEqual([path.name for path in (self.project / ".worklog").iterdir()], ["worklog.sqlite3"])
        self.assertEqual(self.call("snapshot")["result"], {"watermark": 0, "tasks": [], "entry_refs": []})

    def test_same_number_isolated_and_identity_survives_directory_move(self):
        first = self.initialize()
        original = self.create(first)
        other = self.root / "other"
        other.mkdir()
        second = self.initialize(other)
        other_task = self.create(second, other)
        self.assertEqual(original["number"], "T001")
        self.assertEqual(other_task["number"], "T001")
        self.assertNotEqual(original["task_id"], other_task["task_id"])
        self.assertNotEqual(first["project_id"], second["project_id"])
        moved = self.root / "移动后的目录"
        self.project.rename(moved)
        reply = self.call("task-get", "T001", project=moved, cwd=self.root)
        self.assertEqual(reply["result"], {
            **original, "display_status": "TODO", "archived": False, "priority": None, "blocker_refs": [],
        })
        self.assertEqual(reply["project_id"], first["project_id"])
        self.assertEqual(reply["data_generation"], first["data_generation"])
        self.page(first, 0, project=other, code=4, error="project_mismatch")
        request = self.request(first, "cross-project-write", title="wrong project")
        self.call("task-create", data=request, project=other, code=4, error="project_mismatch")

    def test_concurrent_create_allocates_distinct_numbers(self):
        identity = self.initialize()
        with ThreadPoolExecutor(max_workers=4) as pool:
            tasks = list(pool.map(lambda number: self.create(identity, operation=f"create-{number}"), range(6)))
        self.assertEqual({task["number"] for task in tasks}, {f"T{i:03}" for i in range(1, 7)})
        self.assertEqual(self.call("snapshot")["result"]["watermark"], 6)

    def test_concurrent_retries_return_same_receipt(self):
        identity = self.initialize()
        data = self.request(identity, "same-operation", title="同一笔任务")
        with ThreadPoolExecutor(max_workers=4) as pool:
            replies = list(pool.map(lambda _: self.call("task-create", data=data), range(4)))
        self.assertTrue(all(reply == replies[0] for reply in replies))
        self.assertEqual(self.call("snapshot")["result"]["watermark"], 1)

    def test_idempotency_and_old_run_protection_after_handoff(self):
        identity = self.initialize()
        task = self.claim(identity, self.create(identity))
        progress = self.record(identity, task, "progress", "progress")
        receipt = self.call("checkpoint", data=progress)
        next_task = receipt["result"]["task"]
        handoff = self.record(identity, next_task, "handoff", "handoff")
        closed = self.call("handoff", data=handoff)
        self.assertIsNone(closed["result"]["task"]["active_run"])
        active = self.claim(identity, closed["result"]["task"], operation="next-claim")
        self.assertNotEqual(active["active_run"]["run_id"], progress["run_id"])
        # Retry succeeds even with the old run and revision.
        self.assertEqual(self.call("checkpoint", data=progress), receipt)
        self.assertEqual(self.call("handoff", data=handoff), closed)
        self.call("checkpoint", data={**progress, "operation_id": "stale"}, code=4, error="stale_run")
        self.call("checkpoint", data={**progress, "body_md": "changed"}, code=4, error="operation_conflict")
        out = self.call("snapshot")["result"]
        self.assertEqual(out["watermark"], 5)
        self.assertEqual(len(out["entry_refs"]), 2)
        entry = self.call("entry-get", receipt["result"]["entry"]["entry_id"])["result"]
        self.assertEqual(entry["body_md"].encode(), SAMPLE["progress"]["body_md"].encode())

    def test_revision_guard_and_explicit_takeover(self):
        identity = self.initialize()
        created = self.create(identity)
        first = self.claim(identity, created)
        data = self.request(identity, "takeover", first, client="clientB", session_id="sessionB")
        self.call("claim", data=data, code=4, error="claim_conflict")
        run_id = first["active_run"]["run_id"]
        self.call("claim", data={**data, "expected_revision": created["revision"], "replaces_run_id": run_id},
                  code=4, error="revision_conflict")
        second = self.call("claim", data={**data, "replaces_run_id": run_id})["result"]["task"]
        self.assertEqual(second["active_run"]["client"], "clientB")
        self.assertIsNotNone(second["active_run"]["run_id"])
        self.assertNotEqual(second["active_run"]["run_id"], run_id)
        with closing(store.connect(store.project_db(self.project), "ro")) as db:
            self.assertEqual(db.execute("SELECT state FROM task_runs WHERE run_id = ?", (run_id,)).fetchone()[0],
                             "SUPERSEDED")

    def test_mid_write_failure_rolls_back_task_entry_run_change_and_receipt(self):
        identity = self.initialize()
        task = self.claim(identity, self.create(identity))
        before = self.call("snapshot")
        path = store.project_db(self.project)
        with closing(store.connect(path, "rw")) as db:
            db.execute(
                "CREATE TRIGGER test_failure AFTER INSERT ON changes"
                " WHEN NEW.operation_id = 'fault-handoff'"
                " BEGIN SELECT RAISE(ABORT, 'injected failure after inserting change'); END"
            )
        handoff = self.record(identity, task, "fault-handoff", "handoff")
        failure = self.call("handoff", data=handoff, code=6, error="storage_error")
        self.assertIn("injected failure", failure["error"]["details"]["reason"])
        self.assertEqual(self.call("snapshot"), before)
        self.assertEqual(self.page(identity, before["result"]["watermark"])["result"]["changes"], [])
        with closing(store.connect(path, "rw")) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM entries").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT state FROM task_runs").fetchone()[0], "OPEN")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM changes WHERE operation_id = 'fault-handoff'")
                             .fetchone()[0], 0)
            db.execute("DROP TRIGGER test_failure")
        accepted = self.call("handoff", data=handoff)
        self.assertEqual(accepted["result"]["seq"], before["result"]["watermark"] + 1)
        self.assertEqual(self.call("handoff", data=handoff), accepted)

    def test_invalid_json_and_invalid_request_leave_state_unchanged(self):
        identity = self.initialize()
        request = self.request(identity, "create", title="first")
        for raw in (b'{"title":"a","title":"b"}', b'{"title":"\\ud800"}', b'{"title": NaN}', b'[]'):
            self.call("task-create", raw=raw, code=2, error="invalid_input")
        self.call("task-create", data={**request, "extra": "ignored?"}, code=2, error="invalid_input")
        self.assertEqual(self.call("snapshot")["result"]["watermark"], 0)

    def test_protocol_guards_wrong_generation_cursor_and_schema(self):
        identity = self.initialize()
        self.page({**identity, "data_generation": "some-other-generation"}, 0,
                  code=4, error="generation_mismatch")
        self.page(identity, 1, code=4, error="cursor_ahead")
        self.page(identity, -1, code=2, error="invalid_input")
        self.page(identity, 0, limit=501, code=2, error="invalid_input")
        with closing(store.connect(store.project_db(self.project), "rw")) as db:
            db.execute("PRAGMA user_version = 999")
        self.call("snapshot", code=6, error="unsupported_schema")
        body = self.request(identity, "new", title="should never write")
        self.call("task-create", data=body, code=6, error="unsupported_schema")

    def test_snapshot_writer_attempts_to_commit_between_watermark_and_rows(self):
        identity = self.upgraded_legacy_database()
        task = self.create(identity)
        request = self.request(identity, "racing-create", title="边界写入")
        transactions = []
        writers = []
        # Intercept the independent writer just as it attempts to commit. The JSON
        # CLI reply arrives only after the read transaction releases its snapshot.
        writer_code = """
import sys
import worklog
from worklog_store import connect

def notify_commit(path, mode):
    db = connect(path, mode)
    db.set_trace_callback(lambda sql: print("committing", file=sys.stderr, flush=True)
                          if sql == "COMMIT" else None)
    return db

worklog.connect = notify_commit
sys.exit(worklog.main())
"""

        def read_connection(db_path, mode):
            db = store.connect(db_path, mode)

            def trace(statement):
                if statement.startswith("SELECT * FROM tasks ORDER BY"):
                    transactions.append(db.in_transaction)
                    writer = subprocess.Popen(
                        [sys.executable, "-c", writer_code, "--project", str(self.project), "task-create"],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=ROOT, env=self.env,
                    )
                    writers.append(writer)
                    writer.stdin.write(json.dumps(request).encode("utf-8"))
                    writer.stdin.close()
                    writer.stdin = None
                    ready, _, _ = select([writer.stderr], [], [], 5)
                    transactions.append(bool(ready) and writer.stderr.readline() == b"committing\n")

            db.set_trace_callback(trace)
            return db

        try:
            with patch("worklog.connect", side_effect=read_connection):
                _, baseline = worklog.execute(argparse.Namespace(project=str(self.project), command="snapshot"))
            self.assertEqual(len(writers), 1)
            writer = writers[0]
            stdout, stderr = writer.communicate(timeout=10)
            self.assertEqual(writer.returncode, 0, (stdout, stderr))
            change = json.loads(stdout)["result"]
        finally:
            for writer in writers:
                if writer.poll() is None:
                    writer.kill()
                    writer.communicate()
        self.assertEqual(transactions, [True, True], "Snapshot did not overlap the writer's commit attempt.")
        self.assertEqual(baseline["watermark"], 1)
        fields = {"display_status": "TODO", "archived": False, "priority": None, "blocker_refs": []}
        self.assertEqual(baseline["tasks"], [{**task, **fields}])
        after = self.page(identity, baseline["watermark"])["result"]
        self.assertEqual(after["changes"], [{**change, "task": {**change["task"], **fields}}])
        self.assertEqual(self.call("snapshot")["result"]["watermark"], 2)

    def test_cli_only_reader_reconstructs_state_and_replays_duplicate_pages(self):
        identity = self.upgraded_legacy_database()
        task = self.claim(identity, self.create(identity))
        first = self.call("checkpoint", data=self.record(identity, task, "progress-a", "progress"))
        baseline = self.call("snapshot")
        task = first["result"]["task"]
        handoff = self.call("handoff", data=self.record(identity, task, "handoff", "handoff"))
        second = self.claim(identity, handoff["result"]["task"], operation="claim-b")
        self.call("checkpoint", data=self.record(identity, second, "progress-b", "continued"))

        # The consumer sees only CLI JSON, including identity and change references.
        baseline = baseline["result"]
        tasks = {task["task_id"]: task for task in baseline["tasks"]}
        refs = {entry["entry_id"]: entry for entry in baseline["entry_refs"]}
        seen = set()
        pages = []
        cursor = baseline["watermark"]

        def apply(envelope):
            self.assertEqual(envelope["protocol_version"], "2.0")
            self.assertEqual(envelope["project_id"], identity["project_id"])
            self.assertEqual(envelope["data_generation"], identity["data_generation"])
            for change in envelope["result"]["changes"]:
                key = (envelope["project_id"], envelope["data_generation"], change["change_id"])
                if key in seen:
                    continue
                task = change["task"]
                tasks[task["task_id"]] = task
                if change["entry"]:
                    refs[change["entry"]["entry_id"]] = change["entry"]
                seen.add(key)

        while True:
            page = self.page(identity, cursor)
            pages.append(page)
            apply(page)
            cursor = page["result"]["next_after"]
            if not page["result"]["has_more"]:
                break
        for page in reversed(pages):
            apply(page)
        expected = self.call("snapshot")["result"]
        self.assertEqual(list(tasks.values()), expected["tasks"])
        self.assertEqual(list(refs.values()), expected["entry_refs"])
        self.assertEqual(len(seen), 3)
        self.assertEqual(expected["watermark"], cursor)
        for entry, kind in zip(refs.values(), ("progress", "handoff", "continued")):
            original = self.call("entry-get", entry["entry_id"])["result"]
            self.assertEqual(original["body_md"], SAMPLE[kind]["body_md"])
        end = self.page(identity, cursor)["result"]
        self.assertEqual(end, {"changes": [], "has_more": False, "next_after": cursor, "watermark": cursor})

    def test_historical_protocol_sample_matches_public_relay_receipts(self):
        historical = self.root / "published-v0.1.0"
        source_at(LEGACY, historical)
        examples = historical / "examples"
        examples.mkdir()
        for filename in ("relay.py", "sample.json"):
            (examples / filename).write_bytes(subprocess.check_output(
                ["git", "show", f"{LEGACY}:examples/{filename}"], cwd=ROOT,
            ))

        def relay(mode, client=None):
            argv = [sys.executable, str(examples / "relay.py"), "--project", str(self.project), mode]
            if client:
                argv.extend(["--client", client, "--session", client + "-session"])
            result = subprocess.run(argv, capture_output=True, timeout=15, cwd=self.caller, env=self.env)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            return [json.loads(line) for line in result.stdout.splitlines()]

        start = relay("start", "clientA")
        read = relay("read")
        resume = relay("resume", "clientB")
        verify = relay("verify")
        baseline = read[1]["reply"]
        original = read[2]["reply"]
        page = next(call["reply"] for call in verify if call["argv"][0] == "changes")
        self.assertEqual(original["result"]["body_md"], start[3]["request"]["body_md"])
        self.assertEqual(original["result"]["body_md"], SAMPLE["progress"]["body_md"])
        self.assertEqual(page["result"]["changes"][-2:], [call["reply"]["result"] for call in resume[-2:]])
        self.assertEqual(start[4]["reply"]["result"]["task"], baseline["result"]["tasks"][0])
        self.assertEqual(baseline["result"]["watermark"], 4)
        self.assertEqual(page["result"]["next_after"], 6)
        retry, rejected = (call["reply"] for call in verify if call["argv"][0] == "checkpoint")
        self.assertEqual(retry, start[3]["reply"])
        self.assertEqual(rejected["error"]["code"], "stale_run")
        for envelope in (baseline, original, page):
            self.assertEqual(envelope["protocol_version"], "1.0")
            self.assertEqual(envelope["project_id"], baseline["project_id"])
            self.assertEqual(envelope["data_generation"], baseline["data_generation"])

    def test_replay_script_is_standalone_and_uses_separate_callers(self):
        for mode, client, directory in (
            ("start", "clientA", self.root), ("read", None, self.caller),
            ("resume", "clientB", self.caller), ("verify", None, self.root),
        ):
            argv = [sys.executable, str(ROOT / "examples" / "relay.py"), "--project", str(self.project), mode]
            if client:
                argv.extend(["--client", client, "--session", client + "-session"])
            result = subprocess.run(argv, capture_output=True, timeout=15, cwd=directory, env=self.env)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertTrue(all("reply" in json.loads(line) for line in result.stdout.splitlines()))


if __name__ == "__main__":
    unittest.main()
