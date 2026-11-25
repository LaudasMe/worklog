---
name: worklog
description: Use workLog to find tasks, follow tasks across Codex chats, read and save records, change task state or priority, manage projects, hand off to another client, report the loaded CLI and Skill release version, or manually check for workLog updates. Applies to T001-style tasks and requests to 查询摘要、读取上下文、开始任务、继续任务、跟进任务、记录结果、标记完成、接手、交接、查询 workLog 版本或检查更新.
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

## Follow tasks in the current Codex chat

- When the user explicitly uses workLog to start or continue one or more identified tasks, treat that instruction as authorization for this chat to save those tasks' verified progress, concrete TODOs and blockers. The user may authorize other identified tasks later. A lookup, planning discussion or mention of a task grants no follow-up authorization.
- Track lifecycle instructions separately for each task. Start a pending task only when the user explicitly asks to start that task. If asked to continue a pending task, tell the user its current status and leave its base state as is unless they ask to start it. A request to save one specific result authorizes that write and grants no ongoing follow-up authorization.
- Read the host-provided value with `printenv CODEX_THREAD_ID` in the current chat. Use this exact value with `client: "codex"` and `session_id` in the request for `record` and `claim`. If the value is unavailable, tell the user that the chat cannot safely record follow-up with its source and keep it read-only. Never substitute `CODEX_SESSION_ID`, a generated ID, or an ID from another chat or from the user message.
- For each authorized task, run `identity` and `context --task <number or UUID>` with the confirmed absolute workLog project path. Confirm the context matches the project ID and data generation from identity. Use the canonical project path, project ID, data generation and returned task UUID as the task's identity in this chat. Keep these authorizations independently in the chat context. Use the workLog management root for every follow-up CLI call via `--project`, including when the actual work takes place in a separate code directory.
- Continuous follow-up uses schema 4. If `identity` reports `upgrade_required`, use the documented local read workflow and report the existing upgrade option.
- Before continuing work, read the latest progress, open TODOs, blockers, and `task.active_run` in task context. Follow source references as needed. Before a new write, reread task context by UUID and verify the authorized project ID, data generation, task UUID and current chat. Use the current task revision and context position for writes that require them. If the project, data generation or task identity changes, stop the affected write and confirm the intended target.
- For each result, infer its task from the user's instructions, the work performed and the task's goal. Keep writes for different tasks in separate requests. If attribution is ambiguous, ask which task should receive that result. Once the task is clear, ask once whether to follow it if neither this write nor ongoing follow-up is authorized. A reply that only names a task resolves attribution. While attribution or authorization is pending, make no workLog writes for that result, including starting or claiming the task. Continue following other authorized tasks when their results are clear. If authorization is declined, leave the result unwritten. With authorization, confirm identity and current state before saving. Reuse only ongoing follow-up authorization for later clear results on that task.
- With no active run, save authorized task context directly without claiming. For an active run owned by this chat, verify both its client and session ID and include the current `run_id` in `record`. When another client or chat owns an active run, keep task writes on hold and offer the existing handoff or explicit replacement flow. After handoff or confirmation to replace the specific run, reread and verify ownership before writing. A changed active run requires a fresh confirmation before replacement.

## Save verified milestones and handle TODOs

- In an authorized task, save a verified piece of work with task-scoped `record` before reporting the result. Use `PROGRESS` with a concise summary, the actual steps and observed results in `body_md`, `details.processed_scope`, and `details.impacts` (an empty array when appropriate). Cite supporting artifacts or saved records in `refs` when available. Set `details.suggested_next_step` when useful. Use this chat's actual source and the guards described above. A plan, discussion, unverified expectation or repeated report of a saved result does not create progress.
- Before writing, compare the result and evidence with the task's saved progress and recent history. Follow the exact `record-get` or `entry-get` reference when the summary alone is insufficient. Inspect the open TODOs and their original records. Save one task-scoped `TODO` with `details.status: OPEN` for a distinct outstanding action the work actually identified. A suggestion in a progress record is a prompt and does not create a TODO. Keep a matching open TODO as it is when it is mentioned again.
- When new work resolves an existing TODO, read its original record and confirm it is still the current actionable item for this task. Write a `TODO` with `replaces_id` set to that exact record ID, `details.status: RESOLVED`, and `details.outcome_md` explaining what was verified. Supply `refs` to the verified evidence. To save the progress and close the TODO atomically, put the `PROGRESS` first in the same request and cite it with `{"type":"RECORD","batch_index":0}`. An existing saved progress can be cited by its record ID instead. Without evidence of resolution, leave the TODO open.
- Use one operation ID per logical write and retain the complete request until you have a receipt. With `database_busy` or an uncertain receipt, retry that exact request, including its operation ID and guards. On a confirmed revision, context, ownership, or replacement conflict, reread and check for an already-saved result. Apply the identity and ownership checks again. If a verified result still needs a write, build a new request with the fresh guards and a new operation ID. Report what was saved or what prevented the write.

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
- Use `task-complete` after the user explicitly confirms that the entire identified task is complete or explicitly directs you to complete it now. Include the completed scope and reference saved evidence by ID. Read the existing progress or handoff before citing it. Check open task blockers and TODOs in the latest context. Resolve blockers through the existing record workflow. Provide a disposition for each open TODO based on the user's instructions or confirmed work.
- A task with existing evidence can move directly from `TODO` or `PAUSED` to `DONE` after satisfying the completion checks. Verify `result.task.status` and report the completed scope and original evidence references. The completion also closes any active run.
- Use `task-pause` and `task-resume` on explicit requests. Both preserve the current active run. A successful new claim or handoff on a paused task keeps it paused.
- Use `task-cancel` with the user's reason. A terminal task can be archived or restored to the list with `task-archive` or `task-unarchive`. Use `task-reopen` when the user supplies a reason and usable saved evidence or an explicit confirmation. A clear request with a reason can supply the confirmation. Report the new state and available `reopen_candidates`. Carry an old item forward via `record` with `carry_forward_id` only after the user selects it.
- Read the current revision before changing priority with `task-set-priority`. Pass one of `p0,p1,p2,p3` or JSON `null` to clear. On creation include `priority` when the user specifies one.
- If a task command returns `upgrade_required`, report the need for a backup and an explicitly authorized upgrade.

