# workLog bundled CLI

Use Python 3.9 or newer and the absolute path to the bundled `scripts/worklog.py`. The CLI defaults to the current directory. Use `--project <root>` before the command to select another confirmed project. Data is under that project's `.worklog` directory.

| Intent | Command | Input and result |
| --- | --- | --- |
| Read the installed release | `release-version` | Returns `result.release_version` and the running CLI path, with the CLI protocol in `protocol_version`. A missing or invalid manifest yields a null release version and 「无法识别」. |
| Check for an update | `check-update` | Requests official GitHub Releases and returns installed and latest CLI／Skill releases, a comparison status, the Release link when available, and the reason for a failed lookup. |
| Identify project | `identity` | Returns `result.name` and supported read capabilities. Project data responses contain `project_id`, `data_generation` and `protocol_version`. |
| Read task | `task-get T001` | Accepts a display number or UUID. Includes base and display states, archive flag, nullable priority, active run and record references. Includes completion details for a `DONE` task. |
| Discover tasks | `summaries --type task --status IN_PROGRESS` | Default: unarchived tasks. Select `--view pending` or `--view archived`; combine `--status`, `--display-status`, `--blocked`, `--priority p0…p3` and `--task`. Includes base and display states plus nullable priority. |
| Find record summaries | `summaries --type record --status OPEN --task T001` | Also filters by `--kind` or `--blocked`. Use `RESOLVED` for closed work and `SUPERSEDED` for replaced versions. Pass `--limit` and use `next_cursor` with `--cursor` for the next page. |
| Read project or task context | `context`, `context --task T001` | Goals, progress, decisions, impacts, open items, constraints, documents and source references. Task includes `revision` and `context_position`, plus `completion` when `DONE`. Schema 4 task detail includes `entry_refs`, `lifecycle_history` and `reopen_candidates`. |
| Open context record | `record-get <record_id>` | Saved Markdown, details, references and replacement links. |
| Save a context batch | `record` | Read the fields below. The receipt contains the saved IDs and the new task context position. |
| Read original | `entry-get <entry_id>` | Returns unchanged `result.body_md` and metadata. Use an entry ID from the same project's task context, completion evidence or snapshot. |
| List tasks and record references | `snapshot` | Returns `watermark`, all tasks including archived tasks, and progress and handoff references. |
| Read changes after a snapshot | `changes --project-id <id> --data-generation <generation> --after <seq>` | Returns changes in ascending sequence, `next_after`, `has_more` and `watermark`. Pass `--limit` for pages of 1 to 500 changes. |
| Initialize on request | `init` | Reads JSON with `name`. Existing project with the same name returns its existing identity. |
| Create on request | `task-create` | Reads common write fields, `title` and optional `priority`. Returns the new task in `result.task`. |
| Start on request | `task-start` | Reads common write fields, `task_id` and `expected_revision`. Starts a `TODO` task without opening a run. |
| Complete on request | `task-complete` | Reads the start fields plus `completed_scope`, `evidence` and optional `todo_dispositions`. Returns `result.task` with completion details. |
| Pause or resume | `task-pause`, `task-resume` | Same guards as `task-start`. Pause moves `IN_PROGRESS` to `PAUSED`; resume restores `IN_PROGRESS`. Both preserve the active run. |
| Cancel | `task-cancel` | Same guards plus nonempty `reason`. Closes the active run. |
| Archive or unarchive | `task-archive`, `task-unarchive` | Same guards. Applies to `DONE` or `CANCELLED`; preserves the base status. |
| Reopen | `task-reopen` | Same guards plus a nonempty `reason` and at least one `evidence` reference. Returns `TODO` and lists candidates in `result.lifecycle.reopen_candidates`. |
| Set or clear priority | `task-set-priority` | Same guards plus `priority` set to `p0`, `p1`, `p2`, `p3` or JSON `null`. |
| Claim | `claim` | Reads common write fields, `task_id`, `expected_revision`, `client` and `session_id`. When the user confirms replacing an active run, also pass `replaces_run_id` from the latest task. |
| Save progress | `checkpoint` | Reads common write fields, `task_id`, `expected_revision`, `run_id`, `summary`, `body_md` and `next_step`. |
| Hand off | `handoff` | Reads the same fields as `checkpoint`. Returns a new handoff entry and clears the active run. |

`release-version` and `check-update` can run without a project. Both are read-only. The release version comes from `release-version.json` next to the CLI script. The `--version` flag returns the protocol version as plain text. The update check matches stable `vX.Y.Z` tags for the CLI／Skill and excludes drafts and prereleases. Its comparison codes are `update_available`, `up_to_date`, `ahead` and `unknown`. On remote failure, the comparison is `unknown` and the local release is still included when known.

Common write fields are `operation_id`, `project_id` and `data_generation`. Use a newly generated UUID for each new operation. For the original task and handoff writes, `result.seq` and `result.change_id` identify the receipt, `result.task.revision` is the new task revision, and `result.task.active_run.run_id` identifies the run when it is still open.

For `task-start` and `task-complete`, take the latest `expected_revision` from `context --task` and pass the task UUID or display number as `task_id`. Both commands require schema 3 or newer and a CLI compatible with the project's schema. The start command accepts `TODO`. The completion command accepts `TODO`, `IN_PROGRESS` or `PAUSED` and requires a nonempty `completed_scope` and at least one saved evidence reference.

