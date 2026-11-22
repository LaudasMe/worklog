---
name: worklog
description: Use workLog to find tasks, read records, change task state or priority, manage projects, hand off to another client, report the loaded CLI and Skill release version, or manually check for workLog updates. Applies to T001-style tasks and requests to 查询摘要、读取上下文、开始任务、记录结果、标记完成、接手、交接、查询 workLog 版本或检查更新.
---

# workLog

Use the installed workLog CLI for project identities, task state and work records. The skill directory is the directory containing the loaded `SKILL.md`. Use its absolute path, or the skill base directory supplied by the client.

## Show the release version or check for an update

For 「当前用的是哪个 workLog 版本」, use `release-version`. For 「检查 workLog 是否有更新」, use `check-update`. These requests can run from any directory without selecting a project.

Use the absolute directory of the `SKILL.md` loaded for this chat. In Codex, use the path attached to the loaded Skill or the Skill document you read. In OpenCode, use the base directory returned when the built-in `skill` tool loads `worklog`.

The first time you use workLog in a chat, run `release-version` by itself with the loaded Skill's CLI. If the user is asking for the release version, use that result for the reply. Continue the requested work once the command returns.

- In Codex, run `env WORKLOG_CODEX_SKILL=1 python3 -B "<skill directory>/scripts/worklog.py" release-version`. Use this form for the first confirmation and later version queries, so the Codex sidebar can recognize this Skill invocation in the current chat.
- In OpenCode, run `python3 -B "<skill directory>/scripts/worklog.py" release-version`.
- For a manual update check in either host, run `python3 -B "<skill directory>/scripts/worklog.py" check-update` as a separate command.

- For a version query, report `result.release_version` and `protocol_version` separately. If the release version is missing or the loaded CLI lacks the release command, report 「无法识别」. The `--version` flag reports the CLI protocol.
- For update checks, report the installed release, latest formal release, comparison status and any Release link returned by the command. Include the reason when the result contains an error and retry only on request. A missing or unidentifiable local version uses 「无法判断」 for comparison.
- These commands only read information. The user can follow the Release link to download and install manually.

## Confirm the project before calling the CLI

- Use the supplied project root if the user gives one. When asked to work on the current project and the session starts at its root, use the current directory. For another confirmed project, put `--project <root>` before the command.
- A bare task number with multiple possible projects requires a question. Ask which project to use and end the turn before querying any project's identity, task, snapshot or records. If a project selector is canceled or remains unanswered, return the question as plain text and finish the turn. Query several projects only on a request to read each of them, using their separately confirmed roots.

## Discover or update saved project paths

- When explicitly asked to list saved projects, use `projects list`. The CLI checks each stored path against its project ID and reports paths that need attention.
- New projects are registered automatically. On `project_registration_failed`, the database is already available. Use `--project <confirmed-root> projects register` to retry after addressing the error. When the stored ID belongs to a different project, show both identities and follow the user's explicit move or removal instruction before retrying.
- For explicit registration, moves, removal, or upgrades from the shared registry, read the installed command reference. Confirm a single project for backup and upgrade. Use both its path and project ID so the CLI can recheck the selected database.

## Read

```sh
python3 -B "<skill directory>/scripts/worklog.py" summaries --type task --view pending
python3 -B "<skill directory>/scripts/worklog.py" summaries --type task --status DONE
python3 -B "<skill directory>/scripts/worklog.py" context
python3 -B "<skill directory>/scripts/worklog.py" context --task T001
python3 -B "<skill directory>/scripts/worklog.py" --project "<confirmed absolute project directory>" identity
python3 -B "<skill directory>/scripts/worklog.py" --project "<confirmed absolute project directory>" context --task T001
```