## Follow the task lifecycle in Codex

- When the user explicitly starts a task in `TODO`, verify the current task and ownership, then use `task-start` with its latest revision, unless the user requested an authorized claim that already starts it. Reread after the state changes. A request to continue grants follow-up authorization for the identified task. An explicit start or applicable authorized claim is needed to change its base state.
- If a verified issue prevents the task from proceeding, save an open task-scoped `BLOCKER` with the affected work, reason and `details.unblock_condition`. Check the current blockers so the same issue is recorded once. When verified work meets an original blocker's condition, reread it and write a replacement with `details.status: RESOLVED`, the original `blocked_work` and `unblock_condition`, an `outcome_md` explanation and evidence in `refs`. Use the exact original ID in `replaces_id`. The evidence may be a progress in the same batch. Afterward reread the task's base and display states. Other effective blockers remain in force. After the last one is resolved the CLI reports the base state as the display state.
- Save verified acceptance results as progress. Wait for the user to explicitly confirm completion of the entire identified task before calling `task-complete`. Reread current ownership, all effective blockers and TODOs, and the originals of the evidence to be cited. Set `completed_scope` to the confirmed work and include the applicable saved progress from each session in `evidence`. Provide a confirmed disposition for each open TODO. If blockers or dispositions still prevent completion, report the specific remaining items. On success, reread the task and its completion evidence and report the saved state.

## Follow up on items from a finished or cancelled task

- A finished (`DONE`) or cancelled (`CANCELLED`) task can receive historical progress and corrections to those records. Do not add an open task TODO in either state.
- When authorized to record an outcome on a finished task, read the original TODO and its current state first. Use its original ID and the outcome evidence with [the terminal TODO request fields](references/commands.md#terminal-task-todos). Distinguish verified resolution from a disposition such as transferring the work. The saved completion stays intact.
- For a cancelled task, follow the reopen procedure. Carry forward only the original items the user selects and record later outcomes on the carried-forward items.

## Claim, record and hand off

Read [the installed command reference](references/commands.md) before writing. Use the selected project's identity and the selected task's latest `revision` and `context_position`. Send each request as UTF-8 JSON to stdin.

1. **Claim.** Read the current state and the handoff if present. An explicit request to take over a handed-off task authorizes `claim`. If an active run belongs to another session, ask for confirmation before replacing it and use its confirmed `replaces_run_id`. In Codex use the real `CODEX_THREAD_ID` as described above. In other clients use the current client's name and native session identifier when available, or generate a unique label and retain it for the conversation.
2. **Record.** Use `record` for project context and for task context when authorized to write. Use one batch when the same verified progress closes related items. Preserve unresolved questions and other open items unless the work resolves them. Task context can be written without a claim when there is no active run. If another session owns the run, ask before taking over. For the original handoff workflow, use `checkpoint` for progress after a confirmed claim.
3. **Handoff.** Use `handoff` when asked to transfer or close the current run. Include the verified progress and the next action. Show the project and task number, the saved result, and next step after success. A handoff closes the run and preserves the task's base state. A paused task remains paused.
4. **Resolve failures.** For a missing receipt or a busy database, reuse the operation ID and exact request. For stale run, revision conflict, or project or generation mismatch, stop the write and read the current state before deciding how to proceed. An operation with changed input uses a new operation ID.