Pause, resume, cancel, archive, reopen and priority writes require schema 4. Reopen evidence may be a saved work record or `{"type":"USER_CONFIRMATION","note":"<confirmed instruction>"}`. To bring back an available historical TODO or BLOCKER, send a new task-scoped `OPEN` item of the same kind to `record` with `carry_forward_id` set to the selected original record ID. Its original reference is stored automatically.

The public read commands use protocol 2.0 and require schema 4. You can explicitly pass `--protocol-version 2.0` before a public read command. A request for 1.0 returns `invalid_input`. Run local commands without the protocol option. Start synchronization with a 2.0 `snapshot` and pass its `watermark` to `changes --after`. Clients moving from 1.0 start from this new snapshot. Keep the read and applied positions for each protocol version, project and data generation. If a public cursor predates the migration boundary, `cursor_stale` gives the earliest position in `details.min_after`. Read a new snapshot to resume.

An evidence item is `{"type":"RECORD","target_record_id":"<record_id>"}` for current task progress or current project progress linked to this task. An item can also be `{"type":"ENTRY","target_entry_id":"<entry_id>"}` for a saved progress or handoff entry from this task. With open task TODOs, pass `todo_dispositions` as a JSON object keyed by each current TODO's record ID, with a nonempty explanation as the value. Resolve open task blockers through `record` before completing. The `completion` result includes `completed_at`, `completed_scope`, `evidence` with `command` and `id` on each item, and `todo_dispositions`. Completion closes an active run in the same write.

If the user authorizes upgrading an older project, first run `backup --output <new-absolute-backup-path>` with the bundled CLI, then run `upgrade --backup <same-absolute-backup-path>` against that same project. Inspect both results, then retry the operation with the current CLI or Skill. To verify recovery, use the corresponding old CLI on an isolated copy of the backup. Check the saved task, original records, references and receipts before upgrading the copy. For a schema 3 `DONE` task, review completion details locally with `context --task T001`, using the task's actual number.

## Shared project registry

| Intent | Command |
| --- | --- |
| List registered projects | `projects list` reports which paths are available, unavailable, or have a different project ID. |
| Register an existing project | `--project <root> projects register` reads the database identity before adding the path. |
| Update a moved project | `--project <new-root> projects move --from <old-root> --project-id <project-id>` checks the ID at the new path. |
| Remove a registration | `--project <root> projects remove --project-id <project-id>` removes the matching registry item and keeps the database. |

The registry is `~/.local/share/worklog/projects.json` for both clients. A new database is registered automatically. If `init` returns `project_registration_failed`, the database has been created. Check the registry failure and retry with `--project <root> projects register`. An existing database requires explicit registration.

When upgrading from the registry, confirm a single project from the list, then pass its root as `--project` and its ID as `--project-id` to both `backup` and `upgrade`. Each command verifies that ID against the actual database inside its transaction.

## Requests and original records

A checkpoint request can be passed as a single JSON object on stdin. Use a quoted heredoc when preparing JSON in a shell. Substitute IDs and the number for the selected project's current values. Keep newlines in JSON string fields escaped as `\n`.

```sh
python3 -B "<skill directory>/scripts/worklog.py" --project "<project directory>" checkpoint <<'JSON'
{"operation_id":"<new UUID>","project_id":"<project ID>","data_generation":"<data generation>","task_id":"<task ID>","expected_revision":2,"run_id":"<active run ID>","summary":"普通样例通过","body_md":"# 进展\n\n已核对样例。\n","next_step":"核对零金额边界。"}
JSON
```

For `record`, send the common write fields, `scope` and `records`. With `scope: "TASK"`, include `task_id`, `expected_revision` and `expected_context_position` from the current context, plus `run_id` when there is an active run owned by this session. With `scope: "PROJECT"`, omit all task guard fields and optionally set `related_task_id` on individual records. An optional `client` and `session_id` must be supplied together.

Each item needs `kind`, `summary`, `body_md` and `details`, and may have `replaces_id` and `refs`. For a progress, provide `details.processed_scope` (string array) and `details.impacts` (array, empty when the work has no recorded impacts). An impact has `target_type` (`FILE` or `SCOPE`), `target`, `effect` and optional `note`. To close a TODO, QUESTION or BLOCKER, replace its current record with the same kind and scope. Supply `details.status: "RESOLVED"` or `"CANCELLED"`, `details.outcome_md` and evidence. A BLOCKER also needs the original `blocked_work` and `unblock_condition` in `details`. To cite an earlier item in the same request, use `{"type":"RECORD","batch_index":0}` for the first item. To cite a saved record use `{"type":"RECORD","target_record_id":"<...>"}`. File references use `{"type":"FILE","path":"project/relative/path"}` and optional `locator`, old entries use `{"type":"ENTRY","target_entry_id":"<...>"}`.

The CLI returns one UTF-8 JSON object. Read `error.code` when `ok` is false. `not_initialized`, `task_not_found`, `record_not_found` and `entry_not_found` mean that the requested data is unavailable at that path. Refresh state before acting on a conflict or a stale cursor. With `database_busy` or an uncertain receipt, keep the original command and request for a safe retry. If a command reports `upgrade_required`, use local `context --task` or `summaries` to review an older project. Upgrade through the backup and upgrade workflow with the project's authorization. For `task_state_conflict`, refresh the task. For `invalid_evidence`, `task_blocked` or `todo_disposition_required`, refresh the relevant task context and address the reported issue.
