# workLog bundled CLI

Run from any working directory with Python 3.9 or newer. Use the absolute path to the bundled `scripts/worklog.py` and pass `--project` before the command. The project path is the existing project directory, such as `/path/to/my-project`, and data is under its `.worklog` directory.

| Intent | Command | Input and result |
| --- | --- | --- |
| Identify project | `identity` | Returns `result.name` and supported read capabilities. All successful results contain `project_id`, `data_generation` and `protocol_version`. |
| Read task | `task-get T001` | Accepts a display number or UUID. `result` includes `task_id`, `number`, `revision`, `active_run`, `latest_entry`, `latest_handoff` and `next_step`. |
| Read original | `entry-get <entry_id>` | Returns unchanged `result.body_md` and metadata. Use an entry ID from the same project's task result or snapshot. |
| List tasks and record references | `snapshot` | Returns the task list, saved record summaries and IDs, and the latest change number included (`watermark`). |
| Initialize on request | `init` | Reads JSON with `name`. Existing project with the same name returns its existing identity. |
| Create on request | `task-create` | Reads common write fields and `title`. Returns the new task in `result.task`. |
| Claim | `claim` | Reads common write fields, `task_id`, `expected_revision`, `client` and `session_id`. When the user confirms replacing an active run, also pass `replaces_run_id` from the latest task. |
| Save progress | `checkpoint` | Reads common write fields, `task_id`, `expected_revision`, `run_id`, `summary`, `body_md` and `next_step`. |
| Hand off | `handoff` | Reads the same fields as `checkpoint`. Returns a new handoff entry and clears the active run. |

Common write fields are `operation_id`, `project_id` and `data_generation`. Use a newly generated UUID for each new operation. In a successful write result, `result.seq` and `result.change_id` identify the saved change, `result.task.revision` is the new task revision, and `result.task.active_run.run_id` identifies the run when it is still open.

A checkpoint request can be passed as a single JSON object on stdin. Use a quoted heredoc when preparing JSON in a shell. Substitute IDs and the number for the selected project's current values. Keep newlines in JSON string fields escaped as `\n`.

```sh
python3 -B "<skill directory>/scripts/worklog.py" --project "<project directory>" checkpoint <<'JSON'
{"operation_id":"<new UUID>","project_id":"<project ID>","data_generation":"<data generation>","task_id":"<task ID>","expected_revision":2,"run_id":"<active run ID>","summary":"普通样例通过","body_md":"# 进展\n\n已核对样例。\n","next_step":"核对零金额边界。"}
JSON
```

The CLI returns one UTF-8 JSON object. Read `error.code` when `ok` is false. `not_initialized`, `task_not_found` and `entry_not_found` mean the requested data is unavailable at that path. `claim_conflict`, `revision_conflict`, `stale_run`, `operation_conflict`, `project_mismatch` and `generation_mismatch` require checking the applicable state. With `database_busy` or a missing result, keep the original command and request for a safe retry.