- Use `summaries --type task` for all unarchived tasks, `--view pending` for work still open, and `--view archived` for archived tasks. Combine `--status` (base), `--display-status` (shown), `--blocked` and `--priority` as requested. A null priority means 未设置.
- In a new session with only the current project directory, use `summaries` first to discover tasks. Read project and selected task `context`, then follow each `source` using its exact ID. Use `record-get` for local records and `entry-get` for progress and handoff originals on schema 4. Keep project decisions and constraints distinct from the task's own records.
- Look at the saved open items, original work instructions, and referenced current files before continuing. If reading only, leave the project and its database untouched. To check closed or replaced items, use `summaries --type record --status RESOLVED` or `--status SUPERSEDED`.
- Use `context` to confirm the project name, `project_id` and `data_generation`. Use `context --task` for the task ID and latest state. Read `task.completion` for a completed task, then follow its evidence references to the original records. Keep a separate identity and task for each project. For schema 4, fetch `latest_entry` and `latest_handoff` by `entry-get <entry_id>` when the user asks for the original notes or when resuming from a handoff.
- Public reads use protocol 2.0 and require schema 4. If a public read returns `upgrade_required`, use `context` or `summaries` to inspect the existing data. Report if the original progress or handoff text requires an upgrade or retrieval from an isolated backup with the matching older CLI. Use the backup and upgrade workflow when the user authorizes it.
- A request to view, summarize or check status uses only read commands. If the project is uninitialized, report it. Use `init` or `task-create` only for an explicit request to initialize or create.
- When reporting two same-number tasks, identify their projects and present their latest facts separately. A new session can recover the task and handoff from the project CLI.

## Change task state or priority

Read [the installed command reference](references/commands.md) for the request fields.

- On an explicit request to start a `TODO` task, use `task-start` with the latest task revision. Starting moves it to `IN_PROGRESS` without opening a run. Save ordinary progress with the existing `record` command.
- On explicit confirmation or authorization to complete a task, use `task-complete`. Include the completed scope and reference saved evidence by ID. Read the existing progress or handoff before citing it. Check open task blockers and TODOs in the latest context. Resolve blockers through the existing record workflow. Provide a disposition for each open TODO based on the user's instructions or confirmed work.
- A task with existing evidence can move directly from `TODO` or `PAUSED` to `DONE` after satisfying the completion checks. Verify `result.task.status` and report the completed scope and original evidence references. The completion also closes any active run.
- Use `task-pause` and `task-resume` on explicit requests. Both preserve the current active run. A successful new claim or handoff on a paused task keeps it paused.
- Use `task-cancel` with the user's reason. A terminal task can be archived or restored to the list with `task-archive` or `task-unarchive`. Use `task-reopen` when the user supplies a reason and usable saved evidence or an explicit confirmation. A clear request with a reason can supply the confirmation. Report the new state and available `reopen_candidates`. Carry an old item forward via `record` with `carry_forward_id` only after the user selects it.
- Read the current revision before changing priority with `task-set-priority`. Pass one of `p0,p1,p2,p3` or JSON `null` to clear. On creation include `priority` when the user specifies one.
- If a task command returns `upgrade_required`, report the need for a backup and an explicitly authorized upgrade.

## Claim, record and hand off

Read [the installed command reference](references/commands.md) before writing. Use the selected project's identity and the selected task's latest `revision` and `context_position`. Send each request as UTF-8 JSON to stdin.

1. **Claim.** Read the current state and the handoff if present. An explicit request to take over a handed-off task authorizes `claim`. If an active run belongs to another session, ask for confirmation before replacing it. Use the current client's name and the current native session identifier when available, or generate a unique label and retain it for the conversation.
2. **Record.** Use `record` for project context and for task context when authorized to write. Use one batch when the same verified progress closes related items. Preserve unresolved questions and other open items unless the work resolves them. Task context can be written without a claim when there is no active run. If another session owns the run, ask before taking over. For the original handoff workflow, use `checkpoint` for progress after a confirmed claim.
3. **Handoff.** Use `handoff` when asked to transfer or close the current run. Include the verified progress and the next action. Show the project and task number, the saved result, and next step after success. A handoff closes the run and preserves the task's base state. A paused task remains paused.
4. **Resolve failures.** For a missing receipt or a busy database, reuse the operation ID and exact request. For stale run, revision conflict, or project or generation mismatch, stop the write and read the current state before deciding how to proceed. An operation with changed input uses a new operation ID.
