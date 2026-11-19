# workLog bundled CLI

Use Python 3.9 or newer and the absolute path to the bundled `scripts/worklog.py`. The CLI defaults to the current directory. Use `--project <root>` before the command to select another confirmed project. Data is under that project's `.worklog` directory.

| Intent | Command | Input and result |
| --- | --- | --- |
| Identify project | `identity` | Returns `result.name` and supported read capabilities. All successful results contain `project_id`, `data_generation` and `protocol_version`. |
| Read task | `task-get T001` | Accepts a display number or UUID. `result` includes `task_id`, `number`, `revision`, `active_run`, `latest_entry`, `latest_handoff` and `next_step`. |
| Discover tasks | `summaries --type task --status IN_PROGRESS` | Optional `--task` or `--blocked`. Each item has saved summary, task ID, number, status and source. |
| Find record summaries | `summaries --type record --status OPEN --task T001` | Also filters by `--kind` or `--blocked`. Use `RESOLVED` for closed work and `SUPERSEDED` for replaced versions. Pass `--limit` and use `next_cursor` with `--cursor` for the next page. |
| Read project or task context | `context`, `context --task T001` | Goals, progress, decisions, impacts, open items, constraints, documents and source references. Task includes both `revision` and `context_position`. |
| Open context record | `record-get <record_id>` | Saved Markdown, details, references and replacement links. |
| Save a context batch | `record` | Read the fields below. The receipt contains the saved IDs and the new task context position. |
| Read original | `entry-get <entry_id>` | Returns unchanged `result.body_md` and metadata. Use an entry ID from the same project's task result or snapshot. |
| List tasks and record references | `snapshot` | Returns task list, entry references, and a consistency watermark without writing. |
| Initialize on request | `init` | Reads JSON with `name`. Existing project with the same name returns its existing identity. |
| Create on request | `task-create` | Reads common write fields and `title`. Returns the new task in `result.task`. |
| Claim | `claim` | Reads common write fields, `task_id`, `expected_revision`, `client` and `session_id`. When the user confirms replacing an active run, also pass `replaces_run_id` from the latest task. |
| Save progress | `checkpoint` | Reads common write fields, `task_id`, `expected_revision`, `run_id`, `summary`, `body_md` and `next_step`. |
| Hand off | `handoff` | Reads the same fields as `checkpoint`. Returns a new handoff entry and clears the active run. |

Common write fields are `operation_id`, `project_id` and `data_generation`. Use a newly generated UUID for each new operation. In a successful write result, `result.seq` and `result.change_id` identify the receipt, `result.task.revision` is the new task revision, and `result.task.active_run.run_id` identifies the run when it is still open.

A checkpoint request can be passed as a single JSON object on stdin. Use a quoted heredoc when preparing JSON in a shell. Substitute IDs and the number for the selected project's current values. Keep newlines in JSON string fields escaped as `\n`.

```sh
python3 -B "<skill directory>/scripts/worklog.py" --project "<project directory>" checkpoint <<'JSON'
{"operation_id":"<new UUID>","project_id":"<project ID>","data_generation":"<data generation>","task_id":"<task ID>","expected_revision":2,"run_id":"<active run ID>","summary":"普通样例通过","body_md":"# 进展\n\n已核对样例。\n","next_step":"核对零金额边界。"}
JSON
```

For `record`, send the common write fields, `scope` and `records`. With `scope: "TASK"`, include `task_id`, `expected_revision` and `expected_context_position` from the current context, plus `run_id` when there is an active run owned by this session. With `scope: "PROJECT"`, omit all task guard fields and optionally set `related_task_id` on individual records. An optional `client` and `session_id` must be supplied together.

Each item needs `kind`, `summary`, `body_md` and `details`, and may have `replaces_id` and `refs`. For a progress, provide `details.processed_scope` (string array) and `details.impacts` (array, empty when the work has no recorded impacts). An impact has `target_type` (`FILE` or `SCOPE`), `target`, `effect` and optional `note`. To close a TODO, QUESTION or BLOCKER, replace its current record with the same kind and scope. Supply `details.status: "RESOLVED"` or `"CANCELLED"`, `details.outcome_md` and evidence. A BLOCKER also needs the original `blocked_work` and `unblock_condition` in `details`. To cite an earlier item in the same request, use `{"type":"RECORD","batch_index":0}` for the first item. To cite a saved record use `{"type":"RECORD","target_record_id":"<...>"}`. File references use `{"type":"FILE","path":"project/relative/path"}` and optional `locator`, old entries use `{"type":"ENTRY","target_entry_id":"<...>"}`.

The CLI returns one UTF-8 JSON object. Read `error.code` when `ok` is false. `not_initialized`, `task_not_found`, `record_not_found` and `entry_not_found` mean that the requested data is unavailable at that path. Refresh state before acting on a conflict or a stale cursor. With `database_busy` or an uncertain receipt, keep the original command and request for a safe retry. If `record` reports `upgrade_required`, use the explicit backup and upgrade workflow with the project's authorization.
