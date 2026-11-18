---
name: worklog
description: Use workLog to look up a project's recorded task or handoff, claim it, save a progress checkpoint, or hand it to another client. Applies to workLog tasks such as T001 and requests to 查看进展、接手、记录进展或交接 in workLog.
---

# workLog

Use the installed workLog CLI for project identities, task state and work records. The skill directory is the directory containing the loaded `SKILL.md`. Use its absolute path, or the skill base directory supplied by the client.

## Confirm the project before calling the CLI

- Use the project root the user supplied or already confirmed in this conversation. If the user asks about “this project,” use the identified root of that project.
- A bare task number with multiple possible projects requires a question. Ask which project to use and end the turn before querying any project's identity, task, snapshot or records. If a project selector is canceled or remains unanswered, return the question as plain text and finish the turn. Query several projects only on a request to read each of them, using their separately confirmed roots.

## Read

```sh
python3 -B "<skill directory>/scripts/worklog.py" --project "<confirmed absolute project directory>" identity
python3 -B "<skill directory>/scripts/worklog.py" --project "<confirmed absolute project directory>" task-get T001
```

- Run `identity` and `task-get` to confirm the project name, `project_id`, `data_generation`, task ID and latest state. Keep a separate identity and task for each project. Fetch `latest_entry` and `latest_handoff` by `entry-get <entry_id>` when the user asks for the original notes or when resuming from a handoff.
- A request to view, summarize or check status uses only read commands. If the project is uninitialized, report it. Use `init` or `task-create` only for an explicit request to initialize or create.
- When reporting two same-number tasks, identify their projects and present their latest facts separately. A new session can recover the task and handoff from the project CLI.

## Claim, record and hand off

Read [the installed command reference](references/commands.md) for JSON fields and error handling before writing. Run the bundled CLI with the confirmed absolute project directory. Use the project identity from its own response and the task's latest revision. Send each request as UTF-8 JSON to stdin.

1. **Claim.** Read the current state and the handoff if present. An explicit request to take over a handed-off task authorizes `claim`. If an active run belongs to another session, ask for confirmation before replacing it. Use the current client's name and the current native session identifier when available, or generate a unique label and retain it for the conversation.
2. **Record.** Use `checkpoint` for observed progress after a confirmed claim. Include a short summary, Markdown body, and a concrete next step. Preserve supplied Markdown as written. Obtain the current run ID and revision from the successful claim or most recent receipt. If no run for this session is known, ask before claiming unless the user also requested takeover.
3. **Handoff.** Use `handoff` when asked to transfer or close the current run. Include the verified progress and the next action. Show the project and task number, the saved result, and next step after success. A handoff closes the run and leaves the task in progress.
4. **Resolve failures.** For a missing receipt or a busy database, reuse the operation ID and exact request. For stale run, revision conflict, or project or generation mismatch, stop the write and read the current state before deciding how to proceed. An operation with changed input uses a new operation ID.
