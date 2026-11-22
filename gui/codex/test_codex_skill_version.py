import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

PANEL = Path(__file__).parent / "worklog-panel"
sys.path[:0] = [str(PANEL), str(PANEL.parent.parent.parent)]

from codex_skill_version import current_skill_version


# Synthetic UUIDv7 values with a timestamp of 2026-11-22 12:00 UTC.
THREAD = "01a20320-a200-7111-8111-111111111111"
OTHER_THREAD = "01a20320-a200-7222-8222-222222222222"


def invocation(script, version, marker=True):
    payload = {
        "ok": True, "protocol_version": "2.0",
        "result": {
            "command": "release-version", "release_version": version,
            "status": "recognized", "cli_path": str(script),
        },
    }
    prefix = "env WORKLOG_CODEX_SKILL=1 " if marker else ""
    return {
        "type": "event_msg", "payload": {
            "type": "item_completed",
            "item": {
                "type": "CommandExecution", "status": "completed", "exit_code": 0,
                "command": ["/bin/zsh", "-lc", f"{prefix}python3 -B '{script}' release-version"],
                "aggregated_output": json.dumps(payload),
            },
        },
    }


class SkillVersionTest(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        folder = self.home / "sessions/2026/11/22"
        folder.mkdir(parents=True)
        self.rollout = folder / f"rollout-2026-11-22T00-40-14-{THREAD}.jsonl"
        self.script = self.home / "project with spaces/.agents/skills/worklog/scripts/worklog.py"

    def write_session(self, *records, identity=THREAD):
        entries = [{"type": "session_meta", "payload": {"id": identity}}, *records]
        self.rollout.write_text("".join(json.dumps(record) + "\n" for record in entries))

    def test_uses_latest_skill_invocation_and_ignores_standalone_cli(self):
        self.write_session(
            invocation(self.script, "v0.4.0"), invocation(self.script, "v0.5.0"),
            invocation(self.script, "v9.9.9", marker=False),
        )
        self.assertEqual(current_skill_version(THREAD, self.home), "v0.5.0")

    def test_unloaded_session_and_unrelated_thread_cannot_return_a_version(self):
        self.write_session(invocation(self.script, "v0.4.0", marker=False))
        self.assertIsNone(current_skill_version(THREAD, self.home))
        self.assertIsNone(current_skill_version(OTHER_THREAD, self.home))
        self.write_session(invocation(self.script, "v0.4.0"), identity=OTHER_THREAD)
        self.assertIsNone(current_skill_version(THREAD, self.home))

    def test_latest_attempt_with_failed_command_clears_version(self):
        failed = invocation(self.script, "v0.4.1")
        failed["payload"]["item"]["exit_code"] = 2
        self.write_session(invocation(self.script, "v0.4.0"), failed)
        self.assertIsNone(current_skill_version(THREAD, self.home))

    def test_wrong_output_path_and_non_skill_path_do_not_identify_a_version(self):
        changed = invocation(self.script, "v0.4.1")
        changed["payload"]["item"]["aggregated_output"] = json.dumps(
            json.loads(invocation(self.home / "other/worklog.py", "v0.4.1")["payload"]["item"]["aggregated_output"])
        )
        self.write_session(changed)
        self.assertIsNone(current_skill_version(THREAD, self.home))
        self.write_session(invocation(self.home / "unrelated/worklog.py", "v0.4.1"))
        self.assertIsNone(current_skill_version(THREAD, self.home))


if __name__ == "__main__":
    unittest.main()
