---
name: worklog
description: Use workLog to discover project tasks, read project and task context, save results, track open items, or hand off to another client. Applies to workLog tasks such as T001 and requests to 查询摘要、读取上下文、记录结果、接手或交接 in workLog.
---

# workLog

Use the installed workLog CLI for project identities, task state and work records. The skill directory is the directory containing the loaded `SKILL.md`. Use its absolute path, or the skill base directory supplied by the client.

## Confirm the project before calling the CLI

- Use the supplied project root if the user gives one. When asked to work on the current project and the session starts at its root, use the current directory. For another confirmed project, put `--project <root>` before the command.
- A bare task number with multiple possible projects requires a question. Ask which project to use and end the turn before querying any project's identity, task, snapshot or records. If a project selector is canceled or remains unanswered, return the question as plain text and finish the turn. Query several projects only on a request to read each of them, using their separately confirmed roots.

## Read

```sh
python3 -B "<skill directory>/scripts/worklog.py" summaries --type task --status IN_PROGRESS
python3 -B "<skill directory>/scripts/worklog.py" context
python3 -B "<skill directory>/scripts/worklog.py" context --task T001
python3 -B "<skill directory>/scripts/worklog.py" --project "<confirmed absolute project directory>" identity
python3 -B "<skill directory>/scripts/worklog.py" --project "<confirmed absolute project directory>" task-get T001
```

- In a new session with only the current project directory, use `summaries` first to discover tasks. Read project and selected task `context`, then use `record-get` or `entry-get` with the exact ID in each `source` to open the needed original. Keep project decisions and constraints distinct from the task's own records.
- Look at the saved open items, original work instructions, and referenced current files before continuing. If reading only, leave the project and its database untouched. To check closed or replaced items, use `summaries --type record --status RESOLVED` or `--status SUPERSEDED`.
- Run `identity` and `task-get` to confirm the project name, `project_id`, `data_generation`, task ID and latest state. Keep a separate identity and task for each project. Fetch `latest_entry` and `latest_handoff` by `entry-get <entry_id>` when the user asks for the original notes or when resuming from a handoff.
- A request to view, summarize or check status uses only read commands. If the project is uninitialized, report it. Use `init` or `task-create` only for an explicit request to initialize or create.
- When reporting two same-number tasks, identify their projects and present their latest facts separately. A new session can recover the task and handoff from the project CLI.

## Claim, record and hand off

Read [the installed command reference](references/commands.md) before writing. Use the selected project's identity and the selected task's latest `revision` and `context_position`. Send each request as UTF-8 JSON to stdin.

1. **Claim.** Read the current state and the handoff if present. An explicit request to take over a handed-off task authorizes `claim`. If an active run belongs to another session, ask for confirmation before replacing it. Use the current client's name and the current native session identifier when available, or generate a unique label and retain it for the conversation.
2. **Record.** Use `record` for project context and for task context when authorized to write. Use one batch when the same verified progress closes related items. Preserve unresolved questions and other open items unless the work resolves them. Task context can be written without a claim when there is no active run. If another session owns the run, ask before taking over. For the original handoff workflow, use `checkpoint` for progress after a confirmed claim.
3. **Handoff.** Use `handoff` when asked to transfer or close the current run. Include the verified progress and the next action. Show the project and task number, the saved result, and next step after success. A handoff closes the run and leaves the task in progress.
4. **Resolve failures.** For a missing receipt or a busy database, reuse the operation ID and exact request. For stale run, revision conflict, or project or generation mismatch, stop the write and read the current state before deciding how to proceed. An operation with changed input uses a new operation ID.
